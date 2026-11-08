"""Synthetic regressions for the scoped legacy-interval promotion guard."""
from datetime import datetime, timedelta, timezone
import hmac
import json
import sqlite3

import pytest

from missingmcp import store
from missingmcp.health_os.core import APPLE_HEALTH, HealthOS
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp.health_os import tuanhdo_staging as staging

KEY = "synthetic-scan-test-key" * 3
WATCH = ("synthetic.watch", "synthetic.bundle", "Synthetic Watch", "1")
APPROVED = frozenset({WATCH})
START = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
SPECS = {
    "sleepAnalysis": ("sleep_duration", 3, None),
    "stepCount": ("steps", 10, "count"),
    "distanceWalkingRunning": ("distance", 0.01, "km"),
    "activeEnergyBurned": ("active_energy", 1, "kcal"),
    "appleExerciseTime": ("exercise_time", 1, "min"),
    "appleStandTime": ("stand_time", 1, "min"),
    "flightsClimbed": ("flights_climbed", 1, "count"),
    "heartRate": ("heart_rate", 65, "bpm"),
}


def make_repo(sleep="off", activity="off", conn=None):
    if conn is None:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if "paused" in (sleep, activity):
        HealthOSRepository(conn, KEY, sleep_series_mode="active" if sleep == "paused" else sleep,
                           activity_series_mode="active" if activity == "paused" else activity,
                           activity_watch_identity=WATCH if activity != "off" else None)
    repo = HealthOSRepository(conn, KEY, sleep_series_mode=sleep,
                              activity_series_mode=activity,
                              activity_watch_identity=WATCH if activity != "off" else None)
    conn.executescript(staging._SCHEMA)
    conn.execute(staging._DISPOSITION_INDEX)
    conn.commit()
    return repo


def record(kind, start=START, *, value=None, source=True, point=False):
    metric, default, unit = SPECS[kind]
    item = {"type": kind, "value": default if value is None else value,
            "startDate": start.isoformat(),
            "endDate": (start if point or kind == "heartRate" else start + timedelta(minutes=1)).isoformat(),
            "source": WATCH[0] if source else "synthetic.other",
            "sourceBundleId": WATCH[1],
            "deviceDetail": {"model": WATCH[2], "softwareVersion": WATCH[3]}}
    if kind == "sleepAnalysis":
        item["valueName"] = "asleepCore"
    if unit:
        item["unit"] = unit
    return item


def payload(rows, name="synthetic"):
    return {"aggregation": "raw", "records": rows, "workouts": [],
            "units": {}, "exportDate": name}


def add_legacy(repo, metrics, *, known=False, corrupt_last=False):
    """Insert only synthetic historical evidence; never open a real database."""
    for index, metric in enumerate(metrics):
        provenance = f"synthetic-history-{index}"
        raw = {"metric": metric, "value": 1, "unit": "count",
               "observed_at": (START - timedelta(days=30, seconds=index)).isoformat(),
               "provenance_id": provenance, "device_source": "apple_watch"}
        repo.conn.execute(
            "INSERT INTO health_os_observations(event_key,source,payload_enc) VALUES (?,?,?)",
            (provenance, APPLE_HEALTH, store.encrypt(KEY, json.dumps(raw))))
        if known:
            repo.conn.execute("INSERT OR IGNORE INTO health_os_tuanhdo_batches VALUES ('synthetic-history','synthetic','{}',NULL)")
            token = hmac.new(KEY.encode(), provenance.encode("ascii"), "sha256").hexdigest()
            interval = {"start": (START - timedelta(days=2, minutes=index + 1)).isoformat(),
                        "end": (START - timedelta(days=2, minutes=index)).isoformat()}
            repo.conn.execute("INSERT INTO health_os_tuanhdo_records VALUES (?,?,?,?,?,?,?)",
                              ("synthetic-history", index, token, metric,
                               store.encrypt(KEY, json.dumps(interval)), "accepted", "approved_metadata_match"))
    if corrupt_last:
        repo.conn.execute("INSERT INTO health_os_observations VALUES ('synthetic-corrupt',?,'broken',NULL)",
                          (APPLE_HEALTH,))
    repo.conn.commit()


def submit(repo, rows, *, types=None, name="synthetic"):
    return staging.ingest_tuanhdo(
        repo, payload(rows, name), APPROVED,
        approved_types=frozenset(SPECS if types is None else types),
        additive_watch_selection={metric: WATCH for metric in HealthOS.ACTIVITY_SERIES_METRICS}
        if repo.activity_series_start is not None else {})


def scan_queries(trace):
    return [sql for sql in trace if "SELECT payload_enc FROM health_os_observations WHERE source=" in sql]


@pytest.mark.parametrize("sleep", ["active", "paused"])
@pytest.mark.parametrize("activity", ["active", "paused"])
def test_current_series_never_reads_legacy_scan(sleep, activity):
    repo = make_repo(sleep, activity)
    add_legacy(repo, ["steps", "sleep_duration"], corrupt_last=True)
    trace = []
    repo.conn.set_trace_callback(trace.append)
    rows = [record(kind) for kind in (
        "sleepAnalysis", "stepCount", "distanceWalkingRunning", "activeEnergyBurned",
        "appleExerciseTime", "heartRate")]
    receipt = submit(repo, rows)
    assert not scan_queries(trace)
    assert receipt["accepted"] == 1 + int(sleep == "active") + 4 * int(activity == "active")
    assert receipt["held"] == int(sleep == "paused") + 4 * int(activity == "paused")


