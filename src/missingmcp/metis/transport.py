from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import time
import httpx


class Bridge:
    def __init__(self, url, secret):
        self.url, self.secret = url, secret

    async def call(self, action, row, request=None):
        payload = json.dumps({"action": action, "seq": row["seq"], "id": row["id"],
                              "fingerprint": row["fingerprint"], "request": request,
                              "timestamp": int(time.time())}, separators=(",", ":"), ensure_ascii=False)
        signature = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=390, follow_redirects=True) as client:
            response = await client.post(self.url, json={"payload": payload, "signature": signature})
            response.raise_for_status()
            if len(response.content) > 500000:
                raise ValueError("bridge_response_too_large")
            data = response.json()
        if not isinstance(data, dict) or data.get("id") != row["id"] or data.get("seq") != row["seq"]:
            raise ValueError("bridge_correlation_mismatch")
        return data


class Worker:
    def __init__(self, queue, bridge):
        self.queue, self.bridge = queue, bridge

    async def step(self):
        row = self.queue.next()
        if not row:
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
