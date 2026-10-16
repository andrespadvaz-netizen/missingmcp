"""Encrypted local persistence for the private Health OS ingestion boundary."""
from __future__ import annotations

import json
import hmac
import sqlite3
from datetime import datetime, tzinfo
from hashlib import sha256
from threading import RLock
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag

from .. import store
from .core import APPLE_HEALTH, EMERGENCY_INSTRUCTION, EMERGENCY_LEGEND, NON_EVIDENCE_SEMANTICS, SPEEDIANCE, DecisionState, HealthOS, MetricObservation, _canonical_unit
from .importers import observations_from_export, workout_from_export

# Anything that can go wrong while opening one stored row.  A row that cannot
# be decrypted or parsed is evidence of a fault, never evidence of health.
_UNREADABLE = (IndexError, TypeError, ValueError, KeyError, UnicodeError, json.JSONDecodeError, InvalidTag)

# Approved for a separate sleep series; legacy rows remain encrypted in core.
SLEEP_SERIES_START = datetime.fromisoformat("2026-10-16T00:00:00-06:00")
_SLEEP_SERIES_REASONS = (
    "approved_sleep_series_metadata_match", "approved_sleep_series_derived_source_match",
)
_SLEEP_SERIES_ACTIVATION_KEY = "sleep_series_activation_2026_10_16"


