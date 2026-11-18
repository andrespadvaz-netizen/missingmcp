import hashlib

import pytest

from missingmcp.metis.team_handoff import HandoffError, TeamHandoffs


def test_handoff_correlates_without_copying_source_content(tmp_path):
    ledger = TeamHandoffs(str(tmp_path / "handoff.db"))
    args = dict(key="handoff-operation-0001", context="SHOKKO",
                source_task_gid="100", target_project_gid="200", target_agent_gid="300",
                fingerprint=hashlib.sha256(b"approved brief").hexdigest())
    assert ledger.reserve(**args).state == "RESERVED"
    with pytest.raises(HandoffError, match="handoff_target_unverified"):
        ledger.record_evidence(args["key"], "500")
    assert ledger.record_target(args["key"], "400").target_task_gid == "400"
    assert ledger.record_evidence(args["key"], "500").state == "EVIDENCE_SEEN"
    ledger.close()

    reopened = TeamHandoffs(str(tmp_path / "handoff.db"))
    assert reopened.reserve(**args).evidence_story_gid == "500"
    with pytest.raises(HandoffError, match="handoff_idempotency_conflict"):
        reopened.reserve(**(args | {"target_agent_gid": "301"}))
    with pytest.raises(HandoffError, match="handoff_result_conflict"):
        reopened.record_target(args["key"], "401")
    reopened.close()
