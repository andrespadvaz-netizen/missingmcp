import httpx
import pytest
from starlette.testclient import TestClient

from missingmcp.app import build_app
from missingmcp.config import load_config
from missingmcp.metis.asana_agents import AsanaAgents
from missingmcp.metis.asana_dot_events import DotEvents, DotEventError
from test_asana_evaluation import BRIEF, PROFILES


POLICY = {"workspace_gid": "100", "creator_gid": "55", "project_gid": "200", "context": "SHOKKO",
          "audiences": ["internal"], "sources": ["approved project brief"],
          "eligible_task_names": ["Research task"], "specialties": ["research"],
          "routing": {"Research task": "research"},
          "owner_gid": "44"}
EVENT = {"type": "task.created", "task_gid": "300", "brief": BRIEF}


def registered(events):
    import hashlib
    import json
    brief_hash = hashlib.sha256(json.dumps(BRIEF, sort_keys=True).encode()).hexdigest()
    events.db.execute("INSERT INTO asana_dot_sources"
                      "(idem,fingerprint,brief_fingerprint,state,task_gid) "
                      "VALUES ('registered-source-0001',?,?,'CREATED','300')",
                      ("a" * 64, brief_hash))
    return events


def client(creator="55", project="200", assignee="44", stories=None,
           completed=False):
    def handler(request):
        if request.url.path.endswith("/stories"):
            return httpx.Response(200, json={"data": stories or []})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Research task", "workspace": {"gid": "100"},
            "created_by": {"gid": creator}, "projects": [{"gid": project}],
            "assignee": {"gid": assignee}, "completed": completed}})
    return AsanaAgents("token", "100", {"200": "SHOKKO"},
                       allowed_agent_gids={"200": "11"},
                       transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_authenticated_source_creation_is_durable_and_verified(tmp_path):
    import json
    posts = []

    def handler(request):
        if request.url.path.endswith("/projects/200"):
            return httpx.Response(200, json={"data": {"gid": "200",
                "workspace": {"gid": "100"}, "owner": {"gid": "44"},
                "privacy_setting": "private"}})
        if request.method == "POST":
            posts.append(json.loads(request.content)["data"])
            return httpx.Response(201, json={"data": {"gid": "300"}})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Research task", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}, "completed": False}})

    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        allowed_agent_gids={"200": "11"},
                        transport=httpx.MockTransport(handler))
    creation_brief = {k: v for k, v in BRIEF.items() if k != "source_task_gid"}
    event = {"type": "task.create", "idempotency_key": "dot-source-creation-0001",
             "name": "Research task", "brief": creation_brief}
    assert (await events.create_source(asana, event, POLICY))["gid"] == "300"
    assert (await events.create_source(asana, event, POLICY))["gid"] == "300"
    assert events.source_registered("300") and len(posts) == 1
    assert posts[0]["assignee"] == "44"
    assert (await events.created(asana, EVENT | {"brief": creation_brief}, POLICY, PROFILES))[
        "action"] == "eligible"


@pytest.mark.asyncio
async def test_private_project_owner_can_differ_from_human_task_owner(tmp_path):
    import json
    posts = []

    def handler(request):
        if request.url.path.endswith("/projects/200"):
            return httpx.Response(200, json={"data": {"gid": "200",
                "workspace": {"gid": "100"}, "owner": {"gid": "77"},
                "privacy_setting": "private"}})
        if request.method == "POST":
            posts.append(json.loads(request.content)["data"])
            return httpx.Response(201, json={"data": {"gid": "300"}})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Research task", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}, "completed": False}})

    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    event = {"type": "task.create", "idempotency_key": "separate-project-owner-0001",
             "name": "Research task", "brief": BRIEF}
    assert (await events.create_source(asana, event, POLICY))["gid"] == "300"
    assert len(posts) == 1 and posts[0]["assignee"] == "44"


