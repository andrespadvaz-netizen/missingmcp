import httpx
import pytest

from missingmcp.metis.asana_agents import AsanaAgents
from missingmcp.metis.asana_evaluation import evaluate, evaluate_and_dispatch


BRIEF = {
    "context": "SHOKKO", "project_gid": "200", "source_task_gid": "300",
    "agent_value": True, "human_owner_retained": True, "dot_authorized": True,
    "audience": "internal", "deliverable": "Evidence summary",
    "request": "Summarize the permitted evidence", "quality_review_by": "human owner",
    "authorized_sources": ["approved project brief"],
    "specialty": "research",
}
PROFILES = {"11": {"specialties": ["research"], "contexts": ["SHOKKO"],
                   "audiences": ["internal"], "dot_authorized": True}}


def decide(brief=BRIEF, profiles=PROFILES):
    return evaluate(brief, context="SHOKKO", project_gid="200",
                    source_task_gid="300", agents=profiles,
                    allowed_agent_gids=frozenset({"11"}))


def test_evaluation_requires_value_scope_and_unambiguous_agent():
    decision = decide()
    assert decision.action == "handoff" and decision.agent_gid == "11"
    assert decision.idempotency_key == decide().idempotency_key
    for change in ({"agent_value": False}, {"dot_authorized": False},
                   {"authorized_sources": []},
                   {"requires_private_memory": True}, {"audience": "private"},
                   {"quality_review_by": ""}, {"context": "FINAL_FINAL"}):
        assert decide(BRIEF | change).action == "skip"
    assert decide(profiles=PROFILES | {"12": PROFILES["11"]}).agent_gid == "11"


@pytest.mark.asyncio
async def test_evaluation_preserves_human_owner_and_dry_run_has_no_write():
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json={"data": {
            "gid": "300", "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}], "assignee": {"gid": "44"},
            "completed": False}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": "11"},
                         transport=httpx.MockTransport(handler))
    decision, task = await evaluate_and_dispatch(
        client, source_task_gid="300", project_gid="200",
        brief=BRIEF, agents=PROFILES)
    assert decision.action == "handoff" and task is None
    assert methods == ["GET"]
