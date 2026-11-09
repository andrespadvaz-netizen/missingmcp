"""Conservative, explicit evaluation before an Asana AI handoff.

The brief is supplied by the authorized Dot. No private memory or task body is
inferred from Asana, and a decision alone never writes to Asana.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from .asana_agents import AsanaAgents, AsanaError, _gid


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    agent_gid: str | None = None
    specialty: str | None = None
    idempotency_key: str | None = None


def evaluate(brief: Mapping, *, context: str, project_gid: str,
             source_task_gid: str, agents: Mapping[str, Mapping],
             allowed_agent_gids: frozenset[str]) -> Decision:
    """Select exactly one authorized specialty, or abstain closed.

    A Dot must provide a scoped brief and its own authorization. Empty or
    ambiguous evidence is an abstention, never permission to guess.
    """
    if not isinstance(brief, Mapping):
        return Decision("skip", "invalid_brief")
    if brief.get("context") != context or brief.get("project_gid") != project_gid:
        return Decision("skip", "context_mismatch")
    if brief.get("source_task_gid") != source_task_gid:
        return Decision("skip", "source_mismatch")
    if brief.get("agent_value") is not True or brief.get("human_owner_retained") is not True:
        return Decision("skip", "agent_value_or_human_owner_unconfirmed")
    if brief.get("dot_authorized") is not True:
        return Decision("skip", "dot_authorization_missing")
    for field in ("audience", "deliverable", "request", "quality_review_by"):
        if not isinstance(brief.get(field), str) or not brief[field].strip():
            return Decision("skip", f"missing_{field}")
    sources = brief.get("authorized_sources")
    if (not isinstance(sources, list) or not sources or
            any(not isinstance(s, str) or not s.strip() for s in sources)):
        return Decision("skip", "authorized_sources_missing")
    if brief.get("requires_private_memory") is True:
        return Decision("skip", "private_memory_unavailable")
    specialty = brief.get("specialty")
    if not isinstance(specialty, str) or not specialty.strip():
        return Decision("skip", "specialty_missing")
    matches = [gid for gid, profile in agents.items()
               if gid in allowed_agent_gids and isinstance(profile, Mapping)
               and isinstance(profile.get("specialties"), list)
               and isinstance(profile.get("contexts"), list)
               and isinstance(profile.get("audiences"), list)
               and specialty in profile.get("specialties", ())
               and context in profile.get("contexts", ())
               and brief["audience"] in profile.get("audiences", ())
               and profile.get("dot_authorized") is True]
    if len(matches) != 1:
        return Decision("skip", "specialty_unavailable_or_ambiguous")
    agent_gid = _gid(matches[0])
    key = hashlib.sha256(f"{context}:{project_gid}:{source_task_gid}".encode()).hexdigest()[:40]
    return Decision("handoff", "eligible_for_human_review", agent_gid,
                    specialty, f"dot-handoff-{key}")


async def evaluate_and_dispatch(client: AsanaAgents, *, source_task_gid: str,
                                project_gid: str, brief: Mapping,
                                agents: Mapping[str, Mapping],
                                dispatch: bool = False) -> tuple[Decision, dict | None]:
    """Read source task, decide, and optionally create a separate agent task."""
    project_gid, source_task_gid = _gid(project_gid), _gid(source_task_gid)
    context = client.allowed_projects.get(project_gid)
    if context is None:
        raise AsanaError("project_forbidden")
    source = await client.task(source_task_gid)
    if source.get("completed"):
        return Decision("skip", "source_completed"), None
    assignee_gid = str((source.get("assignee") or {}).get("gid", ""))
    if not assignee_gid or any(assignee_gid in gids for gids in client.allowed_agents.values()):
        return Decision("skip", "human_owner_missing"), None
    source_projects = {str(p.get("gid")) for p in source.get("projects", [])}
    if not source_projects or any(client.allowed_projects.get(gid) != context
                                  for gid in source_projects):
        return Decision("skip", "source_context_mismatch"), None
    decision = evaluate(brief, context=context, project_gid=project_gid,
                        source_task_gid=source_task_gid, agents=agents,
                        allowed_agent_gids=client.allowed_agents.get(project_gid, frozenset()))
    if decision.action != "handoff" or not dispatch:
        return decision, None
    # Only the supplied, audience-safe brief is sent. Source task text and GID
    # remain in the private ledger; the human assignee is never changed.
    request = (f"Audiencia: {brief['audience']}\n"
               f"Entregable: {brief['deliverable']}\n"
               f"Fuentes autorizadas: {', '.join(brief['authorized_sources'])}\n"
               "Gasto sujeto a la cuota global compartida de Venture Quest; "
               "este encargo no autoriza solicitudes adicionales.\n"
               f"Revisión de calidad por: {brief['quality_review_by']}\n"
               "El resultado es un borrador hasta que la revisión humana lo apruebe.\n\n"
               f"{brief['request']}")
    task = await client.create_handoff_task(
        source_task_gid=source_task_gid, project_gid=project_gid,
        agent_gid=decision.agent_gid, title=f"Encargo: {brief['deliverable'][:170]}",
        request=request, idempotency_key=decision.idempotency_key)
    return decision, task
