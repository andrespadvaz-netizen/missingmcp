import json

import httpx
import pytest

from missingmcp.metis.asana_agents import AsanaAgents, AsanaError


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
        if request.method == "PUT":
            writes.append(request)
            return httpx.Response(200, json={"data": {}})
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        return httpx.Response(200, json={"data": {
            "gid": "300", "workspace": {"gid": "100"},
            "projects": [{"gid": "200"}], "assignee": {"gid": "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         transport=httpx.MockTransport(handler))
    with pytest.raises(AsanaError, match="task_already_assigned"):
        await client.assign_dedicated_task("300", "11", "200")
    assert not writes


@pytest.mark.asyncio
async def test_assignment_verifies_result_and_does_not_send_task_body():
    assigned = False

    def handler(request):
        nonlocal assigned
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
                         transport=httpx.MockTransport(handler))
    assert (await client.assign_dedicated_task("300", "11", "200"))["assignee"]["gid"] == "11"


@pytest.mark.asyncio
async def test_project_boundary_rejects_before_network():
    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         transport=httpx.MockTransport(lambda request: pytest.fail("network used")))
    with pytest.raises(AsanaError, match="project_forbidden"):
        await client.assign_dedicated_task("300", "11", "999")


@pytest.mark.asyncio
async def test_dedicated_task_create_is_durable_and_idempotent(tmp_path):
    created = []

    def handler(request):
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
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{"gid": "11"}]})
        if request.method == "POST":
            posts.append(1)
            raise httpx.ReadTimeout("lost response")
        return httpx.Response(200, json={"data": {
            "gid": "300", "workspace": {"gid": "100"}, "projects": [{"gid": "200"}],
            "assignee": {"gid": "44"}}})

    client = AsanaAgents("token", "100", {"200": "SHOKKO"},
                         transport=httpx.MockTransport(handler),
                         ledger_path=str(tmp_path / "dispatch.db"))
    args = dict(source_task_gid="300", project_gid="200", agent_gid="11",
                title="Review", request="Check evidence", idempotency_key="same-request-0001")
    with pytest.raises(AsanaError, match="transport_uncertain"):
        await client.create_dedicated_task(**args)
    with pytest.raises(AsanaError, match="dispatch_uncertain"):
        await client.create_dedicated_task(**args)
    assert len(posts) == 1
