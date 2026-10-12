"""Operator CLI for the two Health OS write steps that must not live in Apps Script.

The Apps Script runtime holds only the read key (by design).  Sanitation and
the Apple-era transition need the ingest key, so they run from an operator
shell that receives the keys through environment variables — for example
``railway run health-os-admin ...``, which injects the service variables
without printing them.  Keys are never accepted as arguments and never logged.

Commands:
  report                         read key   GET  /health-os/quarantine
  quarantine-apply --expected N  ingest key POST /health-os/quarantine/apply
  transition --started-at ISO    ingest key POST /health-os/transition
  verify                         read key   POST /health-os/brief (state only)
  coverage                       read key   GET  /health-os/coverage (months per metric/device, no values)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://sweet-love-production-709a.up.railway.app"


def _key(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 32:
        raise SystemExit(f"{name} is not available in the environment")
    return value


def _call(method: str, base: str, path: str, key: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = httpx.request(method, base.rstrip("/") + path, json=body,
                             headers={"Authorization": "Bearer " + key}, timeout=60.0)
    try:
        payload = response.json()
    except ValueError:
        payload = {"status": "NON_JSON_RESPONSE"}
    return {"http": response.status_code, **payload}


def run(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="health-os-admin")
    parser.add_argument("--base-url", default=os.environ.get("HEALTH_OS_BASE_URL", DEFAULT_BASE_URL))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("report")
    apply = sub.add_parser("quarantine-apply")
    apply.add_argument("--expected", type=int, required=True)
    transition = sub.add_parser("transition")
    transition.add_argument("--started-at", required=True)
    sub.add_parser("verify")
    sub.add_parser("coverage")
    args = parser.parse_args(argv)

    if args.command == "report":
        return _call("GET", args.base_url, "/health-os/quarantine", _key("HEALTH_OS_READ_KEY"))
    if args.command == "quarantine-apply":
        return _call("POST", args.base_url, "/health-os/quarantine/apply", _key("HEALTH_OS_INGEST_KEY"),
                     {"confirm": "QUARANTINE", "expected": args.expected})
    if args.command == "transition":
        started = datetime.fromisoformat(args.started_at)
        if started.tzinfo is None:
            raise SystemExit("--started-at must include an offset, e.g. 2026-10-04T00:00:00-06:00")
        return _call("POST", args.base_url, "/health-os/transition", _key("HEALTH_OS_INGEST_KEY"),
                     {"started_at": started.isoformat()})
    if args.command == "coverage":
        return _call("GET", args.base_url, "/health-os/coverage", _key("HEALTH_OS_READ_KEY"))
    # verify: return only non-health operational fields of the brief.
    result = _call("POST", args.base_url, "/health-os/brief", _key("HEALTH_OS_READ_KEY"), {})
    brief = result.get("brief") or {}
    quality = brief.get("quality") or {}
    return {"http": result["http"], "status": result.get("status"), "decision": brief.get("decision"),
            "quality_pass": quality.get("pass"), "stale": quality.get("stale"),
            "persisted_invalid_records": quality.get("persisted_invalid_records"),
            "legend_present": "911" in str(brief.get("legend", ""))}


def main() -> None:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("http") == 200 else 1)
