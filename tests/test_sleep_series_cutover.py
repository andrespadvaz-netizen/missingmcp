"""Synthetic checks for the operator-approved, separate sleep series."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from missingmcp import store
from missingmcp.config import load_config
from missingmcp.health_os.persistence import HealthOSRepository, SLEEP_SERIES_START
from missingmcp.health_os.tuanhdo_staging import (
    ingest_tuanhdo, review_held_tuanhdo, reverse_tuanhdo_operation,
)


WATCH = ("synthetic.watch", "synthetic.bundle", "Synthetic Watch", "1")
APPROVED = frozenset({WATCH})
TYPES = frozenset({"sleepAnalysis", "heartRate"})
KEY = "synthetic-data-key" * 3


def test_mode_configuration_is_explicit_and_validated():
    env = {"GATEWAY_SECRET": "g" * 40, "HEALTH_OS_SLEEP_SERIES_MODE": "active"}
    assert load_config(env).health_os_sleep_series_mode == "active"
    assert load_config({**env, "HEALTH_OS_SLEEP_SERIES_MODE": "paused"}).health_os_sleep_series_mode == "paused"
    with pytest.raises(ValueError, match="HEALTH_OS_SLEEP_SERIES_MODE"):
        load_config({**env, "HEALTH_OS_SLEEP_SERIES_MODE": "unknown"})


def test_activation_is_sticky_even_before_first_new_sleep(tmp_path):
    path = tmp_path / "health.db"
    active = repo(path)
    marker = active.conn.execute(
        "SELECT payload_enc FROM health_os_state WHERE state_key=?",
        ("sleep_series_activation_2026_10_16",),
    ).fetchone()
    assert marker is not None
    assert SLEEP_SERIES_START.isoformat() not in marker[0]  # encrypted at rest
    assert active.conn.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0] == 0
    active.conn.close()
    with pytest.raises(ValueError, match="use paused"):
        repo(path, "off")
    with pytest.raises(ValueError, match="use paused"):
        HealthOSRepository(store.init_db(str(path)), KEY)  # absent mode is off
    with pytest.raises(ValueError, match="invalid sleep series mode"):
        repo(path, "invalid")
    paused = repo(path, "paused")
    assert paused.brief(SLEEP_SERIES_START + timedelta(days=1))["coverage"][0]["valid_days"] == 0
    paused.conn.close()


def test_invalid_activation_marker_fails_closed(tmp_path):
    path = tmp_path / "health.db"
    active = repo(path)
    active.conn.execute(
        "UPDATE health_os_state SET payload_enc=? WHERE state_key=?",
        ("invalid-encrypted-marker", "sleep_series_activation_2026_10_16"),
    )
    active.conn.commit()
    active.conn.close()
    for mode in ("off", "active", "paused"):
        with pytest.raises(ValueError, match="marker invalid"):
            repo(path, mode)


def test_missing_marker_before_any_new_sleep_still_fails_closed(tmp_path):
    path = tmp_path / "health.db"
    active = repo(path)
    active.conn.execute("DELETE FROM health_os_state WHERE state_key=?",
                        ("sleep_series_activation_2026_10_16",))
    active.conn.commit()
    active.conn.close()
    for mode in ("off", "active", "paused"):
        with pytest.raises(ValueError, match="trace incomplete"):
            repo(path, mode)


def test_missing_marker_after_full_reversal_is_caught_by_audit(tmp_path):
    path = tmp_path / "health.db"
    active = repo(path)
    active.set_apple_transition(SLEEP_SERIES_START - timedelta(days=40))
    legacy_sleep(active, SLEEP_SERIES_START - timedelta(days=1))
    start = SLEEP_SERIES_START + timedelta(hours=1)
    assert submit(active, [segment(start, start + timedelta(hours=1))], "new")["accepted"] == 1
    batch_key, operation_id = active.conn.execute(
        "SELECT batch_key,operation_id FROM health_os_tuanhdo_promotion_links"
    ).fetchone()
    assert reverse_tuanhdo_operation(active, operation_id, batch_key=batch_key, expected=1)["reversed"] == 1
    active.conn.execute("DELETE FROM health_os_state WHERE state_key=?",
                        ("sleep_series_activation_2026_10_16",))
    active.conn.commit()
    assert active.conn.execute("SELECT cutover FROM health_os_sleep_series_audit").fetchone()[0] == (
        SLEEP_SERIES_START.isoformat())
    active.conn.close()
    for mode in ("off", "active", "paused"):
        with pytest.raises(ValueError, match="trace incomplete"):
            repo(path, mode)


def test_old_off_instance_fails_reads_and_writes_after_other_instance_activates(tmp_path):
    path = tmp_path / "health.db"
    old = repo(path, "off")
    old.set_apple_transition(SLEEP_SERIES_START - timedelta(days=40))
    legacy_sleep(old, SLEEP_SERIES_START - timedelta(days=1))
    now = SLEEP_SERIES_START + timedelta(days=1)
    assert {x["metric"]: x["valid_days"] for x in old.brief(now)["coverage"]}[
        "sleep_duration"] == 1  # populates the old instance's read cache
    active = repo(path, "active")
    assert {x["metric"]: x["valid_days"] for x in active.brief(now)["coverage"]}[
        "sleep_duration"] == 0
    with pytest.raises(ValueError, match="use paused"):
        old.brief(now)
    with pytest.raises(ValueError, match="use paused"):
        old.ingest("APPLE_HEALTH", [], [])
    with pytest.raises(ValueError, match="use paused"):
        submit(old, [segment(SLEEP_SERIES_START + timedelta(hours=1),
                             SLEEP_SERIES_START + timedelta(hours=2))], "old-process")
    with pytest.raises(ValueError, match="use paused"):
        old.set_apple_transition(SLEEP_SERIES_START)
    old.conn.close()
    active.conn.close()


def test_reversing_every_new_promotion_cannot_restore_legacy_mix(tmp_path):
    path = tmp_path / "health.db"
    active = repo(path)
    active.set_apple_transition(SLEEP_SERIES_START - timedelta(days=1))
    legacy_sleep(active, SLEEP_SERIES_START - timedelta(days=1))
    start = SLEEP_SERIES_START + timedelta(hours=1)
    assert submit(active, [segment(start, start + timedelta(hours=1))], "new")["accepted"] == 1
    batch_key, operation_id = active.conn.execute(
        "SELECT batch_key,operation_id FROM health_os_tuanhdo_promotion_links"
    ).fetchone()
    result = reverse_tuanhdo_operation(active, operation_id, batch_key=batch_key, expected=1)
    assert result["reversed"] == 1
    assert active.conn.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0] == 1
    assert active.conn.execute("SELECT COUNT(*) FROM health_os_quarantine").fetchone()[0] == 1
    assert active.conn.execute("SELECT disposition FROM health_os_tuanhdo_records").fetchone()[0] == "reversed"
    active.conn.close()
    for mode in ("off", "invalid"):
        with pytest.raises(ValueError):
            repo(path, mode)
    with pytest.raises(ValueError, match="use paused"):
        HealthOSRepository(store.init_db(str(path)), KEY)
    paused = repo(path, "paused")
    now = SLEEP_SERIES_START + timedelta(days=1)
    assert {x["metric"]: x["valid_days"] for x in paused.brief(now)["coverage"]}[
        "sleep_duration"] == 0
    assert paused.conn.execute("SELECT COUNT(*) FROM health_os_quarantine").fetchone()[0] == 1
    paused.conn.close()


def repo(path, mode="active"):
    return HealthOSRepository(store.init_db(str(path)), KEY, sleep_series_mode=mode)


def segment(start, end, *, source=True):
    return {
        "type": "sleepAnalysis", "value": 3, "valueName": "asleepCore",
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "source": WATCH[0] if source else "unverified.source",
        "sourceBundleId": WATCH[1] if source else "unverified.bundle",
        "deviceDetail": {"model": WATCH[2], "softwareVersion": WATCH[3]},
    }


def submit(repository, records, date):
    return ingest_tuanhdo(repository, {
        "exportDate": date, "aggregation": "raw", "records": records,
        "workouts": [], "units": {},
    }, APPROVED, approved_types=TYPES)


def legacy_sleep(repository, at):
    return repository.ingest("APPLE_HEALTH", [{
        "metric": "sleep_duration", "value": 60, "unit": "min",
        "observed_at": at.isoformat(), "provenance_id": "legacy-sleep",
        "device_source": "apple_watch",
    }], [])


def test_cutover_excludes_legacy_from_current_but_preserves_history(tmp_path):
    r = repo(tmp_path / "health.db")
    r.set_apple_transition(SLEEP_SERIES_START - timedelta(days=40))
    assert legacy_sleep(r, SLEEP_SERIES_START - timedelta(days=1))["accepted"] == 1
    start = SLEEP_SERIES_START + timedelta(hours=1)
    receipt = submit(r, [segment(start, start + timedelta(hours=1))], "first")
    assert receipt["accepted"] == 1
    assert receipt["reasons"] == {"approved_sleep_series_metadata_match": 1}
    now = SLEEP_SERIES_START + timedelta(days=1)
    coverage = {row["metric"]: row["valid_days"] for row in r.brief(now)["coverage"]}
    assert coverage == {"sleep_duration": 1, "resting_heart_rate": 0}
    sleep_trends = [row for row in r.trends(now)["weeks"] if row["metric"] == "sleep_duration"]
    assert sleep_trends and any(row["value"] == 60 for row in sleep_trends)
    sleep_history = [row for row in r.history()["months"] if row["metric"] == "sleep_duration"]
    assert {row["series"] for row in sleep_history} == {
        "legacy_unverified_interval", "validated_2026_10_16"}
    sleep_months = [row for row in r.coverage() if row["metric"] == "sleep_duration"]
    assert {row["series"] for row in sleep_months} == {
        "legacy_unverified_interval", "validated_2026_10_16"}
    assert r.conn.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0] == 2


def test_cut_boundary_source_and_overlap_fail_closed(tmp_path):
    r = repo(tmp_path / "health.db")
    before = SLEEP_SERIES_START - timedelta(hours=2)
    crossing = segment(before, SLEEP_SERIES_START + timedelta(hours=1))
    old = segment(before - timedelta(hours=2), before - timedelta(hours=1))
    start = SLEEP_SERIES_START + timedelta(hours=1)
    unverified = segment(start, start + timedelta(minutes=30), source=False)
    receipt = submit(r, [crossing, old, unverified], "bounds")
    assert receipt["accepted"] == 0
    assert receipt["reasons"] == {
        "sleep_crosses_series_cut": 1, "sleep_before_series_cut": 1,
        "source_unverified": 1,
    }
    valid = segment(start, start + timedelta(hours=1))
    assert submit(r, [valid], "valid")["accepted"] == 1
    overlapping = segment(start + timedelta(minutes=30), start + timedelta(hours=1, minutes=30))
    assert submit(r, [overlapping], "overlap")["reasons"] == {"interval_overlap_existing": 1}
    assert submit(r, [valid], "reexport")["reasons"] == {"derived_fingerprint_seen": 1}


def test_non_sleep_promotion_is_unchanged_by_sleep_cut(tmp_path):
    r = repo(tmp_path / "health.db")
    old = SLEEP_SERIES_START - timedelta(days=1)
    pulse = {"type": "heartRate", "value": 70, "unit": "count/min",
             "startDate": old.isoformat(), "endDate": old.isoformat(),
             "source": WATCH[0], "sourceBundleId": WATCH[1],
             "deviceDetail": {"model": WATCH[2], "softwareVersion": WATCH[3]}}
    receipt = submit(r, [pulse, segment(old, old + timedelta(hours=1))], "mixed")
    assert receipt["accepted"] == 1
    assert receipt["reasons"] == {
        "approved_metadata_match": 1, "sleep_before_series_cut": 1,
    }


def test_direct_ingest_cannot_join_new_sleep_series(tmp_path):
    r = repo(tmp_path / "health.db")
    r.set_apple_transition(SLEEP_SERIES_START - timedelta(days=1))
    assert legacy_sleep(r, SLEEP_SERIES_START + timedelta(hours=1))["accepted"] == 1
    now = SLEEP_SERIES_START + timedelta(days=1)
    assert {x["metric"]: x["valid_days"] for x in r.brief(now)["coverage"]}["sleep_duration"] == 0
    assert [x["series"] for x in r.history()["months"] if x["metric"] == "sleep_duration"] == [
        "legacy_unverified_interval"]


def test_existing_core_key_and_unapproved_type_stay_held(tmp_path):
    r = repo(tmp_path / "health.db")
    start = SLEEP_SERIES_START + timedelta(hours=1)
    # Generic ingestion can store a same-key sleep observation, but it cannot
    # acquire the verified series marker by doing so.
    legacy_sleep(r, start + timedelta(hours=1))
    colliding = submit(r, [segment(start, start + timedelta(hours=1))], "collision")
    assert colliding["reasons"] == {"core_key_occupied": 1}
    unapproved = ingest_tuanhdo(r, {
        "exportDate": "unapproved", "aggregation": "raw",
        "records": [segment(start + timedelta(hours=2), start + timedelta(hours=3))],
        "workouts": [], "units": {},
    }, APPROVED, approved_types=frozenset({"heartRate"}))
    assert unapproved["reasons"] == {"metric_not_approved": 1}


def test_scoped_review_and_pause_are_reversible_without_deletion(tmp_path):
    path = tmp_path / "health.db"
    old = repo(path, "off")
    old.set_apple_transition(SLEEP_SERIES_START - timedelta(days=1))
    legacy_sleep(old, SLEEP_SERIES_START - timedelta(days=1))
    start = SLEEP_SERIES_START + timedelta(hours=1)
    assert submit(old, [segment(start, start + timedelta(hours=1))], "held")["reasons"] == {
        "legacy_interval_unknown": 1}
    batch_key = old.conn.execute("SELECT batch_key FROM health_os_tuanhdo_batches").fetchone()[0]
    old.conn.close()
    active = repo(path)
    result = review_held_tuanhdo(active, APPROVED, batch_key=batch_key, approved_types=TYPES)
    assert result["promoted"] == 1
    count = active.conn.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0]
    active.conn.close()
    paused = repo(path, "paused")
    assert submit(paused, [segment(start + timedelta(hours=2), start + timedelta(hours=3))],
                  "paused")["reasons"] == {"sleep_series_paused": 1}
    assert paused.conn.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0] == count
    assert {x["metric"]: x["valid_days"] for x in paused.brief(start + timedelta(days=1))["coverage"]}[
        "sleep_duration"] == 1
    paused.conn.close()
    with pytest.raises(ValueError, match="use paused"):
        repo(path, "off")


@pytest.mark.parametrize("missing", [False, True])
def test_corrupt_series_interval_never_enters_current_brief(tmp_path, missing):
    r = repo(tmp_path / "health.db")
    r.set_apple_transition(SLEEP_SERIES_START - timedelta(days=1))
    start = SLEEP_SERIES_START + timedelta(hours=1)
    assert submit(r, [segment(start, start + timedelta(hours=1))], "valid")["accepted"] == 1
    bad_interval = None if missing else store.encrypt(KEY, json.dumps({
        "start": "invalid", "end": "invalid"}))
    r.conn.execute("UPDATE health_os_tuanhdo_records SET interval_enc=? WHERE metric='sleep_duration'",
                   (bad_interval,))
    r.conn.commit()
    r._invalidate()
    now = SLEEP_SERIES_START + timedelta(days=1)
    assert {x["metric"]: x["valid_days"] for x in r.brief(now)["coverage"]}["sleep_duration"] == 0
    assert r.quality(now)["persisted_invalid_records"] == 1
    with pytest.raises(ValueError, match="technical review"):
        r.quarantine_invalid(1)


def test_mismatched_series_fingerprint_never_enters_current_brief(tmp_path):
    r = repo(tmp_path / "health.db")
    r.set_apple_transition(SLEEP_SERIES_START - timedelta(days=1))
    start = SLEEP_SERIES_START + timedelta(hours=1)
    assert submit(r, [segment(start, start + timedelta(hours=1))], "valid")["accepted"] == 1
    r.conn.execute("UPDATE health_os_tuanhdo_records SET fingerprint=? WHERE metric='sleep_duration'",
                   ("synthetic-wrong-fingerprint",))
    r.conn.commit()
    r._invalidate()
    now = SLEEP_SERIES_START + timedelta(days=1)
    assert {x["metric"]: x["valid_days"] for x in r.brief(now)["coverage"]}["sleep_duration"] == 0
    assert r.quality(now)["persisted_invalid_records"] == 1


def test_28_days_require_28_new_verified_days(tmp_path):
    r = repo(tmp_path / "health.db")
    r.set_apple_transition(SLEEP_SERIES_START - timedelta(days=40))
    legacy_sleep(r, SLEEP_SERIES_START - timedelta(days=1))
    records = []
    for day in range(28):
        start = SLEEP_SERIES_START + timedelta(days=day, hours=1)
        records.append(segment(start, start + timedelta(hours=1)))
    receipt = submit(r, records, "28-days")
    assert receipt["accepted"] == 28
    now = SLEEP_SERIES_START + timedelta(days=28)
    assert {x["metric"]: x["valid_days"] for x in r.brief(now)["coverage"]}[
        "sleep_duration"] == 28
    assert r.brief(now)["decision"] == "ABSTENERSE"  # Resting HR still lacks 28 days.
