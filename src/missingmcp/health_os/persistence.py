"""Encrypted local persistence for the private Health OS ingestion boundary."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, tzinfo
from hashlib import sha256
from typing import Any, Mapping

from .. import store
from .core import HealthOS
from .importers import observations_from_export, workout_from_export

_SCHEMA = """
CREATE TABLE IF NOT EXISTS health_os_observations (
 event_key TEXT PRIMARY KEY, source TEXT NOT NULL, payload_enc TEXT NOT NULL,
 received_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS health_os_workouts (
 event_key TEXT PRIMARY KEY, source TEXT NOT NULL, payload_enc TEXT NOT NULL,
 received_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS health_os_state (
 state_key TEXT PRIMARY KEY, payload_enc TEXT NOT NULL,
 updated_at TEXT DEFAULT (datetime('now'))
);
"""


class HealthOSRepository:
    def __init__(self, conn: sqlite3.Connection, secret: str,
                 health_timezone: str | tzinfo = "America/Mexico_City") -> None:
        self.conn, self.secret, self.health_timezone = conn, secret, health_timezone
        conn.executescript(_SCHEMA)
        conn.commit()

    @staticmethod
    def _key(source: str, value: str) -> str:
        return sha256(f"{source}|{value}".encode()).hexdigest()

    def ingest(self, source: str, observations: list[Mapping[str, Any]], workouts: list[Mapping[str, Any]]) -> dict[str, Any]:
        accepted = duplicates = 0
        rejected: list[str] = []
        for raw in observations:
            try:
                item = observations_from_export(source, [raw])[0]
                # Validate canonical units before persisting encrypted raw evidence.
                check = HealthOS(item.observed_at.tzinfo).ingest_observations([item])
                if check.rejected:
                    raise ValueError(check.rejected[0])
                key = HealthOS._observation_key(item)
                encrypted = store.encrypt(self.secret, json.dumps(dict(raw), sort_keys=True))
                cur = self.conn.execute("INSERT OR IGNORE INTO health_os_observations(event_key, source, payload_enc) VALUES (?, ?, ?)", (key, source, encrypted))
                accepted += bool(cur.rowcount)
                duplicates += not bool(cur.rowcount)
            except (TypeError, ValueError) as exc:
                rejected.append(str(exc))
        for raw in workouts:
            try:
                item = workout_from_export(source, raw)
                key = self._key(source, item.workout_id)
                encrypted = store.encrypt(self.secret, json.dumps(dict(raw), sort_keys=True))
                cur = self.conn.execute("INSERT OR IGNORE INTO health_os_workouts(event_key, source, payload_enc) VALUES (?, ?, ?)", (key, source, encrypted))
                accepted += bool(cur.rowcount)
                duplicates += not bool(cur.rowcount)
            except (TypeError, ValueError) as exc:
                rejected.append(str(exc))
        self.conn.commit()
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}

    def _load_health(self, now: datetime) -> HealthOS:
        health = HealthOS(self.health_timezone)
        transition = self.conn.execute(
            "SELECT payload_enc FROM health_os_state WHERE state_key = ?",
            ("apple_transition_started_at",),
        ).fetchone()
        if transition:
            started_at = datetime.fromisoformat(store.decrypt(self.secret, transition["payload_enc"]))
            health.set_apple_transition(started_at)
        for row in self.conn.execute("SELECT source, payload_enc FROM health_os_observations"):
            raw = json.loads(store.decrypt(self.secret, row["payload_enc"]))
            health.ingest_observations(observations_from_export(row["source"], [raw]))
        for row in self.conn.execute("SELECT source, payload_enc FROM health_os_workouts"):
            raw = json.loads(store.decrypt(self.secret, row["payload_enc"]))
            health.ingest_workout(workout_from_export(row["source"], raw))
        return health

    def set_apple_transition(self, started_at: datetime) -> None:
        """Persist the operator-confirmed start of the Apple baseline era."""
        if started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        payload = store.encrypt(self.secret, started_at.isoformat())
        self.conn.execute(
            "INSERT INTO health_os_state(state_key, payload_enc, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(state_key) DO UPDATE SET payload_enc=excluded.payload_enc, updated_at=datetime('now')",
            ("apple_transition_started_at", payload),
        )
        self.conn.commit()

    def quality(self, now: datetime) -> dict[str, object]:
        health = self._load_health(now)
        return health.data_quality(now)

    def brief(self, now: datetime, subjective_flags: tuple[str, ...] = ()) -> dict[str, object]:
        """Minimum operational snapshot; deliberately omits raw health values."""
        health = self._load_health(now)
        decision = health.assess_readiness(now, subjective_flags)
        quality = health.data_quality(now)
        return {
            "decision": decision.state.value,
            "confidence": decision.confidence,
            "factors": list(decision.factors),
            "missing": list(decision.missing),
            "automated_action": decision.automated_action,
            "quality": quality,
        }
