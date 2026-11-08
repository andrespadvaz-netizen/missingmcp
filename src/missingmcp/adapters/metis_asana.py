"""One-person, read-only MCP resource for Asana External MCP."""
from __future__ import annotations

import hashlib
import hmac
import json

from .base import LoginError, LoginOk, SessionExpired
from ..log import log
from ..metis.direct_audit import DirectAudit, SYSTEM
from ..metis.queue import Queue, RequestError, MAX_ENVELOPE_BYTES, validate
from ..store import encrypt, decrypt

ACCOUNT = "asana-andres-1211117380475084"
CONTEXTS = frozenset({"SHOKKO", "FINAL_FINAL", "VENTURE_QUEST"})
# Two separately authorized, bounded probes. Reservations are never refunded
# after a provider dispatch, including failed or uncertain outcomes. Units are
# micro-USD at GPT-5's published rate.
PROBE_BUDGET_MICRO_USD = 100_000
PROBE_MAX_CALLS = 2
PROBE_TOTAL_BUDGET_MICRO_USD = PROBE_BUDGET_MICRO_USD * PROBE_MAX_CALLS
PROBE_MAX_REQUEST_BYTES = 8_192
PROBE_MAX_OUTPUT_TOKENS = 4_096
PROBE_INPUT_OVERHEAD_TOKENS = 1_024


def probe_reservation_micro_usd(request: str, context: str) -> int:
    # A text token cannot represent fewer than one UTF-8 byte. Add a generous
    # allowance for API message framing and role tokens beyond the raw text.
    input_bound = (len(SYSTEM.encode()) +
                   len(f"Contexto: {context}\n\n{request}".encode()) +
                   PROBE_INPUT_OVERHEAD_TOKENS)
    return (input_bound * 5 + 3) // 4 + PROBE_MAX_OUTPUT_TOKENS * 10

TOOLS = [{
    "name": "metis_cross_audit",
    "description": "Run one read-only audit of the evidence explicitly supplied in this call. "
                   "Declare exactly one authorized context; supply a stable idempotency key. "
                   "This tool does not read private chats or Gateway accounts and cannot write to Asana or Metis.",
    "inputSchema": {"type": "object", "additionalProperties": False,
                    "properties": {
                        "request": {"type": "string", "minLength": 1,
                                    "maxLength": PROBE_MAX_REQUEST_BYTES},
                        "idempotency_key": {"type": "string", "minLength": 16, "maxLength": 128},
                        "context": {"type": "object", "additionalProperties": False,
                                    "properties": {
                                        "project": {"type": "string", "enum": sorted(CONTEXTS)},
                                        "source": {"type": "string", "enum": ["current_request", "current_conversation", "visible_project_instructions"]}},
                                    "required": ["project", "source"]}},
                    "required": ["request", "idempotency_key", "context"]},
    "annotations": {"readOnlyHint": True, "destructiveHint": False,
                    "idempotentHint": True, "openWorldHint": True},
}]


