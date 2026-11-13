"""Propose a verified, human-only Asana policy expansion without writing Asana.

Run inside the Gateway service. The resulting JSON is a candidate for human
review in Railway; this script never changes Railway variables or Asana.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re

import httpx

from missingmcp.config import load_config
from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth
from missingmcp.metis.asana_dot_policy import load_dot_policy
from missingmcp.metis.queue import canonical_context


def parse_additions(values):
    additions = {}
    for value in values:
        context, separator, gids = value.partition(":")
        try:
            valid_context = separator and canonical_context(context) == context
        except ValueError:
            valid_context = False
        if not valid_context:
            raise ValueError("invalid_project_context")
        for gid in gids.split(","):
            if not re.fullmatch(r"[0-9]+", gid) or gid in additions:
                raise ValueError("invalid_or_duplicate_project_gid")
            additions[gid] = context
    return additions


def make_candidate(original, additions, retired, observed, profiles_raw, legacy_raw=""):
    if not isinstance(original, dict) or not isinstance(original.get("projects"), dict):
        raise ValueError("multi_project_policy_required")
    workspace = original.get("workspace_gid")
    candidate = copy.deepcopy(original)
    projects = candidate["projects"]
    if (set(additions) & set(projects) or set(additions) & set(retired) or
            any(gid not in projects for gid in retired)):
        raise ValueError("scope_transition_conflict")
    for gid in retired:
        project = observed.get(gid)
        if (not isinstance(project, dict) or
                str((project.get("workspace") or {}).get("gid")) != workspace or
                project.get("archived") is not True):
            raise ValueError("retirement_not_verified_archived")
        del projects[gid]
    for gid, context in additions.items():
        project = observed.get(gid)
        if (not isinstance(project, dict) or str(project.get("gid")) != gid or
                str((project.get("workspace") or {}).get("gid")) != workspace or
                project.get("archived") is not False):
            raise ValueError("addition_not_verified_active")
        owner = str((project.get("owner") or {}).get("gid", ""))
        privacy = project.get("privacy_setting")
        if not owner.isdigit() or privacy not in {"private", "public_to_workspace"}:
            raise ValueError("addition_owner_or_privacy_unverified")
        projects[gid] = {
            "context": context, "owner_gid": owner,
            "project_owner_gid": owner, "privacy_setting": privacy,
            "audiences": ["workspace" if privacy == "public_to_workspace"
                          else "project_members"],
            "sources": ["current_request"], "eligible_task_names": [],
            "routing": {}, "specialties": [], "agent_gids": []}
    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    _, _, scopes, _ = load_dot_policy(encoded, profiles_raw, legacy_raw)
    if any(scopes[gid].get("action_blocked_reason") for gid in additions):
        raise ValueError("new_project_context_conflict")
    return encoded


async def propose(additions, retired):
    original_raw = os.environ["ASANA_METIS_DOT_POLICY_JSON"]
    profiles_raw = os.environ["ASANA_AGENT_PROFILES_JSON"]
    legacy_raw = os.environ.get("ASANA_AGENT_PROJECTS_JSON", "")
    original = json.loads(original_raw)
    load_dot_policy(original_raw, profiles_raw, legacy_raw)
    if len(additions) + len(retired) > 100:
        raise ValueError("too_many_project_checks")
    oauth = AsanaDispatchOAuth(load_config())
    try:
        token = await oauth.access_token()
    finally:
        oauth.db.close()
    observed = {}
    async with httpx.AsyncClient(timeout=20) as http:
        for gid in sorted(set(additions) | set(retired)):
            response = await http.get(
                f"https://app.asana.com/api/1.0/projects/{gid}",
                headers={"Authorization": "Bearer " + token},
                params={"opt_fields":
                        "gid,workspace.gid,owner.gid,privacy_setting,archived"})
            response.raise_for_status()
            observed[gid] = response.json().get("data")
    result = make_candidate(original, additions, retired, observed,
                            profiles_raw, legacy_raw)
    path = "/tmp/asana_policy_candidate.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as candidate_file:
        candidate_file.write(result + "\n")
    print("CANDIDATE_OK", "added", len(additions), "retired", len(retired),
          "projects", len(json.loads(result)["projects"]),
          "sha256", hashlib.sha256(result.encode()).hexdigest(), "path", path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--add", action="append", default=[], metavar="CONTEXT:GID[,GID]")
    parser.add_argument("--retire", nargs="*", default=[], metavar="ARCHIVED_GID")
    args = parser.parse_args()
    additions = parse_additions(args.add)
    if (len(args.retire) != len(set(args.retire)) or
            any(not re.fullmatch(r"[0-9]+", gid) for gid in args.retire)):
        parser.error("retired project GIDs must be distinct and numeric")
    asyncio.run(propose(additions, args.retire))
