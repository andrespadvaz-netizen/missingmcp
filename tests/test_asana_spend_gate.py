"""Synthetic shared provider-cap attestations and durable admission tests."""
import json
import sqlite3
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from missingmcp.metis.asana_spend_gate import (
    SpendGateError, load_attestation, reserve_handoff,
)
from missingmcp.metis.asana_evaluation import Decision
from missingmcp.metis.asana_dot_events import DotEvents
from test_asana_dot_events import EVENT, POLICY, PROFILES, client, registered


def attestation():
    today = datetime.now(ZoneInfo("America/Mexico_City")).date()
    return {"workspace_gid": "100", "operator_gid": "55",
            "cycle_start": (today - timedelta(days=1)).isoformat(),
            "cycle_end": (today + timedelta(days=1)).isoformat(),
            "included_requests_per_cycle": 100,
            "paid_additional_cap_requests": 50,
            "paid_additional_cap_mxn": 550,
            "paid_additional_price_per_request_mxn": 11,
            "paid_additional_cap_enforced": True,
            "gateway_handoff_limit": 1}


def test_attestation_is_server_scoped_and_cycle_bound():
    valid = attestation()
    assert load_attestation(json.dumps(valid), workspace_gid="100",
                            operator_gid="55") == valid
    for changed in ({"workspace_gid": "101"}, {"operator_gid": "56"},
                    {"included_requests_per_cycle": -1},
                    {"included_requests_per_cycle": True},
                    {"paid_additional_cap_requests": 51},
                    {"paid_additional_cap_mxn": 551},
                    {"paid_additional_cap_enforced": False},
                    {"gateway_handoff_limit": 51}):
        with pytest.raises(SpendGateError, match="shared_quota_attestation_invalid"):
            load_attestation(json.dumps(valid | changed), workspace_gid="100",
                             operator_gid="55")
    with pytest.raises(SpendGateError, match="shared_quota_unavailable"):
        load_attestation("", workspace_gid="100", operator_gid="55")
    old_ambiguous = valid | {"provider_cap_requests": 50}
    with pytest.raises(SpendGateError, match="shared_quota_attestation_invalid"):
        load_attestation(json.dumps(old_ambiguous), workspace_gid="100",
                         operator_gid="55")
    without_included = {key: value for key, value in valid.items()
                        if key != "included_requests_per_cycle"}
    with pytest.raises(SpendGateError, match="shared_quota_attestation_invalid"):
        load_attestation(json.dumps(without_included), workspace_gid="100",
                         operator_gid="55")
    expired = valid | {"cycle_end": valid["cycle_start"]}
    with pytest.raises(SpendGateError, match="shared_quota_cycle_inactive"):
        load_attestation(json.dumps(expired), workspace_gid="100",
                         operator_gid="55")


def test_admission_is_durable_and_global_to_gateway(tmp_path):
    path = tmp_path / "admissions.db"
    first = sqlite3.connect(path, isolation_level=None)
    second = sqlite3.connect(path, isolation_level=None)
    valid = attestation()
    assert reserve_handoff(first, key="handoff-one", attestation=valid)
    assert reserve_handoff(second, key="handoff-one", attestation=valid) is False
    with pytest.raises(SpendGateError, match="gateway_handoff_limit_reached"):
        reserve_handoff(second, key="handoff-two", attestation=valid)
    assert first.execute("SELECT COUNT(*) FROM asana_ai_handoff_admissions").fetchone()[0] == 1
    first.close()
    second.close()


@pytest.mark.asyncio
async def test_attested_dispatch_records_pending_human_review(tmp_path, monkeypatch):
    import missingmcp.metis.asana_dot_events as module

    monkeypatch.setenv("ASANA_AI_SHARED_LIMIT_JSON", json.dumps(attestation()))
    events = registered(DotEvents(str(tmp_path / "events.db")))
    original = module.evaluate_and_dispatch
    calls = []

    async def synthetic(client, **kwargs):
        if not kwargs.get("dispatch"):
            return await original(client, **kwargs)
        calls.append(1)
        return Decision("handoff", "eligible_for_human_review", "11", "research",
                        "dot-handoff-synthetic-0001"), {"gid": "301"}

    monkeypatch.setattr(module, "evaluate_and_dispatch", synthetic)
    result = await events.created(client(), EVENT, POLICY, PROFILES, dispatch=True)
    assert result == {"action": "created", "reason": "review_pending", "target_gid": "301"}
    assert (await events.created(client(), EVENT, POLICY, PROFILES, dispatch=True)) == result
    assert len(calls) == 1
    decision = events.db.execute("SELECT review_state,agent_gid FROM asana_dot_decisions").fetchone()
    assert tuple(decision) == ("PENDING", "11")
    assert events.db.execute("SELECT COUNT(*) FROM asana_ai_handoff_admissions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_uncertain_dispatch_keeps_slot_and_requires_reconciliation(tmp_path, monkeypatch):
    import missingmcp.metis.asana_dot_events as module

    monkeypatch.setenv("ASANA_AI_SHARED_LIMIT_JSON", json.dumps(attestation()))
    events = registered(DotEvents(str(tmp_path / "events.db")))
    original = module.evaluate_and_dispatch
    calls = []

    async def synthetic(client, **kwargs):
        if not kwargs.get("dispatch"):
            return await original(client, **kwargs)
        calls.append(1)
        raise RuntimeError("synthetic_lost_response")

    monkeypatch.setattr(module, "evaluate_and_dispatch", synthetic)
    first = await events.created(client(), EVENT, POLICY, PROFILES, dispatch=True)
    assert first == {"action": "blocked", "reason": "dispatch_uncertain_check_required"}
    assert (await events.created(client(), EVENT, POLICY, PROFILES,
                                 dispatch=True)) == first
    assert len(calls) == 1
    assert events.db.execute("SELECT COUNT(*) FROM asana_ai_handoff_admissions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_concurrent_event_admits_only_one_agent_handoff(tmp_path, monkeypatch):
    import missingmcp.metis.asana_dot_events as module

    monkeypatch.setenv("ASANA_AI_SHARED_LIMIT_JSON", json.dumps(attestation()))
    path = str(tmp_path / "events.db")
    first = registered(DotEvents(path))
    second = DotEvents(path)
    original = module.evaluate_and_dispatch
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def synthetic(client, **kwargs):
        if not kwargs.get("dispatch"):
            return await original(client, **kwargs)
        calls.append(1)
        entered.set()
        await release.wait()
        return Decision("handoff", "eligible_for_human_review", "11", "research",
                        "dot-handoff-synthetic-0001"), {"gid": "301"}

    monkeypatch.setattr(module, "evaluate_and_dispatch", synthetic)
    pending = asyncio.create_task(first.created(client(), EVENT, POLICY, PROFILES,
                                                dispatch=True))
    await entered.wait()
    duplicate = await second.created(client(), EVENT, POLICY, PROFILES,
                                     dispatch=True)
    assert duplicate == {"action": "blocked", "reason": "dispatch_uncertain_check_required"}
    release.set()
    assert (await pending)["action"] == "created"
    assert len(calls) == 1
    assert first.db.execute("SELECT COUNT(*) FROM asana_ai_handoff_admissions").fetchone()[0] == 1