@pytest.mark.asyncio
async def test_unrouted_human_task_is_created_but_agent_is_skipped(tmp_path):
    def handler(request):
        if request.url.path.endswith("/projects/200"):
            return httpx.Response(200, json={"data": {"gid": "200",
                "workspace": {"gid": "100"}, "privacy_setting": "private"}})
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"gid": "300"}})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Unrouted human task", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}, "completed": False}})

    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    brief = {k: v for k, v in BRIEF.items() if k != "source_task_gid"}
    event = {"type": "task.create", "idempotency_key": "unrouted-human-task-0001",
             "name": "Unrouted human task", "brief": brief}
    assert (await events.create_source(asana, event, POLICY))["gid"] == "300"
    result = await events.created(asana, {"type": "task.created", "task_gid": "300",
                                          "brief": brief}, POLICY, PROFILES, dispatch=True)
    assert result == {"action": "skip", "reason": "task_not_eligible"}


@pytest.mark.asyncio
@pytest.mark.parametrize("human_only", [False, True])
async def test_uncertain_source_creation_requires_readback_not_repost(tmp_path, human_only):
    posts = []

    def handler(request):
        if request.url.path.endswith("/projects/200"):
            return httpx.Response(200, json={"data": {"gid": "200",
                "workspace": {"gid": "100"}, "owner": {"gid": "44"},
                "privacy_setting": "private"}})
        if request.method == "POST":
            posts.append(1)
            raise httpx.ReadTimeout("lost response")
        if request.url.path.endswith("/projects/200/tasks"):
            return httpx.Response(200, json={"data": [{
                "gid": "300", "notes": "Metis Dot source reference: dot-source-creation-0001"}]})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Research task", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}}})

    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    event = {"type": "task.create", "idempotency_key": "dot-source-creation-0001",
             "name": "Research task", "brief": BRIEF}
    policy = (POLICY | {"eligible_task_names": [], "routing": {}, "specialties": []}
              if human_only else POLICY)
    with pytest.raises(Exception, match="transport_uncertain"):
        await events.create_source(asana, event, policy)
    with pytest.raises(DotEventError, match="source_creation_uncertain"):
        await events.create_source(asana, event, policy)
    assert (await events.reconcile_source(asana, "dot-source-creation-0001", policy))[
        "gid"] == "300"
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_concurrent_source_creation_posts_once(tmp_path):
    import asyncio
    entered, release = asyncio.Event(), asyncio.Event()
    posts = []

    async def handler(request):
        if request.url.path.endswith("/projects/200"):
            return httpx.Response(200, json={"data": {"gid": "200",
                "workspace": {"gid": "100"}, "owner": {"gid": "44"},
                "privacy_setting": "private"}})
        if request.method == "POST":
            posts.append(1)
            entered.set()
            await release.wait()
            return httpx.Response(201, json={"data": {"gid": "300"}})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Research task", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}}})

    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    event = {"type": "task.create", "idempotency_key": "dot-source-creation-0001",
             "name": "Research task", "brief": BRIEF}
    first = asyncio.create_task(events.create_source(asana, event, POLICY))
    await entered.wait()
    with pytest.raises(DotEventError, match="source_creation_uncertain"):
        await events.create_source(asana, event, POLICY)
    release.set()
    assert (await first)["gid"] == "300"
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_public_source_project_is_rejected_before_post(tmp_path):
    writes = []

    def handler(request):
        if request.method == "POST":
            writes.append(1)
        return httpx.Response(200, json={"data": {"gid": "200",
            "workspace": {"gid": "100"}, "owner": {"gid": "44"},
            "privacy_setting": "public_to_workspace"}})

    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    with pytest.raises(DotEventError, match="source_project_privacy_mismatch"):
        await events.create_source(asana, {
            "type": "task.create", "idempotency_key": "dot-source-creation-0001",
            "name": "Research task", "brief": BRIEF}, POLICY)
    assert not writes