def _safe_reason(exc: Exception) -> str:
    """Rejection reason that never echoes a submitted value back."""
    message = str(exc)
    if message.startswith(("could not convert", "invalid literal")):
        return "observation value must be numeric"
    if message.startswith("Invalid isoformat"):
        return "timestamp must be ISO-8601 with an offset"
    return message

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
CREATE TABLE IF NOT EXISTS health_os_sleep_series_audit (
 singleton INTEGER PRIMARY KEY CHECK (singleton=1),
 cutover TEXT NOT NULL, activated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS health_os_quarantine (
 source_table TEXT NOT NULL, event_key TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
 reason TEXT NOT NULL, payload_enc TEXT NOT NULL,
 quarantined_at TEXT DEFAULT (datetime('now')),
 PRIMARY KEY (source_table, event_key)
);
"""


class HealthOSRepository:
    def __init__(self, conn: sqlite3.Connection, data_key: str,
                 health_timezone: str | tzinfo = "America/Mexico_City", *,
                 legacy_key: str | None = None, sleep_series_mode: str = "off") -> None:
        if sleep_series_mode not in {"off", "active", "paused"}:
            raise ValueError("invalid sleep series mode")
        self.conn, self.data_key, self.health_timezone = conn, data_key, health_timezone
        self.sleep_series_mode = sleep_series_mode
        self.sleep_series_start = SLEEP_SERIES_START if sleep_series_mode != "off" else None
        self._write_lock = RLock()
        # Decrypting every stored row took ~35 s in production (Oct 2026).
        # Local writes invalidate the scan; SQLite data_version detects commits
        # made through another connection, including a separate operator process.
        self._scan_cache: tuple[HealthOS, list[dict[str, str]]] | None = None
        self._scan_cache_version: int | None = None
        conn.executescript(_SCHEMA)
        conn.commit()
        # Existing deployments encrypted the three private Health OS tables
        # with GATEWAY_SECRET.  Re-encrypt those rows atomically on first start
        # with the dedicated data key; new deployments have nothing to move.
        self._migrate_legacy_encryption(legacy_key)
        self._ensure_sleep_series_activation()

    def _ensure_sleep_series_activation(self) -> None:
        """Atomically record two independent traces before accepting sleep."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row, audit = self._sleep_series_activation_rows()
            if row is None and audit is None:
                if self._has_new_sleep_series_records():
                    raise ValueError("sleep series activation trace missing; technical review required")
                if self.sleep_series_mode == "paused":
                    raise ValueError("sleep series cannot pause before activation")
                if self.sleep_series_mode == "active":
                    self.conn.execute(
                        "INSERT INTO health_os_state(state_key,payload_enc) VALUES (?,?)",
                        (_SLEEP_SERIES_ACTIVATION_KEY,
                         store.encrypt(self.data_key, SLEEP_SERIES_START.isoformat())),
                    )
                    self.conn.execute(
                        "INSERT INTO health_os_sleep_series_audit(singleton,cutover) VALUES (1,?)",
                        (SLEEP_SERIES_START.isoformat(),),
                    )
            self._assert_sleep_series_mode_current()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _sleep_series_activation_rows(self):
        return (
            self.conn.execute(
                "SELECT payload_enc FROM health_os_state WHERE state_key=?",
                (_SLEEP_SERIES_ACTIVATION_KEY,),
            ).fetchone(),
            self.conn.execute(
                "SELECT cutover FROM health_os_sleep_series_audit WHERE singleton=1",
            ).fetchone(),
        )

    def _assert_sleep_series_mode_current(self) -> None:
        """Fail an old off instance closed after another instance activates."""
        row, audit = self._sleep_series_activation_rows()
        if row is None and audit is None:
            if self._has_new_sleep_series_records():
                raise ValueError("sleep series activation trace missing; technical review required")
            if self.sleep_series_mode != "off":
                raise ValueError("sleep series activation trace missing; technical review required")
            return
        if row is None or audit is None:
            raise ValueError("sleep series activation trace incomplete; technical review required")
        try:
            stored_start = store.decrypt(self.data_key, row["payload_enc"])
        except (*_UNREADABLE, AttributeError) as exc:
            raise ValueError("sleep series marker invalid; technical review required") from exc
        if stored_start != SLEEP_SERIES_START.isoformat() or audit["cutover"] != stored_start:
            raise ValueError("sleep series activation trace differs from approved cutover")
        if self.sleep_series_mode == "off":
            raise ValueError("sleep series was activated; use paused to preserve isolation")

    def _has_new_sleep_series_records(self) -> bool:
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='health_os_tuanhdo_records'").fetchone() is None:
            return False
        return self.conn.execute(
            "SELECT 1 FROM health_os_tuanhdo_records WHERE disposition='accepted' "
            "AND reason IN (?,?) LIMIT 1",
            _SLEEP_SERIES_REASONS,
        ).fetchone() is not None

    def _migrate_legacy_encryption(self, legacy_key: str | None) -> None:
        if not legacy_key or legacy_key == self.data_key:
            return
        updates: list[tuple[str, tuple[str, str]]] = []
        for table, key_column in (("health_os_observations", "event_key"),
                                  ("health_os_workouts", "event_key"),
                                  ("health_os_state", "state_key")):
            for row in self.conn.execute(f"SELECT {key_column}, payload_enc FROM {table}"):
                try:
                    store.decrypt(self.data_key, row["payload_enc"])
                    continue
                except _UNREADABLE:
                    pass
                try:
                    plaintext = store.decrypt(legacy_key, row["payload_enc"])
                except _UNREADABLE:
                    # Unreadable with either key: keep the row untouched.  It is
                    # quarantined at read time (REVISAR_TECNICO), never a crash loop.
                    continue
                updates.append((
                    f"UPDATE {table} SET payload_enc=? WHERE {key_column}=?",
                    (store.encrypt(self.data_key, plaintext), row[key_column]),
                ))
        if updates:
            try:
                self.conn.execute("BEGIN")
                for statement, params in updates:
                    self.conn.execute(statement, params)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    @staticmethod
    def _key(source: str, value: str) -> str:
        return sha256(f"{source}|{value}".encode()).hexdigest()

    def ingest(self, source: str, observations: list[Mapping[str, Any]], workouts: list[Mapping[str, Any]],
               *, commit: bool = True) -> dict[str, Any]:
        with self._write_lock:
            # The quarantine guard in _ingest_locked must share a SQLite write
            # transaction with the eventual insert.  A process-local lock does
            # not serialize another connection performing a reversal.
            owns_transaction = not self.conn.in_transaction
            if owns_transaction:
                self.conn.execute("BEGIN IMMEDIATE")
            try:
                self._assert_sleep_series_mode_current()
                return self._ingest_locked(source, observations, workouts, commit=commit)
            except Exception:
                if owns_transaction:
                    self.conn.rollback()
                raise

    def _ingest_locked(self, source: str, observations: list[Mapping[str, Any]],
                       workouts: list[Mapping[str, Any]], *, commit: bool) -> dict[str, Any]:
        self._invalidate()
        accepted = duplicates = 0
        rejected: list[str] = []
        for raw in observations:
            try:
                item = observations_from_export(source, [raw])[0]
                if item.semantic in NON_EVIDENCE_SEMANTICS:
                    raise ValueError("model-interpreted or transcribed values are not evidence")
                # Validate canonical units before persisting encrypted raw evidence.
                check = HealthOS(item.observed_at.tzinfo).ingest_observations([item])
                if check.rejected:
                    raise ValueError(check.rejected[0])
                value, unit = _canonical_unit(item.metric, item.value, item.unit)
                key = HealthOS._observation_key(MetricObservation(
                    item.metric, value, unit, item.observed_at, item.source, item.provenance_id, item.semantic))
                if self.conn.execute(
                    "SELECT 1 FROM health_os_quarantine WHERE source_table='health_os_observations' "
                    "AND event_key=? AND reason='operator_reversed_tuanhdo'", (key,),
                ).fetchone() is not None:
                    raise ValueError("operator_reversed_tuanhdo")
                encrypted = store.encrypt(self.data_key, json.dumps(dict(raw), sort_keys=True))
                prior = self.conn.execute("SELECT payload_enc FROM health_os_observations WHERE event_key = ?", (key,)).fetchone()
                if prior:
                    # The historic core key omits provenance and producer. A
                    # colliding submission must never replace the first fact.
                    try:
                        previous = json.loads(store.decrypt(self.data_key, prior["payload_enc"]))
                    except _UNREADABLE as exc:
                        raise ValueError("core_key_existing_unreadable") from exc
                    if not isinstance(previous, Mapping):
                        raise ValueError("core_key_existing_unreadable")
                    if any(previous.get(field) != raw.get(field) for field in
                           ("provenance_id", "device_source", "semantic")):
                        raise ValueError("core_key_collision")
                    duplicates += 1
                else:
                    self.conn.execute("INSERT INTO health_os_observations(event_key, source, payload_enc) VALUES (?, ?, ?)", (key, source, encrypted))
                    accepted += 1
            except (TypeError, ValueError) as exc:
                rejected.append(_safe_reason(exc))
        for raw in workouts:
            try:
                item = workout_from_export(source, raw)
                key = self._key(source, item.workout_id)
                encrypted = store.encrypt(self.data_key, json.dumps(dict(raw), sort_keys=True))
                cur = self.conn.execute("INSERT OR IGNORE INTO health_os_workouts(event_key, source, payload_enc) VALUES (?, ?, ?)", (key, source, encrypted))
                accepted += bool(cur.rowcount)
                duplicates += not bool(cur.rowcount)
            except (TypeError, ValueError) as exc:
                rejected.append(_safe_reason(exc))
        if commit:
            self.conn.commit()
        self._invalidate()  # also drops a scan that raced with this write
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}

    def _load_health(self, now: datetime) -> HealthOS:
        return self._scan()[0]

    def _invalidate(self) -> None:
        self._scan_cache = None
        self._scan_cache_version = None

    def _scan(self) -> tuple[HealthOS, list[dict[str, str]]]:
        with self._write_lock:
            while True:
                self._assert_sleep_series_mode_current()
                version = self.conn.execute("PRAGMA data_version").fetchone()[0]
                if self._scan_cache is not None and version == self._scan_cache_version:
                    self._assert_sleep_series_mode_current()
                    return self._scan_cache
                scan = self._scan_uncached()
                # If an external writer committed during the multi-query scan,
                # try again rather than cache a mixed or obsolete view.
                if version == self.conn.execute("PRAGMA data_version").fetchone()[0]:
                    self._assert_sleep_series_mode_current()
                    self._scan_cache = scan
                    self._scan_cache_version = version
                    return scan

    def _scan_uncached(self) -> tuple[HealthOS, list[dict[str, str]]]:
        """Load valid evidence and list every stored row that cannot count.

        Each invalid row is described only by table, key and a fixed reason
        category; no submitted value ever leaves this method.
        """
        health = HealthOS(self.health_timezone, sleep_series_start=self.sleep_series_start)
        invalid: list[dict[str, str]] = []

        def quarantine(table: str, key: str, reason: str) -> None:
            health.record_persisted_invalid()
            invalid.append({"table": table, "key": key, "reason": reason})

        transition = self.conn.execute(
            "SELECT payload_enc FROM health_os_state WHERE state_key = ?",
            ("apple_transition_started_at",),
        ).fetchone()
        if transition:
            try:
                started_at = datetime.fromisoformat(store.decrypt(self.data_key, transition["payload_enc"]))
                health.set_apple_transition(started_at)
            except _UNREADABLE:
                quarantine("health_os_state", "apple_transition_started_at", "undecryptable_or_invalid_transition")
        for row in self.conn.execute("SELECT event_key, source, payload_enc FROM health_os_observations"):
            reason = self._observation_problem(health, row)
            if reason:
                quarantine("health_os_observations", row["event_key"], reason)
        if self.sleep_series_start is not None and self._has_new_sleep_series_records():
            keys: set[str] = set()
            for row in self.conn.execute(
                "SELECT l.core_event_key,r.interval_enc,r.fingerprint,o.status "
                "FROM health_os_tuanhdo_records r "
                "LEFT JOIN health_os_tuanhdo_promotion_links l "
                "ON l.batch_key=r.batch_key AND l.record_index=r.record_index "
                "LEFT JOIN health_os_tuanhdo_promotion_ops o ON o.operation_id=l.operation_id "
                "WHERE r.disposition='accepted' AND r.metric='sleep_duration' "
                "AND r.reason IN (?,?)", _SLEEP_SERIES_REASONS,
            ):
                key = row["core_event_key"]
                item = health._observations.get(key)
                try:
                    interval = json.loads(store.decrypt(self.data_key, row["interval_enc"]))
                    start = datetime.fromisoformat(interval["start"])
                    end = datetime.fromisoformat(interval["end"])
                    fingerprint = (hmac.new(self.data_key.encode("utf-8"),
                                            item.provenance_id.encode("ascii"), "sha256").hexdigest()
                                   if item is not None else None)
                    valid = (row["status"] == "committed"
                             and item is not None and item.metric == "sleep_duration"
                             and row["fingerprint"] == fingerprint
                             and item.source == APPLE_HEALTH
                             and item.device_source == "apple_watch"
                             and item.confidence in {"device_metadata", "watch_source_only"}
                             and start.tzinfo is not None and end.tzinfo is not None
                             and self.sleep_series_start <= start < end
                             and item.observed_at == end)
                except (*_UNREADABLE, AttributeError):
                    valid = False
                if valid:
                    keys.add(key)
                else:
                    quarantine("health_os_tuanhdo_records", key or "missing_link",
                               "invalid_sleep_series_link")
            health.set_sleep_series_keys(keys)
        for row in self.conn.execute("SELECT event_key, source, payload_enc FROM health_os_workouts"):
            if row["source"] not in {APPLE_HEALTH, SPEEDIANCE}:
                quarantine("health_os_workouts", row["event_key"], f"unsupported_source:{row['source']}")
                continue
            try:
                raw = json.loads(store.decrypt(self.data_key, row["payload_enc"]))
            except InvalidTag:
                quarantine("health_os_workouts", row["event_key"], "undecryptable")
                continue
            except _UNREADABLE:
                quarantine("health_os_workouts", row["event_key"], "unparseable")
                continue
            try:
                health.ingest_workout(workout_from_export(row["source"], raw))
            except _UNREADABLE as exc:
                quarantine("health_os_workouts", row["event_key"], "failed_validation:" + _safe_reason(exc))
        return health, invalid

    def _observation_problem(self, health: HealthOS, row) -> str | None:
        if row["source"] not in {APPLE_HEALTH, SPEEDIANCE}:
            return f"unsupported_source:{row['source']}"
        try:
            raw = json.loads(store.decrypt(self.data_key, row["payload_enc"]))
        except InvalidTag:
            return "undecryptable"
        except _UNREADABLE:
            return "unparseable"
        try:
            if str(raw.get("semantic", "measured")) in NON_EVIDENCE_SEMANTICS:
                # Isolated: retained encrypted, never baseline, freshness or state.
                return None
            result = health.ingest_observations(observations_from_export(row["source"], [raw]))
        except _UNREADABLE as exc:
            return "failed_validation:" + _safe_reason(exc)
        if result.rejected:
            # A row the core refuses must never vanish silently.
            return "failed_validation:" + result.rejected[0]
        return None

    def quarantine_report(self) -> dict[str, object]:
        """Counts by reason plus earliest local date per Apple metric; no values."""
        health, invalid = self._scan()
        reasons: dict[str, int] = {}
        for item in invalid:
            label = f"{item['table']}|{item['reason']}"
            reasons[label] = reasons.get(label, 0) + 1
        earliest: dict[str, str] = {}
        for observation in health._observations.values():
            if observation.source != APPLE_HEALTH or observation.semantic in NON_EVIDENCE_SEMANTICS:
                continue
            day = health.local_day(observation.observed_at)
            if observation.metric not in earliest or day < earliest[observation.metric]:
                earliest[observation.metric] = day
        transition = health._apple_baseline_started
        return {
            "invalid_records": len(invalid),
            "by_reason": [{"table": k.split("|", 1)[0], "reason": k.split("|", 1)[1], "count": v}
                          for k, v in sorted(reasons.items())],
            "earliest_apple_day_by_metric": dict(sorted(earliest.items())),
            "apple_transition_day": health.local_day(transition) if transition else None,
            "quarantined_records": self.conn.execute("SELECT COUNT(*) FROM health_os_quarantine").fetchone()[0],
        }

    def quarantine_invalid(self, expected: int) -> dict[str, int]:
        """Operator-acknowledged move of invalid rows out of the calculation.

        Rows are moved, still encrypted, into ``health_os_quarantine`` in one
        transaction.  Nothing is deleted from the database.  ``expected`` must
        match the current count so a stale report can never move other rows.
        """
        self._invalidate()  # count against fresh rows, never a cached scan
        _, invalid = self._scan()
        if any(item["table"] == "health_os_tuanhdo_records" for item in invalid):
            # A broken promotion link cannot be moved with the core-row
            # quarantine operation. Require a scoped technical repair.
            raise ValueError("sleep series link requires technical review")
        if expected != len(invalid):
            raise ValueError("expected count does not match the current invalid records")
        key_columns = {"health_os_observations": "event_key", "health_os_workouts": "event_key",
                       "health_os_state": "state_key"}
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assert_sleep_series_mode_current()
            for item in invalid:
                column = key_columns[item["table"]]
                row = self.conn.execute(
                    f"SELECT * FROM {item['table']} WHERE {column} = ?", (item["key"],)).fetchone()
                if row is None:
                    continue
                self.conn.execute(
                    "INSERT OR REPLACE INTO health_os_quarantine(source_table, event_key, source, reason, payload_enc) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (item["table"], item["key"], row["source"] if "source" in row.keys() else "", item["reason"], row["payload_enc"]))
                self.conn.execute(f"DELETE FROM {item['table']} WHERE {column} = ?", (item["key"],))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self._invalidate()
        return {"quarantined": len(invalid)}

    def set_apple_transition(self, started_at: datetime) -> None:
        """Persist the operator-confirmed start of the Apple baseline era."""
        if started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        with self._write_lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self._assert_sleep_series_mode_current()
                payload = store.encrypt(self.data_key, started_at.isoformat())
                self.conn.execute(
                    "INSERT INTO health_os_state(state_key, payload_enc, updated_at) VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(state_key) DO UPDATE SET payload_enc=excluded.payload_enc, updated_at=datetime('now')",
                    ("apple_transition_started_at", payload),
                )
                self.conn.commit()
                self._invalidate()
            except Exception:
                self.conn.rollback()
                raise

    def quality(self, now: datetime) -> dict[str, object]:
        health = self._load_health(now)
        return health.data_quality(now)

    def history(self) -> dict[str, object]:
        """Monthly aggregates per metric and producer family; no single readings."""
        return {"months": self._load_health(datetime.now().astimezone()).monthly_history()}

    def coverage(self) -> list[dict[str, object]]:
        """First/last month and month count per metric and family; no values."""
        summary: dict[tuple[str, str, str], list[str]] = {}
        for row in self.history()["months"]:
            summary.setdefault((row["metric"], row["device_source"], row.get("series", "")),
                               []).append(row["month"])
        result = []
        for (metric, family, series), months in sorted(summary.items()):
            entry = {"metric": metric, "device_source": family,
                     "first_month": min(months), "last_month": max(months), "months": len(months)}
            if series:
                entry["series"] = series
            result.append(entry)
        return result

    def trends(self, now: datetime) -> dict[str, object]:
        """Weekly descriptive aggregates for the app view; never single readings."""
        return self._load_health(now).weekly_trends(now)

    def brief(self, now: datetime, subjective_flags: tuple[str, ...] = ()) -> dict[str, object]:
        """Minimum operational snapshot; deliberately omits raw health values."""
        health = self._load_health(now)
        decision = health.assess_readiness(now, subjective_flags)
        quality = health.data_quality(now)
        snapshot = {
            "decision": decision.state.value,
            "confidence": decision.confidence,
            "factors": list(decision.factors),
            "missing": list(decision.missing),
            "automated_action": decision.automated_action,
            "quality": quality,
            # Section 7: coverage travels in the e-mail block as day counts only.
            "coverage": health.baseline_coverage(now),
            # Política Operativa v0.5, section 7: always present, every output.
            "legend": EMERGENCY_LEGEND,
        }
        if decision.state == DecisionState.URGENTE:
            snapshot["instruction"] = EMERGENCY_INSTRUCTION
        return snapshot