class MetisAsanaAdapter:
    name = "metis-asana"
    display_name = "Metis para Asana"
    authorize_template = "metis_asana_authorize.html"
    second_factor_template = "metis_asana_authorize.html"
    landing_template = "metis_asana_landing.html"
    static_oauth_client = True

    def __init__(self, config):
        self.config = config
        self.forward = self
        self.credential_version = hashlib.sha256(config.asana_mcp_andres_key.encode()).hexdigest()
        self.queue = Queue(config.db_path, config.gateway_secret)
        self.queue.db.execute("""CREATE TABLE IF NOT EXISTS metis_asana_audits (
            owner TEXT NOT NULL, idem TEXT NOT NULL, fingerprint TEXT NOT NULL,
            state TEXT NOT NULL, response_enc TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(owner, idem))""")
        self.queue.db.execute("""CREATE TABLE IF NOT EXISTS metis_asana_probe_budget (
            owner TEXT PRIMARY KEY, reserved_micro_usd INTEGER NOT NULL,
            call_count INTEGER NOT NULL)""")
        self.direct_audit = DirectAudit(config)

    def login_hint(self, form):
        return "asana-individual"

    def start_login(self, form):
        supplied = form.get("metis_key", "")
        if (not isinstance(supplied, str) or
                not hmac.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(),
                                        self.credential_version)):
            raise LoginError("Clave de conexión no válida.", reason="auth")
        return LoginOk(ACCOUNT, self.credential_version)

    def verify(self, blob):
        if not hmac.compare_digest(blob, self.credential_version):
            raise LoginError("Vuelve a conectar Metis para Asana.", reason="auth")
        return "Metis para Asana"

    async def handle(self, conn, account_key, blob, body):
        if account_key != ACCOUNT or not hmac.compare_digest(blob, self.credential_version):
            raise SessionExpired()

        def response(value, status=200):
            return status, {"Content-Type": "application/json", "Cache-Control": "no-store"}, json.dumps(value, ensure_ascii=False).encode()

        if len(body) > MAX_ENVELOPE_BYTES:
            return response({"error": "too_large"}, 413)
        try:
            req = json.loads(body)
        except (ValueError, UnicodeError):
            return response({"error": "invalid_request"}, 400)
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
            return response({"error": "invalid_request"}, 400)
        method, rid = req["method"], req.get("id")
        if rid is None:
            return (202, {}, b"") if method.startswith("notifications/") else response({"error": "id_required"}, 400)

        def result(value):
            return response({"jsonrpc": "2.0", "id": rid, "result": value})

        def error(code, message):
            return response({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

        if method == "initialize":
            return result({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "metis-asana-gateway", "version": "1.0.0"}})
        if method == "ping":
            return result({})
        if method == "tools/list":
            return result({"tools": TOOLS})
        if method != "tools/call":
            return error(-32601, "Method not found")
        params = req.get("params")
        if not isinstance(params, dict) or params.get("name") != "metis_cross_audit":
            return error(-32602, "Unknown tool")
        args = params.get("arguments")
        try:
            if not isinstance(args, dict) or set(args) != {"request", "idempotency_key", "context"}:
                raise RequestError("Use request, idempotency_key and context only.")
            text, idem, context = validate(args)
            if context not in CONTEXTS:
                raise RequestError("context_forbidden")
            if args["context"].get("source") != "current_request":
                raise RequestError("current_request_required")
            if len(text.encode()) > PROBE_MAX_REQUEST_BYTES:
                raise RequestError("probe_request_too_large")
            if not self.config.metis_openai_api_key:
                raise RequestError("direct_audit_not_configured")
            if self.config.metis_openai_model != "gpt-5":
                raise RequestError("probe_model_price_unverified")
            db = self.queue.db
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            reservation = probe_reservation_micro_usd(text, context)
            if reservation > PROBE_BUDGET_MICRO_USD:
                raise RequestError("probe_budget_exceeded")
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT fingerprint,state,response_enc FROM metis_asana_audits "
                                 "WHERE owner=? AND idem=?", (account_key, idem)).fetchone()
                if row is None:
                    # Count pre-existing dispatches too, including failed or
                    # uncertain calls made before this budget table existed.
                    total = db.execute("SELECT count(*) FROM metis_asana_audits "
                                       "WHERE owner=?", (account_key,)).fetchone()[0]
                    budget = db.execute("SELECT reserved_micro_usd,call_count "
                                        "FROM metis_asana_probe_budget WHERE owner=?",
                                        (account_key,)).fetchone()
                    reserved = budget["reserved_micro_usd"] if budget else 0
                    calls = budget["call_count"] if budget else 0
                    if total >= PROBE_MAX_CALLS or calls >= PROBE_MAX_CALLS or \
                            reserved + reservation > PROBE_TOTAL_BUDGET_MICRO_USD:
                        raise RequestError("probe_budget_exhausted")
                    recent = db.execute("SELECT count(*) FROM metis_asana_audits "
                                        "WHERE owner=? AND created_at > datetime('now','-1 hour')",
                                        (account_key,)).fetchone()[0]
                    if recent >= 20:
                        raise RequestError("hourly_audit_limit")
                    db.execute("INSERT INTO metis_asana_probe_budget(owner,reserved_micro_usd,call_count) "
                               "VALUES (?,?,1) ON CONFLICT(owner) DO UPDATE SET "
                               "reserved_micro_usd=reserved_micro_usd+excluded.reserved_micro_usd, "
                               "call_count=call_count+1", (account_key, reservation))
                    db.execute("INSERT INTO metis_asana_audits(owner,idem,fingerprint,state) "
                               "VALUES (?,?,?,'DISPATCHED')", (account_key, idem, fingerprint))
                elif row["fingerprint"] != fingerprint:
                    raise RequestError("idempotency_conflict")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            if row is not None:
                if row["state"] != "COMPLETED":
                    raise RequestError("outcome_uncertain: operator reconciliation required")
                value = json.loads(decrypt(self.config.gateway_secret, row["response_enc"]))
            else:
                value = await self.direct_audit.run(
                    "ASANA", text, context, max_output_tokens=PROBE_MAX_OUTPUT_TOKENS)
                self.queue.record_direct_audit(account_key, "ASANA", text, value)
                db.execute("UPDATE metis_asana_audits SET state='COMPLETED',response_enc=? "
                           "WHERE owner=? AND idem=?",
                           (encrypt(self.config.gateway_secret, json.dumps(value, ensure_ascii=False)),
                            account_key, idem))
                log("asana-audit-receipt", account=account_key, context=context,
                    status=value.get("status"), receipt_id=value.get("receipt_id"))
            return result({"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                           "isError": False})
        except RequestError as exc:
            return result({"content": [{"type": "text", "text": str(exc)}], "isError": True})