@pytest.mark.asyncio
async def test_public_project_requires_explicit_workspace_audience(tmp_path):
    posts = []

    def handler(request):
        if request.url.path.endswith("/projects/200"):
            return httpx.Response(200, json={"data": {"gid": "200",
                "workspace": {"gid": "100"}, "privacy_setting": "public_to_workspace"}})
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(201, json={"data": {"gid": "300"}})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Research task", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}, "completed": False}})

    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    event = {"type": "task.create", "idempotency_key": "explicit-workspace-task-0001",
             "name": "Research task", "brief": BRIEF | {"audience": "workspace"}}
    with pytest.raises(DotEventError, match="source_audience_policy_invalid"):
        await events.create_source(asana, event,
                                   POLICY | {"privacy_setting": "public_to_workspace",
                                             "audiences": ["internal", "workspace"]})
    assert not posts
    task = await events.create_source(asana, event,
                                      POLICY | {"privacy_setting": "public_to_workspace",
                                                "audiences": ["workspace"]})
    assert task["gid"] == "300" and len(posts) == 1


@pytest.mark.asyncio
async def test_source_creation_rejects_unapproved_audience_before_network(tmp_path):
    events = DotEvents(str(tmp_path / "events.db"))
    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(
                            lambda request: pytest.fail("network used")))
    with pytest.raises(DotEventError, match="source_brief_scope_forbidden"):
        await events.create_source(asana, {
            "type": "task.create", "idempotency_key": "dot-source-creation-0001",
            "name": "Research task", "brief": BRIEF | {"audience": "external"}}, POLICY)


@pytest.mark.asyncio
async def test_event_rejects_wrong_creator_and_deduplicates(tmp_path):
    events = registered(DotEvents(str(tmp_path / "events.db")))
    result = await events.created(client(creator="99"), EVENT, POLICY, PROFILES)
    assert result["reason"] == "creator_not_authorized"
    assert (await events.created(client(), EVENT, POLICY, PROFILES))["reason"] == result["reason"]
    with pytest.raises(DotEventError, match="source_brief_conflict"):
        await events.created(client(), EVENT | {"brief": BRIEF | {"request": "changed"}},
                             POLICY, PROFILES)


@pytest.mark.asyncio
async def test_event_abstains_on_scope_and_blocks_shared_quota(tmp_path):
    events = registered(DotEvents(str(tmp_path / "events.db")))
    assert (await events.created(client(), EVENT, POLICY | {"project_gid": "201"},
                                 PROFILES))["reason"] == "project_scope_mismatch"
    events = registered(DotEvents(str(tmp_path / "other.db")))
    assert (await events.created(client(), EVENT, POLICY, PROFILES,
                                 dispatch=True))["reason"] == "shared_quota_unavailable"


@pytest.mark.asyncio
async def test_event_abstains_if_original_human_owner_changed(tmp_path):
    events = registered(DotEvents(str(tmp_path / "events.db")))
    result = await events.created(client(assignee="77"), EVENT, POLICY, PROFILES,
                                  dispatch=True)
    assert result == {"action": "skip", "reason": "human_owner_changed"}


@pytest.mark.asyncio
async def test_agent_value_requires_server_routing(tmp_path):
    events = registered(DotEvents(str(tmp_path / "events.db")))
    assert (await events.created(client(), EVENT, POLICY | {"routing": {}},
                                 PROFILES))["reason"] == "no_agent_value_for_task_type"


@pytest.mark.asyncio
async def test_event_cannot_be_unblocked_by_event_or_policy_cost_claim(tmp_path):
    events = registered(DotEvents(str(tmp_path / "events.db")))
    policy = POLICY | {"budget": {"scope": "metis.shokko", "cycle": "2026-11",
                                  "max_units": 1_000_000, "max_calls": 50,
                                  "max_per_dispatch_units": 1_000_000,
                                  "enforcement_verified": True}}
    result = await events.created(client(), EVENT | {"claimed_remaining": 50},
                                  policy, PROFILES, dispatch=True)
    assert result == {"action": "blocked", "reason": "shared_quota_unavailable"}
    assert (await events.created(client(), EVENT | {"claimed_remaining": 50},
                                 policy, PROFILES, dispatch=True))["reason"] == "shared_quota_unavailable"


