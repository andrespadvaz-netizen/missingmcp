"""Read-only Asana project coverage audit for the configured Metis workspace.

Run inside the Gateway service: python scripts/asana_active_scope_audit.py
Prints project metadata only. It never logs OAuth tokens or reads tasks.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import re

import httpx

from missingmcp.config import load_config
from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth
from missingmcp.metis.asana_dot_policy import load_dot_policy


async def main(probe_gids=()):
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
        for gid in probe_gids:
            if not re.fullmatch(r"[0-9]+", gid):
                raise ValueError("probe_gid_must_be_numeric")
            response = await http.get(
                f"https://app.asana.com/api/1.0/projects/{gid}",
                headers={"Authorization": "Bearer " + token},
                params={"opt_fields":
                        "gid,name,archived,privacy_setting,workspace.gid,owner.gid,team.gid,team.name"})
            if response.status_code in (403, 404):
                print("PROBE", gid, "INACCESSIBLE", response.status_code)
                continue
            response.raise_for_status()
            project = response.json().get("data")
            if not isinstance(project, dict) or str(project.get("gid")) != gid:
                raise RuntimeError("probe_project_identity_unverified")
            print("PROBE", json.dumps({
                "gid": gid, "name": project.get("name"),
                "workspace_match": str((project.get("workspace") or {}).get("gid")) == workspace,
                "archived": project.get("archived"),
                "privacy": project.get("privacy_setting"),
                "owner_gid": (project.get("owner") or {}).get("gid"),
                "team_gid": (project.get("team") or {}).get("gid"),
                "team_name": (project.get("team") or {}).get("name")},
                ensure_ascii=False))
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", nargs="*", default=[], metavar="PROJECT_GID")
    args = parser.parse_args()
    if len(args.probe) > 20 or len(args.probe) != len(set(args.probe)):
        parser.error("1-20 distinct project GIDs are allowed")
    asyncio.run(main(args.probe))
