"""Standalone private deployment surface for Health OS.

This module deliberately imports no connector adapters or worker manager.  It
may be deployed as a separate Railway service with its own database/secret.
"""
from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import security, store
from ..config import Config, load_config
from ..log import resolve_log_level, setup_logging
from .persistence import HealthOSRepository
from .apple_export import parse_apple_health_export
from .core import APPLE_HEALTH, SPEEDIANCE


def build_health_os_app(config: Config) -> Starlette:
    if not config.health_os_ingest_key or not config.health_os_read_key or not config.health_os_data_key:
        raise ValueError("HEALTH_OS_INGEST_KEY, HEALTH_OS_READ_KEY and HEALTH_OS_DATA_KEY are required for the isolated Health OS service")
    repo = HealthOSRepository(store.init_db(config.db_path), config.health_os_data_key,
                              config.health_os_timezone, legacy_key=config.gateway_secret)

    def authorized_ingest(request) -> bool:
        return hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + config.health_os_ingest_key)

    def authorized_read(request) -> bool:
        return hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + config.health_os_read_key)

    async def ingest(request):
        if not authorized_ingest(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        raw = await security.read_body_limited(request, max_bytes=256_000)
        if raw is None:
            return JSONResponse({"status": "TOO_LARGE"}, status_code=413)
        try:
            body = json.loads(raw.decode("utf-8"))
            source = body["source"]
            if source not in {APPLE_HEALTH, SPEEDIANCE}:
                raise ValueError("unsupported producer")
            result = repo.ingest(source, body.get("observations", []), body.get("workouts", []))
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
        return JSONResponse({"status": "OK", **result}, headers={"Cache-Control": "no-store"})

    async def quality(request):
        if not authorized_read(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        return JSONResponse({"status": "OK", "quality": repo.quality(datetime.now(timezone.utc))}, headers={"Cache-Control": "no-store"})

    async def apple_export(request):
        if not authorized_ingest(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        xml = await security.read_body_limited(request, max_bytes=64 * 1024 * 1024)
        if xml is None:
            return JSONResponse({"status": "TOO_LARGE"}, status_code=413)
        try:
            parsed = parse_apple_health_export(xml)
            result = repo.ingest("APPLE_HEALTH", parsed.observations, parsed.workouts)
        except (TypeError, ValueError, UnicodeError) as exc:
            return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
        return JSONResponse({"status": "OK", **result, "ignored_apple_types": list(parsed.ignored_types)}, headers={"Cache-Control": "no-store"})

    async def brief(request):
        if not authorized_read(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        raw = await security.read_body_limited(request, max_bytes=8_000)
        if raw is None:
            return JSONResponse({"status": "TOO_LARGE"}, status_code=413)
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            flags = body.get("subjective_flags", [])
            if not isinstance(flags, list) or not all(isinstance(x, str) for x in flags):
                raise ValueError("subjective_flags must be a string list")
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
        return JSONResponse({"status": "OK", "brief": repo.brief(datetime.now(timezone.utc), tuple(flags))}, headers={"Cache-Control": "no-store"})

    async def trends(request):
        if not authorized_read(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        return JSONResponse({"status": "OK", "trends": repo.trends(datetime.now(timezone.utc))}, headers={"Cache-Control": "no-store"})

    async def quarantine_report(request):
        if not authorized_read(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        return JSONResponse({"status": "OK", "report": repo.quarantine_report()}, headers={"Cache-Control": "no-store"})

    async def quarantine_apply(request):
        if not authorized_ingest(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        raw = await security.read_body_limited(request, max_bytes=2_000)
        if raw is None:
            return JSONResponse({"status": "TOO_LARGE"}, status_code=413)
        try:
            body = json.loads(raw.decode("utf-8"))
            if body.get("confirm") != "QUARANTINE" or not isinstance(body.get("expected"), int):
                raise ValueError("confirm and expected are required")
            result = repo.quarantine_invalid(body["expected"])
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError, AttributeError):
            return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
        return JSONResponse({"status": "OK", **result}, headers={"Cache-Control": "no-store"})

    async def transition(request):
        if not authorized_ingest(request):
            return JSONResponse({"status": "UNAUTHORIZED"}, status_code=401)
        raw = await security.read_body_limited(request, max_bytes=8_000)
        if raw is None:
            return JSONResponse({"status": "TOO_LARGE"}, status_code=413)
        try:
            body = json.loads(raw.decode("utf-8"))
            started_at = datetime.fromisoformat(body["started_at"])
            repo.set_apple_transition(started_at)
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            return JSONResponse({"status": "INVALID_REQUEST"}, status_code=400)
        return JSONResponse({"status": "OK", "baseline_era": repo._load_health(started_at).baseline_era(started_at).value}, headers={"Cache-Control": "no-store"})

    # Railway injects the deployed commit; exposing its short SHA lets anyone
    # verify which code is live without holding a Health OS credential.
    deployed_commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7] or "unknown"

    async def healthz(request):
        return JSONResponse({"status": "OK", "service": "health-os", "commit": deployed_commit})

    return Starlette(routes=[
        Route("/healthz", healthz), Route("/health-os/ingest", ingest, methods=["POST"]),
        Route("/health-os/import/apple-export", apple_export, methods=["POST"]),
        Route("/health-os/quality", quality), Route("/health-os/brief", brief, methods=["POST"]),
        Route("/health-os/transition", transition, methods=["POST"]),
        Route("/health-os/trends", trends), Route("/health-os/quarantine", quarantine_report),
        Route("/health-os/quarantine/apply", quarantine_apply, methods=["POST"]),
    ])


def main() -> None:
    import uvicorn
    setup_logging()
    uvicorn.run(build_health_os_app(load_config()), host="0.0.0.0", port=load_config().port,
                log_level=resolve_log_level(), access_log=False, log_config=None,
                proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"))