@pytest.mark.asyncio
async def test_human_story_controls_review(tmp_path):
    events = DotEvents(str(tmp_path / "events.db"))
    events.record("300", "a" * 64, "created", "review_pending", "301", "11")
    proposal = {"gid": "399", "created_by": {"gid": "11"},
                "created_at": "2026-11-09T12:00:00Z", "text": "Draft evidence"}
    bad = client(stories=[{"gid": "400", "created_by": {"gid": "11"},
                           "created_at": "2026-11-09T12:01:00Z",
                           "text": "METIS_APPROVE"}, proposal])
    with pytest.raises(DotEventError, match="review_not_pending"):
        await events.review(bad, "300", "400", "44")
    with pytest.raises(DotEventError, match="proposal_evidence_invalid"):
        await events.propose(client(stories=[proposal | {"created_by": None}]),
                             "300", "399")
    assert (await events.propose(bad, "300", "399"))["review_state"] == "PENDING"
    with pytest.raises(DotEventError, match="reviewer_or_evidence_invalid"):
        await events.review(bad, "300", "400", "44")
    early = client(stories=[{"gid": "400", "created_by": {"gid": "44"},
                             "created_at": "2026-11-09T11:59:00Z",
                             "text": "METIS_APPROVE"}, proposal])
    with pytest.raises(DotEventError, match="review_precedes_proposal"):
        await events.review(early, "300", "400", "44")
    good_stories = [{"gid": "400", "created_by": {"gid": "44"},
                     "created_at": "2026-11-09T12:01:00Z",
                     "text": "METIS_REJECT"}, proposal]
    good = client(stories=good_stories)
    with pytest.raises(DotEventError, match="human_source_changed"):
        await events.review(client(stories=good_stories, completed=True),
                            "300", "400", "44")
    assert (await events.review(good, "300", "400", "44"))["review_state"] == "REJECTED"
    with pytest.raises(DotEventError, match="review_not_pending"):
        await events.review(good, "300", "400", "44")


@pytest.mark.asyncio
async def test_anonymous_agent_subtask_requires_human_review_without_claiming_authorship(tmp_path):
    events = DotEvents(str(tmp_path / "events.db"))
    events.record("300", "a" * 64, "created", "review_pending", "301", "11")
    subtask = {"gid": "302", "name": "Draft for review", "notes": "Three synthetic hypotheses",
               "workspace": {"gid": "100"}, "parent": {"gid": "301"},
               "assignee": {"gid": "44"}, "created_by": None,
               "completed": False, "created_at": "2026-11-09T12:00:00Z",
               "modified_at": "2026-11-09T12:01:00Z"}
    stories = [{"gid": "400", "created_by": {"gid": "44"},
                "resource_subtype": "comment_added", "is_edited": False,
                "created_at": "2026-11-09T12:02:00Z", "text": "METIS_APPROVE"}]

    def handler(request):
        path = request.url.path
        if path.endswith("/tasks/301/subtasks"):
            return httpx.Response(200, json={"data": [{"gid": "302", "parent": {"gid": "301"}}]})
        if path.endswith("/tasks/302"):
            return httpx.Response(200, json={"data": subtask})
        if path.endswith("/tasks/301/stories"):
            return httpx.Response(200, json={"data": stories})
        return httpx.Response(200, json={"data": {
            "gid": path.rsplit("/", 1)[-1], "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}], "assignee": {"gid": "11" if path.endswith("/301") else "44"},
            "completed": path.endswith("/301")}})

    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    proposal = await events.propose(asana, "300", subtask_gid="302", owner_gid="44")
    assert proposal == {"review_state": "PENDING", "proposal_gid": "302",
                        "proposal_kind": "subtask", "proposal_provenance": "actor_unavailable"}
    assert await events.propose(asana, "300", subtask_gid="302", owner_gid="44") == proposal
    decision = await events.review(asana, "300", "400", "44")
    assert decision == {"review_state": "APPROVED", "evidence_gid": "400",
                        "proposal_gid": "302", "proposal_kind": "subtask",
                        "proposal_provenance": "actor_unavailable"}
    assert tuple(events.db.execute(
        "SELECT proposal_gid,evidence_gid,reviewer_gid,review_state FROM asana_dot_decisions"
    ).fetchone()) == ("302", "400", "44", "APPROVED")


