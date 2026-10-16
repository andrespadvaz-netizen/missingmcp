"""Synthetic-only checks of TuAnhDo receipt, staging, core and Brief."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import sqlite3
from threading import Event
from zoneinfo import ZoneInfo

import pytest
from starlette.testclient import TestClient

from missingmcp.config import load_config
from missingmcp import store
from missingmcp.health_os.core import APPLE_HEALTH
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp.health_os.service import build_health_os_app
from missingmcp.health_os.tuanhdo_dry_run import _HELD_QUANTITIES, _QUANTITIES
from missingmcp.health_os.tuanhdo_staging import (
    ingest_tuanhdo as _ingest_tuanhdo,
    review_held_tuanhdo as _review_held_tuanhdo,
    reverse_tuanhdo_operation,
)


WATCH = ["example.synthetic.watch", "example.synthetic.bundle", "Example Watch", "0"]
INGEST = {"Authorization": "Bearer " + "i" * 40}
READ = {"Authorization": "Bearer " + "r" * 40}
TEST_TYPES = frozenset(set(_QUANTITIES) | set(_HELD_QUANTITIES) |
                       {"sleepAnalysis", "appleStandHour"})


def ingest_tuanhdo(repo, payload, approved):
    return _ingest_tuanhdo(repo, payload, approved, approved_types=TEST_TYPES)


def only_batch_key(repo):
    keys = [row[0] for row in repo.conn.execute(
        "SELECT batch_key FROM health_os_tuanhdo_batches")]
    assert len(keys) == 1
    return keys[0]


def review_held_tuanhdo(repo, approved, batch_key):
    return _review_held_tuanhdo(repo, approved, batch_key=batch_key,
                                approved_types=TEST_TYPES)


def client(tmp_path, monkeypatch, *, allow_watch=False, promote_types=None,
           derived_sources=None, sleep_mode="off"):
    if allow_watch:
        monkeypatch.setenv("HEALTH_OS_TUANHDO_WATCH_PROVENANCE", json.dumps([WATCH]))
        monkeypatch.setenv("HEALTH_OS_TUANHDO_PROMOTE_TYPES", json.dumps(promote_types if promote_types is not None else [
            "heartRate", "sleepAnalysis", "stepCount", "distanceWalkingRunning",
            "activeEnergyBurned", "bodyMass", "respiratoryRate",
        ]))
    else:
        monkeypatch.delenv("HEALTH_OS_TUANHDO_WATCH_PROVENANCE", raising=False)
        monkeypatch.delenv("HEALTH_OS_TUANHDO_PROMOTE_TYPES", raising=False)
    if derived_sources is None:
        monkeypatch.delenv("HEALTH_OS_TUANHDO_DERIVED_WATCH_SOURCES", raising=False)
    else:
        monkeypatch.setenv("HEALTH_OS_TUANHDO_DERIVED_WATCH_SOURCES", json.dumps(derived_sources))
    keys = {"GATEWAY_SECRET": "g" * 40, "HEALTH_OS_INGEST_KEY": "i" * 40,
            "HEALTH_OS_READ_KEY": "r" * 40, "HEALTH_OS_DATA_KEY": "d" * 40,
            "DB_PATH": str(tmp_path / "h.db"), "DATA_DIR": str(tmp_path),
            "HEALTH_OS_SLEEP_SERIES_MODE": sleep_mode}
    return TestClient(build_health_os_app(load_config(keys)), raise_server_exceptions=False)


def test_http_service_activates_approved_sleep_cutover(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True, sleep_mode="active")
    cut = datetime.fromisoformat("2026-10-16T00:00:00-06:00")
    body = export([row("sleepAnalysis", 3, cut + timedelta(hours=1),
                       cut + timedelta(hours=2))])
    receipt = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert receipt["accepted"] == 1
    assert receipt["reasons"] == {"approved_sleep_series_metadata_match": 1}
    months = app.get("/health-os/history", headers=READ).json()["history"]["months"]
    assert any(x.get("series") == "validated_2026_10_16" for x in months)


def row(kind, value, start, end=None, *, unit=None, source=True, detail=True):
    item = {"type": kind, "value": value, "startDate": start.isoformat(),
            "endDate": (end or start).isoformat(),
            "source": WATCH[0] if source else "example.synthetic.other",
            "sourceBundleId": WATCH[1] if source else "example.synthetic.other",
            "deviceDetail": {"model": WATCH[2], "softwareVersion": WATCH[3]} if detail else None}
    if unit:
        item["unit"] = unit
    if kind == "sleepAnalysis":
        item["valueName"] = {2: "awake", 3: "asleepCore", 4: "asleepDeep"}.get(value, "unknown")
    return item


def export(records, *, date="synthetic-one"):
    return {"exportDate": date, "aggregation": "raw", "records": records,
            "workouts": [], "units": {}}


def counts(path):
    db = sqlite3.connect(path)
    try:
        return tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                     ("health_os_tuanhdo_batches", "health_os_tuanhdo_records", "health_os_observations"))
    finally:
        db.close()


def local_repo():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return HealthOSRepository(conn, "synthetic-data-key" * 3)


def core_count(repo):
    return repo.conn.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0]


def test_unverified_receipt_is_staged_encrypted_and_not_in_brief(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch)
    moment = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([row("heartRate", 60, moment, unit="count/min"),
                   row("bodyMass", 70, moment, unit="kg")])
    assert app.post("/health-os/ingest/tuanhdo", json=body).status_code == 401
    response = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST)
    assert response.status_code == 200
    receipt = response.json()
    assert (receipt["accepted"], receipt["held"], receipt["rejected"]) == (0, 2, 0)
    assert counts(tmp_path / "h.db") == (1, 2, 0)
    db = sqlite3.connect(tmp_path / "h.db")
    encrypted = db.execute("SELECT payload_enc FROM health_os_tuanhdo_batches").fetchone()[0]
    db.close()
    assert "example.synthetic" not in encrypted
    brief = app.post("/health-os/brief", json={}, headers=READ).json()["brief"]
    assert all(item["valid_days"] == 0 for item in brief["coverage"])


def test_verified_export_reaches_brief_without_promoting_ordinary_pulse_to_rhr(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True)
    local_now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Mexico_City"))
    records = []
    for days_ago in range(1, 29):
        day = local_now.date() - timedelta(days=days_ago)
        end = datetime(day.year, day.month, day.day, 8, tzinfo=ZoneInfo("America/Mexico_City"))
        records.append(row("sleepAnalysis", 3, end - timedelta(hours=8), end))
    point = datetime.now(timezone.utc) - timedelta(days=1)
    records += [row("heartRate", 60, point, unit="count/min"),
                row("bodyMass", 70, point, unit="kg")]
    body = export(records)
    started = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    assert app.post("/health-os/transition", json={"started_at": started}, headers=INGEST).status_code == 200
    first = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert (first["accepted"], first["held"], first["duplicates"]) == (29, 1, 0)
    assert first["reasons"]["weight_requires_manual_source"] == 1
    brief = app.post("/health-os/brief", json={}, headers=READ).json()["brief"]
    coverage = {item["metric"]: item["valid_days"] for item in brief["coverage"]}
    assert coverage == {"sleep_duration": 28, "resting_heart_rate": 0}
    assert brief["decision"] == "ABSTENERSE"
    replay = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert replay["replayed"] is True
    assert counts(tmp_path / "h.db") == (1, 30, 29)
    reexport = app.post("/health-os/ingest/tuanhdo", json={**body, "exportDate": "synthetic-two"}, headers=INGEST).json()
    assert (reexport["accepted"], reexport["duplicates"], reexport["held"]) == (0, 29, 1)
    assert counts(tmp_path / "h.db") == (2, 60, 29)


def test_service_metric_scope_keeps_other_watch_types_held(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True, promote_types=["heartRate"])
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([
        row("heartRate", 60, point, unit="count/min"),
        row("heartRateVariabilitySDNN", 40, point, unit="ms"),
    ])
    receipt = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert (receipt["accepted"], receipt["held"], receipt["rejected"]) == (1, 1, 0)
    assert receipt["reasons"] == {"approved_metadata_match": 1, "metric_not_approved": 1}
    assert counts(tmp_path / "h.db") == (1, 2, 1)
    replay = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert replay["replayed"] is True
    assert counts(tmp_path / "h.db") == (1, 2, 1)


def test_service_empty_metric_scope_is_fail_closed(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True, promote_types=[])
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([row("heartRate", 60, point, unit="count/min")])
    receipt = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert (receipt["accepted"], receipt["held"]) == (0, 1)
    assert receipt["reasons"] == {"metric_not_approved": 1}
    assert counts(tmp_path / "h.db") == (1, 1, 0)
    replay = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert replay["replayed"] is True
    assert counts(tmp_path / "h.db") == (1, 1, 0)


def test_direct_ingest_and_review_require_explicit_metric_scope():
    repo = local_repo()
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([row("heartRate", 60, point, unit="count/min")])
    receipt = _ingest_tuanhdo(repo, body, frozenset({tuple(WATCH)}))
    assert receipt["accepted"] == 0
    assert receipt["reasons"] == {"metric_not_approved": 1}
    reviewed = _review_held_tuanhdo(repo, frozenset({tuple(WATCH)}),
                                    batch_key=only_batch_key(repo))
    assert reviewed["promoted"] == 0
    assert core_count(repo) == 0


def test_cross_batch_core_collision_and_same_batch_identity_ambiguity_hold(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True)
    end = datetime(2000, 1, 1, 8, tzinfo=timezone.utc)
    first = row("heartRate", 60, end, unit="count/min")
    assert app.post("/health-os/ingest/tuanhdo", json=export([first]), headers=INGEST).json()["accepted"] == 1
    collision = row("heartRate", 60, end - timedelta(minutes=1), end, unit="count/min")
    receipt = app.post("/health-os/ingest/tuanhdo", json=export([collision], date="two"), headers=INGEST).json()
    assert receipt["reasons"] == {"core_key_occupied": 1}
    assert counts(tmp_path / "h.db") == (2, 2, 1)
    other = row("heartRate", 65, end + timedelta(minutes=1), unit="count/min")
    receipt = app.post("/health-os/ingest/tuanhdo", json=export([other, other], date="three"), headers=INGEST).json()
    assert receipt["accepted"] == 0 and receipt["held"] == 2
    assert receipt["issues"] == {"indistinguishable_records": 1}
    assert counts(tmp_path / "h.db") == (3, 4, 1)


def test_incomplete_provenance_and_overlapping_sleep_are_held(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True)
    start = datetime(2000, 1, 1, 0, tzinfo=timezone.utc)
    rows = [row("heartRate", 60, start, unit="count/min", detail=False),
            row("sleepAnalysis", 3, start, start + timedelta(hours=1)),
            row("sleepAnalysis", 4, start + timedelta(minutes=30), start + timedelta(hours=2)),
            row("heartRate", 61, start + timedelta(hours=3), unit="count/min", source=False)]
    receipt = app.post("/health-os/ingest/tuanhdo", json=export(rows), headers=INGEST).json()
    assert receipt["accepted"] == 0 and receipt["held"] == 4
    assert receipt["reasons"] == {"provenance_incomplete": 1, "sleep_overlap": 2, "source_unverified": 1}
    assert counts(tmp_path / "h.db") == (1, 4, 0)


def test_device_optional_exception_requires_separate_approval_and_retained_review():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    types = frozenset({"restingHeartRate", "sleepAnalysis", "heartRate"})
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    rows = [
        row("restingHeartRate", 60, start, unit="count/min", detail=False),
        row("sleepAnalysis", 3, start, start + timedelta(hours=1), detail=False),
        row("sleepAnalysis", 2, start + timedelta(hours=1),
            start + timedelta(hours=2), detail=False),
        row("heartRate", 61, start + timedelta(hours=3),
            unit="count/min", detail=False),
        row("restingHeartRate", 62, start + timedelta(hours=4),
            unit="count/min", source=False),
        row("restingHeartRate", 63, start + timedelta(hours=5),
            unit="count/min", detail=False),
    ]
    rows[-1]["deviceDetail"] = {"model": WATCH[2]}  # partial detail cannot fall back
    initial = _ingest_tuanhdo(repo, export(rows), approved, approved_types=types)
    assert initial["accepted"] == 0 and initial["held"] == len(rows)
    assert core_count(repo) == 0
    derived = frozenset({tuple(WATCH[:2])})
    reviewed = _review_held_tuanhdo(repo, approved, batch_key=only_batch_key(repo),
                                    approved_types=types,
                                    derived_watch=derived)
    assert reviewed["promoted"] == 2
    assert core_count(repo) == 2
    reasons = dict(repo.conn.execute(
        "SELECT reason,count(*) FROM health_os_tuanhdo_records GROUP BY reason"))
    assert reasons == {"approved_derived_source_match": 2,
                       "awake_not_sleep": 1, "provenance_incomplete": 2,
                       "source_unverified": 1}
    assert _review_held_tuanhdo(repo, approved, batch_key=only_batch_key(repo),
                                approved_types=types,
                                derived_watch=derived)["promoted"] == 0


def test_derived_source_requires_full_watch_anchor_and_metric_scope():
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    payload = export([row("restingHeartRate", 60, start,
                          unit="count/min", detail=False)])
    derived = frozenset({tuple(WATCH[:2])})
    for watch, types in ((frozenset(), frozenset({"restingHeartRate"})),
                         (frozenset({tuple(WATCH)}), frozenset())):
        repo = local_repo()
        receipt = _ingest_tuanhdo(repo, payload, watch, approved_types=types,
                                  derived_watch=derived)
        assert receipt["accepted"] == 0 and core_count(repo) == 0


def test_explicit_device_conflicts_cannot_use_approved_source_pair():
    approved = frozenset({tuple(WATCH)})
    derived = frozenset({tuple(WATCH[:2])})
    kinds = (("restingHeartRate", "count/min", False),
             ("sleepAnalysis", None, False),
             ("heartRate", "count/min", True))
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    for kind, unit, has_detail in kinds:
        for conflicting_device in ("iPhone", "Garmin", "watch"):
            repo = local_repo()
            end = point + timedelta(hours=1) if kind == "sleepAnalysis" else point
            sample = row(kind, 3 if kind == "sleepAnalysis" else 60,
                         point, end, unit=unit, detail=has_detail)
            sample["device"] = conflicting_device
            receipt = _ingest_tuanhdo(repo, export([sample]), approved,
                                      approved_types=frozenset({kind}),
                                      derived_watch=derived)
            assert receipt["reasons"] == {"device_conflict": 1}
            assert core_count(repo) == 0
            assert _review_held_tuanhdo(repo, approved,
                                        batch_key=only_batch_key(repo),
                                        approved_types=frozenset({kind}),
                                        derived_watch=derived)["promoted"] == 0
        # An exact Watch label is consistent metadata, not an extra proof.
        repo = local_repo()
        end = point + timedelta(hours=1) if kind == "sleepAnalysis" else point
        sample = row(kind, 3 if kind == "sleepAnalysis" else 60,
                     point, end, unit=unit, detail=has_detail)
        sample["device"] = WATCH[0]
        receipt = _ingest_tuanhdo(repo, export([sample]), approved,
                                  approved_types=frozenset({kind}),
                                  derived_watch=derived)
        assert receipt["accepted"] == 1


def test_generic_watch_label_recovers_only_exactly_approved_pulse_and_hrv():
    repo = local_repo()
    watch = (WATCH[0], WATCH[1], "Watch", WATCH[3])
    approved = frozenset({watch})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    heart_rate = row("heartRate", 60, point, unit="count/min")
    hrv = row("heartRateVariabilitySDNN", 40, point, unit="ms")
    iphone = row("heartRate", 61, point + timedelta(minutes=1), unit="count/min")
    for sample in (heart_rate, hrv, iphone):
        sample["deviceDetail"]["model"] = "Watch"
    for sample in (heart_rate, hrv):
        sample["device"] = "Apple Watch"
    iphone["device"] = "iPhone"
    assert _ingest_tuanhdo(repo, export([heart_rate, hrv, iphone]),
                           frozenset())["held"] == 3
    batch_key = only_batch_key(repo)
    # These rows were labeled device_conflict by the previous release.
    repo.conn.execute(
        "UPDATE health_os_tuanhdo_records SET reason='device_conflict' "
        "WHERE batch_key=?", (batch_key,),
    )
    repo.conn.commit()
    reviewed = _review_held_tuanhdo(
        repo, approved, approved_types=frozenset({"heartRate", "heartRateVariabilitySDNN"}),
        batch_key=batch_key,
    )
    assert (reviewed["reviewed"], reviewed["promoted"], reviewed["still_held"]) == (3, 2, 1), [
        tuple(row) for row in repo.conn.execute(
            "SELECT metric,reason FROM health_os_tuanhdo_records ORDER BY record_index")
    ]
    assert core_count(repo) == 2
    assert repo.conn.execute(
        "SELECT count(*) FROM health_os_tuanhdo_promotion_links WHERE operation_id=?",
        (reviewed["operation_id"],),
    ).fetchone()[0] == 2
    assert repo.conn.execute(
        "SELECT reason FROM health_os_tuanhdo_records WHERE batch_key=? AND record_index=2",
        (batch_key,),
    ).fetchone()[0] == "device_conflict"

    wrong_source = row("heartRate", 60, point, unit="count/min", source=False)
    wrong_source["deviceDetail"]["model"] = "Watch"
    wrong_source["device"] = "Apple Watch"
    other_repo = local_repo()
    result = _ingest_tuanhdo(
        other_repo, export([wrong_source]), approved,
        approved_types=frozenset({"heartRate"}),
    )
    assert result["accepted"] == 0 and core_count(other_repo) == 0

    other_type = row("restingHeartRate", 60, point, unit="count/min")
    other_type["deviceDetail"]["model"] = "Watch"
    other_type["device"] = "Apple Watch"
    another_repo = local_repo()
    result = _ingest_tuanhdo(
        another_repo, export([other_type]), approved,
        approved_types=frozenset({"restingHeartRate"}),
    )
    assert result["reasons"] == {"device_conflict": 1}
    assert core_count(another_repo) == 0


def test_source_only_assurance_is_persisted_and_lowers_readiness_confidence():
    repo = local_repo()
    now = datetime.now(timezone.utc)
    repo.set_apple_transition(now - timedelta(days=40))
    observations = []
    for days_ago in range(1, 29):
        at = now - timedelta(days=days_ago)
        observations.extend((
            {"metric": "sleep_duration", "value": 480, "unit": "min",
             "observed_at": at.isoformat(), "provenance_id": f"sleep-{days_ago}",
             "device_source": "apple_watch", "confidence": "watch_source_only"},
            {"metric": "resting_heart_rate", "value": 60, "unit": "bpm",
             "observed_at": at.isoformat(), "provenance_id": f"rhr-{days_ago}",
             "device_source": "apple_watch", "confidence": "device_metadata"},
        ))
    assert repo.ingest("APPLE_HEALTH", observations, [])["accepted"] == 56
    brief = repo.brief(now)
    assert brief["decision"] == "INFORMATIVO"
    assert brief["confidence"] == "low"
    assert any("device metadata absent" in factor for factor in brief["factors"])


def test_held_review_does_not_touch_unapproved_type():
    repo = local_repo()
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([row("heartRate", 60, point, unit="count/min"),
                   row("heartRateVariabilitySDNN", 40, point, unit="ms")])
    initial = _ingest_tuanhdo(repo, body, frozenset(),
                              approved_types=frozenset({"heartRate"}))
    assert initial["reasons"] == {"source_unverified": 2}
    reviewed = _review_held_tuanhdo(repo, frozenset({tuple(WATCH)}),
                                    batch_key=only_batch_key(repo),
                                    approved_types=frozenset({"heartRate"}))
    assert len(reviewed.pop("operation_id")) == 32
    assert reviewed == {"reviewed": 1, "promoted": 1,
                        "duplicates_marked": 0, "still_held": 0}
    reasons = dict(repo.conn.execute(
        "SELECT metric,reason FROM health_os_tuanhdo_records"))
    assert reasons == {"heart_rate": "approved_metadata_match",
                       "hrv": "source_unverified"}


def test_scoped_reversal_preserves_ciphertext_history_and_other_batches():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    derived = frozenset({tuple(WATCH[:2])})
    now = datetime.now(timezone.utc)
    repo.set_apple_transition(now - timedelta(days=50))
    readiness_rows = []
    for days_ago in range(1, 29):
        end = now - timedelta(days=days_ago)
        readiness_rows.append(row("sleepAnalysis", 3, end - timedelta(hours=8),
                                  end, detail=False))
        readiness_rows.append(row("restingHeartRate", 60, end,
                                  unit="count/min", detail=False))
    payload = export(readiness_rows)
    initial = _ingest_tuanhdo(repo, payload, frozenset())
    assert initial["held"] == 56
    batch_key = repo.conn.execute(
        "SELECT batch_key FROM health_os_tuanhdo_batches").fetchone()[0]
    other = _ingest_tuanhdo(repo, export([row("heartRate", 65, now,
                                         unit="count/min")], date="other"),
                            approved, approved_types=frozenset({"heartRate"}))
    assert other["accepted"] == 1
    unrelated = export([row("heartRate", 66, now - timedelta(hours=1),
                            unit="count/min")], date="unrelated-held")
    assert _ingest_tuanhdo(repo, unrelated, frozenset())["held"] == 1
    types = frozenset({"sleepAnalysis", "restingHeartRate"})
    reviewed = _review_held_tuanhdo(repo, approved, approved_types=types,
                                    derived_watch=derived, batch_key=batch_key)
    assert reviewed["promoted"] == 56 and reviewed["reviewed"] == 56
    operation_id = reviewed["operation_id"]
    assert repo.conn.execute("SELECT count(*) FROM health_os_tuanhdo_promotion_links "
                             "WHERE operation_id=?", (operation_id,)).fetchone()[0] == 56
    brief_before = repo.brief(now)
    assert brief_before["decision"] == "INFORMATIVO"
    assert brief_before["confidence"] == "low"
    assert [entry["valid_days"] for entry in brief_before["coverage"]] == [28, 28]
    linked = dict(repo.conn.execute(
        "SELECT o.event_key,o.payload_enc FROM health_os_observations AS o "
        "JOIN health_os_tuanhdo_promotion_links AS l ON l.core_event_key=o.event_key "
        "WHERE l.operation_id=?", (operation_id,)))
    assert len(linked) == 56
    with pytest.raises(ValueError, match="expected count"):
        reverse_tuanhdo_operation(repo, operation_id, batch_key=batch_key, expected=55)
    with pytest.raises(ValueError, match="operation not found for batch"):
        reverse_tuanhdo_operation(repo, operation_id, batch_key="other", expected=56)
    result = reverse_tuanhdo_operation(repo, operation_id,
                                      batch_key=batch_key, expected=56)
    assert result == {"reversed": 56, "total_reversed": 56, "replayed": False}
    assert core_count(repo) == 1
    saved = dict(repo.conn.execute(
        "SELECT event_key,payload_enc FROM health_os_quarantine "
        "WHERE reason='operator_reversed_tuanhdo'"))
    assert saved == linked and all(WATCH[0] not in blob for blob in saved.values())
    assert repo.conn.execute("SELECT count(*) FROM health_os_tuanhdo_records "
                             "WHERE batch_key=? AND disposition='reversed'",
                             (batch_key,)).fetchone()[0] == 56
    assert repo.conn.execute("SELECT status FROM health_os_tuanhdo_promotion_ops "
                             "WHERE operation_id=?", (operation_id,)).fetchone()[0] == "reversed"
    brief_after = repo.brief(now)
    assert [entry["valid_days"] for entry in brief_after["coverage"]] == [0, 0]
    assert brief_after["decision"] == "ABSTENERSE"
    assert reverse_tuanhdo_operation(repo, operation_id,
                                     batch_key=batch_key, expected=56)["replayed"] is True
    replay = _ingest_tuanhdo(repo, payload, approved, approved_types=types,
                             derived_watch=derived)
    assert replay["replayed"] is True and replay["reversed"] == 56
    reexport = _ingest_tuanhdo(repo, export(readiness_rows, date="new-header"),
                               approved, approved_types=types, derived_watch=derived)
    assert reexport["accepted"] == 0
    assert reexport["reasons"] == {"previously_reversed": 56}
    same_core_key = dict(readiness_rows[1])
    same_core_key["device"] = WATCH[0]  # different fingerprint, identical core key
    reserved = _ingest_tuanhdo(repo, export([same_core_key], date="changed-metadata"),
                               approved, approved_types=types, derived_watch=derived)
    assert reserved["reasons"] == {"core_key_previously_linked": 1}
    native_retry = repo.ingest("APPLE_HEALTH", [{"metric": "resting_heart_rate",
        "value": 60, "unit": "bpm", "observed_at": same_core_key["endDate"],
        "provenance_id": "different-synthetic-path", "device_source": "apple_watch"}], [])
    assert native_retry["accepted"] == 0
    assert native_retry["rejected"] == ["operator_reversed_tuanhdo"]
    latest_batch = repo.conn.execute(
        "SELECT batch_key FROM health_os_tuanhdo_batches ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    assert _review_held_tuanhdo(repo, approved, batch_key=latest_batch,
                                approved_types=types,
                                derived_watch=derived)["promoted"] == 0
    assert core_count(repo) == 1


def test_reversal_mid_operation_failure_is_atomic_and_retryable():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    now = datetime.now(timezone.utc)
    rows = [row("heartRate", 60 + i, now - timedelta(minutes=i),
                unit="count/min") for i in range(2)]
    _ingest_tuanhdo(repo, export(rows), frozenset())
    batch_key = repo.conn.execute("SELECT batch_key FROM health_os_tuanhdo_batches").fetchone()[0]
    reviewed = _review_held_tuanhdo(repo, approved,
                                    approved_types=frozenset({"heartRate"}),
                                    batch_key=batch_key)
    operation_id = reviewed["operation_id"]
    assert core_count(repo) == 2
    repo.conn.execute("CREATE TEMP TRIGGER fail_second_quarantine "
                      "BEFORE INSERT ON health_os_quarantine "
                      "WHEN (SELECT count(*) FROM health_os_quarantine)>=1 "
                      "BEGIN SELECT RAISE(ABORT,'synthetic midway failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic midway failure"):
        reverse_tuanhdo_operation(repo, operation_id,
                                  batch_key=batch_key, expected=2)
    assert core_count(repo) == 2
    assert repo.conn.execute("SELECT count(*) FROM health_os_quarantine").fetchone()[0] == 0
    assert repo.conn.execute("SELECT count(*) FROM health_os_tuanhdo_records "
                             "WHERE disposition='accepted'").fetchone()[0] == 2
    assert repo.conn.execute("SELECT status FROM health_os_tuanhdo_promotion_ops "
                             "WHERE operation_id=?", (operation_id,)).fetchone()[0] == "committed"
    repo.conn.execute("DROP TRIGGER fail_second_quarantine")
    assert reverse_tuanhdo_operation(repo, operation_id,
                                     batch_key=batch_key, expected=2)["reversed"] == 2
    assert core_count(repo) == 0


def test_reversal_quarantine_collision_aborts_without_touching_core():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    _ingest_tuanhdo(repo, export([row("heartRate", 60, point,
                                     unit="count/min")]), frozenset())
    batch_key = repo.conn.execute("SELECT batch_key FROM health_os_tuanhdo_batches").fetchone()[0]
    op = _review_held_tuanhdo(repo, approved, approved_types=frozenset({"heartRate"}),
                              batch_key=batch_key)["operation_id"]
    core = repo.conn.execute("SELECT event_key,source,payload_enc "
                             "FROM health_os_observations").fetchone()
    repo.conn.execute("INSERT INTO health_os_quarantine"
                      "(source_table,event_key,source,reason,payload_enc) VALUES (?,?,?,?,?)",
                      ("health_os_observations", core["event_key"], core["source"],
                       "synthetic_collision", core["payload_enc"]))
    repo.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        reverse_tuanhdo_operation(repo, op, batch_key=batch_key, expected=1)
    assert core_count(repo) == 1
    assert repo.conn.execute("SELECT disposition FROM health_os_tuanhdo_records").fetchone()[0] == "accepted"
    assert repo.conn.execute("SELECT status FROM health_os_tuanhdo_promotion_ops "
                             "WHERE operation_id=?", (op,)).fetchone()[0] == "committed"


def test_direct_ingest_promotion_has_reversible_operation_link():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    result = _ingest_tuanhdo(repo, export([row("heartRate", 60, point,
                                              unit="count/min")]), approved,
                             approved_types=frozenset({"heartRate"}))
    assert result["accepted"] == 1
    operation = repo.conn.execute(
        "SELECT operation_id,batch_key,kind,promoted_count FROM health_os_tuanhdo_promotion_ops"
    ).fetchone()
    assert (operation["kind"], operation["promoted_count"]) == ("ingest", 1)
    assert reverse_tuanhdo_operation(repo, operation["operation_id"],
        batch_key=operation["batch_key"], expected=1)["reversed"] == 1
    assert core_count(repo) == 0


def test_reversal_and_direct_ingest_serialize_across_connections(tmp_path, monkeypatch):
    db_path = tmp_path / "reversal-race.db"
    def open_repo():
        conn = sqlite3.connect(db_path, timeout=3, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return HealthOSRepository(conn, "synthetic-data-key" * 3)

    reverser = open_repo()
    ingesting = open_repo()
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    receipt = _ingest_tuanhdo(
        reverser, export([row("heartRate", 60, point, unit="count/min")]),
        frozenset({tuple(WATCH)}), approved_types=frozenset({"heartRate"}),
    )
    assert receipt["accepted"] == 1
    operation = reverser.conn.execute(
        "SELECT operation_id,batch_key FROM health_os_tuanhdo_promotion_ops"
    ).fetchone()
    encrypted = reverser.conn.execute(
        "SELECT payload_enc FROM health_os_observations"
    ).fetchone()[0]
    observation = json.loads(store.decrypt(reverser.data_key, encrypted))
    checked_quarantine = Event()
    resume_ingest = Event()
    original_encrypt = store.encrypt

    def pause_after_guard(key, value):
        if value == json.dumps(observation, sort_keys=True):
            checked_quarantine.set()
            assert resume_ingest.wait(3)
        return original_encrypt(key, value)

    monkeypatch.setattr(store, "encrypt", pause_after_guard)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ingest_future = pool.submit(ingesting.ingest, APPLE_HEALTH, [observation], [])
            assert checked_quarantine.wait(3)
            reverse_future = pool.submit(
                reverse_tuanhdo_operation, reverser, operation["operation_id"],
                batch_key=operation["batch_key"], expected=1,
            )
            with pytest.raises(FutureTimeout):
                reverse_future.result(timeout=0.2)
            resume_ingest.set()
            assert ingest_future.result(timeout=3) == {
                "accepted": 0, "duplicates": 1, "rejected": [],
            }
            assert reverse_future.result(timeout=3)["reversed"] == 1
    finally:
        resume_ingest.set()
        ingesting.conn.close()
        reverser.conn.close()

    verifier = sqlite3.connect(db_path)
    try:
        assert verifier.execute("SELECT count(*) FROM health_os_observations").fetchone()[0] == 0
        assert verifier.execute("SELECT count(*) FROM health_os_quarantine WHERE reason=?",
                                ("operator_reversed_tuanhdo",)).fetchone()[0] == 1
    finally:
        verifier.close()


def test_cached_reader_sees_external_reversal_but_not_rolled_back_attempt(tmp_path):
    db_path = tmp_path / "external-reversal.db"
    def open_repo():
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return HealthOSRepository(conn, "synthetic-data-key" * 3)

    writer = open_repo()
    reader = open_repo()
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    now = point + timedelta(hours=1)
    try:
        writer.set_apple_transition(point - timedelta(days=1))
        assert _ingest_tuanhdo(
            writer, export([row("heartRate", 60, point, unit="count/min")]),
            frozenset({tuple(WATCH)}), approved_types=frozenset({"heartRate"}),
        )["accepted"] == 1
        operation = writer.conn.execute(
            "SELECT operation_id,batch_key FROM health_os_tuanhdo_promotion_ops"
        ).fetchone()
        writer.conn.execute(
            "CREATE TRIGGER abort_reversal BEFORE INSERT ON health_os_quarantine "
            "BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END"
        )
        writer.conn.commit()
        assert reader.quality(now)["coverage_days"] == 1
        cached = reader._scan_cache
        with pytest.raises(sqlite3.IntegrityError, match="synthetic rollback"):
            reverse_tuanhdo_operation(
                writer, operation["operation_id"], batch_key=operation["batch_key"],
                expected=1,
            )
        assert reader.quality(now)["coverage_days"] == 1
        assert reader._scan_cache is cached
        writer.conn.execute("DROP TRIGGER abort_reversal")
        writer.conn.commit()
        assert reverse_tuanhdo_operation(
            writer, operation["operation_id"], batch_key=operation["batch_key"],
            expected=1,
        )["reversed"] == 1
        assert reader.quality(now)["coverage_days"] == 0
        assert reader._scan_cache is not cached
    finally:
        reader.conn.close()
        writer.conn.close()


def test_derived_source_service_config_is_closed_and_requires_watch_anchor(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True,
                 promote_types=["restingHeartRate"],
                 derived_sources=[WATCH[:2]])
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([row("restingHeartRate", 60, point,
                       unit="count/min", detail=False)])
    receipt = app.post("/health-os/ingest/tuanhdo", json=body, headers=INGEST).json()
    assert receipt["reasons"] == {"approved_derived_source_match": 1}
    assert counts(tmp_path / "h.db") == (1, 1, 1)
    with pytest.raises(ValueError, match="derived source"):
        client(tmp_path, monkeypatch, allow_watch=False,
               derived_sources=[WATCH[:2]])


def test_wrong_export_shape_and_oversize_body_never_enter_staging(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True)
    assert app.post("/health-os/ingest/tuanhdo", json=export([]), headers=INGEST).status_code == 200
    wrong_shape = {"data": {"metrics": []}, "aggregation": "raw"}
    assert app.post("/health-os/ingest/tuanhdo", json=wrong_shape, headers=INGEST).status_code == 400
    # The Health Auto Export shape is not silently treated as an empty export.
    assert counts(tmp_path / "h.db") == (1, 0, 0)
    nonraw = {"records": [], "workouts": [], "aggregation": "summary"}
    assert app.post("/health-os/ingest/tuanhdo", json=nonraw, headers=INGEST).status_code == 400
    assert counts(tmp_path / "h.db") == (1, 0, 0)
    oversize = b"x" * (16 * 1024 * 1024 + 1)
    assert app.post("/health-os/ingest/tuanhdo", content=oversize, headers=INGEST).status_code == 413
    assert counts(tmp_path / "h.db") == (1, 0, 0)


def test_receipt_never_reflects_rejected_health_value(tmp_path, monkeypatch):
    app = client(tmp_path, monkeypatch, allow_watch=True)
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    submitted = row("heartRate", "SENSITIVE-SYNTHETIC-VALUE", point, unit="count/min")
    response = app.post("/health-os/ingest/tuanhdo", json=export([submitted]), headers=INGEST)
    assert response.status_code == 200
    assert response.json()["reasons"] == {"value_invalid": 1}
    assert response.json()["issues"] == {"value_invalid": 1}
    assert "SENSITIVE-SYNTHETIC-VALUE" not in response.text
    assert counts(tmp_path / "h.db") == (1, 1, 0)


def test_additive_intervals_across_batches_hold_in_both_orders():
    approved = frozenset({tuple(WATCH)})
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    for kind, unit, first_value, second_value in (
        ("sleepAnalysis", None, 3, 4), ("stepCount", "count", 100, 110),
    ):
        first = row(kind, first_value, start, start + timedelta(hours=2), unit=unit)
        second = row(kind, second_value, start + timedelta(hours=1),
                     start + timedelta(hours=3), unit=unit)
        for before, after in ((first, second), (second, first)):
            repo = local_repo()
            assert ingest_tuanhdo(repo, export([before], date="A"), approved)["accepted"] == 1
            receipt = ingest_tuanhdo(repo, export([after], date="B"), approved)
            assert receipt["accepted"] == 0 and receipt["reasons"] == {"interval_overlap_existing": 1}
            assert core_count(repo) == 1
            assert ingest_tuanhdo(repo, export([after], date="B"), approved)["replayed"] is True
            assert core_count(repo) == 1


def test_held_provenance_requires_explicit_review_then_is_idempotent():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    sample = row("heartRate", 60, point, unit="count/min")
    initial = ingest_tuanhdo(repo, export([sample], date="A"), frozenset())
    assert initial["held"] == 1 and core_count(repo) == 0
    resent = ingest_tuanhdo(repo, export([sample], date="B"), approved)
    assert resent["duplicates"] == 0 and resent["reasons"] == {"previous_held": 1}
    assert core_count(repo) == 0
    batches = [record[0] for record in repo.conn.execute(
        "SELECT batch_key FROM health_os_tuanhdo_batches ORDER BY rowid")]
    first_review = review_held_tuanhdo(repo, approved, batches[0])
    second_review = review_held_tuanhdo(repo, approved, batches[1])
    assert (first_review["reviewed"], first_review["promoted"]) == (1, 1)
    assert (second_review["reviewed"], second_review["duplicates_marked"]) == (1, 1)
    assert core_count(repo) == 1
    assert ingest_tuanhdo(repo, export([sample], date="A"), approved)["accepted"] == 1
    assert ingest_tuanhdo(repo, export([sample], date="B"), approved)["duplicates"] == 1
    assert review_held_tuanhdo(repo, approved, batches[0])["reviewed"] == 0


def test_concurrent_exact_retries_promote_once():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([row("heartRate", 60, point, unit="count/min")])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: ingest_tuanhdo(repo, body, approved), range(4)))
    assert sorted(result["replayed"] for result in results) == [False, True, True, True]
    assert core_count(repo) == 1


def test_two_connections_racing_same_batch_are_idempotent(tmp_path):
    path = str(tmp_path / "shared.db")
    first = HealthOSRepository(store.init_db(path), "synthetic-data-key" * 3)
    second = HealthOSRepository(store.init_db(path), "synthetic-data-key" * 3)
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    body = export([row("heartRate", 60, point, unit="count/min")])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda repo: ingest_tuanhdo(repo, body, approved),
                                (first, second)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert core_count(first) == 1


def test_unknown_legacy_interval_blocks_additive_promotion():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    native = {"metric": "sleep_duration", "value": 60, "unit": "min",
              "observed_at": (start + timedelta(hours=1)).isoformat(),
              "provenance_id": "native-synthetic", "device_source": "apple_watch"}
    assert repo.ingest("APPLE_HEALTH", [native], [])["accepted"] == 1
    candidate = row("sleepAnalysis", 3, start + timedelta(days=1),
                    start + timedelta(days=1, hours=1))
    receipt = ingest_tuanhdo(repo, export([candidate]), approved)
    assert receipt["accepted"] == 0
    assert receipt["reasons"] == {"legacy_interval_unknown": 1}
    assert core_count(repo) == 1


@pytest.mark.parametrize("kind,unit", [
    ("stepCount", "count"),
    ("distanceWalkingRunning", "km"),
    ("activeEnergyBurned", "kcal"),
    ("appleExerciseTime", "min"),
    ("appleStandTime", "min"),
    ("flightsClimbed", "count"),
])
def test_additive_points_remain_encrypted_and_held_across_reexport_and_review(kind, unit):
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    first = export([row(kind, 100, point, unit=unit)], date="A")
    second = export([row(kind, 200, point, unit=unit)], date="B")
    for body, sources in ((first, frozenset()), (second, approved)):
        receipt = ingest_tuanhdo(repo, body, sources)
        assert (receipt["accepted"], receipt["held"], receipt["rejected"]) == (0, 1, 0)
        assert receipt["reasons"] == {"additive_point_no_duration": 1}
        assert receipt["issues"] == {"additive_point_no_duration": 1}
    assert core_count(repo) == 0
    assert ingest_tuanhdo(repo, first, approved)["replayed"] is True
    assert ingest_tuanhdo(repo, second, approved)["replayed"] is True
    for batch in repo.conn.execute("SELECT batch_key FROM health_os_tuanhdo_batches"):
        reviewed = review_held_tuanhdo(repo, approved, batch[0])
        assert len(reviewed.pop("operation_id")) == 32
        assert reviewed == {
            "reviewed": 0, "promoted": 0, "duplicates_marked": 0, "still_held": 0}
    assert core_count(repo) == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM health_os_tuanhdo_batches").fetchone()[0] == 2
    assert repo.conn.execute("SELECT COUNT(*) FROM health_os_tuanhdo_records").fetchone()[0] == 2
    encrypted = [row["payload_enc"] for row in repo.conn.execute(
        "SELECT payload_enc FROM health_os_tuanhdo_batches ORDER BY rowid")]
    assert all(WATCH[0] not in blob for blob in encrypted)
    assert [json.loads(store.decrypt(repo.data_key, blob)) for blob in encrypted] == [first, second]


def test_diary_rhr_sleep_missing_device_hold_while_verified_hrv_and_pulse_stay_distinct():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    rows = [
        row("restingHeartRate", 55, point, unit="count/min", detail=False),
        row("sleepAnalysis", 3, point, point + timedelta(hours=1), detail=False),
        row("walkingHeartRateAverage", 90, point, unit="count/min", detail=False),
        row("heartRateVariabilitySDNN", 45, point, unit="ms"),
        row("heartRate", 60, point, unit="count/min"),
        row("oxygenSaturation", 0.97, point, unit="%"),
    ]
    receipt = ingest_tuanhdo(repo, export(rows), approved)
    assert receipt["accepted"] == 2 and receipt["held"] == 4
    assert receipt["reasons"] == {
        "approved_metadata_match": 2,
        "provenance_incomplete": 3,
        "percentage_semantics_unverified": 1,
    }
    assert receipt["issues"] == {"provenance_incomplete": 3}
    health = repo._scan()[0]
    assert {sample.metric for sample in health._observations.values()} == {"hrv", "heart_rate"}


@pytest.mark.parametrize("kind,unit", [
    ("appleExerciseTime", "min"),
    ("appleStandTime", "min"),
    ("flightsClimbed", "count"),
])
def test_new_additive_types_block_interbatch_overlap(kind, unit):
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    first = row(kind, 1, start, start + timedelta(hours=1), unit=unit)
    second = row(kind, 2, start + timedelta(minutes=30),
                 start + timedelta(hours=2), unit=unit)
    assert ingest_tuanhdo(repo, export([first], date="A"), approved)["accepted"] == 1
    receipt = ingest_tuanhdo(repo, export([second], date="B"), approved)
    assert receipt["reasons"] == {"interval_overlap_existing": 1}
    assert core_count(repo) == 1


def test_instant_respiratory_rate_is_not_treated_as_additive_point():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    receipt = ingest_tuanhdo(repo, export([
        row("respiratoryRate", 14, point, unit="count/min"),
        row("oxygenSaturation", 97, point, unit="%"),
    ]), approved)
    assert receipt["reasons"] == {
        "approved_metadata_match": 1, "percentage_semantics_unverified": 1,
    }
    health = repo._scan()[0]
    assert {sample.metric for sample in health._observations.values()} == {"respiratory_rate"}


def test_rejected_diary_rows_keep_fixed_per_record_reasons_in_staging():
    repo = local_repo()
    approved = frozenset({tuple(WATCH)})
    point = datetime(2000, 1, 1, tzinfo=timezone.utc)
    unknown_sleep = row("sleepAnalysis", 9, point, point + timedelta(hours=1))
    unknown_sleep["valueName"] = "unknown"
    unknown_type = row("exampleUnknownType", 1, point, unit="count")
    wrong_unit = row("heartRateVariabilitySDNN", 45, point, unit="bpm")
    receipt = ingest_tuanhdo(repo, export([unknown_sleep, unknown_type, wrong_unit]), approved)
    assert receipt["accepted"] == 0 and receipt["rejected"] == 3
    assert receipt["reasons"] == {
        "sleep_stage_unsupported": 1, "type_unsupported": 1, "unit_unsupported": 1,
    }
    assert core_count(repo) == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM health_os_tuanhdo_records").fetchone()[0] == 3
