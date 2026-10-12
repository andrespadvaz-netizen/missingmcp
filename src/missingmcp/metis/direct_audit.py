from __future__ import annotations

import time
import uuid

import httpx


SYSTEM = """You are the receiving model in the Metis dual-model system.
Perform the requested transfer, review or audit using only the self-contained evidence supplied. Keep project
contexts strictly separated. Do not perform writes, send messages, change a
CANON, or claim access to sources that are not in the request. Distinguish
verified facts from inferences. If a real decision from Andres is indispensable,
begin the answer with REQUIRES_ANDRES: and state the single decision needed.
Otherwise answer in Spanish and use the shortest complete response that satisfies
the request. Do not invent objections when the request is a simple receipt check."""

PRICE_USD_PER_1K = {
    "OPENAI": {"input": 0.00125, "output": 0.010},
    "ANTHROPIC": {"input": 0.002, "output": 0.010},
}


class DirectAuditError(RuntimeError):
    def __init__(self, code: str, retryable: bool, provider_request_id: str | None = None,
                 *, usage: dict | None = None, model: str | None = None,
                 provider_status: str | None = None, incomplete_reason: str | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.provider_request_id = provider_request_id
        self.usage = usage
        self.model = model
        self.provider_status = provider_status
        self.incomplete_reason = incomplete_reason


class DirectAudit:
    """One bounded provider request; no queue, bridge, polling or write path."""

    def __init__(self, config):
        self.config = config

    @property
    def enabled(self):
        return bool(self.config.metis_openai_api_key and self.config.metis_anthropic_api_key)

    async def run(self, origin_model: str, request: str, context_project: str):
        started = time.monotonic()
        receipt_id = str(uuid.uuid4())
        target = "ANTHROPIC" if origin_model == "OPENAI" else "OPENAI"
        try:
            if target == "ANTHROPIC":
                answer, usage, model, provider_id = await self._anthropic(request, context_project)
            else:
                answer, usage, model, provider_id = await self._openai(request, context_project)
        except DirectAuditError as exc:
            cost = self._cost(target, exc.usage)
            return {
                "status": "FAILED", "stage": f"{target}_REQUEST", "error_code": exc.code,
                "retryable": exc.retryable, "provider_request_id": exc.provider_request_id,
                "usage": exc.usage, "model": exc.model,
                "provider_status": exc.provider_status,
                "incomplete_reason": exc.incomplete_reason,
                "cost_known": cost is not None, "cost_usd": cost,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "receipt_id": receipt_id, "context_project": context_project,
                "target_model_provider": target, "transport": "DIRECT_API",
                "writes_attempted": 0,
            }
        status = "REQUIRES_ANDRES" if answer.lstrip().upper().startswith("REQUIRES_ANDRES:") else "COMPLETED"
        cost = self._cost(target, usage)
        return {
            "status": status, "final_answer": answer, "model": model,
            "provider_request_id": provider_id, "usage": usage,
            "cost_known": cost is not None, "cost_usd": cost,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "receipt_id": receipt_id, "context_project": context_project,
            "target_model_provider": target, "transport": "DIRECT_API",
            "writes_attempted": 0,
        }

    async def _anthropic(self, request, context):
        body = {
            "model": self.config.metis_anthropic_model,
            "max_tokens": 4096,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": f"Contexto: {context}\n\n{request}"}],
        }
        data, request_id = await self._post("https://api.anthropic.com/v1/messages", body, {
            "x-api-key": self.config.metis_anthropic_api_key,
            "anthropic-version": "2023-06-01",
        })
        blocks = data.get("content", [])
        answer = "\n".join(x.get("text", "") for x in blocks if isinstance(x, dict) and x.get("type") == "text").strip()
        if not answer:
            raise DirectAuditError("EMPTY_PROVIDER_RESPONSE", False, data.get("id") or request_id)
        return answer, data.get("usage", {}), data.get("model", body["model"]), data.get("id") or request_id

    async def _openai(self, request, context):
        body = {
            "model": self.config.metis_openai_model,
            "input": [{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"Contexto: {context}\n\n{request}"}],
            # This budget includes hidden reasoning tokens. A 4096-token cap
            # exhausted on a substantive audit before any visible text appeared.
            "max_output_tokens": 8192,
            "reasoning": {"effort": "low"},
        }
        data, request_id = await self._post("https://api.openai.com/v1/responses", body, {
            "Authorization": f"Bearer {self.config.metis_openai_api_key}",
        })
        answer = data.get("output_text", "")
        if not answer:
            answer = "\n".join(
                c.get("text", "") for item in data.get("output", []) if isinstance(item, dict)
                for c in item.get("content", []) if isinstance(c, dict)
                and c.get("type") in {"output_text", "text"}
            ).strip()
        status = data.get("status")
        reason = (data.get("incomplete_details") or {}).get("reason")
        if not answer or status in {"incomplete", "failed"}:
            raise DirectAuditError(
                "OUTPUT_TOKEN_LIMIT" if reason == "max_output_tokens" else
                "PROVIDER_INCOMPLETE" if status == "incomplete" else
                "PROVIDER_FAILED" if status == "failed" else "EMPTY_PROVIDER_RESPONSE",
                False, data.get("id") or request_id,
                usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
                model=data.get("model"), provider_status=status,
                incomplete_reason=reason)
        return answer, data.get("usage", {}), data.get("model", body["model"]), data.get("id") or request_id

    async def _post(self, url, body, headers):
        timeout = httpx.Timeout(self.config.metis_direct_audit_timeout, connect=10)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=body, headers={"content-type": "application/json", **headers})
        except httpx.TimeoutException as exc:
            raise DirectAuditError("PROVIDER_TIMEOUT", False) from exc
        except httpx.HTTPError as exc:
            raise DirectAuditError("PROVIDER_CONNECTION_ERROR", True) from exc
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        if response.status_code >= 400:
            retryable = response.status_code in {408, 409, 429} or response.status_code >= 500
            raise DirectAuditError(f"PROVIDER_HTTP_{response.status_code}", retryable, request_id)
        if len(response.content) > 2_000_000:
            raise DirectAuditError("PROVIDER_RESPONSE_TOO_LARGE", False, request_id)
        try:
            return response.json(), request_id
        except ValueError as exc:
            raise DirectAuditError("PROVIDER_INVALID_JSON", False, request_id) from exc

    @staticmethod
    def _cost(provider, usage):
        if not isinstance(usage, dict):
            return None
        input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            return None
        price = PRICE_USD_PER_1K[provider]
        return round(input_tokens / 1000 * price["input"] + output_tokens / 1000 * price["output"], 12)
