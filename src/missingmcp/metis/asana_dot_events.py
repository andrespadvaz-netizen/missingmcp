"""Authenticated Metis Dot event processing; no other Dot is enabled here."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import re
from datetime import datetime
from collections.abc import Mapping

from .asana_evaluation import evaluate_and_dispatch


class DotEventError(RuntimeError):
    pass


class DotEvents:
    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path, isolation_level=None, timeout=20,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=20000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS asana_dot_decisions (
            source_gid TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            action TEXT NOT NULL, reason TEXT NOT NULL, target_gid TEXT,
            agent_gid TEXT, proposal_gid TEXT,
            review_state TEXT NOT NULL DEFAULT 'NONE', evidence_gid TEXT,
            reviewer_gid TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        for column in ("agent_gid", "proposal_gid"):
            if column not in {r[1] for r in self.db.execute("PRAGMA table_info(asana_dot_decisions)")}:
                self.db.execute(f"ALTER TABLE asana_dot_decisions ADD COLUMN {column} TEXT")
        self.db.execute("""CREATE TABLE IF NOT EXISTS asana_dot_sources (
            idem TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            brief_fingerprint TEXT, source_name TEXT,
            state TEXT NOT NULL, task_gid TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        for column in ("brief_fingerprint", "source_name"):
            if column not in {r[1] for r in self.db.execute("PRAGMA table_info(asana_dot_sources)")}:
                self.db.execute(f"ALTER TABLE asana_dot_sources ADD COLUMN {column} TEXT")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS asana_dot_source_task "
                        "ON asana_dot_sources(task_gid) WHERE task_gid IS NOT NULL")

    def source_registered(self, task_gid: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM asana_dot_sources WHERE task_gid=? AND state='CREATED'",
            (task_gid,)).fetchone() is not None

    async def create_source(self, client, event: Mapping, policy: Mapping):
        """Reserve before Asana POST; duplicate or uncertain calls never repost."""
        if not isinstance(event, Mapping) or event.get("type") != "task.create":
            raise DotEventError("invalid_create_event")
        key = event.get("idempotency_key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            raise DotEventError("invalid_create_key")
        name = event.get("name")
        project = policy.get("project_gid")
        owner = policy.get("owner_gid")
        if (not isinstance(name, str) or name not in policy.get("eligible_task_names", ()) or
                not isinstance(project, str) or not project.isdigit() or
                not isinstance(owner, str) or not owner.isdigit()):
            raise DotEventError("source_scope_forbidden")
        brief = event.get("brief")
        if (not isinstance(brief, Mapping) or brief.get("context") != policy.get("context") or
                brief.get("project_gid") != project or
                brief.get("audience") not in policy.get("audiences", ()) or
                not isinstance(brief.get("authorized_sources"), list) or
                not brief["authorized_sources"] or
                any(not isinstance(source, str) or
                    source not in policy.get("sources", ())
                    for source in brief["authorized_sources"])):
            raise DotEventError("source_brief_scope_forbidden")
        await self._verify_project(client, project)
        payload = {"name": name, "projects": [project], "assignee": owner,
                   "notes": f"Metis Dot source reference: {key}"}
        fingerprint = hashlib.sha256(json.dumps(
            {"payload": payload, "brief": event.get("brief")}, sort_keys=True).encode()).hexdigest()
        brief_fingerprint = hashlib.sha256(json.dumps(
            event.get("brief"), sort_keys=True).encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM asana_dot_sources WHERE idem=?", (key,)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO asana_dot_sources"
                                "(idem,fingerprint,brief_fingerprint,source_name,state) "
                                "VALUES (?,?,?,?,'POST_SENT')", (key, fingerprint,
                                                                 brief_fingerprint, name))
            elif row["fingerprint"] != fingerprint:
                raise DotEventError("source_idempotency_conflict")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        if row is not None:
            if row["state"] == "CREATED":
                task = await client.task(row["task_gid"])
                self._verify_source(task, project, owner, name, policy.get("creator_gid"))
                return task
            raise DotEventError("source_creation_uncertain")
        try:
            body = await client._request("POST", "/tasks", data=payload)
        except Exception:
            # Keep POST_SENT even on transport failure: a lost response may
            # conceal a successful write. Reconciliation is read-only.
            raise
        gid = str((body.get("data") or {}).get("gid", ""))
        if not gid.isdigit():
            raise DotEventError("source_creation_uncertain")
        task = await client.task(gid)
        self._verify_source(task, project, owner, name, policy.get("creator_gid"))
        self.db.execute("UPDATE asana_dot_sources SET state='CREATED',task_gid=? "
                        "WHERE idem=? AND state='POST_SENT'", (gid, key))
        return task

    @staticmethod
    async def _verify_project(client, project):
        project_body = await client._request("GET", f"/projects/{project}",
                                             params={"opt_fields": "gid,workspace.gid,privacy_setting"})
        observed_project = project_body.get("data") or {}
        if (str(observed_project.get("gid")) != project or
                str((observed_project.get("workspace") or {}).get("gid")) != client.workspace_gid or
                observed_project.get("privacy_setting") != "private"):
            raise DotEventError("source_project_not_private")

    @staticmethod
    def _verify_source(task, project, owner, name, creator):
        if (task.get("name") != name or
                {str(p.get("gid")) for p in task.get("projects", ())} != {project} or
                str((task.get("assignee") or {}).get("gid")) != owner or
                str((task.get("created_by") or {}).get("gid")) != creator):
            raise DotEventError("source_task_unverified")

    async def reconcile_source(self, client, key: str, policy: Mapping):
        row = self.db.execute("SELECT * FROM asana_dot_sources WHERE idem=?", (key,)).fetchone()
        if row is None or row["state"] != "POST_SENT":
            raise DotEventError("source_reconciliation_not_pending")
        project, owner = policy["project_gid"], policy["owner_gid"]
        await self._verify_project(client, project)
        matches = [t async for t in client._pages(
            f"/projects/{project}/tasks",
            params={"opt_fields": "gid,notes"})
            if (t.get("notes") or "") == f"Metis Dot source reference: {key}"]
        if len(matches) != 1:
            raise DotEventError("source_creation_uncertain")
        gid = str(matches[0].get("gid", ""))
        if not gid.isdigit():
            raise DotEventError("source_creation_uncertain")
        task = await client.task(gid)
        self._verify_source(task, project, owner, row["source_name"],
                            policy.get("creator_gid"))
        if row["source_name"] not in policy.get("eligible_task_names", ()):
            raise DotEventError("source_scope_forbidden")
        self.db.execute("UPDATE asana_dot_sources SET state='CREATED',task_gid=? "
                        "WHERE idem=? AND state='POST_SENT'", (gid, key))
        return task

    def record(self, source_gid: str, fingerprint: str, action: str,
               reason: str, target_gid: str | None = None,
               agent_gid: str | None = None):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM asana_dot_decisions WHERE source_gid=?",
                                  (source_gid,)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO asana_dot_decisions "
                                "(source_gid,fingerprint,action,reason,target_gid,agent_gid,review_state) "
                                "VALUES (?,?,?,?,?,?,?)", (source_gid, fingerprint, action,
                                reason, target_gid, agent_gid, "PENDING" if target_gid else "NONE"))
            elif row["fingerprint"] != fingerprint:
                raise DotEventError("event_conflict")
            elif target_gid and row["target_gid"] not in (None, target_gid):
                raise DotEventError("target_conflict")
            elif target_gid and row["target_gid"] is None:
                self.db.execute("UPDATE asana_dot_decisions SET action=?,reason=?,"
                                "target_gid=?,agent_gid=?,review_state='PENDING' WHERE source_gid=?",
                                (action, reason, target_gid, agent_gid, source_gid))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    async def created(self, client, event: Mapping, policy: Mapping, profiles: Mapping,
                      *, dispatch: bool = False):
        """Verify creator and fixed policy before any reservation or write."""
        if not isinstance(event, Mapping) or event.get("type") != "task.created":
            raise DotEventError("invalid_event")
        source_gid = event.get("task_gid")
        if not isinstance(source_gid, str) or not source_gid.isdigit():
            raise DotEventError("invalid_task_gid")
        source_row = self.db.execute(
            "SELECT brief_fingerprint FROM asana_dot_sources "
            "WHERE task_gid=? AND state='CREATED'", (source_gid,)).fetchone()
        if source_row is None:
            raise DotEventError("source_not_created_by_integration")
        brief_hash = hashlib.sha256(json.dumps(
            event.get("brief"), sort_keys=True).encode()).hexdigest()
        if source_row["brief_fingerprint"] != brief_hash:
            raise DotEventError("source_brief_conflict")
        fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        existing = self.db.execute("SELECT * FROM asana_dot_decisions WHERE source_gid=?",
                                   (source_gid,)).fetchone()
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise DotEventError("event_conflict")
            if existing["action"] != "UNCERTAIN":
                return {"action": existing["action"], "reason": existing["reason"],
                        "target_gid": existing["target_gid"]}
            return {"action": "blocked", "reason": "dispatch_uncertain_check_required"}
        source = await client.task(source_gid)
        if str((source.get("workspace") or {}).get("gid", "")) != str(policy.get("workspace_gid", "")):
            self.record(source_gid, fingerprint, "skip", "workspace_scope_mismatch")
            return {"action": "skip", "reason": "workspace_scope_mismatch"}
        creator = str((source.get("created_by") or {}).get("gid", ""))
        if creator != str(policy.get("creator_gid", "")) or not creator:
            self.record(source_gid, fingerprint, "skip", "creator_not_authorized")
            return {"action": "skip", "reason": "creator_not_authorized"}
        project_gid = policy.get("project_gid")
        if (not isinstance(project_gid, str) or
                project_gid not in {str(p.get("gid")) for p in source.get("projects", [])}):
            self.record(source_gid, fingerprint, "skip", "project_scope_mismatch")
            return {"action": "skip", "reason": "project_scope_mismatch"}
        if str((source.get("assignee") or {}).get("gid", "")) != str(policy.get("owner_gid", "")):
            self.record(source_gid, fingerprint, "skip", "human_owner_changed")
            return {"action": "skip", "reason": "human_owner_changed"}
        if (not policy.get("eligible_task_names") or
                source.get("name") not in policy["eligible_task_names"]):
            self.record(source_gid, fingerprint, "skip", "task_not_eligible")
            return {"action": "skip", "reason": "task_not_eligible"}
        routing = policy.get("routing")
        routed_specialty = routing.get(source["name"]) if isinstance(routing, Mapping) else None
        if (not isinstance(routed_specialty, str) or
                routed_specialty not in policy.get("specialties", ())):
            self.record(source_gid, fingerprint, "skip", "no_agent_value_for_task_type")
            return {"action": "skip", "reason": "no_agent_value_for_task_type"}
        brief = event.get("brief")
        if not isinstance(brief, Mapping):
            brief = {}
        brief = dict(brief) | {"source_task_gid": source_gid,
                              "agent_value": True,
                              "specialty": routed_specialty,
                              "dot_authorized": True,
                              "human_owner_retained": bool(source.get("assignee")),
                              "quality_review_by": str(policy.get("owner_gid", ""))}
        if (brief.get("audience") not in policy.get("audiences", ()) or
                not isinstance(brief.get("authorized_sources"), list) or
                not set(brief["authorized_sources"]).issubset(set(policy.get("sources", ())))):
            self.record(source_gid, fingerprint, "skip", "audience_or_sources_forbidden")
            return {"action": "skip", "reason": "audience_or_sources_forbidden"}
        decision, _ = await evaluate_and_dispatch(
            client, source_task_gid=source_gid, project_gid=project_gid,
            brief=brief, agents=profiles)
        if decision.action != "handoff":
            self.record(source_gid, fingerprint, "skip", decision.reason)
            return {"action": "skip", "reason": decision.reason}
        if not dispatch:
            return {"action": "eligible", "reason": decision.reason}
        # Venture Quest's 50 additional requests (MXN 11 each, MXN 550 max)
        # are shared with Dash. This connector cannot read or reserve that
        # global balance. No event field or operator assertion can substitute
        # for an atomic, authoritative shared-quota check.
        self.record(source_gid, fingerprint, "blocked", "shared_quota_unavailable")
        return {"action": "blocked", "reason": "shared_quota_unavailable"}

    async def propose(self, client, source_gid: str, story_gid: str):
        row = self.db.execute("SELECT * FROM asana_dot_decisions WHERE source_gid=?",
                              (source_gid,)).fetchone()
        if row is None or row["review_state"] != "PENDING" or not row["agent_gid"]:
            raise DotEventError("proposal_not_pending")
        stories = await client.stories(row["target_gid"])
        matches = [s for s in stories if str(s.get("gid")) == story_gid]
        if (len(matches) != 1 or
                str((matches[0].get("created_by") or {}).get("gid")) != row["agent_gid"] or
                not (matches[0].get("text") or "").strip()):
            raise DotEventError("proposal_evidence_invalid")
        if row["proposal_gid"] not in (None, story_gid):
            raise DotEventError("proposal_conflict")
        self.db.execute("UPDATE asana_dot_decisions SET proposal_gid=? "
                        "WHERE source_gid=? AND proposal_gid IS NULL", (story_gid, source_gid))
        return {"review_state": "PENDING", "proposal_gid": story_gid}

    async def review(self, client, source_gid: str, story_gid: str, reviewer_gid: str):
        row = self.db.execute("SELECT * FROM asana_dot_decisions WHERE source_gid=?",
                              (source_gid,)).fetchone()
        if row is None or row["review_state"] != "PENDING" or not row["proposal_gid"]:
            raise DotEventError("review_not_pending")
        stories = await client.stories(row["target_gid"])
        matches = [s for s in stories if str(s.get("gid")) == story_gid]
        if len(matches) != 1 or str((matches[0].get("created_by") or {}).get("gid")) != reviewer_gid:
            raise DotEventError("reviewer_or_evidence_invalid")
        text = (matches[0].get("text") or "").strip()
        if text not in ("METIS_APPROVE", "METIS_REJECT"):
            raise DotEventError("review_decision_invalid")
        proposals = [s for s in stories if str(s.get("gid")) == row["proposal_gid"]]
        try:
            proposal_at = datetime.fromisoformat(proposals[0]["created_at"].replace("Z", "+00:00"))
            review_at = datetime.fromisoformat(matches[0]["created_at"].replace("Z", "+00:00"))
            if review_at <= proposal_at:
                raise DotEventError("review_precedes_proposal")
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise DotEventError("review_chronology_unverified") from exc
        state = "APPROVED" if text == "METIS_APPROVE" else "REJECTED"
        self.db.execute("UPDATE asana_dot_decisions SET review_state=?,evidence_gid=?,"
                        "reviewer_gid=? WHERE source_gid=? AND review_state='PENDING'",
                        (state, story_gid, reviewer_gid, source_gid))
        if not self.db.execute("SELECT changes()").fetchone()[0]:
            raise DotEventError("review_race")
        return {"review_state": state, "evidence_gid": story_gid}

    async def reconcile(self, client, source_gid: str, project_gid: str, context: str):
        """Resolve an uncertain event only from a uniquely observed Asana task."""
        row = self.db.execute("SELECT * FROM asana_dot_decisions WHERE source_gid=?",
                              (source_gid,)).fetchone()
        if row is None or row["action"] != "UNCERTAIN":
            raise DotEventError("reconciliation_not_pending")
        key = "dot-handoff-" + hashlib.sha256(
            f"{context}:{project_gid}:{source_gid}".encode()).hexdigest()[:40]
        handoff = client.handoffs.db.execute(
            "SELECT target_agent_gid FROM team_handoffs WHERE key=?", (key,)).fetchone()
        if handoff is None:
            raise DotEventError("handoff_not_found")
        task = await client.reconcile_handoff(key, project_gid, handoff["target_agent_gid"])
        self.record(source_gid, row["fingerprint"], "created", "review_pending",
                    str(task["gid"]), handoff["target_agent_gid"])
        return {"action": "created", "reason": "review_pending",
                "target_gid": str(task["gid"])}
