import json

import httpx
import pytest

from missingmcp.metis.asana_agents import AsanaAgents, AsanaError


def dispatch_metadata(request, *, privacy="private"):
    if request.url.path.endswith("/projects/200"):
        return httpx.Response(200, json={"data": {
            "gid": "200", "workspace": {"gid": "100"},
            "owner": {"gid": "44"}, "privacy_setting": privacy}})
    if request.url.path.endswith("/agents/11"):
        return httpx.Response(200, json={"data": {
            "gid": "11", "workspace": {"gid": "100"},
            "resource_subtype": "asana_teammate"}})
    return None


@pytest.mark.asyncio
async def test_discovery_pages_and_early_access_header():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Asana-Enable"] == "ai_teammate_actors"
        assert request.headers["Authorization"] == "Bearer private-token"
        if "offset=second" in str(request.url):
            return httpx.Response(200, json={"data": [{"gid": "22"}], "next_page": None})
        return httpx.Response(200, json={"data": [{"gid": "11"}],
                                          "next_page": {"offset": "second"}})

    client = AsanaAgents("private-token", "100", {"200": "SHOKKO"},
                         transport=httpx.MockTransport(handler))
    assert [a["gid"] for a in await client.discover()] == ["11", "22"]
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_assignment_rejects_human_owner_without_write():
    writes = []

    def handler(request):
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.method == "PUT":
            writes.append(request)
            return httpx.Response(200, json={"data": {}})
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        return httpx.Response(200, json={"data": {
            "gid": "300", "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}], "assignee": {"gid": "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler))
    with pytest.raises(AsanaError, match="task_already_assigned"):
        await client.assign_dedicated_task("300", "11", "200")
    assert not writes


@pytest.mark.asyncio
async def test_assignment_verifies_result_and_does_not_send_task_body():
    assigned = False

    def handler(request):
        nonlocal assigned
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.method == "PUT":
            assert json.loads(request.content) == {"data": {"assignee": "11"}}
            assigned = True
            return httpx.Response(200, json={"data": {"gid": "300"}})
        return httpx.Response(200, json={"data": {
            "gid": "300", "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}],
            "assignee": {"gid": "11"} if assigned else None}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler))
    assert (await client.assign_dedicated_task("300", "11", "200"))["assignee"]["gid"] == "11"


@pytest.mark.asyncio
async def test_project_boundary_rejects_before_network():
    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         transport=httpx.MockTransport(lambda request: pytest.fail("network used")))
    with pytest.raises(AsanaError, match="project_forbidden"):
        await client.assign_dedicated_task("300", "11", "999")


def test_one_agent_cannot_be_allowlisted_across_contexts():
    with pytest.raises(ValueError, match="across contexts"):
        AsanaAgents("token", "100", {"200": "SHOKKO", "201": "VENTURE_QUEST"},
                    allowed_agent_gids={"200": "11", "201": "11"})


@pytest.mark.asyncio
async def test_team_dispatch_allows_distinct_agents_and_project_owners():
    def handler(request):
        path = request.url.path
        if path.endswith("/projects/200") or path.endswith("/projects/201"):
            project = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"data": {
                "gid": project, "workspace": {"gid": "100"},
                "owner": {"gid": "44" if project == "200" else "45"},
                "privacy_setting": "private"}})
        if path.endswith("/agents/11") or path.endswith("/agents/12"):
            agent = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"data": {
                "gid": agent, "workspace": {"gid": "100"},
                "resource_subtype": "asana_teammate"}})
        if path.endswith("/agents"):
            return httpx.Response(200, json={"data": [
                {"gid": "11"}, {"gid": "12"}]})
        pytest.fail("unexpected request")

    client = AsanaAgents("token", "100", {"200": "SHOKKO", "201": "VENTURE_QUEST"},
                         allowed_agent_gids={"200": ["11", "12"], "201": ["12"]},
                         expected_project_owner_gids={"200": "44", "201": "45"},
                         cross_context_agent_gids=["12"],
                         transport=httpx.MockTransport(handler))
    await client._verify_dispatch_target("200", "11")
    await client._verify_dispatch_target("201", "12")
    with pytest.raises(AsanaError, match="agent_not_allowlisted"):
        await client._verify_dispatch_target("201", "11")


@pytest.mark.asyncio
async def test_dispatch_requires_verified_owner_even_for_allowlisted_agent():
    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": ["11"]},
                         transport=httpx.MockTransport(lambda request: pytest.fail("network used")))
    with pytest.raises(AsanaError, match="dispatch_owner_not_configured"):
        await client._verify_dispatch_target("200", "11")


@pytest.mark.asyncio
async def test_dedicated_task_create_is_durable_and_idempotent(tmp_path):
    created = []

    def handler(request):
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.method == "POST":
            body = json.loads(request.content)["data"]
            assert body["projects"] == ["200"]
            assert body["assignee"] == "11"
            assert body["name"] == "Review"
            created.append(body)
            return httpx.Response(201, json={"data": {"gid": "301"}})
        gid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"data": {
            "gid": gid, "workspace": {"gid": "100"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "11"} if gid == "301" else {"gid": "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"))
    args = dict(source_task_gid="300", project_gid="200", agent_gid="11",
                title="Review", request="Check evidence", idempotency_key="same-request-0001")
    assert (await client.create_dedicated_task(**args))["gid"] == "301"
    assert (await client.create_dedicated_task(**args))["gid"] == "301"
    assert len(created) == 1


