import hashlib

import pytest

from missingmcp.metis.team_budget import BudgetError, TeamBudget


def fp(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_reservation_survives_restart_and_uncertain_call_consumes_capacity(tmp_path):
    path = str(tmp_path / "team.db")
    a = TeamBudget(path)
    args = dict(key="operation-id-0001", scope="gateway:openai", cycle="2026-11",
                fingerprint=fp("same operation"), units=53_429,
                max_units=60_000, max_calls=1)
    assert a.reserve(**args).state == "RESERVED"
    a.close()

    b = TeamBudget(path)
    assert b.reserve(**args).state == "RESERVED"
    with pytest.raises(BudgetError, match="budget_exhausted"):
        b.reserve(**(args | {"key": "operation-id-0002"}))
    assert b.record_receipt(args["key"], "provider-receipt").state == "RECEIPTED"
    with pytest.raises(BudgetError, match="budget_receipt_conflict"):
        b.record_receipt(args["key"], "different-receipt")
    b.close()


def test_scopes_cycles_and_idempotency_are_separate(tmp_path):
    budget = TeamBudget(str(tmp_path / "team.db"))
    base = dict(scope="asana:dispatch", cycle="2026-11-05", fingerprint=fp("work"),
                units=1, max_units=1, max_calls=1)
    budget.reserve(key="operation-id-0001", **base)
    with pytest.raises(BudgetError, match="budget_idempotency_conflict"):
        budget.reserve(key="operation-id-0001", **(base | {"cycle": "2026-12-05"}))
    assert budget.reserve(key="operation-id-0002", **(base | {"cycle": "2026-12-05"})).units == 1
    assert budget.reserve(key="operation-id-0003", **(base | {"scope": "gateway:openai"})).units == 1
    budget.close()


def test_two_workers_cannot_overspend_same_cycle(tmp_path):
    path = str(tmp_path / "team.db")
    first, second = TeamBudget(path), TeamBudget(path)
    base = dict(scope="gateway:openai", cycle="2026-11", fingerprint=fp("work"),
                units=40, max_units=60, max_calls=2)
    first.reserve(key="operation-id-0001", **base)
    with pytest.raises(BudgetError, match="budget_exhausted"):
        second.reserve(key="operation-id-0002", **base)
    first.close()
    second.close()
