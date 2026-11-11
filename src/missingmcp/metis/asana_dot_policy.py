"""Server-owned Metis Asana scopes. Discovery never grants write authority."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

from .queue import canonical_context


class DotPolicyError(ValueError):
    pass


def _gid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise DotPolicyError("invalid_policy_gid")
    return value


def load_dot_policy(raw: str, profiles_raw: str,
                    legacy_projects_raw: str = ""):
    """Return exact project scopes and profiles from trusted server variables.

    The old single-project shape remains supported. A workspace-wide discovery
    result is never promoted into this registry automatically.
    """
    try:
        config, profiles = json.loads(raw), json.loads(profiles_raw)
    except (TypeError, ValueError) as exc:
        raise DotPolicyError("invalid_dot_policy_json") from exc
    if not isinstance(config, Mapping) or not isinstance(profiles, Mapping):
        raise DotPolicyError("invalid_dot_policy_shape")
    try:
        legacy_projects = json.loads(legacy_projects_raw) if legacy_projects_raw else {}
    except (TypeError, ValueError) as exc:
        raise DotPolicyError("invalid_legacy_project_contexts") from exc
    if not isinstance(legacy_projects, Mapping):
        raise DotPolicyError("invalid_legacy_project_contexts")
    workspace_gid = _gid(config.get("workspace_gid"))
    creator_gid = _gid(config.get("creator_gid"))
    projects = config.get("projects")
    if projects is None:
        project_gid = _gid(config.get("project_gid"))
        projects = {project_gid: config}
        multi = False
    elif not isinstance(projects, Mapping) or not 1 <= len(projects) <= 100:
        raise DotPolicyError("invalid_project_registry")
    else:
        multi = True
    defaults = config.get("project_defaults", {})
    if (not isinstance(defaults, Mapping) or
            set(defaults) - {"owner_gid", "project_owner_gid", "privacy_setting",
                             "audiences", "sources", "eligible_task_names",
                             "routing", "specialties", "agent_gids"}):
        raise DotPolicyError("invalid_project_defaults")
    scopes = {}
    for project_gid, entry in projects.items():
        project_gid = _gid(project_gid)
        if not isinstance(entry, Mapping):
            raise DotPolicyError("invalid_project_scope")
        scope = dict(defaults) | dict(entry) | {"workspace_gid": workspace_gid,
                               "creator_gid": creator_gid,
                               "project_gid": project_gid}
        context = scope.get("context")
        try:
            if canonical_context(context) != context:
                raise DotPolicyError("noncanonical_project_context")
        except ValueError as exc:
            raise DotPolicyError("invalid_project_context") from exc
        _gid(scope.get("owner_gid"))
        if scope.get("project_owner_gid") is not None:
            _gid(scope["project_owner_gid"])
        privacy = scope.get("privacy_setting", "private")
        if privacy not in {"private", "public_to_workspace"}:
            raise DotPolicyError("invalid_project_privacy")
        for field in ("audiences", "sources", "eligible_task_names", "specialties"):
            values = scope.get(field)
            if (not isinstance(values, list) or
                    any(not isinstance(v, str) or not v for v in values) or
                    len(values) != len(set(values))):
                raise DotPolicyError("invalid_" + field)
        if not scope["audiences"] or not scope["sources"]:
            raise DotPolicyError("audience_or_sources_missing")
        if ((multi and privacy == "private" and scope["audiences"] != ["project_members"]) or
                (privacy == "public_to_workspace" and scope["audiences"] != ["workspace"])):
            raise DotPolicyError("privacy_audience_mismatch")
        routing = scope.get("routing")
        if (not isinstance(routing, Mapping) or
                any(not isinstance(name, str) or not name or
                    specialty not in scope["specialties"]
                    for name, specialty in routing.items())):
            raise DotPolicyError("invalid_agent_routing")
        agent_gids = scope.get("agent_gids", list(profiles) if not multi else [])
        if (not isinstance(agent_gids, list) or
                any(_gid(gid) not in profiles for gid in agent_gids)):
            raise DotPolicyError("invalid_agent_allowlist")
        scope["agent_gids"] = agent_gids
        if (project_gid in legacy_projects and
                legacy_projects[project_gid] != context):
            scope["action_blocked_reason"] = "project_context_conflict"
        scopes[project_gid] = scope
    return workspace_gid, creator_gid, scopes, dict(profiles)


def select_scope(scopes: Mapping, project_gid: str):
    scope = scopes.get(project_gid)
    if scope is None:
        raise DotPolicyError("project_not_authorized")
    if scope.get("action_blocked_reason"):
        raise DotPolicyError(scope["action_blocked_reason"])
    return scope