@pytest.mark.asyncio
async def test_uncertain_create_is_not_repeated(tmp_path):
    posts = []

    def handler(request):
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.method == "POST":
            posts.append(1)
            raise httpx.ReadTimeout("lost response")
        return httpx.Response(200, json={"data": {
            "gid": "300", "workspace": {"gid": "100"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"))
    args = dict(source_task_gid="300", project_gid="200", agent_gid="11",
                title="Review", request="Check evidence", idempotency_key="same-request-0001")
    with pytest.raises(AsanaError, match="transport_uncertain"):
        await client.create_dedicated_task(**args)
    with pytest.raises(AsanaError, match="dispatch_uncertain"):
        await client.create_dedicated_task(**args)
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_uncertain_handoff_reconciles_one_visible_task_without_repost(tmp_path):
    posts = []
    key = "uncertain-handoff-0001"

    def handler(request):
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.url.path.endswith("/projects/200/tasks"):
            return httpx.Response(200, json={"data": [{
                "gid": "301", "notes": f"Encargo Metis. Referencia: {key}\n",
                "assignee": {"gid": "11"}}]})
        if request.method == "POST":
            posts.append(1)
            raise httpx.ReadTimeout("lost response")
        gid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"data": {
            "gid": gid, "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}],
            "assignee": {"gid": "11" if gid == "301" else "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"),
                         handoff_ledger_path=str(tmp_path / "handoff.db"))
    with pytest.raises(AsanaError, match="transport_uncertain"):
        await client.create_handoff_task(source_task_gid="300", project_gid="200",
                                         agent_gid="11", title="Review", request="Evidence",
                                         idempotency_key=key)
    assert (await client.reconcile_handoff(key, "200", "11"))["gid"] == "301"
    assert (await client.reconcile_handoff(key, "200", "11"))["gid"] == "301"
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_concurrent_same_handoff_never_posts_twice(tmp_path):
    import asyncio
    started = asyncio.Event()
    release = asyncio.Event()
    posts = []

    async def handler(request):
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.method == "POST":
            posts.append(1)
            started.set()
            await release.wait()
            return httpx.Response(201, json={"data": {"gid": "301"}})
        gid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"data": {
            "gid": gid, "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}],
            "assignee": {"gid": "11" if gid == "301" else "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"),
                         handoff_ledger_path=str(tmp_path / "handoff.db"))
    args = dict(source_task_gid="300", project_gid="200", agent_gid="11",
                title="Review", request="Evidence", idempotency_key="concurrent-handoff-0001")
    first = asyncio.create_task(client.create_handoff_task(**args))
    await started.wait()
    with pytest.raises(AsanaError, match="dispatch_uncertain"):
        await client.create_handoff_task(**args)
    release.set()
    assert (await first)["gid"] == "301"
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_dispatch_rejects_public_project_and_unlisted_agent_before_write(tmp_path):
    writes = []

    def handler(request):
        if request.method != "GET":
            writes.append(request)
        meta = dispatch_metadata(request, privacy="public_to_workspace")
        if meta is not None:
            return meta
        return httpx.Response(200, json={"data": [{"gid": "11"}]})

    client = AsanaAgents("token", "100", {"200": "VENTURE_QUEST"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"))
    args = dict(project_gid="200", agent_gid="11", title="Review",
                request="Only supplied evidence", idempotency_key="safe-dispatch-0001")
    with pytest.raises(AsanaError, match="dispatch_project_not_private_or_owned"):
        await client.create_dedicated_task(**args)
    with pytest.raises(AsanaError, match="agent_not_allowlisted"):
        await client.create_dedicated_task(**(args | {"agent_gid": "12"}))
    assert writes == []
    assert client.ledger.execute("SELECT count(*) FROM asana_agent_dispatches").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_self_contained_brief_has_no_source_task_link(tmp_path):
    posted = []

    def handler(request):
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.method == "POST":
            posted.append(json.loads(request.content)["data"])
            return httpx.Response(201, json={"data": {"gid": "301"}})
        return httpx.Response(200, json={"data": {
            "gid": "301", "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}], "assignee": {"gid": "11"}}})

    client = AsanaAgents("token", "100", {"200": "VENTURE_QUEST"},
                         allowed_agent_gids={"200": "11"},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"))
    task = await client.create_dedicated_task(
        project_gid="200", agent_gid="11", title="Evidence review",
        request="Evaluate only 2 + 2 = 4", idempotency_key="safe-dispatch-0002")
    assert task["gid"] == "301"
    assert posted[0]["notes"] == "Encargo Metis. Referencia: safe-dispatch-0002\nContexto: VENTURE_QUEST\n\nEvaluate only 2 + 2 = 4"


@pytest.mark.asyncio
async def test_cross_project_handoff_records_provenance_without_exposing_source(tmp_path):
    posted = []

    def handler(request):
        meta = dispatch_metadata(request)
        if meta is not None:
            return meta
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.method == "POST":
            posted.append(json.loads(request.content)["data"])
            return httpx.Response(201, json={"data": {"gid": "301"}})
        task_gid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"data": {
            "gid": task_gid, "workspace": {"gid": "100"},
            "projects": [{"gid": "201" if task_gid == "300" else "200"}],
            "assignee": {"gid": "11"} if task_gid == "301" else {"gid": "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO", "201": "SHOKKO"},
                         allowed_agent_gids={"200": ["11"]},
                         expected_project_owner_gids={"200": "44"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"),
                         handoff_ledger_path=str(tmp_path / "handoff.db"))
    args = dict(source_task_gid="300", project_gid="200", agent_gid="11",
                title="Prepare launch brief", request="Use this approved summary only",
                idempotency_key="handoff-shokko-0001")
    assert (await client.create_handoff_task(**args))["gid"] == "301"
    assert (await client.create_handoff_task(**args))["gid"] == "301"
    assert len(posted) == 1
    assert "300" not in posted[0]["notes"]
    assert tuple(client.handoffs.db.execute(
        "SELECT source_task_gid,target_task_gid FROM team_handoffs").fetchone()) == ("300", "301")