@pytest.mark.parametrize("rows,types", [
    ([], []),
    ([record("heartRate")], ["heartRate"]),
    ([record("flightsClimbed")], []),
    ([record("appleStandTime", point=True)], ["appleStandTime"]),
])
def test_no_reachable_legacy_guard_skips_scan(rows, types):
    repo = make_repo()
    add_legacy(repo, [], corrupt_last=True)
    trace = []
    repo.conn.set_trace_callback(trace.append)
    submit(repo, rows, types=types)
    assert not scan_queries(trace)


def test_empty_required_metrics_does_not_query_database():
    repo = make_repo()
    trace = []
    repo.conn.set_trace_callback(trace.append)
    assert staging._unknown_interval_metrics(repo, required_metrics=frozenset()) == frozenset()
    assert not trace


@pytest.mark.parametrize("required,expected,calls", [
    ({"steps"}, {"steps"}, 1),
    ({"steps", "stand_time"}, {"steps", "stand_time"}, 2),
    ({"sleep_duration"}, staging._ADDITIVE, 3),
])
def test_early_stop_and_corruption_remain_conservative(monkeypatch, required, expected, calls):
    repo = make_repo()
    add_legacy(repo, ["steps", "stand_time"], corrupt_last=True)
    original, seen = store.decrypt, []
    def tracked(secret, blob):
        seen.append(blob)
        return original(secret, blob)
    monkeypatch.setattr(store, "decrypt", tracked)
    assert staging._unknown_interval_metrics(repo, required_metrics=frozenset(required)) == expected
    assert len(seen) == calls


def test_relevant_legacy_corruption_blocks_before_early_stop():
    repo = make_repo()
    add_legacy(repo, [], corrupt_last=True)
    receipt = submit(repo, [record("sleepAnalysis")])
    assert receipt["reasons"] == {"legacy_interval_unknown": 1}


def test_mixed_current_series_and_legacy_blockers():
    repo = make_repo("active", "active")
    add_legacy(repo, ["stand_time"], corrupt_last=True)
    receipt = submit(repo, [record("sleepAnalysis"), record("stepCount"), record("appleStandTime")])
    assert receipt["accepted"] == 2
    assert receipt["reasons"]["legacy_interval_unknown"] == 1


def test_legacy_without_blocker_still_scans_every_row(monkeypatch):
    repo = make_repo()
    add_legacy(repo, ["steps"] * 257, known=True)
    original, seen = store.decrypt, []
    def tracked(secret, blob):
        seen.append(blob)
        return original(secret, blob)
    monkeypatch.setattr(store, "decrypt", tracked)
    assert staging._unknown_interval_metrics(repo, required_metrics=frozenset({"steps"})) == frozenset()
    assert len(seen) == 257


def test_default_review_contract_still_blocks_all_on_corruption():
    repo = make_repo()
    add_legacy(repo, ["steps", "stand_time"], corrupt_last=True)
    assert staging._unknown_interval_metrics(repo) == staging._ADDITIVE


@pytest.mark.parametrize("mode", ["active", "paused"])
def test_activity_cut_and_source_policy_not_released(mode):
    repo = make_repo("active", mode)
    rows = [record("stepCount", START - timedelta(days=50)),
            record("stepCount", START + timedelta(minutes=5), source=False)]
    receipt = submit(repo, rows)
    assert receipt["accepted"] == 0
    assert receipt["held"] == 2
    assert receipt["reasons"] == {
        "activity_before_series_cut" if mode == "active" else "activity_series_paused": 1,
        "source_not_selected": 1,
    }


def test_explicit_held_review_still_checks_legacy_corruption():
    repo = make_repo()
    add_legacy(repo, [], corrupt_last=True)
    receipt = submit(repo, [record("sleepAnalysis")], types=[])
    assert receipt["reasons"] == {"metric_not_approved": 1}
    batch_key = repo.conn.execute("SELECT batch_key FROM health_os_tuanhdo_batches").fetchone()[0]
    reviewed = staging.review_held_tuanhdo(repo, APPROVED, batch_key=batch_key,
                                         approved_types=frozenset({"sleepAnalysis"}))
    assert reviewed["reviewed"] == 1
    assert reviewed["still_held"] == 1
    assert reviewed["promoted"] == 0
    row = repo.conn.execute("SELECT disposition,reason FROM health_os_tuanhdo_records").fetchone()
    assert tuple(row) == ("held", "legacy_interval_unknown")


def test_current_series_replay_overlap_reversal_remain_guarded():
    repo = make_repo("active", "active")
    row = record("stepCount")
    first = submit(repo, [row], name="first")
    assert first["accepted"] == 1
    replay = submit(repo, [row], name="first")
    assert replay["replayed"] is True
    duplicate = submit(repo, [row], name="second")
    assert duplicate["duplicates"] == 1
    overlapping = record("stepCount", START + timedelta(seconds=30), value=11)
    assert submit(repo, [overlapping], name="overlap")["reasons"] == {"interval_overlap_existing": 1}
    operation = repo.conn.execute("SELECT operation_id,batch_key FROM health_os_tuanhdo_promotion_ops WHERE promoted_count=1").fetchone()
    staging.reverse_tuanhdo_operation(repo, operation["operation_id"], batch_key=operation["batch_key"], expected=1)
    assert submit(repo, [row], name="third")["reasons"] == {"previously_reversed": 1}
