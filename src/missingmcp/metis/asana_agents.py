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
from collections.abc import Mapping

import httpx
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
                 transport: httpx.AsyncBaseTransport | None = None,
                 ledger_path: str | None = None):
        if not bearer or not isinstance(bearer, str):
            raise ValueError("Asana bearer required")
        self.bearer = bearer
        self.workspace_gid = _gid(workspace_gid)
        self.allowed_projects = {_gid(gid): canonical_context(context)
                                 for gid, context in allowed_project_gids.items()}
        if not self.allowed_projects:
            raise ValueError("At least one project mapping is required")
        self.transport = transport
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
                                   params={"opt_fields": "gid,name,workspace.gid,description,behavior_guidance"})
        agent = body.get("data")
        if not isinstance(agent, dict) or str(agent.get("workspace", {}).get("gid")) != self.workspace_gid:
            raise AsanaError("agent_workspace_mismatch")
        return agent

    async def task(self, task_gid: str):
        body = await self._request("GET", f"/tasks/{_gid(task_gid)}",
                                   params={"opt_fields": "gid,name,assignee.gid,projects.gid,workspace.gid,completed,modified_at"})
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
        discovered = await self.discover()
        if not any(str(a.get("gid")) == _gid(agent_gid) for a in discovered):
            raise AsanaError("agent_not_discovered")
        task = await self.task(task_gid)
        if project_gid not in {str(p.get("gid")) for p in task.get("projects", [])}:
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

    async def create_dedicated_task(self, *, source_task_gid: str, project_gid: str,
                                    agent_gid: str, title: str, request: str,
                                    idempotency_key: str):
        """Create one separate agent task, reserving a durable key before POST.

        If the API or process fails after dispatch, the key stays uncertain.
        Manual reconciliation must locate the created task before any new key.
        """
        if self.ledger is None:
            raise AsanaError("durable_ledger_required")
        project_gid, source_task_gid, agent_gid = map(_gid,
                                                       (project_gid, source_task_gid, agent_gid))
        if project_gid not in self.allowed_projects:
            raise AsanaError("project_forbidden")
        if not isinstance(idempotency_key, str) or not IDEM.fullmatch(idempotency_key):
            raise ValueError("Invalid idempotency key")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Title must contain 1-200 characters")
        if not isinstance(request, str) or not request.strip() or len(request.encode()) > 10000:
            raise ValueError("Request must contain 1-10000 UTF-8 bytes")
        source = await self.task(source_task_gid)
        if project_gid not in {str(p.get("gid")) for p in source.get("projects", [])}:
            raise AsanaError("source_project_mismatch")
        agents = await self.discover()
        if not any(str(a.get("gid")) == agent_gid for a in agents):
            raise AsanaError("agent_not_discovered")
        payload = {"name": title.strip(), "projects": [project_gid],
                   "assignee": agent_gid,
                   "notes": (f"Encargo Metis. Contexto: {self.allowed_projects[project_gid]}\n"
                             f"Tarea original: https://app.asana.com/0/0/{source_task_gid}\n\n{request}")}
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
                return await self.task(row["task_gid"])
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
        return await self.task(gid)

    async def stories(self, task_gid: str):
        # Story GIDs, not author display names, are stable de-duplication keys.
        await self.task(task_gid)
        return [story async for story in self._pages(
            f"/tasks/{_gid(task_gid)}/stories",
            params={"opt_fields": "gid,created_at,resource_subtype,text,created_by.gid"})]
