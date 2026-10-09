from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import re
import time
import httpx
from ..log import private_transport
from .writes import WriteWorker


class LensContextBridgeError(RuntimeError):
    """A bounded, non-sensitive reason why a Lens bridge read was rejected."""


# One-time operator-reviewed accounting reconciliations. Each is bound to an
# immutable execution id and sequence: it can never affect a later execution
# or make another provider request.
_INTERRUPTED_RECEIPTS = {
    ("260d7063-1b3e-4423-9f73-36056249914e", 17): {
        # 1,316 input × $1.25/M + 1,034 output × $10/M; no cached input.
        "cost_usd": 0.011984,
        "error": "ENGINE_INTERRUPTED",
        "basis": "PROVIDER_USAGE_RECEIPT",
        # SHA-256 of the canonicalized, non-secret provider usage receipt.
        "evidence_sha256": "1fab0d8424d321718e63314cc370a582f27cc87c5362153822316efc73429780",
    },
    ("ae7489f4-3a7a-401a-b646-f67c56494e5d", 34): {
        # Operator-reviewed conservative reserve. OpenAI reported $0.03514625;
        # Anthropic failed during dispatch without a request id or usage. Charge
        # the complete authorized run ceiling instead of asserting an unknown
        # provider invoice. This can overstate spend but can never understate it.
        "cost_usd": 2.0,
        "error": "PROVIDER_ERROR",
        "basis": "CONSERVATIVE_RUN_BUDGET_RESERVE",
        "evidence_sha256": "11eb05d6d30e7eb8aa5f72a6820ca8223b4ebd9070e5fde15326e1096daa82eb",
    },
    ("49bea683-e185-4aca-86f6-6d2e6922c2e4", 37): {
        # The bridge transport exceeded its 390-second boundary and returned
        # no engine id or usage. Reserve the complete authorized run ceiling;
        # never retry an execution whose provider outcome is unknown.
        "cost_usd": 2.0,
        "error": "TRANSPORT_UNCERTAIN",
        "basis": "CONSERVATIVE_RUN_BUDGET_RESERVE",
        "evidence_sha256": "316d6ffecba8f637735246074033080a3d204a4b47d2b59b8810c5d447244268",
    },
}


class Bridge:
    def __init__(self, url, secret):
        self.url, self.secret = url, secret

    async def call(self, action, row, request=None):
        return await self._call({"action": action, "seq": row["seq"], "id": row["id"],
                              "fingerprint": row["fingerprint"], "request": request,
                              "origin_model": row["origin_model"] if "origin_model" in row.keys() else "ANTHROPIC"}, row)

    async def call_write(self, action, row, write):
        return await self._call({"action": action, "seq": row["seq"], "id": row["id"],
                                 "fingerprint": row["fingerprint"], "write": write}, row)

    async def call_lens_context(self, request: str, session_id: str | None = None):
        """Signed, read-only Lens retrieval. It never enters the durable spend queue."""
        if not isinstance(request, str) or not request.strip() or len(request.encode("utf-8")) > 3_500:
            raise ValueError("invalid_lens_context_request")
        if session_id is not None and not isinstance(session_id, str):
            raise ValueError("invalid_lens_context_session")
        payload_data = {"action": "lens_context", "request": request, "timestamp": int(time.time())}
        if session_id is not None:
            payload_data["session_id"] = session_id
        payload = json.dumps(
            payload_data,
            separators=(",", ":"), ensure_ascii=False)
        signature = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = private_transport.set(True)
        try:
            # Apps Script can take longer than a conventional HTTP API while it
            # opens a bounded, read-only provider session.  This route never
            # enters the durable execution queue, so its timeout is isolated
            # from productive-write transport limits.
            async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
                response = await client.post(self.url, json={"payload": payload, "signature": signature})
                if response.status_code < 200 or response.status_code >= 300:
                    raise LensContextBridgeError(f"BRIDGE_HTTP_{response.status_code}")
                if len(response.content) > 80_000:
                    raise LensContextBridgeError("BRIDGE_RESPONSE_TOO_LARGE")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise LensContextBridgeError("BRIDGE_INVALID_JSON") from exc
        except httpx.TimeoutException as exc:
            raise LensContextBridgeError("BRIDGE_TIMEOUT") from exc
        finally:
            private_transport.reset(token)
        try:
            return _validate_lens_context_response(data)
        except ValueError as exc:
            # Do not expose a Bridge body: it can contain internal errors or
            # credentials.  The contract class alone is sufficient to repair
            # the route safely.
            raise LensContextBridgeError("BRIDGE_CONTRACT_REJECTED") from exc

    async def _call(self, envelope, row):
        payload = json.dumps({**envelope, "timestamp": int(time.time())}, separators=(",", ":"), ensure_ascii=False)
        signature = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = private_transport.set(True)
        try:
            async with httpx.AsyncClient(timeout=390, follow_redirects=True) as client:
                response = await client.post(self.url, json={"payload": payload, "signature": signature})
                response.raise_for_status()
                if len(response.content) > 500000:
                    raise ValueError("bridge_response_too_large")
                data = response.json()
        finally:
            private_transport.reset(token)
        if not isinstance(data, dict) or data.get("id") != row["id"] or data.get("seq") != row["seq"]:
            raise ValueError("bridge_correlation_mismatch")
        return data