@pytest.mark.asyncio
@pytest.mark.parametrize("changed,reason", [
    ({"parent": {"gid": "999"}}, "proposal_subtask_invalid"),
    ({"assignee": {"gid": "99"}}, "proposal_subtask_invalid"),
    ({"notes": ""}, "proposal_subtask_invalid"),
    ({"created_by": {"gid": "44"}}, "proposal_creator_mismatch"),
])
async def test_subtask_proposal_rejects_wrong_scope_or_empty_artifact(tmp_path, changed, reason):
    events = DotEvents(str(tmp_path / "events.db"))
    events.record("300", "a" * 64, "created", "review_pending", "301", "11")
    subtask = {"gid": "302", "name": "Draft", "notes": "Evidence",
               "workspace": {"gid": "100"}, "parent": {"gid": "301"},
               "assignee": {"gid": "44"}, "created_by": None,
               "completed": False, "created_at": "2026-11-09T12:00:00Z",
               "modified_at": "2026-11-09T12:01:00Z"} | changed

    def handler(request):
        if request.url.path.endswith("/tasks/301/subtasks"):
            return httpx.Response(200, json={"data": [{"gid": "302"}]})
        if request.url.path.endswith("/tasks/302"):
            return httpx.Response(200, json={"data": subtask})
        return httpx.Response(200, json={"data": {
            "gid": "301", "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}], "assignee": {"gid": "11"}}})

    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    with pytest.raises(DotEventError, match=reason):
        await events.propose(asana, "300", subtask_gid="302", owner_gid="44")
    assert events.db.execute(
        "SELECT proposal_gid FROM asana_dot_decisions").fetchone()[0] is None


@pytest.mark.asyncio
async def test_subtask_modified_after_approval_comment_remains_pending(tmp_path):
    events = DotEvents(str(tmp_path / "events.db"))
    events.record("300", "a" * 64, "created", "review_pending", "301", "11")
    subtask = {"gid": "302", "name": "Draft", "notes": "Evidence",
               "workspace": {"gid": "100"}, "parent": {"gid": "301"},
               "assignee": {"gid": "44"}, "created_by": None,
               "completed": False, "created_at": "2026-11-09T12:00:00Z",
               "modified_at": "2026-11-09T12:01:00Z"}
    stories = [{"gid": "400", "created_by": {"gid": "44"},
                "resource_subtype": "comment_added", "is_edited": False,
                "created_at": "2026-11-09T12:02:00Z", "text": "METIS_APPROVE"}]

    def handler(request):
        path = request.url.path
        if path.endswith("/tasks/301/subtasks"):
            return httpx.Response(200, json={"data": [{"gid": "302"}]})
        if path.endswith("/tasks/302"):
            return httpx.Response(200, json={"data": subtask})
        if path.endswith("/tasks/301/stories"):
            return httpx.Response(200, json={"data": stories})
        return httpx.Response(200, json={"data": {
            "gid": path.rsplit("/", 1)[-1], "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}],
            "assignee": {"gid": "11" if path.endswith("/301") else "44"},
            "completed": False}})

    asana = AsanaAgents("token", "100", {"200": "SHOKKO"},
                        transport=httpx.MockTransport(handler))
    await events.propose(asana, "300", subtask_gid="302", owner_gid="44")
    subtask["modified_at"] = "2026-11-09T12:03:00Z"
    with pytest.raises(DotEventError, match="proposal_changed_after_review"):
        await events.review(asana, "300", "400", "44")
    assert events.db.execute(
        "SELECT review_state FROM asana_dot_decisions").fetchone()[0] == "PENDING"


def test_authenticated_review_accepts_verified_subtask_and_human_comment(tmp_path, monkeypatch):
    import json
    import missingmcp.app as app_module
    from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth

    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps(POLICY))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", json.dumps(PROFILES))
    cfg = load_config({"GATEWAY_SECRET": "g" * 40, "DATA_DIR": str(tmp_path),
                       "DB_PATH": str(tmp_path / "gateway.db"),
                       "PUBLIC_URL": "https://gateway.example.com",
                       "METIS_BRIDGE_URL": "https://script.google.com/macros/s/synthetic/exec",
                       "METIS_BRIDGE_SECRET": "b" * 40, "METIS_OPERATOR_KEY": "m" * 40,
                       "ASANA_DISPATCH_CLIENT_ID": "123456789",
                       "ASANA_DISPATCH_CLIENT_SECRET": "c" * 40,
                       "ASANA_DISPATCH_SETUP_KEY": "s" * 40})
    events = DotEvents(cfg.db_path)
    events.db.execute("INSERT INTO asana_dot_sources"
                      "(idem,fingerprint,state,task_gid,project_gid) "
                      "VALUES ('synthetic-source-0001',?,'CREATED','300','200')",
                      ("a" * 64,))
    events.record("300", "a" * 64, "created", "review_pending", "301", "11")
    events.db.close()
    methods = []

    def upstream(request):
        methods.append(request.method)
        path = request.url.path
        if path.endswith("/tasks/301/subtasks"):
            return httpx.Response(200, json={"data": [{"gid": "302"}]})
        if path.endswith("/tasks/302"):
            return httpx.Response(200, json={"data": {
                "gid": "302", "name": "Draft", "notes": "Synthetic evidence",
                "workspace": {"gid": "100"}, "parent": {"gid": "301"},
                "assignee": {"gid": "44"}, "created_by": None,
                "completed": False, "created_at": "2026-11-09T12:00:00Z",
                "modified_at": "2026-11-09T12:01:00Z"}})
        if path.endswith("/tasks/301/stories"):
            return httpx.Response(200, json={"data": [{
                "gid": "400", "created_by": {"gid": "44"},
                "resource_subtype": "comment_added", "is_edited": False,
                "created_at": "2026-11-09T12:02:00Z", "text": "METIS_APPROVE"}]})
        return httpx.Response(200, json={"data": {
            "gid": path.rsplit("/", 1)[-1], "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}],
            "assignee": {"gid": "11" if path.endswith("/301") else "44"},
            "completed": path.endswith("/301")}})

    original = AsanaAgents
    monkeypatch.setattr(app_module, "AsanaAgents", lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(upstream)))

    async def access_token(_self):
        return "synthetic-token"

    monkeypatch.setattr(AsanaDispatchOAuth, "access_token", access_token)
    api = TestClient(build_app(cfg))
    headers = {"Authorization": "Bearer " + "m" * 40}
    assert api.post("/asana-dot/event", json={"type": "task.proposal",
                    "task_gid": "300", "subtask_gid": "302"}).status_code == 401
    proposed = api.post("/asana-dot/event", json={"type": "task.proposal",
                        "task_gid": "300", "subtask_gid": "302"}, headers=headers)
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["proposal_provenance"] == "actor_unavailable"
    reviewed = api.post("/asana-dot/event", json={"type": "task.review",
                        "task_gid": "300", "story_gid": "400"}, headers=headers)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["review_state"] == "APPROVED"
    assert reviewed.json()["proposal_kind"] == "subtask"
    assert set(methods) == {"GET"}


def test_event_endpoint_requires_existing_metis_credential(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps(POLICY))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", json.dumps(PROFILES))
    cfg = load_config({"GATEWAY_SECRET": "g" * 40, "DATA_DIR": str(tmp_path),
                       "DB_PATH": str(tmp_path / "gateway.db"),
                       "PUBLIC_URL": "https://gateway.example.com",
                       "METIS_BRIDGE_URL": "https://script.google.com/macros/s/test123/exec",
                       "METIS_BRIDGE_SECRET": "b" * 40,
                       "METIS_OPERATOR_KEY": "m" * 40,
                       "ASANA_DISPATCH_CLIENT_ID": "1220338382227997",
                       "ASANA_DISPATCH_CLIENT_SECRET": "c" * 40,
                       "ASANA_DISPATCH_SETUP_KEY": "s" * 40})
    app = TestClient(build_app(cfg))
    assert app.post("/asana-dot/event", json=EVENT).status_code == 401
    assert app.post("/asana-dot/event", json=EVENT,
                    headers={"Authorization": "Bearer wrong"}).status_code == 401
    from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth
    async def token(_self):
        return "synthetic-token"
    monkeypatch.setattr(AsanaDispatchOAuth, "access_token", token)
    monkeypatch.setattr("missingmcp.app.AsanaAgents", lambda *a, **k: client())
    assert app.post("/asana-dot/event", json=[],
                    headers={"Authorization": "Bearer " + "m" * 40}).status_code == 409
    response = app.post("/asana-dot/event", json=EVENT,
                        headers={"Authorization": "Bearer " + "m" * 40})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "source_not_created_by_integration"


@pytest.mark.parametrize("human_only", [False, True])
def test_http_create_is_authenticated_durable_and_quota_blocked(tmp_path, monkeypatch,
                                                                human_only):
    import json
    import missingmcp.app as app_module
    from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth

    policy = POLICY | {"eligible_task_names": [], "routing": {}, "specialties": []} if human_only else POLICY
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps(policy))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", json.dumps({} if human_only else PROFILES))
    cfg = load_config({"GATEWAY_SECRET": "g" * 40, "DATA_DIR": str(tmp_path),
                       "DB_PATH": str(tmp_path / "gateway.db"),
                       "PUBLIC_URL": "https://gateway.example.com",
                       "METIS_BRIDGE_URL": "https://script.google.com/macros/s/synthetic/exec",
                       "METIS_BRIDGE_SECRET": "b" * 40, "METIS_OPERATOR_KEY": "m" * 40,
                       "ASANA_DISPATCH_CLIENT_ID": "1220338382227997",
                       "ASANA_DISPATCH_CLIENT_SECRET": "c" * 40,
                       "ASANA_DISPATCH_SETUP_KEY": "s" * 40})
    posts = []

    def upstream(request):
        if request.url.path.endswith("/projects/200"):
            return httpx.Response(200, json={"data": {"gid": "200",
                "workspace": {"gid": "100"}, "owner": {"gid": "44"},
                "privacy_setting": "private"}})
        if request.method == "POST":
            posts.append(json.loads(request.content)["data"])
            return httpx.Response(201, json={"data": {"gid": "300"}})
        return httpx.Response(200, json={"data": {
            "gid": "300", "name": "Research task", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}, "completed": False}})

    original = AsanaAgents
    monkeypatch.setattr(app_module, "AsanaAgents", lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(upstream)))
    async def access_token(_self):
        return "synthetic-token"
    monkeypatch.setattr(AsanaDispatchOAuth, "access_token", access_token)
    client = TestClient(build_app(cfg))
    event = {"type": "task.create", "idempotency_key": "http-source-creation-0001",
             "name": "Research task",
             "brief": {k: v for k, v in BRIEF.items() if k != "source_task_gid"}}
    assert client.post("/asana-dot/event", json=event).status_code == 401
    headers = {"Authorization": "Bearer " + "m" * 40}
    first = client.post("/asana-dot/event", json=event, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"source_task_gid": "300", "evaluation": (
        {"action": "skip", "reason": "task_not_eligible"} if human_only else
        {"action": "blocked", "reason": "shared_quota_unavailable"})}
    second = client.post("/asana-dot/event", json=event, headers=headers)
    assert second.status_code == 200 and second.json()["source_task_gid"] == "300"
    assert len(posts) == 1 and posts[0]["assignee"] == "44"
    assert "Solicitud: Summarize the permitted evidence" in posts[0]["notes"]


