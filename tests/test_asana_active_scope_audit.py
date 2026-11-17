"""A missing workspace listing must not mark a readable project inactive."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


_path = Path(__file__).resolve().parents[1] / "scripts" / "asana_active_scope_audit.py"
_spec = importlib.util.spec_from_file_location("asana_active_scope_audit", _path)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


@pytest.mark.asyncio
@pytest.mark.parametrize("listed_completed", [False, True])
async def test_direct_get_recovers_registered_active_project(
        tmp_path, monkeypatch, capsys, listed_completed):
    policy = {"workspace_gid": "100", "creator_gid": "55", "projects": {
        "200": {"context": "SHOKKO", "owner_gid": "44",
                "project_owner_gid": "44", "privacy_setting": "private",
                "audiences": ["project_members"], "sources": ["current_request"],
                "eligible_task_names": [], "routing": {}, "specialties": [],
                "agent_gids": []},
        "201": {"context": "ANDREA", "owner_gid": "46",
                "project_owner_gid": "46", "privacy_setting": "private",
                "audiences": ["project_members"], "sources": ["current_request"],
                "eligible_task_names": [], "routing": {}, "specialties": [],
                "agent_gids": []}}}
    monkeypatch.setenv("ASANA_METIS_DOT_POLICY_JSON", json.dumps(policy))
    monkeypatch.setenv("ASANA_AGENT_PROFILES_JSON", "{}")
    monkeypatch.delenv("ASANA_AGENT_PROJECTS_JSON", raising=False)
    monkeypatch.setattr(audit, "load_config", lambda: object())

    class OAuth:
        def __init__(self, _config):
            self.db = self
        async def access_token(self):
            return "synthetic-token"
        def close(self):
            pass

    monkeypatch.setattr(audit, "AsanaDispatchOAuth", OAuth)
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.url.path.endswith("/workspaces/100/projects"):
            return httpx.Response(200, json={"data": [{
                "gid": "200", "name": "Listed", "archived": False,
                "completed": listed_completed,
                "workspace": {"gid": "100"}, "owner": {"gid": "44"},
                "privacy_setting": "private"}], "next_page": None})
        assert request.url.path.endswith("/projects/201")
        return httpx.Response(200, json={"data": {
            "gid": "201", "name": "Direct", "archived": False,
            "completed": False,
            "workspace": {"gid": "100"}, "owner": {"gid": "46"},
            "privacy_setting": "private"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(audit.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    await audit.main()
    output = capsys.readouterr().out
    assert f"ACTIVOS_LISTADOS {0 if listed_completed else 1}" in output
    assert f"FINALIZADOS_LISTADOS {1 if listed_completed else 0}" in output
    assert f"REGISTRADOS_ACTIVOS {1 if listed_completed else 2}" in output
    assert f"REGISTRADOS_FINALIZADOS {1 if listed_completed else 0}" in output
    if listed_completed:
        assert "REGISTRADO_FINALIZADO 200" in output
    assert "REGISTRADOS_ACTIVOS_DIRECTOS 1" in output
    assert "REGISTRADO_ACTIVO_DIRECTO 201" in output
    assert "REGISTRADO_NO_ACTIVO" not in output
    assert "synthetic-token" not in output
    assert methods == ["GET", "GET"]


@pytest.mark.parametrize("change,expected", [
    ({"archived": True}, "ARCHIVADO"),
    ({"archived": None}, "NO_VERIFICADO"),
    ({"completed": True}, "FINALIZADO"),
    ({"completed": None}, "NO_VERIFICADO"),
    ({"workspace": {"gid": "999"}}, "OTRO_WORKSPACE"),
    ({"owner": {"gid": "99"}}, "POLITICA_CONFLICTO"),
    ({"privacy_setting": "public_to_workspace"}, "POLITICA_CONFLICTO"),
])
def test_direct_project_requires_matching_authority(change, expected):
    project = {"gid": "201", "archived": False, "completed": False,
               "workspace": {"gid": "100"}, "owner": {"gid": "46"},
               "privacy_setting": "private"}
    project.update(change)
    scope = {"project_gid": "201", "project_owner_gid": "46",
             "privacy_setting": "private"}
    assert audit.registered_project_state(project, scope, "100") == expected
