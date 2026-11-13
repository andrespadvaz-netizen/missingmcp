"""Authenticated Metis Dot event processing; no other Dot is enabled here."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import re
from datetime import datetime
from collections.abc import Mapping

from .asana_evaluation import evaluate_and_dispatch
from .asana_spend_gate import SpendGateError, load_attestation, reserve_handoff


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
        for column in ("agent_gid", "proposal_gid", "proposal_kind",
                       "proposal_fingerprint", "proposal_provenance"):
            if column not in {r[1] for r in self.db.execute("PRAGMA table_info(asana_dot_decisions)")}:
                self.db.execute(f"ALTER TABLE asana_dot_decisions ADD COLUMN {column} TEXT")
        self.db.execute("""CREATE TABLE IF NOT EXISTS asana_dot_sources (
            idem TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            brief_fingerprint TEXT, source_name TEXT,
            state TEXT NOT NULL, task_gid TEXT, project_gid TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        for column in ("brief_fingerprint", "source_name", "project_gid"):
            if column not in {r[1] for r in self.db.execute("PRAGMA table_info(asana_dot_sources)")}:
                self.db.execute(f"ALTER TABLE asana_dot_sources ADD COLUMN {column} TEXT")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS asana_dot_source_task "
                        "ON asana_dot_sources(task_gid) WHERE task_gid IS NOT NULL")

    def source_registered(self, task_gid: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM asana_dot_sources WHERE task_gid=? AND state='CREATED'",
            (task_gid,)).fetchone() is not None

    def source_project(self, *, task_gid: str | None = None,
                       idempotency_key: str | None = None) -> str | None:
        if task_gid is not None:
            if not isinstance(task_gid, str) or not re.fullmatch(r"[0-9]+", task_gid):
                raise DotEventError("invalid_task_gid")
            row = self.db.execute("SELECT project_gid FROM asana_dot_sources "
                                  "WHERE task_gid=? AND state='CREATED'",
                                  (task_gid,)).fetchone()
        elif idempotency_key is not None:
            if (not isinstance(idempotency_key, str) or
                    not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", idempotency_key)):
                raise DotEventError("invalid_create_key")
            row = self.db.execute("SELECT project_gid FROM asana_dot_sources "
                                  "WHERE idem=?", (idempotency_key,)).fetchone()
        else:
            raise ValueError("source_lookup_required")
        if row is None:
            raise DotEventError("source_not_created_by_integration")
        return row["project_gid"]

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
        creator = policy.get("creator_gid")
        if (not isinstance(name, str) or not name.strip() or len(name) > 200 or
                any(ord(ch) < 32 for ch in name) or
                not isinstance(project, str) or not project.isdigit() or
                not isinstance(owner, str) or not owner.isdigit() or
                not isinstance(creator, str) or not creator.isdigit()):
            raise DotEventError("source_scope_forbidden")
        brief = event.get("brief")
        if (not isinstance(brief, Mapping) or brief.get("context") != policy.get("context") or
                brief.get("project_gid") != project or
                brief.get("audience") not in policy.get("audiences", ()) or
                not isinstance(brief.get("authorized_sources"), list) or
                not brief["authorized_sources"] or
                any(not isinstance(source, str) or
                    source not in policy.get("sources", ())
                    for source in brief["authorized_sources"]) or
                any(not isinstance(brief.get(field), str) or not brief[field].strip()
                    for field in ("deliverable", "request")) or
                brief.get("requires_private_memory") is True):
            raise DotEventError("source_brief_scope_forbidden")
        await self._verify_project(client, project, policy)
        notes = (f"Metis Dot source reference: {key}\n\n"
                 f"Contexto: {brief['context']}\n"
                 f"Audiencia: {brief['audience']}\n"
                 f"Fuentes autorizadas: {', '.join(brief['authorized_sources'])}\n"
                 f"Entregable: {brief['deliverable']}\n"
                 f"Solicitud: {brief['request']}")
        if len(notes.encode("utf-8")) > 10_000:
            raise DotEventError("source_brief_too_large")
        payload = {"name": name, "projects": [project], "assignee": owner,
                   "notes": notes}
        fingerprint = hashlib.sha256(json.dumps(
            {"payload": payload, "brief": event.get("brief")}, sort_keys=True).encode()).hexdigest()
        brief_fingerprint = hashlib.sha256(json.dumps(
            event.get("brief"), sort_keys=True).encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM asana_dot_sources WHERE idem=?", (key,)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO asana_dot_sources"
                                "(idem,fingerprint,brief_fingerprint,source_name,state,project_gid) "
                                "VALUES (?,?,?,?,'POST_SENT',?)", (key, fingerprint,
                                                                    brief_fingerprint, name,
                                                                    project))
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
    async def _verify_project(client, project, policy):
        expected_privacy = policy.get("privacy_setting", "private")
        if (expected_privacy not in {"private", "public_to_workspace"} or
                (expected_privacy == "public_to_workspace" and
                 policy.get("audiences") != ["workspace"])):
            raise DotEventError("source_audience_policy_invalid")
        project_body = await client._request("GET", f"/projects/{project}",
                                             params={"opt_fields": "gid,workspace.gid,privacy_setting"})
        observed_project = project_body.get("data") or {}
        if (str(observed_project.get("gid")) != project or
                str((observed_project.get("workspace") or {}).get("gid")) != client.workspace_gid or
                observed_project.get("privacy_setting") != expected_privacy):
            raise DotEventError("source_project_privacy_mismatch")

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
        if row["project_gid"] not in (None, project):
            raise DotEventError("source_project_policy_changed")
        await self._verify_project(client, project, policy)
        matches = [t async for t in client._pages(
            f"/projects/{project}/tasks",
            params={"opt_fields": "gid,notes"})
            if ((t.get("notes") or "") == f"Metis Dot source reference: {key}" or
                (t.get("notes") or "").startswith(f"Metis Dot source reference: {key}\n"))]
        if len(matches) != 1:
            raise DotEventError("source_creation_uncertain")
        gid = str(matches[0].get("gid", ""))
        if not gid.isdigit():
            raise DotEventError("source_creation_uncertain")
        task = await client.task(gid)
        self._verify_source(task, project, owner, row["source_name"],
                            policy.get("creator_gid"))
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
            "SELECT brief_fingerprint,project_gid FROM asana_dot_sources "
            "WHERE task_gid=? AND state='CREATED'", (source_gid,)).fetchone()
        if source_row is None:
            raise DotEventError("source_not_created_by_integration")
        if source_row["project_gid"] not in (None, policy.get("project_gid")):
            raise DotEventError("source_project_policy_changed")
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
                {str(p.get("gid")) for p in source.get("projects", [])} != {project_gid}):
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
        # An administrator attests Asana's provider-enforced shared cap for
        # this billing cycle. The Gateway separately bounds its handoffs;
        # it does not pretend to know Dash's remaining balance. Asana may
        # reject an agent task after the shared cap is reached.
        try:
            attestation = load_attestation(
                os.environ.get("ASANA_AI_SHARED_LIMIT_JSON", ""),
                workspace_gid=policy["workspace_gid"],
                operator_gid=policy["creator_gid"])
            fresh = reserve_handoff(
                self.db, key=decision.idempotency_key,
                attestation=attestation)
        except SpendGateError as exc:
            self.record(source_gid, fingerprint, "blocked", str(exc))
            return {"action": "blocked", "reason": str(exc)}
        if not fresh:
            self.record(source_gid, fingerprint, "UNCERTAIN",
                        "dispatch_uncertain_check_required")
            return {"action": "blocked", "reason": "dispatch_uncertain_check_required"}
        try:
            dispatched, target = await evaluate_and_dispatch(
                client, source_task_gid=source_gid, project_gid=project_gid,
                brief=brief, agents=profiles, dispatch=True)
        except Exception:
            # A lost response may conceal a successful task or paid use.
            # Keep the admission and require read-only reconciliation.
            self.record(source_gid, fingerprint, "UNCERTAIN",
                        "dispatch_uncertain_check_required")
            return {"action": "blocked", "reason": "dispatch_uncertain_check_required"}
        if dispatched.action != "handoff" or target is None:
            self.record(source_gid, fingerprint, "UNCERTAIN",
                        "dispatch_uncertain_check_required")
            return {"action": "blocked", "reason": "dispatch_uncertain_check_required"}
        self.record(source_gid, fingerprint, "created", "review_pending",
                    str(target["gid"]), dispatched.agent_gid)
        return {"action": "created", "reason": "review_pending",
                "target_gid": str(target["gid"])}

    @staticmethod
    def _story_time(value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise DotEventError("review_chronology_unverified") from exc
        if parsed.tzinfo is None:
            raise DotEventError("review_chronology_unverified")
        return parsed

    @staticmethod
    async def _subtask_evidence(client, target_gid: str, agent_gid: str,
                                owner_gid: str, subtask_gid: str):
        target = await client.task(target_gid)
        if (str((target.get("assignee") or {}).get("gid")) != agent_gid or
                {str(p.get("gid")) for p in target.get("projects", ())} !=
                set(client.allowed_projects)):
            raise DotEventError("proposal_target_agent_changed")
        children = await client.subtasks(target_gid)
        if (len(children) != 1 or
                str(children[0].get("gid")) != subtask_gid):
            raise DotEventError("proposal_subtask_ambiguous")
        task = await client.subtask_detail(subtask_gid)
        if (not isinstance(task, Mapping) or
                str(task.get("gid")) != subtask_gid or
                str((task.get("workspace") or {}).get("gid")) != client.workspace_gid or
                str((task.get("parent") or {}).get("gid")) != target_gid or
                str((task.get("assignee") or {}).get("gid")) != owner_gid or
                task.get("completed") is not False or
                not isinstance(task.get("name"), str) or
                not task["name"].strip() or
                not isinstance(task.get("notes"), str) or
                not task["notes"].strip()):
            raise DotEventError("proposal_subtask_invalid")
        creator = str((task.get("created_by") or {}).get("gid") or "")
        if creator and creator != agent_gid:
            raise DotEventError("proposal_creator_mismatch")
        created = DotEvents._story_time(task.get("created_at"))
        modified = DotEvents._story_time(task.get("modified_at"))
        if modified < created:
            raise DotEventError("proposal_chronology_invalid")
        fingerprint = hashlib.sha256(json.dumps(
            {"gid": subtask_gid, "name": task["name"], "notes": task["notes"]},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return created, modified, fingerprint, ("agent_gid" if creator else
                                                "actor_unavailable")

    async def propose(self, client, source_gid: str, story_gid: str | None = None,
                      *, subtask_gid: str | None = None, owner_gid: str | None = None):
        row = self.db.execute("SELECT * FROM asana_dot_decisions WHERE source_gid=?",
                              (source_gid,)).fetchone()
        if row is None or row["review_state"] != "PENDING" or not row["agent_gid"]:
            raise DotEventError("proposal_not_pending")
        if (story_gid is None) == (subtask_gid is None):
            raise DotEventError("proposal_evidence_type_invalid")
        if subtask_gid is not None:
            if (not isinstance(owner_gid, str) or not owner_gid.isdigit() or
                    not isinstance(subtask_gid, str) or not subtask_gid.isdigit()):
                raise DotEventError("proposal_evidence_type_invalid")
            _, _, fingerprint, provenance = await self._subtask_evidence(
                client, row["target_gid"], row["agent_gid"], owner_gid,
                subtask_gid)
            evidence_gid, kind = subtask_gid, "subtask"
        else:
            stories = await client.stories(row["target_gid"])
            matches = [s for s in stories if str(s.get("gid")) == story_gid]
            if (len(matches) != 1 or
                    str((matches[0].get("created_by") or {}).get("gid")) != row["agent_gid"] or
                    not (matches[0].get("text") or "").strip() or
                    matches[0].get("resource_subtype") not in (None, "comment_added") or
                    matches[0].get("is_edited") is True):
                raise DotEventError("proposal_evidence_invalid")
            fingerprint = hashlib.sha256(matches[0]["text"].encode()).hexdigest()
            provenance = "agent_gid"
            evidence_gid, kind = story_gid, "story"
        if row["proposal_gid"] not in (None, evidence_gid):
            raise DotEventError("proposal_conflict")
        if row["proposal_gid"] is not None:
            if ((row["proposal_kind"] or "story") != kind or
                    row["proposal_fingerprint"] not in (None, fingerprint)):
                raise DotEventError("proposal_conflict")
        else:
            changed = self.db.execute(
                "UPDATE asana_dot_decisions SET proposal_gid=?,proposal_kind=?,"
                "proposal_fingerprint=?,proposal_provenance=? "
                "WHERE source_gid=? AND proposal_gid IS NULL AND review_state='PENDING'",
                (evidence_gid, kind, fingerprint, provenance, source_gid)).rowcount
            if not changed:
                raise DotEventError("proposal_race")
        return {"review_state": "PENDING", "proposal_gid": evidence_gid,
                "proposal_kind": kind, "proposal_provenance": provenance}

    async def review(self, client, source_gid: str, story_gid: str, reviewer_gid: str):
        row = self.db.execute("SELECT * FROM asana_dot_decisions WHERE source_gid=?",
                              (source_gid,)).fetchone()
        if row is None or row["review_state"] != "PENDING" or not row["proposal_gid"]:
            raise DotEventError("review_not_pending")
        human = await client.task(source_gid)
        if (str((human.get("assignee") or {}).get("gid")) != reviewer_gid or
                human.get("completed") is not False or
                {str(p.get("gid")) for p in human.get("projects", ())} !=
                set(client.allowed_projects)):
            raise DotEventError("human_source_changed")
        stories = await client.stories(row["target_gid"])
        matches = [s for s in stories if str(s.get("gid")) == story_gid]
        if (len(matches) != 1 or
                str((matches[0].get("created_by") or {}).get("gid")) != reviewer_gid or
                matches[0].get("resource_subtype") not in (None, "comment_added") or
                matches[0].get("is_edited") is True):
            raise DotEventError("reviewer_or_evidence_invalid")
        text = (matches[0].get("text") or "").strip()
        if text not in ("METIS_APPROVE", "METIS_REJECT"):
            raise DotEventError("review_decision_invalid")
        review_at = self._story_time(matches[0].get("created_at"))
        if row["proposal_kind"] == "subtask":
            created, modified, fingerprint, provenance = await self._subtask_evidence(
                client, row["target_gid"], row["agent_gid"], reviewer_gid,
                row["proposal_gid"])
            if (fingerprint != row["proposal_fingerprint"] or
                    provenance != row["proposal_provenance"] or
                    modified > review_at):
                raise DotEventError("proposal_changed_after_review")
            proposal_at = created
        else:
            proposals = [s for s in stories if str(s.get("gid")) == row["proposal_gid"]]
            if (len(proposals) != 1 or
                    str((proposals[0].get("created_by") or {}).get("gid")) != row["agent_gid"] or
                    proposals[0].get("resource_subtype") not in (None, "comment_added") or
                    proposals[0].get("is_edited") is True):
                raise DotEventError("proposal_evidence_invalid")
            if (row["proposal_fingerprint"] and
                    hashlib.sha256((proposals[0].get("text") or "").encode()).hexdigest()
                    != row["proposal_fingerprint"]):
                raise DotEventError("proposal_changed_after_review")
            proposal_at = self._story_time(proposals[0].get("created_at"))
        if review_at <= proposal_at:
            raise DotEventError("review_precedes_proposal")
        state = "APPROVED" if text == "METIS_APPROVE" else "REJECTED"
        self.db.execute("UPDATE asana_dot_decisions SET review_state=?,evidence_gid=?,"
                        "reviewer_gid=? WHERE source_gid=? AND review_state='PENDING'",
                        (state, story_gid, reviewer_gid, source_gid))
        if not self.db.execute("SELECT changes()").fetchone()[0]:
            raise DotEventError("review_race")
        return {"review_state": state, "evidence_gid": story_gid,
                "proposal_gid": row["proposal_gid"],
                "proposal_kind": row["proposal_kind"] or "story",
                "proposal_provenance": row["proposal_provenance"] or "agent_gid"}

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
