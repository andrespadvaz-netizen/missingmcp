"""Encrypted, fail-closed staging for the TuAnhDo HealthExport JSON shape.

This is a separate ingest path. A derived fingerprint is only a comparison
hint; it is never represented as an original HealthKit UUID.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from time import perf_counter
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag

from .. import store
from .core import APPLE_HEALTH, HealthOS, MetricObservation, _canonical_unit
from .persistence import HealthOSRepository
from .tuanhdo_dry_run import Sample, parse_health_export


_SCHEMA = """
CREATE TABLE IF NOT EXISTS health_os_tuanhdo_batches (
 batch_key TEXT PRIMARY KEY, payload_enc TEXT NOT NULL, receipt_json TEXT NOT NULL,
 received_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS health_os_tuanhdo_records (
 batch_key TEXT NOT NULL, record_index INTEGER NOT NULL,
 fingerprint TEXT, metric TEXT, interval_enc TEXT,
 disposition TEXT NOT NULL, reason TEXT NOT NULL,
 PRIMARY KEY (batch_key, record_index),
 FOREIGN KEY (batch_key) REFERENCES health_os_tuanhdo_batches(batch_key)
);
CREATE INDEX IF NOT EXISTS health_os_tuanhdo_fingerprint
 ON health_os_tuanhdo_records(fingerprint);
CREATE INDEX IF NOT EXISTS health_os_tuanhdo_intervals
 ON health_os_tuanhdo_records(metric, disposition);
CREATE TABLE IF NOT EXISTS health_os_tuanhdo_promotion_ops (
 operation_id TEXT PRIMARY KEY, batch_key TEXT, kind TEXT NOT NULL,
 approved_types_json TEXT NOT NULL, reviewed_count INTEGER NOT NULL,
 promoted_count INTEGER NOT NULL, status TEXT NOT NULL,
 created_at TEXT DEFAULT (datetime('now')), reversed_at TEXT
);
CREATE TABLE IF NOT EXISTS health_os_tuanhdo_promotion_links (
 batch_key TEXT NOT NULL, record_index INTEGER NOT NULL,
 operation_id TEXT NOT NULL, core_event_key TEXT NOT NULL UNIQUE,
 PRIMARY KEY (batch_key, record_index),
 FOREIGN KEY (operation_id) REFERENCES health_os_tuanhdo_promotion_ops(operation_id)
);
CREATE INDEX IF NOT EXISTS health_os_tuanhdo_links_operation
 ON health_os_tuanhdo_promotion_links(operation_id);
CREATE TABLE IF NOT EXISTS health_os_tuanhdo_ingest_attempts (
 attempt_id TEXT PRIMARY KEY, batch_key TEXT, operation_id TEXT,
 status TEXT NOT NULL CHECK(status IN ('started','committed','replayed','invalid','failed','interrupted')),
 started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 committed_at TEXT, finished_at TEXT, phase_ms_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS health_os_tuanhdo_attempt_batch
 ON health_os_tuanhdo_ingest_attempts(batch_key);
"""

# Exact (source, sourceBundleId, deviceDetail.model,
# deviceDetail.softwareVersion) tuples approved as Apple Watch evidence.
WatchProvenance = frozenset[tuple[str, str, str, str]]
DerivedWatchProvenance = frozenset[tuple[str, str]]
AdditiveWatchSelection = Mapping[str, tuple[str, str, str, str]]
_DEVICE_OPTIONAL_TYPES = frozenset({"restingHeartRate", "sleepAnalysis"})
_GENERIC_WATCH_TYPES = frozenset({
    "heartRate", "heartRateVariabilitySDNN", "stepCount", "distanceWalkingRunning",
    "activeEnergyBurned", "appleExerciseTime", "flightsClimbed", "respiratoryRate",
})
_ADDITIVE = frozenset({
    "sleep_duration", "steps", "distance", "active_energy",
    "exercise_time", "stand_time", "flights_climbed",
})


def _watch_match(sample: Sample, approved: WatchProvenance,
                 derived: DerivedWatchProvenance) -> str | None:
    detail = sample.device_detail
    if isinstance(detail, Mapping):
        fields = (sample.source, sample.source_bundle_id,
                  detail.get("model"), detail.get("softwareVersion"))
        if (all(isinstance(value, str) and value for value in fields)
                and fields in approved
                and (sample.device in (None, fields[0], fields[2])
                     or (sample.exported_type in _GENERIC_WATCH_TYPES
                         and fields[2] == "Watch" and sample.device == "Apple Watch"))):
            # The generic label is only an alias after exact technical proof.
            # It is consistent only after the four exact fields are approved;
            # arbitrary labels, including iPhone, remain conflicts.
            return "device"
        # A partial or conflicting device cannot fall back to source-only proof.
        return None
    source_pair = (sample.source, sample.source_bundle_id)
    if (sample.exported_type in _DEVICE_OPTIONAL_TYPES
            and all(isinstance(value, str) and value for value in source_pair)
            and source_pair in derived
            and any(watch[:2] == source_pair
                    and sample.device in (None, watch[0], watch[2])
                    for watch in approved)):
        return "derived_source"
    return None


def _select_additive_watch(samples: tuple[Sample, ...],
                           selection: AdditiveWatchSelection) -> tuple[Sample, ...]:
    """Opt-in single-source series for activity; preserve all other holds.

    This does not reconcile pre-existing core rows with unknown intervals.
    Those remain blocked independently by _promote.
    """
    result = list(samples)
    for index, sample in enumerate(samples):
        if sample.metric in {"steps", "distance"} and sample.metric not in selection:
            if sample.hold_reason in {"source_unverified", "quantity_overlap", "core_key_collision"}:
                result[index] = replace(sample, hold_reason="source_not_selected")
    for metric, identity in selection.items():
        chosen = []
        for index, sample in enumerate(samples):
            if sample.metric != metric:
                continue
            if _watch_match(sample, frozenset({identity}), frozenset()) == "device":
                chosen.append(index)
            elif sample.hold_reason in {"source_unverified", "quantity_overlap", "core_key_collision"}:
                result[index] = replace(sample, hold_reason="source_not_selected")
        # Re-evaluate overlaps within the selected source. Cross-source
        # collisions never release a selected row if its own intervals clash.
        chosen.sort(key=lambda i: (samples[i].start_at, samples[i].end_at))
        conflicts: set[int] = set()
        key_groups: dict[tuple[float, str, datetime], list[int]] = {}
        for index in chosen:
            sample = samples[index]
            key = (sample.value, sample.unit, sample.end_at.astimezone(timezone.utc))
            key_groups.setdefault(key, []).append(index)
        collisions = {index for indexes in key_groups.values() if len(indexes) > 1
                      for index in indexes}
        longest = None
        for index in chosen:
            if longest is not None and samples[index].start_at < samples[longest].end_at:
                conflicts.update((longest, index))
            if longest is None or samples[index].end_at > samples[longest].end_at:
                longest = index
        for index in chosen:
            if samples[index].hold_reason in {"source_unverified", "quantity_overlap", "core_key_collision"}:
                result[index] = replace(samples[index], hold_reason=(
                    "core_key_collision" if index in collisions else
                    "quantity_overlap" if index in conflicts else "source_unverified"))
    return tuple(result)


def activity_cross_day_exclusions(repo: HealthOSRepository, now: datetime,
                                  window_days: int = 7) -> dict[tuple[str, str], int]:
    """Count distinct retained samples on each affected local day, without values."""
    if repo.activity_series_start is None:
        return {}
    if repo.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                         "name='health_os_tuanhdo_records'").fetchone() is None:
        return {}
    zone = ZoneInfo(repo.health_timezone) if isinstance(repo.health_timezone, str) else repo.health_timezone
    today = now.astimezone(zone).date()
    counts: dict[tuple[str, str], int] = {}
    seen: set[tuple[str, str, str]] = set()
    rows = repo.conn.execute(
        "SELECT fingerprint,metric,interval_enc FROM health_os_tuanhdo_records "
        "WHERE disposition='held' AND reason='activity_crosses_local_day'")
    for row in rows:
        interval = json.loads(store.decrypt(repo.data_key, row["interval_enc"]))
        start = datetime.fromisoformat(interval["start"]).astimezone(zone).date()
        end = (datetime.fromisoformat(interval["end"]) - timedelta(microseconds=1)).astimezone(zone).date()
        for offset in range(window_days):
            day = today - timedelta(days=offset)
            if start <= day <= end:
                key = (row["metric"], day.isoformat())
                unique = (row["fingerprint"], *key)
                if unique not in seen:
                    seen.add(unique)
                    counts[key] = counts.get(key, 0) + 1
    return counts


def _linked_sleep_segments(repo: HealthOSRepository, now: datetime) -> list[Sample]:
    """Only accepted post-cut stages whose staging identity still matches core."""
    if repo.sleep_series_start is None:
        return []
    health = repo._load_health(now)
    if not health._sleep_series_keys:
        return []
    if repo.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='health_os_tuanhdo_records'").fetchone() is None:
        return []
    rows = repo.conn.execute(
        "SELECT r.batch_key,r.record_index,r.fingerprint,l.core_event_key,b.payload_enc "
        "FROM health_os_tuanhdo_records r "
        "JOIN health_os_tuanhdo_promotion_links l "
        "ON l.batch_key=r.batch_key AND l.record_index=r.record_index "
        "JOIN health_os_tuanhdo_batches b ON b.batch_key=r.batch_key "
        "WHERE r.disposition='accepted' AND r.metric='sleep_duration'").fetchall()
    parsed: dict[str, dict[int, Sample]] = {}
    segments: list[Sample] = []
    for row in rows:
        if row["core_event_key"] not in health._sleep_series_keys:
            continue
        batch_key = row["batch_key"]
        if batch_key not in parsed:
            payload = json.loads(store.decrypt(repo.data_key, row["payload_enc"]))
            parsed[batch_key] = {sample.record_index: sample
                                 for sample in parse_health_export(payload).samples}
        sample = parsed[batch_key].get(row["record_index"])
        if (sample is None or sample.sleep_stage not in {"core", "deep", "rem"}
                or sample.start_at < repo.sleep_series_start
                or _fingerprint_token(repo, sample) != row["fingerprint"]
                or _normalized(sample, "device")[1] != row["core_event_key"]):
            continue
        segments.append(sample)
    return segments


def accepted_sleep_episodes(repo: HealthOSRepository, now: datetime) -> list[dict[str, Any]]:
    """Observed phase episodes, stitched by time and source, never by date."""
    health = repo._load_health(now)
    # Two hours is only a stitching allowance for gaps in exported phases;
    # it is never evidence that an episode is a complete night's sleep.
    link_gap = timedelta(hours=2)
    grouped: dict[tuple[Any, ...], list[Sample]] = {}
    for sample in _linked_sleep_segments(repo, now):
        detail = sample.device_detail if isinstance(sample.device_detail, Mapping) else {}
        identity = (sample.source, sample.source_bundle_id,
                    detail.get("model"), detail.get("softwareVersion"))
        grouped.setdefault(identity, []).append(sample)
    episodes = []
    for samples in grouped.values():
        samples.sort(key=lambda sample: (sample.start_at, sample.end_at))
        current: dict[str, Any] | None = None
        for sample in samples:
            if current is None or sample.start_at > current["end"] + link_gap:
                current = {"start": sample.start_at, "end": sample.end_at,
                           "observed_phase_minutes": 0.0, "segment_count": 0,
                           "unobserved_gap_minutes": 0.0}
                episodes.append(current)
            else:
                current["unobserved_gap_minutes"] += max(
                    0.0, (sample.start_at - current["end"]).total_seconds() / 60)
                current["end"] = max(current["end"], sample.end_at)
            current["observed_phase_minutes"] += (sample.end_at - sample.start_at).total_seconds() / 60
            current["segment_count"] += 1
    return [{"first_start": item["start"].isoformat(),
             "last_end": item["end"].isoformat(),
             "local_day": health.local_day(item["end"]),
             "observed_phase_minutes": round(item["observed_phase_minutes"], 1),
             "unobserved_gap_minutes": round(item["unobserved_gap_minutes"], 1),
             "segment_count": item["segment_count"],
             "coverage": "observed_stages_only"}
            for item in sorted(episodes, key=lambda item: item["start"])]


def accepted_sleep_nights(repo: HealthOSRepository, now: datetime) -> list[dict[str, Any]]:
    """Compatibility alias: returns observed episodes, never complete nights."""
    return accepted_sleep_episodes(repo, now)


def accepted_watch_identity_by_key(
        repo: HealthOSRepository, now: datetime,
        approved_watch: WatchProvenance = frozenset(),
        derived_watch: DerivedWatchProvenance = frozenset(),
) -> dict[str, tuple[str, ...] | None]:
    """Resolve approved Watch proof for linked, accepted core keys.

    Exact device proof is a four-field identity. Device-optional resting heart
    rate and sleep stages may use an approved two-field derived source pair.
    Neither proof is included in the private response.
    """
    if repo.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='health_os_tuanhdo_records'").fetchone() is None:
        return {}
    health = repo._load_health(now)
    metrics = ("heart_rate", "resting_heart_rate", "hrv", "sleep_duration", "steps", "distance",
               "active_energy", "exercise_time", "respiratory_rate")
    placeholders = ",".join("?" for _ in metrics)
    rows = repo.conn.execute(
        "SELECT r.batch_key,r.record_index,r.fingerprint,r.metric,l.core_event_key,b.payload_enc "
        "FROM health_os_tuanhdo_records r "
        "JOIN health_os_tuanhdo_promotion_links l ON l.batch_key=r.batch_key "
        "AND l.record_index=r.record_index "
        "JOIN health_os_tuanhdo_batches b ON b.batch_key=r.batch_key "
        f"WHERE r.disposition='accepted' AND r.metric IN ({placeholders})", metrics)
    parsed: dict[str, dict[int, Sample]] = {}
    identities: dict[str, tuple[str, ...] | None] = {}
    for row in rows:
        key = row["core_event_key"]
        item = health._observations.get(key)
        if item is None or not health._current_evidence(item) or item.metric != row["metric"]:
            continue
        batch_key = row["batch_key"]
        if batch_key not in parsed:
            payload = json.loads(store.decrypt(repo.data_key, row["payload_enc"]))
            parsed[batch_key] = {sample.record_index: sample
                                 for sample in parse_health_export(payload).samples}
        sample = parsed[batch_key].get(row["record_index"])
        match = (_watch_match(sample, approved_watch, derived_watch)
                 if sample is not None else None)
        if (sample is None or sample.metric != row["metric"] or match is None
                or _fingerprint_token(repo, sample) != row["fingerprint"]
                or _normalized(sample, match, activity_series=(
                    item.metric in health.ACTIVITY_SERIES_METRICS
                    and item.confidence == health.ACTIVITY_SERIES_CONFIDENCE))[1] != key):
            continue
        detail = sample.device_detail if isinstance(sample.device_detail, Mapping) else {}
        fields = (sample.source, sample.source_bundle_id,
                  detail.get("model"), detail.get("softwareVersion"))
        if match == "device":
            identity = (fields if all(isinstance(value, str) and value for value in fields)
                        else None)
        else:
            pair = fields[:2]
            matching_watches = [watch for watch in approved_watch
                                if watch[:2] == pair and watch[2] == "Watch"]
            identity = (pair if item.metric in {"resting_heart_rate", "sleep_duration"}
                        and item.confidence == "watch_source_only"
                        and len(matching_watches) == 1 else None)
        if key in identities and identities[key] != identity:
            identities[key] = None
        else:
            identities[key] = identity
    return identities


def accepted_sleep_phases(repo: HealthOSRepository, now: datetime) -> dict[str, Any] | None:
    """Last validated local sleep day, aggregated from linked accepted stages."""
    health = repo._load_health(now)
    totals: dict[str, dict[str, float]] = {}
    for sample in _linked_sleep_segments(repo, now):
        day = health.local_day(sample.end_at)
        bucket = totals.setdefault(day, {"core": 0.0, "deep": 0.0, "rem": 0.0})
        bucket[sample.sleep_stage] += (sample.end_at - sample.start_at).total_seconds() / 60
    if not totals:
        return None
    day = max(totals)
    return {"local_day": day, "phase_minutes": {name: round(value, 1)
            for name, value in totals[day].items()}, "source": "linked_validated_sleep_series"}


def _normalized(sample: Sample, match: str, *, activity_series: bool = False) -> tuple[dict[str, Any], str]:
    assurance = (HealthOS.ACTIVITY_SERIES_CONFIDENCE if activity_series else
                 "device_metadata" if match == "device" else "watch_source_only")
    observation = {
        "metric": sample.metric, "value": sample.value, "unit": sample.unit,
        "observed_at": sample.end_at.isoformat(), "semantic": "measured",
        "provenance_id": sample.fingerprint, "device_source": "apple_watch",
        "confidence": assurance,
    }
    value, unit = _canonical_unit(sample.metric, sample.value, sample.unit)
    item = MetricObservation(sample.metric, value, unit, sample.end_at,
                             APPLE_HEALTH, sample.fingerprint, "measured",
                             confidence=assurance, device_source="apple_watch")
    return observation, HealthOS._observation_key(item)


def _fingerprint_token(repo: HealthOSRepository, sample: Sample) -> str:
    # The indexed comparison key is keyed: a copied DB cannot be searched for
    # guessed low-entropy observations without the separate data key.
    return hmac.new(repo.data_key.encode("utf-8"), sample.fingerprint.encode("ascii"),
                    "sha256").hexdigest()


def _unknown_interval_metrics(repo: HealthOSRepository) -> frozenset[str]:
    """Legacy core rows have no start time; do not infer safe non-overlap."""
    unknown: set[str] = set()
    for row in repo.conn.execute(
        "SELECT payload_enc FROM health_os_observations WHERE source=?", (APPLE_HEALTH,),
    ):
        try:
            raw = json.loads(store.decrypt(repo.data_key, row["payload_enc"]))
            metric, provenance = raw["metric"], raw["provenance_id"]
            if metric not in _ADDITIVE:
                continue
            token = hmac.new(repo.data_key.encode("utf-8"), provenance.encode("ascii"),
                             "sha256").hexdigest()
            known = repo.conn.execute(
                "SELECT 1 FROM health_os_tuanhdo_records "
                "WHERE fingerprint=? AND disposition='accepted' LIMIT 1", (token,),
            ).fetchone()
            if known is None:
                unknown.add(metric)
        except (IndexError, TypeError, ValueError, KeyError, UnicodeError, InvalidTag, AttributeError):
            return _ADDITIVE
    return frozenset(unknown)


def _interval_blob(repo: HealthOSRepository, sample: Sample) -> str:
    return store.encrypt(repo.data_key, json.dumps({
        "start": sample.start_at.isoformat(), "end": sample.end_at.isoformat(),
    }))


def _stored_interval(start_text: Any, end_text: Any) -> tuple[datetime, datetime] | None:
    """Use the same validity rule for stored rows and newly accepted rows."""
    start, end = datetime.fromisoformat(start_text), datetime.fromisoformat(end_text)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        return None
    return start, end


class _AcceptedIntervals:
    """Accepted intervals, decrypted once per metric within an ingest transaction."""

    def __init__(self, repo: HealthOSRepository,
                 phases: dict[str, float] | None = None) -> None:
        self._repo = repo
        self._by_metric: dict[str, _IntervalIndex | None] = {}
        self._phases = phases

    def _in_current_series(self, metric: str, start: datetime) -> bool:
        if metric == "sleep_duration" and self._repo.sleep_series_start is not None:
            return start >= self._repo.sleep_series_start
        if (metric in HealthOS.ACTIVITY_SERIES_METRICS
                and self._repo.activity_series_start is not None):
            return start >= self._repo.activity_series_start
        return True

    def get(self, metric: str) -> _IntervalIndex | None:
        """A damaged stored interval blocks the whole metric, as in the full scan."""
        if metric not in self._by_metric:
            load_start = perf_counter()
            intervals: list[tuple[datetime, datetime]] = []
            damaged = False
            for row in self._repo.conn.execute(
                "SELECT interval_enc FROM health_os_tuanhdo_records "
                "WHERE metric=? AND disposition='accepted'", (metric,),
            ):
                try:
                    interval = json.loads(store.decrypt(self._repo.data_key, row["interval_enc"]))
                    parsed = _stored_interval(interval["start"], interval["end"])
                    if parsed is None:
                        damaged = True
                        break
                except (IndexError, TypeError, ValueError, KeyError, UnicodeError,
                        AttributeError, InvalidTag):
                    damaged = True
                    break
                if self._in_current_series(metric, parsed[0]):
                    intervals.append(parsed)
            self._by_metric[metric] = None if damaged else _IntervalIndex(intervals)
            if self._phases is not None:
                self._phases["interval_load_ms"] = round(
                    self._phases.get("interval_load_ms", 0)
                    + (perf_counter() - load_start) * 1000, 3)
        return self._by_metric[metric]

    def add(self, sample: Sample) -> None:
        # A metric not yet loaded will read this newly inserted row on first get().
        if sample.metric not in self._by_metric or self._by_metric[sample.metric] is None:
            return
        try:
            # Match the exact serialization round trip used by _interval_blob().
            parsed = _stored_interval(sample.start_at.isoformat(), sample.end_at.isoformat())
        except (TypeError, ValueError, AttributeError):
            parsed = None
        if parsed is None:
            self._by_metric[sample.metric] = None
        elif self._in_current_series(sample.metric, parsed[0]):
            self._by_metric[sample.metric].add(*parsed)


class _IntervalIndex:
    """Exact overlap queries over accepted intervals, including nested ones."""

    _FLUSH_SIZE = 256

    def __init__(self, intervals: list[tuple[datetime, datetime]]) -> None:
        ordered = sorted(intervals, key=lambda pair: pair[0])
        self.starts = [start for start, _ in ordered]
        self.ends = [end for _, end in ordered]
        self.pending: list[tuple[datetime, datetime]] = []
        self.max_ends: list[datetime] = []
        for end in self.ends:
            self.max_ends.append(max(self.max_ends[-1], end) if self.max_ends else end)

    def add(self, start: datetime, end: datetime) -> None:
        self.pending.append((start, end))
        if len(self.pending) >= self._FLUSH_SIZE:
            # Merge in bounded blocks: reverse-ordered batches remain fast.
            from heapq import merge
            ordered = merge(zip(self.starts, self.ends),
                            sorted(self.pending, key=lambda pair: pair[0]))
            self.starts = []
            self.ends = []
            self.max_ends = []
            for item_start, item_end in ordered:
                self.starts.append(item_start)
                self.ends.append(item_end)
                prior = self.max_ends[-1] if self.max_ends else None
                self.max_ends.append(max(prior, item_end) if prior else item_end)
            self.pending.clear()

    def overlaps(self, start: datetime, end: datetime) -> bool:
        # Strict inequalities retain the existing contiguous-interval rule.
        last = bisect_left(self.starts, end) - 1
        return ((last >= 0 and self.max_ends[last] > start)
                or any(item_start < end and item_end > start
                       for item_start, item_end in self.pending))


def _interval_conflict(repo: HealthOSRepository, sample: Sample,
                       accepted: _AcceptedIntervals | None = None) -> bool:
    if sample.metric not in _ADDITIVE or sample.start_at == sample.end_at:
        return False
    intervals = (accepted if accepted is not None else _AcceptedIntervals(repo)).get(sample.metric)
    return intervals is None or intervals.overlaps(sample.start_at, sample.end_at)


def _promote(repo: HealthOSRepository, sample: Sample, approved_watch: WatchProvenance,
             unknown_intervals: frozenset[str], *, review: bool = False,
             approved_types: frozenset[str] = frozenset(),
             derived_watch: DerivedWatchProvenance = frozenset(),
             accepted_intervals: _AcceptedIntervals | None = None) -> tuple[str, str, str | None]:
    # Preserve this exclusion reason even if the parser also found an overlap:
    # no cross-midnight quantity may enter a complete-looking daily sum.
    new_activity = (sample.metric in HealthOS.ACTIVITY_SERIES_METRICS
                    and repo.activity_series_start is not None)
    zone = ZoneInfo(repo.health_timezone) if isinstance(repo.health_timezone, str) else repo.health_timezone
    crosses_local_day = (sample.start_at.astimezone(zone).date() !=
                         (sample.end_at - timedelta(microseconds=1)).astimezone(zone).date())
    if (new_activity and repo.activity_series_mode == "active"
            and sample.exported_type in approved_types
            and sample.start_at >= repo.activity_series_start
            and _watch_match(sample, frozenset({repo.activity_watch_identity}), frozenset()) == "device"
            and crosses_local_day):
        return "held", "activity_crosses_local_day", None
    if sample.hold_reason != "source_unverified":
        return "held", sample.hold_reason, None
    if sample.exported_type == "bodyMass":
        return "held", "weight_requires_manual_source", None
    match = _watch_match(sample, approved_watch, derived_watch)
    if match is None:
        matching_sources = [watch for watch in approved_watch
                            if watch[:2] == (sample.source, sample.source_bundle_id)]
        if (sample.device is not None and matching_sources
                and all(sample.device not in (watch[0], watch[2])
                        for watch in matching_sources)):
            return "held", "device_conflict", None
        detail = sample.device_detail or {}
        incomplete = (not sample.source or not sample.source_bundle_id or
                      not isinstance(detail, Mapping) or not detail.get("model") or
                      not detail.get("softwareVersion"))
        return "held", "provenance_incomplete" if incomplete else "source_unverified", None
    if sample.exported_type not in approved_types:
        return "held", "metric_not_approved", None
    if new_activity:
        if repo.activity_series_mode == "paused":
            return "held", "activity_series_paused", None
        if _watch_match(sample, frozenset({repo.activity_watch_identity}), frozenset()) != "device":
            return "held", "activity_source_not_selected", None
        if sample.start_at < repo.activity_series_start:
            return "held", ("activity_crosses_series_cut" if sample.end_at > repo.activity_series_start
                            else "activity_before_series_cut"), None
    new_sleep = sample.metric == "sleep_duration" and repo.sleep_series_start is not None
    if new_sleep:
        if sample.start_at < repo.sleep_series_start:
            return "held", ("sleep_crosses_series_cut" if sample.end_at > repo.sleep_series_start
                            else "sleep_before_series_cut"), None
        if repo.sleep_series_mode == "paused":
            return "held", "sleep_series_paused", None
    prior = repo.conn.execute(
        "SELECT disposition FROM health_os_tuanhdo_records WHERE fingerprint=?",
        (_fingerprint_token(repo, sample),),
    ).fetchall()
    if any(row["disposition"] == "accepted" for row in prior):
        return "duplicate", "derived_fingerprint_seen", None
    if any(row["disposition"] == "reversed" for row in prior):
        return "held", "previously_reversed", None
    if prior and not review:
        # A held copy must be reviewed explicitly before any copy can become
        # evidence. A reexport is not approval of the older held record.
        return "held", "previous_held", None
    try:
        observation, core_key = _normalized(sample, match, activity_series=new_activity)
        if sample.metric in unknown_intervals and not (new_sleep or new_activity):
            return "held", "legacy_interval_unknown", None
        if _interval_conflict(repo, sample, accepted_intervals):
            return "held", "interval_overlap_existing", None
        if repo.conn.execute(
            "SELECT 1 FROM health_os_observations WHERE event_key=?", (core_key,),
        ).fetchone() is not None:
            return "held", "core_key_occupied", None
        if repo.conn.execute(
            "SELECT 1 FROM health_os_tuanhdo_promotion_links WHERE core_event_key=?",
            (core_key,),
        ).fetchone() is not None:
            return "held", "core_key_previously_linked", None
        result = repo.ingest(APPLE_HEALTH, [observation], [], commit=False,
                             activity_series=new_activity)
        if result["accepted"] == 1:
            if new_activity:
                return "accepted", "approved_activity_series_metadata_match", core_key
            if new_sleep:
                return "accepted", ("approved_sleep_series_metadata_match" if match == "device"
                                    else "approved_sleep_series_derived_source_match"), core_key
            return "accepted", ("approved_metadata_match" if match == "device"
                                else "approved_derived_source_match"), core_key
    except (TypeError, ValueError):
        pass
    return "held", "core_rejected", None


def _current_receipt(conn, batch_key: str, base: Mapping[str, Any]) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT disposition, reason FROM health_os_tuanhdo_records WHERE batch_key=?",
        (batch_key,),
    ).fetchall()
    dispositions = Counter(row["disposition"] for row in rows)
    reasons = Counter(row["reason"] for row in rows)
    return {**base, "accepted": dispositions["accepted"],
            "duplicates": dispositions["duplicate"], "held": dispositions["held"],
            "rejected": dispositions["rejected"], "reversed": dispositions["reversed"],
            "reasons": dict(sorted(reasons.items()))}


def _start_promotion_op(conn, batch_key: str | None, kind: str,
                        approved_types: frozenset[str]) -> str:
    operation_id = uuid4().hex
    conn.execute(
        "INSERT INTO health_os_tuanhdo_promotion_ops"
        "(operation_id,batch_key,kind,approved_types_json,reviewed_count,promoted_count,status) "
        "VALUES (?,?,?,?,0,0,'committed')",
        (operation_id, batch_key, kind, json.dumps(sorted(approved_types))),
    )
    return operation_id


def _link_promotion(conn, batch_key: str, record_index: int,
                    operation_id: str, core_event_key: str) -> None:
    conn.execute(
        "INSERT INTO health_os_tuanhdo_promotion_links"
        "(batch_key,record_index,operation_id,core_event_key) VALUES (?,?,?,?)",
        (batch_key, record_index, operation_id, core_event_key),
    )


_DISPOSITION_INDEX = ("CREATE INDEX IF NOT EXISTS health_os_tuanhdo_disposition_reason "
                      "ON health_os_tuanhdo_records(disposition, reason)")


def migrate_tuanhdo_disposition_index(repo: HealthOSRepository) -> float:
    """Explicit, idempotent migration; run before traffic on a reviewed copy."""
    with repo._write_lock:
        repo.conn.executescript(_SCHEMA)
        started = perf_counter()
        repo.conn.execute(_DISPOSITION_INDEX)
        repo.conn.commit()
        return round((perf_counter() - started) * 1000, 3)


def start_tuanhdo_attempt(repo: HealthOSRepository, attempt_id: str) -> None:
    """Persist receipt of an authenticated request before reading its body."""
    with repo._write_lock:
        repo.conn.executescript(_SCHEMA)
        has_index = repo.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND "
            "name='health_os_tuanhdo_disposition_reason'").fetchone()
        if has_index is None:
            if repo.conn.execute(
                "SELECT 1 FROM health_os_tuanhdo_records LIMIT 1").fetchone() is not None:
                raise RuntimeError("health_os_tuanhdo_disposition_reason migration required")
            # Fresh empty databases need no data scan.
            repo.conn.execute(_DISPOSITION_INDEX)
        repo.conn.execute(
            "INSERT INTO health_os_tuanhdo_ingest_attempts(attempt_id,status) VALUES (?,'started')",
            (attempt_id,),
        )
        repo.conn.commit()


def finish_tuanhdo_attempt(repo: HealthOSRepository, attempt_id: str,
                           status: str, phases: Mapping[str, float]) -> None:
    if status not in {"invalid", "failed", "interrupted"}:
        raise ValueError("invalid attempt terminal state")
    with repo._write_lock:
        repo.conn.execute(
            "UPDATE health_os_tuanhdo_ingest_attempts SET status=?, "
            "finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), phase_ms_json=? "
            "WHERE attempt_id=? AND status='started'",
            (status, json.dumps(phases, sort_keys=True), attempt_id),
        )
        repo.conn.commit()


def ingest_tuanhdo(repo: HealthOSRepository, payload: Mapping[str, Any],
                   approved_watch: WatchProvenance = frozenset(), *,
                   approved_types: frozenset[str] = frozenset(),
                   derived_watch: DerivedWatchProvenance = frozenset(),
                   additive_watch_selection: AdditiveWatchSelection = {},
                   attempt_id: str | None = None,
                   phases: dict[str, float] | None = None) -> dict[str, Any]:
    """Stage every row and promote only unambiguous, verified observations.

    The response contains fixed reason names and counts only. Workouts remain
    in the encrypted batch until a validated workout mapping exists.
    """
    _validate_additive_selection(additive_watch_selection, approved_watch)
    if attempt_id is None:
        attempt_id = uuid4().hex
        start_tuanhdo_attempt(repo, attempt_id)
    wait_start = perf_counter()
    with repo._write_lock:
        measured = phases if phases is not None else {}
        measured["lock_wait_ms"] = round((perf_counter() - wait_start) * 1000, 3)
        return _ingest_tuanhdo_locked(repo, payload, approved_watch, approved_types,
                                      derived_watch, additive_watch_selection,
                                      attempt_id, measured)


def _validate_additive_selection(selection: AdditiveWatchSelection,
                                 approved_watch: WatchProvenance) -> None:
    if any(metric not in HealthOS.ACTIVITY_SERIES_METRICS or identity not in approved_watch
           for metric, identity in selection.items()):
        raise ValueError("additive selection needs an approved exact Watch identity")


def _ingest_tuanhdo_locked(repo: HealthOSRepository, payload: Mapping[str, Any],
                           approved_watch: WatchProvenance,
                           approved_types: frozenset[str],
                           derived_watch: DerivedWatchProvenance,
                           additive_watch_selection: AdditiveWatchSelection,
                           attempt_id: str, phases: dict[str, float]) -> dict[str, Any]:
    stage_start = perf_counter()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("records"), list):
        raise ValueError("records_required")
    parsed = parse_health_export(payload)
    phases["parse_ms"] = round((perf_counter() - stage_start) * 1000, 3)
    if parsed.aggregation != "raw":
        raise ValueError("aggregation_not_raw")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    batch_key = sha256(canonical.encode("utf-8")).hexdigest()
    phases["canonical_ms"] = round((perf_counter() - stage_start) * 1000 - phases["parse_ms"], 3)
    conn = repo.conn
    conn.executescript(_SCHEMA)
    commit_attempted = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        repo._assert_sleep_series_mode_current()
        repo._assert_activity_series_mode_current()
        previous = conn.execute(
            "SELECT receipt_json FROM health_os_tuanhdo_batches WHERE batch_key=?",
            (batch_key,),
        ).fetchone()
        if previous is not None:
            receipt_start = perf_counter()
            receipt = _current_receipt(conn, batch_key, json.loads(previous["receipt_json"]))
            phases["receipt_ms"] = round((perf_counter() - receipt_start) * 1000, 3)
            prior_op = conn.execute(
                "SELECT operation_id FROM health_os_tuanhdo_promotion_ops "
                "WHERE batch_key=? AND kind='ingest' ORDER BY rowid LIMIT 1", (batch_key,),
            ).fetchone()
            phases["stage_ms"] = round((perf_counter() - stage_start) * 1000, 3)
            conn.execute(
                "UPDATE health_os_tuanhdo_ingest_attempts SET batch_key=?,operation_id=?,"
                "status='replayed',committed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
                "phase_ms_json=? WHERE attempt_id=? AND status='started'",
                (batch_key, prior_op["operation_id"] if prior_op else None,
                 json.dumps(phases, sort_keys=True), attempt_id),
            )
            commit_attempted = True
            commit_start = perf_counter()
            conn.commit()
            phases["commit_ms"] = round((perf_counter() - commit_start) * 1000, 3)
            return {**receipt, "replayed": True}
        samples = {sample.record_index: sample for sample in
                   _select_additive_watch(parsed.samples, additive_watch_selection)}
        unknown_start = perf_counter()
        unknown_intervals = _unknown_interval_metrics(repo)
        phases["unknown_scan_ms"] = round((perf_counter() - unknown_start) * 1000, 3)
        accepted_intervals = _AcceptedIntervals(repo, phases)
        loop_start = perf_counter()
        conn.execute(
            "INSERT INTO health_os_tuanhdo_batches(batch_key,payload_enc,receipt_json) VALUES (?,?,?)",
            (batch_key, store.encrypt(repo.data_key, canonical), "{}"),
        )
        operation_id = _start_promotion_op(conn, batch_key, "ingest", approved_types)
        promoted = 0
        for index, _record in enumerate(parsed.raw_records):
            sample = samples.get(index)
            if sample is None:
                disposition = "rejected"
                reason = parsed.rejected_by_index.get(index, "parser_rejected")
                core_event_key = None
            else:
                disposition, reason, core_event_key = _promote(
                    repo, sample, approved_watch, unknown_intervals,
                    approved_types=approved_types, derived_watch=derived_watch,
                    accepted_intervals=accepted_intervals)
            conn.execute(
                "INSERT INTO health_os_tuanhdo_records"
                "(batch_key,record_index,fingerprint,metric,interval_enc,disposition,reason) "
                "VALUES (?,?,?,?,?,?,?)",
                (batch_key, index, _fingerprint_token(repo, sample) if sample else None,
                 sample.metric if sample else None,
                 _interval_blob(repo, sample) if sample else None, disposition, reason),
            )
            if disposition == "accepted":
                accepted_intervals.add(sample)
            if core_event_key is not None:
                _link_promotion(conn, batch_key, index, operation_id, core_event_key)
                promoted += 1
        conn.execute(
            "UPDATE health_os_tuanhdo_promotion_ops SET reviewed_count=?, promoted_count=? "
            "WHERE operation_id=?", (len(parsed.raw_records), promoted, operation_id))
        receipt = {
            "records_received": len(parsed.raw_records),
            "recognized": len(parsed.samples),
            "workouts_held": len(parsed.workouts),
            "issues": dict(parsed.issues),
        }
        conn.execute("UPDATE health_os_tuanhdo_batches SET receipt_json=? WHERE batch_key=?",
                     (json.dumps(receipt, sort_keys=True), batch_key))
        phases["promote_ms"] = round((perf_counter() - loop_start) * 1000, 3)
        phases["stage_ms"] = round((perf_counter() - stage_start) * 1000, 3)
        conn.execute(
            "UPDATE health_os_tuanhdo_ingest_attempts SET batch_key=?,operation_id=?,"
            "status='committed',committed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
            "phase_ms_json=? WHERE attempt_id=? AND status='started'",
            (batch_key, operation_id, json.dumps(phases, sort_keys=True), attempt_id),
        )
        commit_attempted = True
        commit_start = perf_counter()
        conn.commit()
        phases["commit_ms"] = round((perf_counter() - commit_start) * 1000, 3)
        repo._invalidate()
    except Exception as exc:
        conn.rollback()
        repo._invalidate()
        # A failed COMMIT can have an uncertain outcome. Preserve 'started'
        # for reconciliation rather than claiming the batch did not persist.
        if not commit_attempted:
            finish_tuanhdo_attempt(repo, attempt_id,
                                   "invalid" if isinstance(exc, (TypeError, ValueError))
                                   else "failed", phases)
        raise
    receipt_start = perf_counter()
    current = _current_receipt(conn, batch_key, receipt)
    phases["receipt_ms"] = round((perf_counter() - receipt_start) * 1000, 3)
    return {**current, "replayed": False}


def review_held_tuanhdo(repo: HealthOSRepository,
                        approved_watch: WatchProvenance, *,
                        batch_key: str,
                        approved_types: frozenset[str] = frozenset(),
                        derived_watch: DerivedWatchProvenance = frozenset(),
                        additive_watch_selection: AdditiveWatchSelection = {}) -> dict[str, int | str]:
    """Explicit local review after an operator approves exact metadata tuples.

    There is deliberately no HTTP route for this action. The first retained
    copy can be promoted; later copies become duplicates only after that.
    """
    if not isinstance(batch_key, str) or not batch_key:
        raise ValueError("exact batch_key is required for held review")
    _validate_additive_selection(additive_watch_selection, approved_watch)
    with repo._write_lock:
        return _review_held_tuanhdo_locked(repo, approved_watch, approved_types,
                                           derived_watch, batch_key, additive_watch_selection)


def _review_held_tuanhdo_locked(repo: HealthOSRepository,
                                approved_watch: WatchProvenance,
                                approved_types: frozenset[str],
                                derived_watch: DerivedWatchProvenance,
                                batch_key: str,
                                additive_watch_selection: AdditiveWatchSelection) -> dict[str, int | str]:
    conn = repo.conn
    conn.executescript(_SCHEMA)
    parsed_batches: dict[str, dict[int, Sample]] = {}
    outcome: Counter[str] = Counter()
    try:
        conn.execute("BEGIN IMMEDIATE")
        repo._assert_sleep_series_mode_current()
        repo._assert_activity_series_mode_current()
        if conn.execute(
                "SELECT 1 FROM health_os_tuanhdo_batches WHERE batch_key=?",
                (batch_key,)).fetchone() is None:
            raise ValueError("unknown batch_key")
        query = ("SELECT batch_key, record_index FROM health_os_tuanhdo_records "
                 "WHERE disposition='held' AND reason IN "
                 "('source_unverified','previous_held','metric_not_approved',"
                 "'provenance_incomplete','device_conflict','legacy_interval_unknown',"
                 "'sleep_series_paused','quantity_overlap','source_not_selected',"
                 "'activity_series_off','activity_series_paused')")
        candidates = conn.execute(query + " AND batch_key=? ORDER BY rowid",
                                  (batch_key,)).fetchall()
        operation_id = _start_promotion_op(conn, batch_key, "held_review", approved_types)
        unknown_intervals = _unknown_interval_metrics(repo)
        for row in candidates:
            batch_key = row["batch_key"]
            if batch_key not in parsed_batches:
                batch = conn.execute(
                    "SELECT payload_enc FROM health_os_tuanhdo_batches WHERE batch_key=?",
                    (batch_key,),
                ).fetchone()
                payload = json.loads(store.decrypt(repo.data_key, batch["payload_enc"]))
                parsed_batches[batch_key] = {
                    sample.record_index: sample for sample in _select_additive_watch(
                        parse_health_export(payload).samples, additive_watch_selection)
                }
            sample = parsed_batches[batch_key].get(row["record_index"])
            if sample is None:
                outcome["still_held"] += 1
                continue
            if sample.exported_type not in approved_types:
                # Leave unrelated held rows untouched during a scoped review.
                continue
            outcome["reviewed"] += 1
            disposition, reason, core_event_key = _promote(
                repo, sample, approved_watch, unknown_intervals, review=True,
                approved_types=approved_types, derived_watch=derived_watch)
            if disposition == "accepted":
                outcome["promoted"] += 1
                if core_event_key is None:
                    raise ValueError("accepted record has no core event key")
                _link_promotion(conn, batch_key, row["record_index"], operation_id,
                                core_event_key)
            elif disposition == "duplicate":
                outcome["duplicates_marked"] += 1
            else:
                outcome["still_held"] += 1
            conn.execute(
                "UPDATE health_os_tuanhdo_records SET disposition=?, reason=? "
                "WHERE batch_key=? AND record_index=?",
                (disposition, reason, batch_key, row["record_index"]),
            )
        conn.execute(
            "UPDATE health_os_tuanhdo_promotion_ops SET reviewed_count=?, promoted_count=? "
            "WHERE operation_id=?",
            (outcome["reviewed"], outcome["promoted"], operation_id),
        )
        conn.commit()
        repo._invalidate()
    except Exception:
        conn.rollback()
        repo._invalidate()
        raise
    return {"reviewed": outcome["reviewed"], "promoted": outcome["promoted"],
            "duplicates_marked": outcome["duplicates_marked"],
            "still_held": outcome["still_held"], "operation_id": operation_id}


def reverse_tuanhdo_operation(repo: HealthOSRepository, operation_id: str, *,
                              batch_key: str, expected: int) -> dict[str, int | bool]:
    """Move only this operation's core rows to encrypted quarantine, atomically.

    The staged export and an operation audit row remain. This local-only action
    has no HTTP route and never decrypts the clinical payload.
    """
    if (not isinstance(operation_id, str) or not operation_id
            or not isinstance(batch_key, str) or not batch_key
            or isinstance(expected, bool) or not isinstance(expected, int)
            or expected < 0):
        raise ValueError("operation_id, batch_key and nonnegative expected count required")
    with repo._write_lock:
        conn = repo.conn
        conn.executescript(_SCHEMA)
        try:
            conn.execute("BEGIN IMMEDIATE")
            repo._assert_sleep_series_mode_current()
            repo._assert_activity_series_mode_current()
            operation = conn.execute(
                "SELECT batch_key,promoted_count,status FROM health_os_tuanhdo_promotion_ops "
                "WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if operation is None or operation["batch_key"] != batch_key:
                raise ValueError("operation not found for batch")
            if operation["promoted_count"] != expected:
                raise ValueError("expected count does not match operation")
            if operation["status"] == "reversed":
                conn.commit()
                return {"reversed": 0, "total_reversed": expected, "replayed": True}
            if operation["status"] != "committed":
                raise ValueError("operation status is not reversible")
            links = conn.execute(
                "SELECT l.record_index,l.core_event_key,r.disposition "
                "FROM health_os_tuanhdo_promotion_links AS l "
                "JOIN health_os_tuanhdo_records AS r "
                "ON r.batch_key=l.batch_key AND r.record_index=l.record_index "
                "WHERE l.operation_id=? AND l.batch_key=? ORDER BY l.record_index",
                (operation_id, batch_key),
            ).fetchall()
            if len(links) != expected or any(row["disposition"] != "accepted" for row in links):
                raise ValueError("promotion links do not match accepted records")
            for link in links:
                event_key = link["core_event_key"]
                source = conn.execute(
                    "SELECT source FROM health_os_observations WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if source is None or source["source"] != APPLE_HEALTH:
                    raise ValueError("linked Apple Health observation missing")
                conn.execute(
                    "INSERT INTO health_os_quarantine"
                    "(source_table,event_key,source,reason,payload_enc) "
                    "SELECT 'health_os_observations',event_key,source,"
                    "'operator_reversed_tuanhdo',payload_enc "
                    "FROM health_os_observations WHERE event_key=?",
                    (event_key,),
                )
                if conn.execute(
                    "DELETE FROM health_os_observations WHERE event_key=?", (event_key,),
                ).rowcount != 1:
                    raise ValueError("linked observation changed during reversal")
                conn.execute(
                    "UPDATE health_os_tuanhdo_records SET disposition='reversed', "
                    "reason='operator_reversed_tuanhdo' "
                    "WHERE batch_key=? AND record_index=?",
                    (batch_key, link["record_index"]),
                )
            conn.execute(
                "UPDATE health_os_tuanhdo_promotion_ops "
                "SET status='reversed',reversed_at=datetime('now') WHERE operation_id=?",
                (operation_id,),
            )
            conn.commit()
            return {"reversed": expected, "total_reversed": expected, "replayed": False}
        except Exception:
            conn.rollback()
            raise
        finally:
            repo._invalidate()
