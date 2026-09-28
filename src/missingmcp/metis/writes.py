"""Durable, encrypted write intents. No provider credential lives in this layer.

An intent is committed before dispatch; recovery only asks the signed bridge for
its receipt. An uncertain operation pauses writes without disabling read runs.
"""
from __future__ import annotations

import hashlib
import json
import time

from ..store import encrypt, decrypt


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS metis_write_actions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        execution_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        fingerprint TEXT NOT NULL, action_enc TEXT NOT NULL,
        status TEXT NOT NULL, dispatched REAL, dispatch_seq INTEGER UNIQUE, result_enc TEXT,
        UNIQUE(execution_id, ordinal))""")
    db.execute("CREATE TABLE IF NOT EXISTS metis_write_pause (execution_id TEXT PRIMARY KEY)")


def validate_plan(result):
    plan = result.get("write_plan", [])
    if not isinstance(plan, list) or len(plan) > 8:
        raise ValueError("invalid_write_plan")
    if not plan:
        return []
    if (result.get("status") != "COMPLETED" or result.get("write_plan_verified") is not True
            or result.get("cost_known") is not True or result.get("requires_review")
            or not result.get("resolved_context")):
        raise ValueError("unverified_write_plan")
    seen = set()
    for index, item in enumerate(plan, 1):
        if not isinstance(item, dict) or set(item) != {"ordinal", "action"} or item["ordinal"] != index or isinstance(item["ordinal"], bool):
            raise ValueError("invalid_write_identity")
        action = item["action"]
        expected = {"context", "destination", "provider", "object_id", "operation", "payload", "snapshot", "policy_revision", "policy_hash"}
        if (not isinstance(action, dict) or set(action) != expected or
                action["context"] != result["resolved_context"] or
                action["provider"] not in {"NOTION", "ASANA", "DRIVE"} or
                action["operation"] not in {"create", "update"} or
                not isinstance(action["object_id"], str) or not isinstance(action["payload"], dict) or
                not isinstance(action["snapshot"], dict) or len(canonical(action).encode("utf-8")) > 32000):
            raise ValueError("invalid_write_action")
        target_id = action["object_id"].replace("-", "").lower() if action["provider"] == "NOTION" else action["object_id"]
        target = (action["provider"], target_id, canonical(action["payload"]) if action["operation"] == "create" else None)
        if target in seen:
            raise ValueError("conflicting_write_targets")
        seen.add(target)
    return plan


def schedule(queue, execution_id, result):
    """Called inside Queue.finish's transaction: all or no intents are recorded."""
    plan = validate_plan(result)
    if not plan:
        return result["status"]
    if queue.db.execute("SELECT 1 FROM metis_write_pause LIMIT 1").fetchone():
        result["final_answer"] = "Las escrituras están pausadas por una operación incierta. El plan no se ejecutó."
        result["write_error"] = "WRITES_PAUSED"
        return "REQUIRES_ANDRES"
    for item in plan:
        action = canonical(item["action"])
        queue.db.execute("""INSERT INTO metis_write_actions
            (execution_id,ordinal,fingerprint,action_enc,status) VALUES (?,?,?,?,'PREPARED')""",
            (execution_id, item["ordinal"], hashlib.sha256(action.encode()).hexdigest(), encrypt(queue.secret, action)))
    return "APPLYING"


def receipts(queue, execution_id):
    values = []
    for row in queue.db.execute("SELECT * FROM metis_write_actions WHERE execution_id=? ORDER BY ordinal", (execution_id,)):
        result = json.loads(decrypt(queue.secret, row["result_enc"])) if row["result_enc"] else {}
        values.append({**result, "ordinal": row["ordinal"], "status": row["status"]})
    return values


