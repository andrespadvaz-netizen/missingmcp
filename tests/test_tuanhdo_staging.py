"""Synthetic-only checks of TuAnhDo receipt, staging, core and Brief."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest
from starlette.testclient import TestClient

from missingmcp.config import load_config
from missingmcp import store
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp.health_os.service import build_health_os_app
from missingmcp.health_os.tuanhdo_dry_run import _HELD_QUANTITIES, _QUANTITIES
from missingmcp.health_os.tuanhdo_staging import (
    ingest_tuanhdo as _ingest_tuanhdo,
    review_held_tuanhdo as _review_held_tuanhdo,
)


WATCH = ["example.synthetic.watch", "example.synthetic.bundle", "Example Watch", "0"]
INGEST = {"Authorization": "Bearer " + "i" * 40}
READ = {"Authorization": "Bearer " + "r" * 40}
TEST_TYPES = frozenset(set(_QUANTITIES) | set(_HELD_QUANTITIES) |
                       {"sleepAnalysis", "appleStandHour"})


def ingest_tuanhdo(repo, payload, approved):
    return _ingest_tuanhdo(repo, payload, approved, approved_types=TEST_TYPES)


def review_held_tuanhdo(repo, approved):
    return _review_held_tuanhdo(repo, approved, approved_types=TEST_TYPES)


def client(tmp_path, monkeypatch, *, allow_watch=False, promote_types=None):
    if allow_watch:
        monkeypatch.setenv("HEALTH_OS_TUANHDO_WATCH_PROVENANCE", json.dumps([WATCH]))
        monkeypatch.setenv("HEALTH_OS_TUANHDO_PROMOTE_TYPES", json.dumps(promote_types if promote_types is not None else [
            "heartRate", "sleepAnalysis", "stepCount", "distanceWalkingRunning",
            "activeEnergyBurned", "bodyMass", "respiratoryRate",
        ]))
    else:
        monkeypatch.delenv("HEALTH_OS_TUANHDO_WATCH_PROVENANCE", raising=False)
        monkeypatch.delenv("HEALTH_OS_TUANHDO_PROMOTE_TYPES", raising=False)
    keys = {"GATEWAY_SECRET": "g" * 40, "HEALTH_OS_INGEST_KEY": "i" * 40,
            "HEALTH_OS_READ_KEY": "r" * 40, "HEALTH_OS_DATA_KEY": "d" * 40,
            "DB_PATH": str(tmp_path / "h.db"), "DATA_DIR": str(tmp_path)}
    return TestClient(build_health_os_app(load_config(keys)), raise_server_exceptions=False)


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
    reviewed = _review_held_tuanhdo(repo, frozenset({tuple(WATCH)}))
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
    reviewed = review_held_tuanhdo(repo, approved)
    assert reviewed == {"reviewed": 2, "promoted": 1, "duplicates_marked": 1, "still_held": 0}
    assert core_count(repo) == 1
    assert ingest_tuanhdo(repo, export([sample], date="A"), approved)["accepted"] == 1
    assert ingest_tuanhdo(repo, export([sample], date="B"), approved)["duplicates"] == 1
    assert review_held_tuanhdo(repo, approved)["reviewed"] == 0


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
    assert review_held_tuanhdo(repo, approved) == {
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
