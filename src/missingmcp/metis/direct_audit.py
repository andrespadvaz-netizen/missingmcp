from __future__ import annotations

import time
import uuid

import httpx


SYSTEM = """You are the independent reviewer in the Metis dual-model system.
Audit only the self-contained evidence supplied in the request. Keep project
contexts strictly separated. Do not perform writes, send messages, change a
CANON, or claim access to sources that are not in the request. Distinguish
verified facts from inferences. If a real decision from Andres is indispensable,
begin the answer with REQUIRES_ANDRES: and state the single decision needed.
Otherwise return a complete, actionable audit in Spanish."""


class DirectAuditError(RuntimeError):
    def __init__(self, code: str, retryable: bool, provider_request_id: str | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.provider_request_id = provider_request_id


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
            return {
                "status": "FAILED", "stage": f"{target}_REQUEST", "error_code": exc.code,
                "retryable": exc.retryable, "provider_request_id": exc.provider_request_id,
                "cost_known": False, "cost_usd": None,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "receipt_id": receipt_id, "context_project": context_project,
                "target_model_provider": target, "transport": "DIRECT_API",
                "writes_attempted": 0,
            }
        status = "REQUIRES_ANDRES" if answer.lstrip().upper().startswith("REQUIRES_ANDRES:") else "COMPLETED"
        return {
            "status": status, "final_answer": answer, "model": model,
            "provider_request_id": provider_id, "usage": usage,
            "cost_known": False, "cost_usd": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "receipt_id": receipt_id, "context_project": context_project,
            "target_model_provider": target, "transport": "DIRECT_API",
            "writes_attempted": 0,
        }

    async def _anthropic(self, request, context):
        body = {
            "model": self.config.metis_anthropic_model,
            "max_tokens": 8192,
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
            "max_output_tokens": 8192,
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
        if not answer:
            raise DirectAuditError("EMPTY_PROVIDER_RESPONSE", False, data.get("id") or request_id)
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
