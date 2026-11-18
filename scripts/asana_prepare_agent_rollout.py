"""Prepare, but never apply, an all-project Asana agent route proposal.

Run in the Gateway after creating and reviewing five context-specific agents:
python scripts/asana_prepare_agent_rollout.py --agent ANDREA:GID --agent METIS:GID \
  --agent ARQUITECTO_INTERIOR:GID --agent METIS/VAL:GID \
  --agent FINAL_FINAL_PUBLIC:GID --exclude-project CLIENT_PORTAL_GID \
  --exclude-project CLIENT_PORTAL_GID --exclude-project CLIENT_PORTAL_GID

The proposal uses only currently registered projects. It does not read tasks,
assign agents, change Asana permissions, or update Railway variables.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re

from missingmcp.config import load_config
from missingmcp.metis.asana_agents import AsanaAgents
from missingmcp.metis.asana_dispatch_oauth import AsanaDispatchOAuth
from missingmcp.metis.asana_dot_policy import load_dot_policy
from missingmcp.metis.asana_evaluation import evaluate


NEW_CONTEXTS = ("ANDREA", "METIS", "ARQUITECTO_INTERIOR", "METIS/VAL",
                "FINAL_FINAL_PUBLIC")
PRIVATE_TITLE = "Preparar borrador para revisión con agente"
PUBLIC_TITLE = "Preparar borrador público para revisión"
SPECIALTY = "scoped_coordination"
DEFAULT_SPECIALTIES = {
    "SHOKKO": "growth_analysis",
    "FINAL_FINAL": "creative_delivery",
    "VENTURE_QUEST": "economics_governance",
}


def parse_agents(values):
    agents = {}
    for value in values:
        context, separator, gid = value.partition(":")
        if (not separator or context not in NEW_CONTEXTS or context in agents or
                not re.fullmatch(r"[0-9]+", gid)):
            raise ValueError("invalid_or_duplicate_agent_mapping")
        agents[context] = gid
    if set(agents) != set(NEW_CONTEXTS) or len(set(agents.values())) != len(agents):
        raise ValueError("five_distinct_context_agents_required")
    return agents


def parse_specialties(values):
    overrides = {}
    for value in values:
        gid, separator, specialty = value.partition(":")
        if (not separator or not re.fullmatch(r"[0-9]+", gid) or
                not specialty or gid in overrides):
            raise ValueError("invalid_project_specialty_override")
        overrides[gid] = specialty
    return overrides


def parse_excluded(values):
    if (len(values) != 3 or len(set(values)) != 3 or
            any(not re.fullmatch(r"[0-9]+", gid) for gid in values)):
        raise ValueError("three_distinct_excluded_project_gids_required")
    return frozenset(values)


def route_agent(scope, profiles, name):
    specialty = scope["routing"].get(name)
    if specialty is None:
        return None
    decision = evaluate({
        "context": scope["context"], "project_gid": scope["project_gid"],
        "source_task_gid": "1", "agent_value": True,
        "human_owner_retained": True, "dot_authorized": True,
        "audience": scope["audiences"][0], "deliverable": "audit",
        "request": "audit", "quality_review_by": scope["owner_gid"],
        "authorized_sources": scope["sources"], "specialty": specialty,
    }, context=scope["context"], project_gid=scope["project_gid"],
        source_task_gid="1", agents=profiles,
        allowed_agent_gids=frozenset(scope["agent_gids"]))
    return decision.agent_gid if decision.action == "handoff" else None


def make_candidate(original, profiles, legacy_raw, agents, overrides=None,
                   excluded=()):
    raw = json.dumps(original, ensure_ascii=False, separators=(",", ":"))
    profile_raw = json.dumps(profiles, ensure_ascii=False, separators=(",", ":"))
    _, _, scopes, _ = load_dot_policy(raw, profile_raw, legacy_raw)
    if len(scopes) != 29 or any(gid in profiles for gid in agents.values()):
        raise ValueError("unexpected_project_count_or_existing_agent")
    if any(scope.get("action_blocked_reason") for scope in scopes.values()):
        raise ValueError("project_context_conflict")
    overrides = dict(overrides or {})
    excluded = set(excluded)
    if (len(excluded) != 3 or not excluded <= set(scopes) or
            any(scopes[gid]["agent_gids"] or
                scopes[gid]["eligible_task_names"] or
                scopes[gid]["routing"] for gid in excluded)):
        raise ValueError("excluded_project_must_be_human_only")
    if set(overrides) - (set(scopes) - excluded):
        raise ValueError("override_project_not_registered")
    candidate, new_profiles = copy.deepcopy(original), copy.deepcopy(profiles)
    for gid in excluded:
        del candidate["projects"][gid]
    for context, gid in agents.items():
        new_profiles[gid] = {
            "specialties": [SPECIALTY],
            "contexts": ["FINAL_FINAL" if context == "FINAL_FINAL_PUBLIC" else context],
            "audiences": ["workspace" if context == "FINAL_FINAL_PUBLIC"
                          else "project_members"],
            "dot_authorized": True,
        }

    templates = {}
    for scope in scopes.values():
        if (scope["context"] in {"SHOKKO", "FINAL_FINAL", "VENTURE_QUEST"}
                and scope.get("privacy_setting", "private") == "private"):
            valid = {name: (scope["routing"][name], agent)
                     for name in scope["eligible_task_names"]
                     if (agent := route_agent(scope, profiles, name))}
            if valid:
                by_specialty = templates.setdefault(scope["context"], {})
                for name, (specialty, agent) in valid.items():
                    prior = by_specialty.setdefault(specialty, (name, agent))
                    if prior != (name, agent):
                        raise ValueError("ambiguous_existing_context_routes")
    if set(templates) != {"SHOKKO", "FINAL_FINAL", "VENTURE_QUEST"}:
        raise ValueError("missing_existing_context_routes")

    for gid, scope in scopes.items():
        if gid in excluded:
            continue
        context = scope["context"]
        privacy = scope.get("privacy_setting", "private")
        if context == "FINAL_FINAL" and privacy == "public_to_workspace":
            selected = {PUBLIC_TITLE: (SPECIALTY, agents["FINAL_FINAL_PUBLIC"])}
        elif context in templates:
            specialty = overrides.pop(gid, DEFAULT_SPECIALTIES[context])
            route = templates[context].get(specialty)
            if route is None:
                raise ValueError("specialty_not_available_in_context")
            selected = {route[0]: (specialty, route[1])}
        elif context in agents:
            if gid in overrides:
                raise ValueError("override_requires_existing_context_agent")
            selected = {PRIVATE_TITLE: (SPECIALTY, agents[context])}
        else:
            raise ValueError("unmapped_project_context")
        entry = candidate["projects"][gid]
        entry.update({
            "eligible_task_names": list(selected),
            "routing": {name: specialty for name, (specialty, _) in selected.items()},
            "specialties": list(dict.fromkeys(specialty for specialty, _ in selected.values())),
            "agent_gids": list(dict.fromkeys(agent for _, agent in selected.values())),
        })
    if overrides:
        raise ValueError("override_requires_existing_context_agent")
    candidate_raw = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    profiles_raw = json.dumps(new_profiles, ensure_ascii=False, separators=(",", ":"))
    _, _, proposed_scopes, proposed_profiles = load_dot_policy(
        candidate_raw, profiles_raw, legacy_raw)
    if (set(proposed_scopes) != set(scopes) - excluded or
            any(scope.get("action_blocked_reason") or
                any(route_agent(scope, proposed_profiles, name) is None
                    for name in scope["eligible_task_names"]) or
                not scope["eligible_task_names"]
                for scope in proposed_scopes.values())):
        raise ValueError("candidate_not_fully_routable")
    return candidate_raw, profiles_raw


def save_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(content + "\n")


async def main(agents, overrides, excluded):
    original = json.loads(os.environ["ASANA_METIS_DOT_POLICY_JSON"])
    profiles = json.loads(os.environ["ASANA_AGENT_PROFILES_JSON"])
    legacy_raw = os.environ.get("ASANA_AGENT_PROJECTS_JSON", "")
    candidate_raw, profiles_raw = make_candidate(
        original, profiles, legacy_raw, agents, overrides, excluded)
    workspace = original["workspace_gid"]
    oauth = AsanaDispatchOAuth(load_config())
    try:
        token = await oauth.access_token()
    finally:
        oauth.db.close()
    client = AsanaAgents(token, workspace,
                         {gid: entry["context"] for gid, entry in original["projects"].items()})
    discovered = {str(item.get("gid")) for item in await client.discover()}
    if not set(agents.values()) <= discovered:
        raise ValueError("new_agent_not_discovered")
    for gid in agents.values():
        await client.agent(gid)
    policy_path = "/tmp/asana_agent_policy_candidate.json"
    profiles_path = "/tmp/asana_agent_profiles_candidate.json"
    save_private(policy_path, candidate_raw)
    save_private(profiles_path, profiles_raw)
    print("AGENT_CANDIDATE_OK", "projects", len(json.loads(candidate_raw)["projects"]),
          "excluded", len(excluded), "new_agents", len(agents),
          "policy_sha256", hashlib.sha256(candidate_raw.encode()).hexdigest(),
          "profiles_sha256", hashlib.sha256(profiles_raw.encode()).hexdigest(),
          "policy_path", policy_path, "profiles_path", profiles_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="append", required=True,
                        metavar="CONTEXT:GID")
    parser.add_argument("--specialty", action="append", default=[],
                        metavar="PROJECT_GID:SPECIALTY")
    parser.add_argument("--exclude-project", action="append", required=True,
                        metavar="PROJECT_GID")
    args = parser.parse_args()
    asyncio.run(main(parse_agents(args.agent), parse_specialties(args.specialty),
                     parse_excluded(args.exclude_project)))
