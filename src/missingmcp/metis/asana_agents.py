"""Bounded Asana AI Teammate discovery and task coordination.

The early-access agent API only accepts agent GIDs as task assignees. This
client never treats an agent GID as a human user or a general ACL principal.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from collections.abc import Collection, Mapping, Sequence

import httpx
from .team_handoff import TeamHandoffs
from .queue import canonical_context

API = "https://app.asana.com/api/1.0"
GID = re.compile(r"^[0-9]+$")
IDEM = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class AsanaError(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


def _gid(value: str) -> str:
    if not isinstance(value, str) or not GID.fullmatch(value):
        raise ValueError("Asana GID must be numeric")
    return value


class AsanaAgents:
    def __init__(self, bearer: str, workspace_gid: str,
                 allowed_project_gids: Mapping[str, str], *,
                 allowed_agent_gids: Mapping[str, str | Sequence[str]] | None = None,
                 expected_project_owner_gids: Mapping[str, str] | None = None,
                 expected_project_privacies: Mapping[str, str] | None = None,
                 cross_context_agent_gids: Collection[str] = (),
                 transport: httpx.AsyncBaseTransport | None = None,
                 ledger_path: str | None = None,
                 handoff_ledger_path: str | None = None):
        if not bearer or not isinstance(bearer, str):
            raise ValueError("Asana bearer required")
        self.bearer = bearer
        self.workspace_gid = _gid(workspace_gid)
        self.allowed_projects = {_gid(gid): canonical_context(context)
                                 for gid, context in allowed_project_gids.items()}
        if not self.allowed_projects:
            raise ValueError("At least one project mapping is required")
        self.allowed_agents = {
            _gid(project): frozenset(_gid(agent) for agent in
                                     ([agents] if isinstance(agents, str) else agents))
            for project, agents in (allowed_agent_gids or {}).items()}
        if self.allowed_agents.keys() - self.allowed_projects.keys():
            raise ValueError("Agent mapping contains a forbidden project")
        if any(not agents for agents in self.allowed_agents.values()):
            raise ValueError("Every mapped project needs at least one agent")
        cross_context_agents = frozenset(_gid(agent) for agent in cross_context_agent_gids)
        agent_contexts = {}
        for project, agents in self.allowed_agents.items():
            context = self.allowed_projects[project]
            for agent in agents:
                if (agent in agent_contexts and agent_contexts[agent] != context and
                        agent not in cross_context_agents):
                    raise ValueError("One agent cannot dispatch across contexts")
                agent_contexts[agent] = context
        if cross_context_agents - set(agent_contexts):
            raise ValueError("Cross-context exception contains an unmapped agent")
        self.expected_project_owners = {
            _gid(project): _gid(owner)
            for project, owner in (expected_project_owner_gids or {}).items()}
        if self.expected_project_owners.keys() - self.allowed_projects.keys():
            raise ValueError("Owner mapping contains a forbidden project")
        self.expected_project_privacies = dict(expected_project_privacies or {})
        if (self.expected_project_privacies.keys() - self.allowed_projects.keys() or
                any(value not in {"private", "public_to_workspace"}
                    for value in self.expected_project_privacies.values())):
            raise ValueError("Invalid project privacy mapping")
        self.transport = transport
        self.handoffs = TeamHandoffs(handoff_ledger_path) if handoff_ledger_path else None
        self.ledger = None
        if ledger_path:
            self.ledger = sqlite3.connect(ledger_path, isolation_level=None)
            self.ledger.row_factory = sqlite3.Row
            self.ledger.execute("PRAGMA journal_mode=WAL")
            self.ledger.execute("""CREATE TABLE IF NOT EXISTS asana_agent_dispatches (
                idem TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                task_gid TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))""")

    async def _request(self, method: str, path: str, *, params=None, data=None):
        headers = {"Authorization": f"Bearer {self.bearer}",
                   "Asana-Enable": "ai_teammate_actors"}
        async with httpx.AsyncClient(base_url=API, headers=headers,
                                     transport=self.transport, timeout=20) as client:
            for attempt in range(3 if method == "GET" else 1):
                try:
                    response = await client.request(method, path, params=params,
                                                    json={"data": data} if data is not None else None)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if method == "GET" and attempt < 2:
                        await asyncio.sleep(0.2 * (2 ** attempt))
                        continue
                    raise AsanaError("transport_uncertain" if method != "GET" else "transport_failed") from exc
                if method == "GET" and response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(min(float(response.headers.get("Retry-After", "0.2")), 2))
                    continue
                if response.status_code >= 400:
                    raise AsanaError("asana_http_error", response.status_code)
                try:
                    return response.json()
                except ValueError as exc:
                    raise AsanaError("invalid_asana_response", response.status_code) from exc
        raise AsanaError("retry_exhausted")

    async def _pages(self, path: str, *, params=None):
        offset = None
        seen = set()
        while True:
            query = dict(params or {})
            query["limit"] = 100
            if offset:
                query["offset"] = offset
            body = await self._request("GET", path, params=query)
            data = body.get("data")
            if not isinstance(data, list):
                raise AsanaError("invalid_asana_response")
            for item in data:
                yield item
            offset = (body.get("next_page") or {}).get("offset")
            if not offset:
                break
            if offset in seen:
                raise AsanaError("pagination_loop")
            seen.add(offset)

    async def discover(self):
        return [agent async for agent in self._pages(
            f"/workspaces/{self.workspace_gid}/agents")]

    async def agent(self, agent_gid: str):
        body = await self._request("GET", f"/agents/{_gid(agent_gid)}",
                                   params={"opt_fields": "gid,name,workspace.gid,resource_subtype"})
        agent = body.get("data")
        if (not isinstance(agent, dict) or str(agent.get("gid")) != agent_gid or
                str(agent.get("workspace", {}).get("gid")) != self.workspace_gid or
                agent.get("resource_subtype") != "asana_teammate"):
            raise AsanaError("agent_workspace_mismatch")
        return agent

    async def _verify_dispatch_target(self, project_gid: str, agent_gid: str):
        if agent_gid not in self.allowed_agents.get(project_gid, ()):
            raise AsanaError("agent_not_allowlisted")
        if project_gid not in self.expected_project_owners:
            raise AsanaError("dispatch_owner_not_configured")
        body = await self._request("GET", f"/projects/{project_gid}",
                                   params={"opt_fields": "gid,workspace.gid,owner.gid,privacy_setting"})
        project = body.get("data")
        if (not isinstance(project, dict) or str(project.get("gid")) != project_gid or
                str((project.get("workspace") or {}).get("gid")) != self.workspace_gid or
                project.get("privacy_setting") !=
                self.expected_project_privacies.get(project_gid, "private") or
                str((project.get("owner") or {}).get("gid")) !=
                self.expected_project_owners[project_gid]):
            raise AsanaError("dispatch_project_not_private_or_owned")
        discovered = await self.discover()
        if not any(str(a.get("gid")) == agent_gid for a in discovered):
            raise AsanaError("agent_not_discovered")
        await self.agent(agent_gid)

    async def task(self, task_gid: str):
        body = await self._request("GET", f"/tasks/{_gid(task_gid)}",
                                   params={"opt_fields": "gid,name,assignee.gid,created_by.gid,projects.gid,workspace.gid,completed,modified_at"})
        task = body.get("data")
        if not isinstance(task, dict) or str(task.get("workspace", {}).get("gid")) != self.workspace_gid:
            raise AsanaError("task_workspace_mismatch")
        if not ({str(p.get("gid")) for p in task.get("projects", [])}
                & self.allowed_projects.keys()):
            raise AsanaError("task_project_forbidden")
        return task

    async def assign_dedicated_task(self, task_gid: str, agent_gid: str,
                                    project_gid: str):
        """Assign a dedicated, unassigned task without overwriting a human owner.

        An uncertain PUT is reconciled by a fresh GET; it is never blindly
        repeated. Caller must maintain the original human-owned task separately.
        """
        project_gid = _gid(project_gid)
        if project_gid not in self.allowed_projects:
            raise AsanaError("project_forbidden")
        agent_gid = _gid(agent_gid)
        await self._verify_dispatch_target(project_gid, agent_gid)
        task = await self.task(task_gid)
        if {str(p.get("gid")) for p in task.get("projects", [])} != {project_gid}:
            raise AsanaError("task_project_mismatch")
        assignee = task.get("assignee")
        if assignee:
            if str(assignee.get("gid")) == agent_gid:
                return task
            raise AsanaError("task_already_assigned")
        try:
            await self._request("PUT", f"/tasks/{_gid(task_gid)}", data={"assignee": agent_gid})
        except AsanaError as exc:
            if exc.code != "transport_uncertain":
                raise
            observed = await self.task(task_gid)
            if str((observed.get("assignee") or {}).get("gid")) != agent_gid:
                raise AsanaError("assignment_uncertain") from exc
            return observed
        observed = await self.task(task_gid)
        if str((observed.get("assignee") or {}).get("gid")) != agent_gid:
            raise AsanaError("assignment_unverified")
        return observed

    async def create_dedicated_task(self, *, project_gid: str,
                                    agent_gid: str, title: str, request: str,
                                    idempotency_key: str,
                                    source_task_gid: str | None = None):
        """Create one separate agent task, reserving a durable key before POST.

        If the API or process fails after dispatch, the key stays uncertain.
        Manual reconciliation must locate the created task before any new key.
        """
        if self.ledger is None:
            raise AsanaError("durable_ledger_required")
        project_gid, agent_gid = map(_gid, (project_gid, agent_gid))
        if project_gid not in self.allowed_projects:
            raise AsanaError("project_forbidden")
        if not isinstance(idempotency_key, str) or not IDEM.fullmatch(idempotency_key):
            raise ValueError("Invalid idempotency key")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Title must contain 1-200 characters")
        if not isinstance(request, str) or not request.strip() or len(request.encode()) > 10000:
            raise ValueError("Request must contain 1-10000 UTF-8 bytes")
        await self._verify_dispatch_target(project_gid, agent_gid)
        if source_task_gid is not None:
            source_task_gid = _gid(source_task_gid)
            source = await self.task(source_task_gid)
            if {str(p.get("gid")) for p in source.get("projects", [])} != {project_gid}:
                raise AsanaError("source_project_mismatch")
        payload = {"name": title.strip(), "projects": [project_gid],
                   "assignee": agent_gid,
                   "notes": (f"Encargo Metis. Referencia: {idempotency_key}\nContexto: {self.allowed_projects[project_gid]}\n"
                             + (f"Tarea original: https://app.asana.com/0/0/{source_task_gid}\n"
                                if source_task_gid else "") + f"\n{request}")}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        db = self.ledger
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT * FROM asana_agent_dispatches WHERE idem=?",
                             (idempotency_key,)).fetchone()
            if row is None:
                db.execute("INSERT INTO asana_agent_dispatches(idem,fingerprint,state) "
                           "VALUES (?,?,'DISPATCHED')", (idempotency_key, fingerprint))
            elif row["fingerprint"] != fingerprint:
                raise AsanaError("idempotency_conflict")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        if row is not None:
            if row["state"] == "CREATED":
                return await self._verified_agent_task(row["task_gid"],
                                                       project_gid, agent_gid)
            raise AsanaError("dispatch_uncertain")
        try:
            body = await self._request("POST", "/tasks", data=payload)
        except AsanaError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                db.execute("UPDATE asana_agent_dispatches SET state='REJECTED' WHERE idem=?",
                           (idempotency_key,))
            raise
        gid = str((body.get("data") or {}).get("gid", ""))
        if not GID.fullmatch(gid):
            raise AsanaError("creation_uncertain")
        db.execute("UPDATE asana_agent_dispatches SET state='CREATED', task_gid=? WHERE idem=?",
                   (gid, idempotency_key))
        return await self._verified_agent_task(gid, project_gid, agent_gid)

    async def create_handoff_task(self, *, source_task_gid: str,
                                  project_gid: str, agent_gid: str,
                                  title: str, request: str,
                                  idempotency_key: str):
        """Correlate two tasks in one context without leaking source details.

        The caller supplies an audience-safe, self-contained request. The
        source GID is kept in the private ledger and linked from the new task;
        source task contents are not copied into the agent brief.
        """
        if self.handoffs is None or self.ledger is None:
            raise AsanaError("handoff_ledger_required")
        if not isinstance(idempotency_key, str) or not IDEM.fullmatch(idempotency_key):
            raise ValueError("Invalid idempotency key")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Title must contain 1-200 characters")
        if not isinstance(request, str) or not request.strip() or len(request.encode()) > 10000:
            raise ValueError("Request must contain 1-10000 UTF-8 bytes")
        project_gid, source_task_gid, agent_gid = map(
            _gid, (project_gid, source_task_gid, agent_gid))
        context = self.allowed_projects.get(project_gid)
        if context is None:
            raise AsanaError("project_forbidden")
        await self._verify_dispatch_target(project_gid, agent_gid)
        source = await self.task(source_task_gid)
        source_projects = {str(p.get("gid")) for p in source.get("projects", [])}
        if (not source_projects or any(
                self.allowed_projects.get(gid) != context for gid in source_projects)):
            raise AsanaError("handoff_context_mismatch")
        payload = {"source": source_task_gid, "project": project_gid,
                   "agent": agent_gid, "title": title, "request": request}
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.handoffs.reserve(
            key=idempotency_key, context=context, source_task_gid=source_task_gid,
            target_project_gid=project_gid, target_agent_gid=agent_gid,
            fingerprint=fingerprint)
        task = await self.create_dedicated_task(
            project_gid=project_gid, agent_gid=agent_gid, title=title,
            request=request, idempotency_key=idempotency_key)
        self.handoffs.record_target(idempotency_key, str(task["gid"]))
        return task

    async def _verified_agent_task(self, task_gid: str, project_gid: str,
                                   agent_gid: str):
        task = await self.task(task_gid)
        if ({str(p.get("gid")) for p in task.get("projects", [])} != {project_gid} or
                str((task.get("assignee") or {}).get("gid")) != agent_gid):
            raise AsanaError("created_task_unverified")
        return task

    async def stories(self, task_gid: str):
        # Story GIDs, not author display names, are stable de-duplication keys.
        await self.task(task_gid)
        return [story async for story in self._pages(
            f"/tasks/{_gid(task_gid)}/stories",
            params={"opt_fields": "gid,created_at,resource_subtype,text,created_by.gid,is_edited"})]

    async def subtasks(self, task_gid: str):
        """Read direct child identifiers; do not infer their creator from UI text."""
        await self.task(task_gid)
        return [child async for child in self._pages(
            f"/tasks/{_gid(task_gid)}/subtasks",
            params={"opt_fields": "gid,parent.gid"})]

    async def subtask_detail(self, task_gid: str):
        body = await self._request("GET", f"/tasks/{_gid(task_gid)}", params={
            "opt_fields": "gid,name,notes,workspace.gid,parent.gid,assignee.gid,"
                          "created_by.gid,created_at,modified_at,completed"})
        return body.get("data")

    async def reconcile_handoff(self, key: str, project_gid: str, agent_gid: str):
        """Check Asana after an uncertain POST; never issue another POST."""
        if self.ledger is None or self.handoffs is None or not IDEM.fullmatch(key):
            raise AsanaError("reconciliation_unavailable")
        row = self.ledger.execute("SELECT * FROM asana_agent_dispatches WHERE idem=?",
                                  (key,)).fetchone()
        if row is None:
            raise AsanaError("dispatch_not_found")
        if row["task_gid"]:
            return await self._verified_agent_task(row["task_gid"], project_gid, agent_gid)
        matches = [task async for task in self._pages(
            f"/projects/{_gid(project_gid)}/tasks",
            params={"opt_fields": "gid,notes,assignee.gid,projects.gid"})
            if f"Referencia: {key}\n" in (task.get("notes") or "")
            and str((task.get("assignee") or {}).get("gid")) == agent_gid]
        if len(matches) != 1:
            raise AsanaError("dispatch_uncertain")
        gid = _gid(str(matches[0]["gid"]))
        task = await self._verified_agent_task(gid, project_gid, agent_gid)
        self.ledger.execute("UPDATE asana_agent_dispatches SET state='CREATED',task_gid=? "
                            "WHERE idem=? AND task_gid IS NULL", (gid, key))
        self.handoffs.record_target(key, gid)
        return task
