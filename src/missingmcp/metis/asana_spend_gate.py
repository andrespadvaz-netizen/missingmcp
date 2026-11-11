"""Operator-attested Asana provider cap and durable handoff admission.

Asana enforces the shared monetary ceiling. This ledger limits this Gateway's
handoff attempts; it is deliberately not presented as a measurement of Dash's
remaining requests or as a per-agent budget.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class SpendGateError(ValueError):
    pass


def load_attestation(raw: str, *, workspace_gid: str,
                     operator_gid: str, now: datetime | None = None) -> dict:
    if not raw:
        raise SpendGateError("shared_quota_unavailable")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SpendGateError("shared_quota_attestation_invalid") from exc
    if not isinstance(value, dict) or set(value) != {
            "workspace_gid", "operator_gid", "cycle_start", "cycle_end",
            "provider_cap_requests", "provider_cap_mxn", "price_per_request_mxn",
            "provider_cap_enforced", "gateway_handoff_limit"}:
        raise SpendGateError("shared_quota_attestation_invalid")
    if (value["workspace_gid"] != workspace_gid or
            value["operator_gid"] != operator_gid or
            value["provider_cap_requests"] != 50 or
            value["provider_cap_mxn"] != 550 or
            value["price_per_request_mxn"] != 11 or
            value["provider_cap_enforced"] is not True or
            type(value["gateway_handoff_limit"]) is not int or
            not 1 <= value["gateway_handoff_limit"] <= 50):
        raise SpendGateError("shared_quota_attestation_invalid")
    try:
        start = datetime.strptime(value["cycle_start"], "%Y-%m-%d").date()
        end = datetime.strptime(value["cycle_end"], "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise SpendGateError("shared_quota_attestation_invalid") from exc
    current = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo("America/Mexico_City")).date()
    if not start <= current < end or (end - start).days > 32:
        raise SpendGateError("shared_quota_cycle_inactive")
    return value


def reserve_handoff(db: sqlite3.Connection, *, key: str, attestation: dict) -> bool:
    """Reserve one Gateway handoff slot before any Asana agent-task POST.

    False means this exact key was reserved already; the caller must reconcile
    the uncertain result, never retry under another key. Reservations are not
    refunded because a lost response may conceal a successful paid request.
    """
    db.execute("""CREATE TABLE IF NOT EXISTS asana_ai_handoff_admissions (
        handoff_key TEXT PRIMARY KEY, cycle_start TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM asana_ai_handoff_admissions WHERE handoff_key=?",
                      (key,)).fetchone():
            db.execute("COMMIT")
            return False
        count = db.execute(
            "SELECT COUNT(*) FROM asana_ai_handoff_admissions WHERE cycle_start=?",
            (attestation["cycle_start"],)).fetchone()[0]
        if count >= attestation["gateway_handoff_limit"]:
            raise SpendGateError("gateway_handoff_limit_reached")
        db.execute("INSERT INTO asana_ai_handoff_admissions(handoff_key,cycle_start) "
                   "VALUES (?,?)", (key, attestation["cycle_start"]))
        db.execute("COMMIT")
        return True
    except BaseException:
        db.execute("ROLLBACK")
        raise
