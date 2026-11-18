"""Read-only check of configured Asana agent routes; no OAuth or task access.

Run inside the Gateway service: python scripts/asana_route_scope_audit.py
The result checks local policy and profiles, not native Asana agent permissions.
"""
from __future__ import annotations

import os

from missingmcp.metis.asana_dot_policy import load_dot_policy
from missingmcp.metis.asana_evaluation import evaluate


def audit_scope(scope, profiles):
    """Return route status using the same decision gate as a real handoff."""
    if scope.get("action_blocked_reason"):
        return "BLOCKED", 0, 0
    names = scope["eligible_task_names"]
    if not names and not scope["agent_gids"]:
        return "HUMAN_ONLY", 0, 0
    available = 0
    for name in names:
        specialty = scope["routing"].get(name)
        if specialty is None:
            continue
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
        available += decision.action == "handoff"
    if available == len(names) and available:
        return "COMPLETE", available, len(names)
    if available:
        return "PARTIAL", available, len(names)
    return "UNROUTABLE", 0, len(names)


def main():
    _, _, scopes, profiles = load_dot_policy(
        os.environ["ASANA_METIS_DOT_POLICY_JSON"],
        os.environ["ASANA_AGENT_PROFILES_JSON"],
        os.environ.get("ASANA_AGENT_PROJECTS_JSON", ""))
    totals = {name: 0 for name in
              ("COMPLETE", "PARTIAL", "HUMAN_ONLY", "UNROUTABLE", "BLOCKED")}
    details = []
    for gid, scope in sorted(scopes.items()):
        status, available, declared = audit_scope(scope, profiles)
        totals[status] += 1
        details.append((gid, scope["context"], status, available, declared))
    print("ROUTE_SCOPE_AUDIT", "projects", len(scopes),
          *(f"{status.lower()}={count}" for status, count in totals.items()))
    for gid, context, status, available, declared in details:
        print("ROUTE_SCOPE", gid, context, status, f"{available}/{declared}")


if __name__ == "__main__":
    main()