def test_http_multiscope_requires_registered_project_and_preserves_owner(tmp_path, monkeypatch):
    import json
    import missingmcp.app as app_module
    from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth

    policy = {"workspace_gid": "100", "creator_gid": "55", "projects": {
        "200": {"context": "SHOKKO", "owner_gid": "44", "privacy_setting": "private",
                "audiences": ["project_members"], "sources": ["current_request"],
                "eligible_task_names": [], "routing": {}, "specialties": [], "agent_gids": []},
        "201": {"context": "ANDREA", "owner_gid": "46",
                "privacy_setting": "public_to_workspace", "audiences": ["workspace"],
                "sources": ["current_request"], "eligible_task_names": [],
                "routing": {}, "specialties": [], "agent_gids": []}}}
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps(policy))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", "{}")
    cfg = load_config({"GATEWAY_SECRET": "g" * 40, "DATA_DIR": str(tmp_path),
                       "DB_PATH": str(tmp_path / "gateway.db"),
                       "PUBLIC_URL": "https://gateway.example.com",
                       "METIS_BRIDGE_URL": "https://script.google.com/macros/s/synthetic/exec",
                       "METIS_BRIDGE_SECRET": "b" * 40, "METIS_OPERATOR_KEY": "m" * 40,
                       "ASANA_DISPATCH_CLIENT_ID": "1220338382227997",
                       "ASANA_DISPATCH_CLIENT_SECRET": "c" * 40,
                       "ASANA_DISPATCH_SETUP_KEY": "s" * 40})
    posts = []
    def upstream(request):
        if request.url.path.endswith("/projects/201"):
            return httpx.Response(200, json={"data": {"gid": "201",
                "workspace": {"gid": "100"}, "privacy_setting": "public_to_workspace"}})
        if request.method == "POST":
            posts.append(json.loads(request.content)["data"])
            return httpx.Response(201, json={"data": {"gid": "301"}})
        return httpx.Response(200, json={"data": {
            "gid": "301", "name": "Synthetic work", "workspace": {"gid": "100"},
            "created_by": {"gid": "55"}, "projects": [{"gid": "201"}],
            "assignee": {"gid": "46"}, "completed": False}})
    original = AsanaAgents
    monkeypatch.setattr(app_module, "AsanaAgents", lambda *args, **kwargs:
                        original(*args, **kwargs, transport=httpx.MockTransport(upstream)))
    async def token(_self):
        return "synthetic-token"
    monkeypatch.setattr(AsanaDispatchOAuth, "access_token", token)
    app = TestClient(build_app(cfg))
    headers = {"Authorization": "Bearer " + "m" * 40}
    brief = {"context": "ANDREA", "project_gid": "201", "audience": "workspace",
             "authorized_sources": ["current_request"], "deliverable": "Draft",
             "request": "Synthetic task"}
    event = {"type": "task.create", "idempotency_key": "multiscope-create-0001",
             "name": "Synthetic work", "brief": brief}
    assert app.post("/asana-dot/event", json=event).status_code == 401
    malformed = app.post("/asana-dot/event", json={"type": "task.review",
                         "task_gid": ["301"], "story_gid": "400"}, headers=headers)
    assert malformed.status_code == 409 and malformed.json()["reason"] == "invalid_task_gid"
    forbidden = app.post("/asana-dot/event", json=event | {"brief": brief | {"project_gid": "202"}},
                         headers=headers)
    assert forbidden.status_code == 409 and forbidden.json()["reason"] == "project_not_authorized"
    created = app.post("/asana-dot/event", json=event, headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["evaluation"] == {"action": "skip", "reason": "task_not_eligible"}
    assert app.post("/asana-dot/event", json=event, headers=headers).status_code == 200
    assert len(posts) == 1 and posts[0]["projects"] == ["201"]
    assert posts[0]["assignee"] == "46"
    monkeypatch.setenv("ASANA_AGENT_PROJECTS_JSON", json.dumps({"201": "SHOKKO"}))
    blocked = app.post("/asana-dot/event", json=event | {
        "idempotency_key": "multiscope-create-0002"}, headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "project_context_conflict"
    assert len(posts) == 1
