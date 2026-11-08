"""Durable correlation between source work, AI assignment and visible evidence.

This ledger stores IDs and fingerprints only. It does not copy source content,
change Asana permissions or assert that an AI answer is approved by a human.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .queue import canonical_context

GID = re.compile(r"^[0-9]+$")
KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class HandoffError(RuntimeError):
    pass


def _gid(value: str) -> str:
    if not isinstance(value, str) or not GID.fullmatch(value):
        raise ValueError("invalid_handoff_gid")
    return value


@dataclass(frozen=True)
class Handoff:
    key: str
    context: str
    source_task_gid: str
    target_project_gid: str
    target_agent_gid: str
    target_task_gid: str | None
    evidence_story_gid: str | None
    state: str


class TeamHandoffs:
    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path, isolation_level=None, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=20000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS team_handoffs (
            key TEXT PRIMARY KEY, context TEXT NOT NULL,
            source_task_gid TEXT NOT NULL, target_project_gid TEXT NOT NULL,
            target_agent_gid TEXT NOT NULL, fingerprint TEXT NOT NULL,
            target_task_gid TEXT, evidence_story_gid TEXT,
            state TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))""")

    @staticmethod
    def _record(row: sqlite3.Row) -> Handoff:
        return Handoff(row["key"], row["context"], row["source_task_gid"],
                       row["target_project_gid"], row["target_agent_gid"],
                       row["target_task_gid"], row["evidence_story_gid"], row["state"])

    def reserve(self, *, key: str, context: str, source_task_gid: str,
                target_project_gid: str, target_agent_gid: str,
                fingerprint: str) -> Handoff:
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError("invalid_handoff_key")
        context = canonical_context(context)
        source_task_gid = _gid(source_task_gid)
        target_project_gid = _gid(target_project_gid)
        target_agent_gid = _gid(target_agent_gid)
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("invalid_handoff_fingerprint")
        fields = (context, source_task_gid, target_project_gid, target_agent_gid, fingerprint)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM team_handoffs WHERE key=?", (key,)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO team_handoffs(key,context,source_task_gid,"
                                "target_project_gid,target_agent_gid,fingerprint,state) "
                                "VALUES (?,?,?,?,?,?,'RESERVED')", (key, *fields))
                row = self.db.execute("SELECT * FROM team_handoffs WHERE key=?", (key,)).fetchone()
            elif tuple(row[field] for field in ("context", "source_task_gid", "target_project_gid",
                                                   "target_agent_gid", "fingerprint")) != fields:
                raise HandoffError("handoff_idempotency_conflict")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self._record(row)

    def record_target(self, key: str, target_task_gid: str) -> Handoff:
        target_task_gid = _gid(target_task_gid)
        return self._advance(key, "target_task_gid", target_task_gid, "CREATED")

    def record_evidence(self, key: str, evidence_story_gid: str) -> Handoff:
        evidence_story_gid = _gid(evidence_story_gid)
        return self._advance(key, "evidence_story_gid", evidence_story_gid, "EVIDENCE_SEEN")

    def _advance(self, key: str, field: str, value: str, state: str) -> Handoff:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM team_handoffs WHERE key=?", (key,)).fetchone()
            if row is None:
                raise HandoffError("handoff_missing")
            if field == "evidence_story_gid" and row["target_task_gid"] is None:
                raise HandoffError("handoff_target_unverified")
            if row[field] is not None and row[field] != value:
                raise HandoffError("handoff_result_conflict")
            if row["evidence_story_gid"] is not None:
                state = "EVIDENCE_SEEN"
            self.db.execute(f"UPDATE team_handoffs SET {field}=?,state=? WHERE key=?",
                            (value, state, key))
            row = self.db.execute("SELECT * FROM team_handoffs WHERE key=?", (key,)).fetchone()
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self._record(row)

    def close(self):
        self.db.close()