class WriteWorker:
    def __init__(self, queue, bridge):
        self.queue, self.bridge = queue, bridge
        self.last_reconciliation = None

    async def reconcile_known_object(self):
        """Ask only for a receipt; never resend an uncertain mutation."""
        if self.last_reconciliation is not None and time.monotonic() - self.last_reconciliation < 60:
            return
        q = self.queue
        row = q.db.execute("""SELECT a.* FROM metis_write_actions a
            JOIN metis_write_pause p ON p.execution_id=a.execution_id
            WHERE a.status='UNCERTAIN' ORDER BY a.dispatch_seq DESC LIMIT 1""").fetchone()
        if not row or not row['result_enc']:
            return
        self.last_reconciliation = time.monotonic()
        prior = json.loads(decrypt(q.secret, row['result_enc']))
        if not prior.get('provider_object_id'):
            return
        action = json.loads(decrypt(q.secret, row['action_enc']))
        wire = {'seq': row['dispatch_seq'], 'id': row['execution_id'], 'fingerprint': row['fingerprint']}
        try:
            reply = await self.bridge.call_write('write_status', wire, action)
        except Exception:
            return
        result = reply.get('result', {})
        if (reply.get('state') != 'DONE' or result.get('status') != 'CONFIRMED'
                or result.get('verified') is not True
                or result.get('provider_object_id') != prior['provider_object_id']
                or result.get('provider') != action['provider']
                or result.get('reconciliation', {}).get('method') != 'READBACK_KNOWN_OBJECT'):
            return
        q.db.execute('BEGIN IMMEDIATE')
        try:
            changed = q.db.execute("""UPDATE metis_write_actions SET status='CONFIRMED',result_enc=?
                WHERE seq=? AND status='UNCERTAIN' AND result_enc=?""",
                (encrypt(q.secret, json.dumps(result, ensure_ascii=False)), row['seq'], row['result_enc'])).rowcount
            if changed and not q.db.execute("SELECT 1 FROM metis_write_actions WHERE execution_id=? AND status='UNCERTAIN'", (row['execution_id'],)).fetchone():
                q.db.execute('DELETE FROM metis_write_pause WHERE execution_id=?', (row['execution_id'],))
            if changed:
                self.finish_execution({'id': row['execution_id']}, reconciled=True)
            q.db.execute('COMMIT')
        except BaseException:
            q.db.execute('ROLLBACK')
            raise

    async def step(self, execution):
        q = self.queue
        row = q.db.execute("""SELECT * FROM metis_write_actions WHERE execution_id=?
            AND status IN ('PREPARED','SENT') ORDER BY ordinal LIMIT 1""", (execution["id"],)).fetchone()
        if not row:
            self.finish_execution(execution)
            return
        if q.db.execute("SELECT 1 FROM metis_write_pause LIMIT 1").fetchone():
            self.finish_action(execution, row, {"status": "REJECTED", "verified": False, "error": "WRITES_PAUSED"})
            return
        action = "write_status"
        if row["status"] == "PREPARED":
            # CAS plus synchronous FULL commit before any network operation.
            q.db.execute("BEGIN IMMEDIATE")
            try:
                sequence = q.db.execute("SELECT coalesce(max(dispatch_seq),0)+1 FROM metis_write_actions").fetchone()[0]
                claimed = q.db.execute("UPDATE metis_write_actions SET status='SENT',dispatched=?,dispatch_seq=? WHERE seq=? AND status='PREPARED'",
                                       (time.time(), sequence, row["seq"])).rowcount
                q.db.execute("COMMIT")
            except BaseException:
                q.db.execute("ROLLBACK")
                raise
            if not claimed:
                return
            action = "write"
            row = q.db.execute("SELECT * FROM metis_write_actions WHERE seq=?", (row["seq"],)).fetchone()
        wire = {"seq": row["dispatch_seq"], "id": execution["id"], "fingerprint": row["fingerprint"]}
        payload = json.loads(decrypt(q.secret, row["action_enc"]))
        try:
            reply = await self.bridge.call_write(action, wire, payload)
            result = reply.get("result") if reply.get("state") == "DONE" else None
            if isinstance(result, dict) and result.get("status") in {"CONFIRMED", "REJECTED", "UNCERTAIN"}:
                if result["status"] == "CONFIRMED" and (result.get("verified") is not True or not result.get("provider_object_id")):
                    result = {"status": "UNCERTAIN", "verified": False, "error": "INVALID_WRITE_RECEIPT"}
                self.finish_action(execution, row, result)
                return
        except Exception:
            # Never print an exception containing provider content/credentials.
            pass
        current = q.db.execute("SELECT dispatched FROM metis_write_actions WHERE seq=?", (row["seq"],)).fetchone()
        if current["dispatched"] and time.time() - current["dispatched"] > 900:
            self.finish_action(execution, row, {"status": "UNCERTAIN", "verified": False, "error": "WRITE_TRANSPORT_UNCERTAIN"})

    def finish_action(self, execution, row, result):
        q = self.queue
        q.db.execute("BEGIN IMMEDIATE")
        try:
            changed = q.db.execute("UPDATE metis_write_actions SET status=?,result_enc=? WHERE seq=? AND status IN ('PREPARED','SENT')",
                (result["status"], encrypt(q.secret, json.dumps(result, ensure_ascii=False)), row["seq"])).rowcount
            if changed and result["status"] != "CONFIRMED":
                q.db.execute("UPDATE metis_write_actions SET status='SKIPPED' WHERE execution_id=? AND status='PREPARED'", (execution["id"],))
                if result["status"] == "UNCERTAIN":
                    q.db.execute("INSERT OR IGNORE INTO metis_write_pause VALUES (?)", (execution["id"],))
            q.db.execute("COMMIT")
        except BaseException:
            q.db.execute("ROLLBACK")
            raise
        self.finish_execution(execution)

    def finish_execution(self, execution, reconciled=False):
        q = self.queue
        entries = receipts(q, execution["id"])
        if not entries or any(x["status"] in {"PREPARED", "SENT"} for x in entries):
            return
        success = all(x["status"] == "CONFIRMED" for x in entries)
        status = "COMPLETED" if success else "REQUIRES_ANDRES"
        current = q.db.execute("SELECT result_enc FROM metis_executions WHERE id=?", (execution["id"],)).fetchone()
        result = json.loads(decrypt(q.secret, current["result_enc"]))
        result["status"] = status
        lines = ["Escrituras completadas y verificadas." if success else "El plan no se completó. Estos son los resultados comprobados:"]
        for item in entries:
            if item["status"] == "CONFIRMED":
                lines.append(f"- Operación {item['ordinal']}: verificada. {item.get('url', item['provider_object_id'])}")
            elif item["status"] == "UNCERTAIN":
                lines.append(f"- Operación {item['ordinal']}: resultado incierto; no se repetirá automáticamente.")
            else:
                lines.append(f"- Operación {item['ordinal']}: no ejecutada ({item['status']}).")
        result["final_answer"] = "\n".join(lines)
        result["write_receipts"] = entries
        q.db.execute("UPDATE metis_executions SET status=?,result_enc=? WHERE id=? AND (status='APPLYING' OR (? AND status='REQUIRES_ANDRES'))",
                     (status, encrypt(q.secret, json.dumps(result, ensure_ascii=False)), execution["id"], reconciled))
