"""Encrypted, fail-closed staging for the TuAnhDo HealthExport JSON shape.

This is a separate ingest path. A derived fingerprint is only a comparison
hint; it is never represented as an original HealthKit UUID.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import hmac
import json
from typing import Any, Mapping
from uuid import uuid4

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
"""

# Exact (source, sourceBundleId, deviceDetail.model,
# deviceDetail.softwareVersion) tuples approved as Apple Watch evidence.
WatchProvenance = frozenset[tuple[str, str, str, str]]
DerivedWatchProvenance = frozenset[tuple[str, str]]
_DEVICE_OPTIONAL_TYPES = frozenset({"restingHeartRate", "sleepAnalysis"})
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
                and sample.device in (None, fields[0], fields[2])):
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


def _normalized(sample: Sample, match: str) -> tuple[dict[str, Any], str]:
    assurance = "device_metadata" if match == "device" else "watch_source_only"
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


def _interval_conflict(repo: HealthOSRepository, sample: Sample) -> bool:
    if sample.metric not in _ADDITIVE or sample.start_at == sample.end_at:
        return False
    for row in repo.conn.execute(
        "SELECT interval_enc FROM health_os_tuanhdo_records "
        "WHERE metric=? AND disposition='accepted'", (sample.metric,),
    ):
        try:
            interval = json.loads(store.decrypt(repo.data_key, row["interval_enc"]))
            start, end = datetime.fromisoformat(interval["start"]), datetime.fromisoformat(interval["end"])
        except (IndexError, TypeError, ValueError, KeyError, UnicodeError, InvalidTag):
            # A damaged interval cannot justify another additive observation.
            return True
        if max(sample.start_at, start) < min(sample.end_at, end):
            return True
    return False


def _promote(repo: HealthOSRepository, sample: Sample, approved_watch: WatchProvenance,
             unknown_intervals: frozenset[str], *, review: bool = False,
             approved_types: frozenset[str] = frozenset(),
             derived_watch: DerivedWatchProvenance = frozenset()) -> tuple[str, str, str | None]:
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
        observation, core_key = _normalized(sample, match)
        if sample.metric in unknown_intervals:
            return "held", "legacy_interval_unknown", None
        if _interval_conflict(repo, sample):
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
        result = repo.ingest(APPLE_HEALTH, [observation], [], commit=False)
        if result["accepted"] == 1:
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


def ingest_tuanhdo(repo: HealthOSRepository, payload: Mapping[str, Any],
                   approved_watch: WatchProvenance = frozenset(), *,
                   approved_types: frozenset[str] = frozenset(),
                   derived_watch: DerivedWatchProvenance = frozenset()) -> dict[str, Any]:
    """Stage every row and promote only unambiguous, verified observations.

    The response contains fixed reason names and counts only. Workouts remain
    in the encrypted batch until a validated workout mapping exists.
    """
    with repo._write_lock:
        return _ingest_tuanhdo_locked(repo, payload, approved_watch, approved_types,
                                      derived_watch)


def _ingest_tuanhdo_locked(repo: HealthOSRepository, payload: Mapping[str, Any],
                           approved_watch: WatchProvenance,
                           approved_types: frozenset[str],
                           derived_watch: DerivedWatchProvenance) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("records"), list):
        raise ValueError("records_required")
    parsed = parse_health_export(payload)
    if parsed.aggregation != "raw":
        raise ValueError("aggregation_not_raw")
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False)
    batch_key = sha256(canonical.encode("utf-8")).hexdigest()
    conn = repo.conn
    conn.executescript(_SCHEMA)
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT receipt_json FROM health_os_tuanhdo_batches WHERE batch_key=?",
            (batch_key,),
        ).fetchone()
        if previous is not None:
            receipt = _current_receipt(conn, batch_key, json.loads(previous["receipt_json"]))
            conn.commit()
            return {**receipt, "replayed": True}
        samples = {sample.record_index: sample for sample in parsed.samples}
        unknown_intervals = _unknown_interval_metrics(repo)
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
                    approved_types=approved_types, derived_watch=derived_watch)
            conn.execute(
                "INSERT INTO health_os_tuanhdo_records"
                "(batch_key,record_index,fingerprint,metric,interval_enc,disposition,reason) "
                "VALUES (?,?,?,?,?,?,?)",
                (batch_key, index, _fingerprint_token(repo, sample) if sample else None,
                 sample.metric if sample else None,
                 _interval_blob(repo, sample) if sample else None, disposition, reason),
            )
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
        conn.commit()
        repo._invalidate()
    except Exception:
        conn.rollback()
        repo._invalidate()
        raise
    return {**_current_receipt(conn, batch_key, receipt), "replayed": False}


def review_held_tuanhdo(repo: HealthOSRepository,
                        approved_watch: WatchProvenance, *,
                        batch_key: str,
                        approved_types: frozenset[str] = frozenset(),
                        derived_watch: DerivedWatchProvenance = frozenset()) -> dict[str, int | str]:
    """Explicit local review after an operator approves exact metadata tuples.

    There is deliberately no HTTP route for this action. The first retained
    copy can be promoted; later copies become duplicates only after that.
    """
    if not isinstance(batch_key, str) or not batch_key:
        raise ValueError("exact batch_key is required for held review")
    with repo._write_lock:
        return _review_held_tuanhdo_locked(repo, approved_watch, approved_types,
                                           derived_watch, batch_key)


def _review_held_tuanhdo_locked(repo: HealthOSRepository,
                                approved_watch: WatchProvenance,
                                approved_types: frozenset[str],
                                derived_watch: DerivedWatchProvenance,
                                batch_key: str) -> dict[str, int | str]:
    conn = repo.conn
    conn.executescript(_SCHEMA)
    parsed_batches: dict[str, dict[int, Sample]] = {}
    outcome: Counter[str] = Counter()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
                "SELECT 1 FROM health_os_tuanhdo_batches WHERE batch_key=?",
                (batch_key,)).fetchone() is None:
            raise ValueError("unknown batch_key")
        query = ("SELECT batch_key, record_index FROM health_os_tuanhdo_records "
                 "WHERE disposition='held' AND reason IN "
                 "('source_unverified','previous_held','metric_not_approved','provenance_incomplete')")
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
                    sample.record_index: sample for sample in parse_health_export(payload).samples
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
