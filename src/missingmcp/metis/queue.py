from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import time
import uuid
from ..store import encrypt, decrypt

TERMINAL = {"COMPLETED", "FAILED", "REQUIRES_ANDRES"}
KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class RequestError(ValueError):
    pass


def validate(args):
    if not isinstance(args, dict) or set(args) != {"request", "idempotency_key"}:
        raise RequestError("Use request and idempotency_key only.")
    text, key = args["request"], args["idempotency_key"]
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16000:
        raise RequestError("request must contain 1–16000 UTF-8 bytes.")
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise RequestError("idempotency_key must contain 16–128 letters, digits, _ or -.")
    return text, key


class Queue:
    """One SQLite queue on the existing persistent volume; writes commit before I/O.

    Never reset sequence numbers or delete rows: they are permanent spend receipts.
    An interrupted DISPATCHED row is reconciled with the bridge, never re-executed.
    """

    def __init__(self, path, secret):
        self.secret = secret
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS metis_executions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, idem TEXT NOT NULL,
            fingerprint TEXT NOT NULL, request_enc TEXT NOT NULL,
            status TEXT NOT NULL, created REAL NOT NULL, dispatched REAL,
            result_enc TEXT, UNIQUE(owner, idem))""")
        self.db.execute("CREATE TABLE IF NOT EXISTS metis_pause (execution_id TEXT PRIMARY KEY)")

    def create(self, owner, args):
        text, key = validate(args)
        fp = hashlib.sha256(text.encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM metis_executions WHERE owner=? AND idem=?", (owner, key)).fetchone()
            if row:
                if row["fingerprint"] != fp:
                    raise RequestError("idempotency_conflict: reuse the key only for the identical request.")
            else:
                if self.db.execute("SELECT 1 FROM metis_pause LIMIT 1").fetchone():
                    raise RequestError("gateway_paused: an uncertain execution requires operator review.")
                # Durable ingress limits supplement OAuth's per-IP/token limits.
                now = time.time()
                if self.db.execute("SELECT count(*) FROM metis_executions WHERE created>?", (now-3600,)).fetchone()[0] >= 20:
                    raise RequestError("hourly_request_limit")
                if self.db.execute("SELECT count(*) FROM metis_executions WHERE status IN ('QUEUED','DISPATCHED')").fetchone()[0] >= 5:
                    raise RequestError("queue_full")
                eid = str(uuid.uuid4())
                self.db.execute("INSERT INTO metis_executions (id,owner,idem,fingerprint,request_enc,status,created) VALUES (?,?,?,?,?,'QUEUED',?)",
                                (eid, owner, key, fp, encrypt(self.secret, text), now))
                row = self.db.execute("SELECT * FROM metis_executions WHERE id=?", (eid,)).fetchone()
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.view(row)

    def get(self, owner, eid):
        if not isinstance(eid, str):
            raise RequestError("invalid_execution_id")
        row = self.db.execute("SELECT * FROM metis_executions WHERE id=? AND owner=?", (eid, owner)).fetchone()
        if not row:
            raise RequestError("execution_not_found")
        return self.view(row)

    def view(self, row):
        result = json.loads(decrypt(self.secret, row["result_enc"])) if row["result_enc"] else {}
        return {**result, "execution_id": row["id"], "correlation_id": row["id"],
                "status": row["status"], "created_at": row["created"],
                "gateway_paused": bool(self.db.execute("SELECT 1 FROM metis_pause LIMIT 1").fetchone()),
                "poll_after_seconds": 15 if row["status"] not in TERMINAL else None}

    def next(self):
        if self.db.execute("SELECT 1 FROM metis_pause LIMIT 1").fetchone():
            return None
        return self.db.execute("SELECT * FROM metis_executions WHERE status IN ('QUEUED','DISPATCHED') ORDER BY seq LIMIT 1").fetchone()

    def claim(self, row):
        return self.db.execute("UPDATE metis_executions SET status='DISPATCHED', dispatched=? WHERE id=? AND status='QUEUED'",
                               (time.time(), row["id"])).rowcount == 1

    def finish(self, row, result):
        status = result.get("status")
        if status not in TERMINAL:
            raise ValueError("invalid_terminal_status")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if result.get("requires_review"):
                self.db.execute("INSERT OR IGNORE INTO metis_pause VALUES (?)", (row["id"],))
            self.db.execute("UPDATE metis_executions SET status=?,result_enc=? WHERE id=? AND status='DISPATCHED'",
                            (status, encrypt(self.secret, json.dumps(result, ensure_ascii=False)), row["id"]))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def request(self, row):
        return decrypt(self.secret, row["request_enc"])

    def close(self):
        self.db.close()
