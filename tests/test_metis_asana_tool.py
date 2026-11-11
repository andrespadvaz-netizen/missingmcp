import json

import pytest

from missingmcp.adapters.metis import MetisAdapter, MetisChatGPTAdapter
from missingmcp.config import load_config


def config(tmp_path):
    return load_config({
        "GATEWAY_SECRET": "g" * 40, "DATA_DIR": str(tmp_path),
        "DB_PATH": str(tmp_path / "gateway.db"),
        "PUBLIC_URL": "https://gateway.example.com",
        "METIS_BRIDGE_URL": "https://script.google.com/macros/s/synthetic/exec",
        "METIS_BRIDGE_SECRET": "b" * 40, "METIS_OPERATOR_KEY": "m" * 40,
        "ASANA_DISPATCH_CLIENT_ID": "1220338382227997",
        "ASANA_DISPATCH_CLIENT_SECRET": "c" * 40,
        "ASANA_DISPATCH_SETUP_KEY": "s" * 40,
    })


def request(method, name=None, arguments=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if name:
        body["params"] = {"name": name, "arguments": arguments}
    return json.dumps(body).encode()


@pytest.mark.asyncio
async def test_metis_create_tool_is_only_listed_with_policy(tmp_path, monkeypatch):
    adapter = MetisAdapter(config(tmp_path))
    _, _, raw = await adapter.handle(None, "metis-operator",
                                     adapter.credential_version, request("tools/list"))
    assert "metis_asana_create_task" not in [t["name"] for t in json.loads(raw)["result"]["tools"]]
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps({
        "workspace_gid": "100", "project_gid": "200", "context": "SHOKKO",
        "owner_gid": "44", "creator_gid": "55", "audiences": ["internal"],
        "sources": ["current_request"], "eligible_task_names": [],
        "routing": {}, "specialties": []}))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", json.dumps({"11": {}}))
    adapter = MetisAdapter(config(tmp_path))
    _, _, raw = await adapter.handle(None, "metis-operator",
                                     adapter.credential_version, request("tools/list"))
    assert "metis_asana_create_task" in [t["name"] for t in json.loads(raw)["result"]["tools"]]
    create_tool = next(t for t in json.loads(raw)["result"]["tools"]
                       if t["name"] == "metis_asana_create_task")
    brief_schema = create_tool["inputSchema"]["properties"]["brief"]
    assert brief_schema["properties"]["context"]["const"] == "SHOKKO"
    assert brief_schema["properties"]["authorized_sources"]["items"]["enum"] == ["current_request"]
    other = MetisChatGPTAdapter(config(tmp_path))
    _, _, raw = await other.handle(None, "metis-operator", other.credential_version,
                                   request("tools/list"))
    assert "metis_asana_create_task" in [t["name"] for t in json.loads(raw)["result"]["tools"]]
    with pytest.raises(Exception):
        await adapter.handle(None, "wrong-account", adapter.credential_version,
                             request("tools/list"))
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps({
        "workspace_gid": "100", "project_gid": "200", "context": "SHOKKO",
        "owner_gid": "44", "creator_gid": "55",
        "audiences": ["internal"], "sources": ["current_request"],
        "privacy_setting": "public_to_workspace", "eligible_task_names": [],
        "routing": {}, "specialties": []}))
    guarded = MetisChatGPTAdapter(config(tmp_path))
    _, _, raw = await guarded.handle(None, "metis-operator", guarded.credential_version,
                                     request("tools/list"))
    assert "metis_asana_create_task" not in [t["name"] for t in json.loads(raw)["result"]["tools"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [MetisAdapter, MetisChatGPTAdapter])
async def test_metis_tool_uses_server_identity_and_durable_creation(tmp_path, monkeypatch,
                                                                    adapter_type):
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps({
        "workspace_gid": "100", "project_gid": "200", "context": "SHOKKO",
        "owner_gid": "44", "creator_gid": "55", "audiences": ["internal"],
        "sources": ["current_request"], "eligible_task_names": [],
        "routing": {}, "specialties": []}))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", json.dumps({"11": {}}))
    calls = []

    class OAuth:
        def __init__(self, config):
            self.db = self
        async def access_token(self):
            return "synthetic-token"
        def close(self):
            pass

    class Client:
        ledger = None
        handoffs = None
        def __init__(self, bearer, *args, **kwargs):
            assert bearer == "synthetic-token"

    class Events:
        def __init__(self, path):
            self.db = self
        async def create_source(self, client, event, policy):
            calls.append(event)
            return {"gid": "300"}
        async def created(self, client, event, policy, profiles, dispatch):
            assert dispatch is True and event["task_gid"] == "300"
            return {"action": "blocked", "reason": "shared_quota_unavailable"}
        def close(self):
            pass

    monkeypatch.setattr("missingmcp.adapters.metis.AsanaDispatchOAuth", OAuth)
    monkeypatch.setattr("missingmcp.adapters.metis.AsanaAgents", Client)
    monkeypatch.setattr("missingmcp.adapters.metis.DotEvents", Events)
    adapter = adapter_type(config(tmp_path))
    args = {"idempotency_key": "source-event-000001", "name": "Research task",
            "brief": {"context": "SHOKKO", "project_gid": "200"}}
    _, _, raw = await adapter.handle(None, "metis-operator",
                                     adapter.credential_version,
                                     request("tools/call", "metis_asana_create_task", args))
    result = json.loads(raw)["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["source_task_gid"] == "300"
    assert len(calls) == 1 and calls[0]["type"] == "task.create"
