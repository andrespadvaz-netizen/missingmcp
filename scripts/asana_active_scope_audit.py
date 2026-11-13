"""Read-only Asana project coverage audit for the configured Metis workspace.

Run inside the Gateway service: python scripts/asana_active_scope_audit.py
Prints project metadata only. It never logs OAuth tokens or reads tasks.
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx

from missingmcp.config import load_config
from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth
from missingmcp.metis.asana_dot_policy import load_dot_policy


async def main():
    workspace, _, scopes, _ = load_dot_policy(
        os.environ["ASANA_METIS_DOT_POLICY_JSON"],
        os.environ["ASANA_AGENT_PROFILES_JSON"],
        os.environ.get("ASANA_AGENT_PROJECTS_JSON", ""))
    oauth = AsanaDispatchOAuth(load_config())
    try:
        token = await oauth.access_token()
    finally:
        oauth.db.close()
    active = {}
    archived_count = 0
    offset = None
    seen_offsets = set()
    async with httpx.AsyncClient(timeout=20) as http:
        while True:
            params = {"limit": 100, "opt_fields":
                      "gid,name,archived,privacy_setting,owner.gid,team.gid,team.name"}
            if offset:
                params["offset"] = offset
            response = await http.get(
                f"https://app.asana.com/api/1.0/workspaces/{workspace}/projects",
                headers={"Authorization": "Bearer " + token}, params=params)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise RuntimeError("invalid_project_inventory")
            for project in body["data"]:
                if not isinstance(project, dict):
                    raise RuntimeError("invalid_project_inventory")
                gid = str(project.get("gid", ""))
                state = project.get("archived")
                if not gid.isdigit() or not isinstance(state, bool):
                    raise RuntimeError("project_identity_or_archive_state_unverified")
                if state:
                    archived_count += 1
                elif gid in active:
                    raise RuntimeError("duplicate_project_in_inventory")
                else:
                    active[gid] = project
            next_page = body.get("next_page")
            offset = next_page.get("offset") if isinstance(next_page, dict) else None
            if not offset:
                break
            if offset in seen_offsets:
                raise RuntimeError("project_inventory_pagination_loop")
            seen_offsets.add(offset)
    pending = sorted(set(active) - set(scopes))
    inactive_registered = sorted(set(scopes) - set(active))
    print("ACTIVOS", len(active), "PENDIENTES", len(pending),
          "ARCHIVADOS_VISIBLES", archived_count,
          "REGISTRADOS_NO_ACTIVOS", len(inactive_registered))
    for gid in pending:
        p = active[gid]
        print(json.dumps({
            "gid": gid, "name": p.get("name"),
            "privacy": p.get("privacy_setting"),
            "owner_gid": (p.get("owner") or {}).get("gid"),
            "team_gid": (p.get("team") or {}).get("gid"),
            "team_name": (p.get("team") or {}).get("name")},
            ensure_ascii=False))
    for gid in inactive_registered:
        print("REGISTRADO_NO_ACTIVO", gid)


if __name__ == "__main__":
    asyncio.run(main())
