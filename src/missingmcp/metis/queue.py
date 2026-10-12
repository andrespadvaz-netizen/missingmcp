from __future__ import annotations
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from ..store import encrypt, decrypt
from . import writes

TERMINAL = {"COMPLETED", "FAILED", "REQUIRES_ANDRES"}
MAX_REQUEST_BYTES = 128_000
MAX_ENVELOPE_BYTES = 1_048_576
KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
REGISTERED_CONTEXT_ROOTS = {
    "METIS", "ANDREA", "SHOKKO", "VENTURE_QUEST",
    "ARQUITECTO_INTERIOR", "PERSONAL", "FINAL_FINAL",
}


class RequestError(ValueError):
    pass


def canonical_context(value):
    """Resolve an explicit label without consulting content or guessing a project.

    Human-facing labels may contain spaces around ``/`` or inside a segment.
    Only a registered root is authoritative; subproject segments are preserved as
    deterministic uppercase identifiers so PERSONAL / Health OS is transportable.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise RequestError("context.project requires the exact registered project ID.")
    parts = [part.strip() for part in value.strip().split("/")]
    if any(not part or part in {".", ".."} for part in parts):
        raise RequestError("context.project requires the exact registered project ID.")
    normalized = []
    for part in parts:
        part = re.sub(r"[\s-]+", "_", part.upper().lstrip("."))
        if not re.fullmatch(r"[A-Z0-9_.-]+", part):
            raise RequestError("context.project requires the exact registered project ID.")
        normalized.append(part)
    if normalized[0] not in REGISTERED_CONTEXT_ROOTS:
        raise RequestError("context_unregistered: use a registered project ID in context.project or the first Contexto: line.")
    return "/".join(normalized)


def validate(args):
    if not isinstance(args, dict) or set(args) not in ({"request", "idempotency_key"}, {"request", "idempotency_key", "context"}):
        raise RequestError("Use request, idempotency_key and optional context only.")
    text, key = args["request"], args["idempotency_key"]
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_REQUEST_BYTES:
        raise RequestError(f"request must contain 1–{MAX_REQUEST_BYTES} UTF-8 bytes.")
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise RequestError("idempotency_key must contain 16–128 letters, digits, _ or -.")
    declared = re.match(r"\s*Contexto:\s*([^\r\n]+)", text, re.IGNORECASE)
    context_project = canonical_context(declared.group(1)) if declared else None
    if "context" in args:
        context = args["context"]
        if not isinstance(context, dict) or set(context) != {"project", "source"}:
            raise RequestError("context requires project and source.")
        project = canonical_context(context["project"])
        if not isinstance(context['source'], str) or context["source"] not in {"current_request", "current_conversation", "visible_project_instructions"}:
            raise RequestError("context.source must identify available conversation context, not retrieved instructions.")
        if declared and context_project != project:
            raise RequestError("context_conflict: explicit request context differs from structured context.")
        context_project = project
        # The authenticated client asserts context, not authority. Engine policy still validates it.
        # Carry provenance in the encrypted request, covered by existing fingerprint/signature.
        text = f"Contexto: {project}\nProcedencia del contexto declarada por el cliente: {context['source']}\n\n{text}"
        if len(text.encode()) > MAX_REQUEST_BYTES:
            raise RequestError(f"request plus context exceeds {MAX_REQUEST_BYTES} UTF-8 bytes; no content was truncated.")
    if context_project is None:
        raise RequestError("context_required: provide context.project or begin the request with an exact Contexto: line; no execution was created.")
    return text, key, context_project


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
        self.db.execute("""CREATE TABLE IF NOT EXISTS metis_direct_audits (
            receipt_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
            context_project TEXT NOT NULL, origin_model TEXT NOT NULL,
            target_provider TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, elapsed_ms INTEGER NOT NULL,
            cost_known INTEGER NOT NULL, cost_usd REAL,
            provider_request_id TEXT, request_sha256 TEXT NOT NULL,
            receipt_enc TEXT NOT NULL)""")
        writes.initialize(self.db)
        columns = {x[1] for x in self.db.execute("PRAGMA table_info(metis_executions)")}
        if "origin_model" not in columns:
            self.db.execute("ALTER TABLE metis_executions ADD COLUMN origin_model TEXT NOT NULL DEFAULT 'ANTHROPIC'")
        if "context_project" not in columns:
            self.db.execute("ALTER TABLE metis_executions ADD COLUMN context_project TEXT NOT NULL DEFAULT 'UNSCOPED'")

    def create(self, owner, args, origin_model="ANTHROPIC"):
        if origin_model not in {"ANTHROPIC", "OPENAI"}:
            raise RequestError("invalid_origin")
        text, key, context_project = validate(args)
        fp = hashlib.sha256(text.encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM metis_executions WHERE owner=? AND idem=?", (owner, key)).fetchone()
            if row:
                if row["fingerprint"] != fp:
                    raise RequestError("idempotency_conflict: reuse the key only for the identical request.")
            else:
                if self.db.execute("SELECT 1 FROM metis_pause p JOIN metis_executions e ON e.id=p.execution_id WHERE e.context_project=? LIMIT 1", (context_project,)).fetchone():
                    raise RequestError("context_paused: an uncertain execution in this context requires operator review.")
                # Durable ingress limits supplement OAuth's per-IP/token limits.
                now = time.time()
                if self.db.execute("SELECT count(*) FROM metis_executions WHERE created>?", (now-3600,)).fetchone()[0] >= 20:
                    raise RequestError("hourly_request_limit")
                if self.db.execute("SELECT count(*) FROM metis_executions WHERE status IN ('QUEUED','DISPATCHED','APPLYING')").fetchone()[0] >= 5:
                    raise RequestError("queue_full")
                eid = str(uuid.uuid4())
                self.db.execute("INSERT INTO metis_executions (id,owner,idem,fingerprint,request_enc,status,created,context_project) VALUES (?,?,?,?,?,'QUEUED',?,?)",
                                (eid, owner, key, fp, encrypt(self.secret, text), now, context_project))
                self.db.execute("UPDATE metis_executions SET origin_model=? WHERE id=?", (origin_model, eid))
                row = self.db.execute("SELECT * FROM metis_executions WHERE id=?", (eid,)).fetchone()
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.view(row)

    def record_direct_audit(self, owner, origin_model, request, result):
        """Persist the direct relay receipt without creating queue work or external writes."""
        receipt_id = result.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise ValueError("direct_receipt_id_required")
        cost = result.get("cost_usd")
        self.db.execute("""INSERT OR IGNORE INTO metis_direct_audits
            (receipt_id,owner,context_project,origin_model,target_provider,status,
             created,elapsed_ms,cost_known,cost_usd,provider_request_id,
             request_sha256,receipt_enc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                receipt_id, owner, result.get("context_project", "UNSCOPED"),
                origin_model, result.get("target_model_provider", "UNKNOWN"),
                result.get("status", "FAILED"), time.time(),
                int(result.get("elapsed_ms") or 0),
                1 if result.get("cost_known") is True else 0,
                cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
                result.get("provider_request_id"), hashlib.sha256(request.encode()).hexdigest(),
                encrypt(self.secret, json.dumps(result, ensure_ascii=False))))
        return receipt_id

    def get(self, owner, eid):
        if not isinstance(eid, str):
            raise RequestError("invalid_execution_id")
        row = self.db.execute("SELECT * FROM metis_executions WHERE id=? AND owner=?", (eid, owner)).fetchone()
        if not row:
            raise RequestError("execution_not_found")
        return self.view(row)

    def view(self, row):
        result = json.loads(decrypt(self.secret, row["result_enc"])) if row["result_enc"] else {}
        plan = result.pop("write_plan", [])
        if plan:
            result["write_plan_count"] = len(plan)
            result["write_receipts"] = writes.receipts(self, row["id"])
        result["writes_paused"] = bool(self.db.execute("SELECT 1 FROM metis_write_pause LIMIT 1").fetchone())
        return {**result, "execution_id": row["id"], "correlation_id": row["id"],
                "status": row["status"], "created_at": row["created"],
                "dispatched_at": row["dispatched"],
                "queue_wait_ms": (round((row["dispatched"] - row["created"]) * 1000)
                                  if row["dispatched"] is not None else None),
                "context_project": row["context_project"],
                "context_paused": bool(self.db.execute("SELECT 1 FROM metis_pause p JOIN metis_executions e ON e.id=p.execution_id WHERE e.context_project=? LIMIT 1", (row["context_project"],)).fetchone()),
                "gateway_paused": False,
                "poll_after_seconds": 15 if row["status"] not in TERMINAL else None}

    def next(self):
        return self.db.execute("""SELECT e.* FROM metis_executions e
            WHERE e.status IN ('QUEUED','DISPATCHED','APPLYING')
              AND NOT EXISTS (SELECT 1 FROM metis_pause p JOIN metis_executions pe ON pe.id=p.execution_id WHERE pe.context_project=e.context_project)
            ORDER BY e.seq LIMIT 1""").fetchone()

    def claim(self, row):
        return self.db.execute("UPDATE metis_executions SET status='DISPATCHED', dispatched=? WHERE id=? AND status='QUEUED'",
                               (time.time(), row["id"])).rowcount == 1

    def paused_execution(self):
        return self.db.execute("SELECT e.* FROM metis_executions e JOIN metis_pause p ON p.execution_id=e.id ORDER BY e.seq LIMIT 1").fetchone()

    def reconcile_accounting(self, row, result):
        """Accept only an operator-reviewed bridge receipt; never rerun a failure."""
        proof = result.get("accounting_reconciliation", {})
        cost = result.get("cost_usd")
        if (result.get("status") != "FAILED" or result.get("cost_known") is not True
                or result.get("requires_review") is not False
                or isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or cost < 0
                or not isinstance(proof, dict)
                or proof.get("execution_id") != row["id"] or proof.get("seq") != row["seq"]
                or proof.get("ledger_verified") is not True
                or not re.fullmatch(r"[a-f0-9]{64}", str(proof.get("evidence_sha256", "")))):
            return False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.db.execute("SELECT * FROM metis_executions WHERE id=?", (row["id"],)).fetchone()
            original = json.loads(decrypt(self.secret, current["result_enc"]))
            paused = self.db.execute("SELECT 1 FROM metis_pause WHERE execution_id=?", (row["id"],)).fetchone()
            if not paused or current["status"] != "FAILED" or original.get("cost_known") is not False:
                self.db.execute("COMMIT")
                return False
            reviewed = {**result, "original_failure": original}
            self.db.execute("UPDATE metis_executions SET result_enc=? WHERE id=?", (encrypt(self.secret, json.dumps(reviewed)), row["id"]))
            self.db.execute("DELETE FROM metis_pause WHERE execution_id=?", (row["id"],))
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reconcile_terminal(self, row, result):
        """Replace an uncertain timeout with the signed terminal bridge receipt.

        The bridge is monotonic by (id, seq), so a later DONE receipt for the
        same pair is stronger evidence than the gateway's local timeout.
        """
        status = result.get("status")
        cost = result.get("cost_usd")
        if (status not in TERMINAL or result.get("cost_known") is not True
                or result.get("requires_review") is not False
                or isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or cost < 0):
            return False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.db.execute("SELECT * FROM metis_executions WHERE id=? AND seq=?",
                                      (row["id"], row["seq"])).fetchone()
            paused = self.db.execute("SELECT 1 FROM metis_pause WHERE execution_id=?", (row["id"],)).fetchone()
            original = json.loads(decrypt(self.secret, current["result_enc"])) if current and current["result_enc"] else {}
            uncertain_errors = {
                "TRANSPORT_UNCERTAIN", "ENGINE_INTERRUPTED",
                "ENGINE_WORKER_INTERRUPTED", "DURABLE_PROVIDER_UNCERTAIN",
            }
            if (not current or current["status"] != "FAILED" or not paused
                    or original.get("error") not in uncertain_errors):
                self.db.execute("COMMIT")
                return False
            reviewed = {**result, "transport_reconciliation": {
                "execution_id": row["id"], "seq": row["seq"],
                "basis": "SIGNED_TERMINAL_BRIDGE_RECEIPT"},
                "original_failure": original}
            self.db.execute("UPDATE metis_executions SET status=?,result_enc=? WHERE id=?",
                            (status, encrypt(self.secret, json.dumps(reviewed, ensure_ascii=False)), row["id"]))
            self.db.execute("DELETE FROM metis_pause WHERE execution_id=?", (row["id"],))
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, row, result):
        status = result.get("status")
        if status not in TERMINAL:
            raise ValueError("invalid_terminal_status")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.db.execute("SELECT status FROM metis_executions WHERE id=?", (row["id"],)).fetchone()
            if not current or current["status"] != "DISPATCHED":
                self.db.execute("COMMIT")
                return
            try:
                status = writes.schedule(self, row["id"], result)
            except ValueError:
                # A malformed signed plan must terminate without provider I/O,
                # rather than leaving this execution polling forever.
                result = {**result, "status": "REQUIRES_ANDRES", "write_plan": [],
                          "write_plan_verified": False, "write_error": "INVALID_WRITE_PLAN",
                          "final_answer": "El plan no superó la validación. No se ejecutaron escrituras."}
                status = "REQUIRES_ANDRES"
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
