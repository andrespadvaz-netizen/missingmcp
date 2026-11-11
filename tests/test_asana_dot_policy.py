"""Synthetic boundaries for global discovery and project-scoped authority."""
import json

import httpx
import pytest

from missingmcp.adapters.metis import MetisAdapter
from missingmcp.metis.asana_dot_policy import DotPolicyError, load_dot_policy, select_scope
from test_metis_asana_tool import config, request


def registry():
    return {
        "workspace_gid": "100", "creator_gid": "55", "projects": {
            "200": {"context": "SHOKKO", "owner_gid": "44",
                    "privacy_setting": "private", "audiences": ["project_members"],
                    "sources": ["current_request"], "eligible_task_names": [],
                    "routing": {}, "specialties": [], "agent_gids": []},
            "201": {"context": "ANDREA", "owner_gid": "46",
                    "privacy_setting": "public_to_workspace", "audiences": ["workspace"],
                    "sources": ["current_request"], "eligible_task_names": [],
                    "routing": {}, "specialties": [], "agent_gids": []},
        }}


def test_registry_selects_only_explicit_scopes():
    workspace, creator, scopes, profiles = load_dot_policy(json.dumps(registry()), "{}")
    assert (workspace, creator, profiles) == ("100", "55", {})
    assert select_scope(scopes, "200")["owner_gid"] == "44"
    assert select_scope(scopes, "201")["context"] == "ANDREA"
    with pytest.raises(DotPolicyError, match="project_not_authorized"):
        select_scope(scopes, "202")


def test_registry_rejects_broadened_audience_and_unapproved_agents():
    policy = registry()
    policy["projects"]["200"]["audiences"] = ["workspace"]
    with pytest.raises(DotPolicyError, match="privacy_audience_mismatch"):
        load_dot_policy(json.dumps(policy), "{}")
    policy = registry()
    policy["projects"]["200"]["agent_gids"] = ["99"]
    with pytest.raises(DotPolicyError, match="invalid_agent_allowlist"):
        load_dot_policy(json.dumps(policy), "{}")


@pytest.mark.asyncio
async def test_discovery_filters_private_projects_and_never_writes(tmp_path, monkeypatch):
    import missingmcp.adapters.metis as module

    monkeypatch.setenv("ASANA_WORKSPACE_GID", "100")
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps(registry()))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", "{}")
    adapter = MetisAdapter(config(tmp_path))

    class OAuth:
        def __init__(self, _config):
            self.db = self
        async def access_token(self):
            return "synthetic-token"
        def close(self):
            pass

    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"data": [
            {"gid": "200", "name": "Approved private", "privacy_setting": "private"},
            {"gid": "202", "name": "Other private", "privacy_setting": "private"},
            {"gid": "201", "name": "Workspace project", "privacy_setting": "public_to_workspace"},
            {"gid": "203", "name": "Unapproved workspace", "privacy_setting": "public_to_workspace",
             "owner": {"gid": "66"}},
        ], "next_page": None})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(module, "AsanaDispatchOAuth", OAuth)
    monkeypatch.setattr(module.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = await adapter.asana_workspace_inventory()
    assert calls == ["GET"]
    assert result["complete"] is True
    assert result["other_private_project_count"] == 1
    assert result["other_restricted_project_count"] == 0
    assert {p["gid"] for p in result["projects"]} == {"200", "201", "203"}
    assert next(p for p in result["projects"] if p["gid"] == "203")["authorized"] is False
    assert next(p for p in result["projects"] if p["gid"] == "203")[
        "project_owner_candidate_gid"] == "66"
    monkeypatch.setenv("ASANA_WORKSPACE_ADMIN_DISCOVERY", "true")
    full_result = await adapter.asana_workspace_inventory()
    assert full_result["other_private_project_count"] == 0
    assert next(p for p in full_result["projects"] if p["gid"] == "202")[
        "authorized"] is False
    monkeypatch.delenv("ASANA_WORKSPACE_ADMIN_DISCOVERY")
    _, _, listed = await adapter.handle(None, "metis-operator",
                                         adapter.credential_version, request("tools/list"))
    assert "metis_asana_workspace_inventory" in [
        tool["name"] for tool in json.loads(listed)["result"]["tools"]]
    _, _, called = await adapter.handle(None, "metis-operator",
                                         adapter.credential_version,
                                         request("tools/call", "metis_asana_workspace_inventory", {}))
    assert json.loads(called)["result"]["isError"] is False