class Worker:
    def __init__(self, queue, bridge):
        self.queue, self.bridge = queue, bridge
        self.last_review = None
        self.write_worker = WriteWorker(queue, bridge)

    def reconcile_interrupted_receipt(self, row):
        receipt = _INTERRUPTED_RECEIPTS.get((row["id"], row["seq"]))
        if receipt is None:
            return False
        result = {
            "status": "FAILED",
            "error": receipt["error"],
            "cost_known": True,
            "cost_usd": receipt["cost_usd"],
            "requires_review": False,
            "final_answer": None,
            "accounting_reconciliation": {
                "execution_id": row["id"],
                "seq": row["seq"],
                "ledger_verified": True,
                "evidence_sha256": receipt["evidence_sha256"],
                "basis": receipt["basis"],
            },
        }
        return self.queue.reconcile_accounting(row, result)

    async def step(self):
        paused = self.queue.paused_execution()
        if paused:
            if self.reconcile_interrupted_receipt(paused):
                return
            if self.last_review is not None and time.monotonic() - self.last_review < 60:
                return
            self.last_review = time.monotonic()
            # A paused gateway may read an explicitly reviewed receipt, never run.
            try:
                reply = await self.bridge.call("status", paused)
                if reply.get("state") == "DONE" and isinstance(reply.get("result"), dict):
                    self.queue.reconcile_accounting(paused, reply["result"])
            except (httpx.HTTPError, ValueError, TypeError):
                pass
            return
        await self.write_worker.reconcile_known_object()
        row = self.queue.next()
        if not row:
            return
        if row["status"] == "APPLYING":
            await self.write_worker.step(row)
            return
        if row["status"] == "QUEUED" and self.queue.claim(row):
            # From this commit onward a transport failure NEVER causes another run.
            action, request = "run", self.queue.request(row)
        else:
            action, request = "status", None
        try:
            reply = await self.bridge.call(action, row, request)
        except (httpx.HTTPError, ValueError, TypeError):
            self.expire_uncertain(row)
            return  # keep the durable dispatch receipt and reconcile later
        if reply.get("state") == "DONE":
            result = reply.get("result")
            if isinstance(result, dict) and result.get("status") in {"COMPLETED", "FAILED", "REQUIRES_ANDRES"}:
                self.queue.finish(row, result)
                return
        self.expire_uncertain(row)
        # Missing/expired bridge receipts are NOT proof no paid call happened.
        # The row remains DISPATCHED; block subsequent spend until inspected.

    def expire_uncertain(self, row):
        if row["dispatched"] and time.time() - row["dispatched"] > 900:
            self.queue.finish(row, {"status":"FAILED", "error":"TRANSPORT_UNCERTAIN",
                "requires_review":True, "cost_known":False, "cost_usd":None,
                "engine_execution_id":None, "final_answer":None})

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.step()
            except Exception:
                # No exception body (could contain payload/keys) reaches logs.
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass


def _validate_lens_context_response(data):
    """Accept only the narrow, read-only Lens capsule contract from Bridge."""
    statuses = {"READY", "REQUIRES_CONTEXT", "ABSTAIN", "INVALID_REQUEST", "UNAVAILABLE"}
    if not isinstance(data, dict) or data.get("status") not in statuses:
        raise ValueError("invalid_lens_context_response")
    if data["status"] != "READY":
        return data
    context = data.get("resolved_context")
    documents = data.get("documents")
    if (not isinstance(context, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", context)
            or not isinstance(documents, list) or len(documents) > 4):
        raise ValueError("invalid_lens_context_response")
    for document in documents:
        if (not isinstance(document, dict) or document.get("context") != context
                or document.get("source") not in {"NOTION", "ASANA", "DRIVE", "CALENDAR"}
                or not isinstance(document.get("content"), str)
                or len(document["content"].encode("utf-8")) > 1_200):
            raise ValueError("invalid_lens_context_response")
    return data
