"""Synthetic-only checks for bounded batch reads in the private report."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from starlette.testclient import TestClient

from missingmcp import store
from missingmcp.config import load_config
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp.health_os.private_report import build_private_report
from missingmcp.health_os.service import build_health_os_app
from missingmcp.health_os import tuanhdo_staging as staging


WATCH = ("synthetic.watch", "synthetic.bundle", "Watch", "1")
APPROVED = frozenset({WATCH})
KEY = "synthetic-test-key-not-a-credential"
START = datetime(2026, 10, 20, 7, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
TYPES = frozenset({"sleepAnalysis", "heartRate", "restingHeartRate", "stepCount"})


def record(kind, value, start, end=None, *, derived=False):
    result = {"type": kind, "value": value, "startDate": start.isoformat(),
              "endDate": (end or start).isoformat(), "source": WATCH[0],
              "sourceBundleId": WATCH[1],
              "deviceDetail": None if derived else {"model": WATCH[2], "softwareVersion": WATCH[3]}}
    if kind == "sleepAnalysis":
        result["valueName"] = {3: "asleepCore", 4: "asleepDeep", 5: "asleepREM"}[value]
    else:
        result["unit"] = "count" if kind == "stepCount" else "count/min"
    return result


def make_repo(*, batches=2, samples=4, derived=False, modes=True):
    repo = HealthOSRepository(store.init_db(":memory:"), KEY,
                              sleep_series_mode="active" if modes else "off",
                              activity_series_mode="active" if modes else "off",
                              activity_watch_identity=WATCH if modes else None)
    repo.set_apple_transition(START - timedelta(days=10))
    for batch in range(batches):
        records = []
        for index in range(samples):
            start = START + timedelta(days=batch, minutes=index * 2)
            records += [record("sleepAnalysis", 3, start, start + timedelta(minutes=1), derived=derived),
                        record("heartRate", 60 + index % 10, start),
                        record("restingHeartRate", 55 + index % 3, start + timedelta(seconds=1), derived=derived),
                        record("stepCount", 20, start, start + timedelta(minutes=1))]
        payload = {"exportDate": f"synthetic-{batch}", "aggregation": "raw", "records": records,
                   "workouts": [], "units": {}}
        receipt = staging.ingest_tuanhdo(
            repo, payload, APPROVED, approved_types=TYPES,
            derived_watch=frozenset({WATCH[:2]}) if derived else frozenset(),
            additive_watch_selection={"steps": WATCH} if modes else {})
        assert receipt["accepted"] > 0
    repo._load_health(NOW)  # Keep core-cache loading outside batch-read assertions.
    return repo


def report(repo, module=staging, *, approved=APPROVED, derived=frozenset()):
    return build_private_report(
        repo._load_health(NOW), NOW,
        sleep_episodes=module.accepted_sleep_episodes(repo, NOW),
        watch_identity_by_key=module.accepted_watch_identity_by_key(repo, NOW, approved, derived),
        cross_day_exclusions=module.activity_cross_day_exclusions(repo, NOW, window_days=15))


@pytest.mark.parametrize("reader", ["sleep", "identity"])
def test_reads_each_required_batch_payload_once_without_row_amplification(reader):
    repo = make_repo(samples=12)
    queries = []
    repo.conn.set_trace_callback(queries.append)
    try:
        if reader == "sleep":
            result = staging.accepted_sleep_episodes(repo, NOW)
            assert sum(row["segment_count"] for row in result) == 24
        else:
            result = staging.accepted_watch_identity_by_key(repo, NOW, APPROVED)
            assert len(result) == 96
    finally:
        repo.conn.set_trace_callback(None)
    batch_reads = [q for q in queries if q.startswith("SELECT payload_enc FROM health_os_tuanhdo_batches")]
    assert len(batch_reads) == 2
    assert all("b.payload_enc" not in q for q in queries)
    repo.conn.close()


def test_report_read_does_not_change_database():
    repo = make_repo()
    before = list(repo.conn.iterdump())
    first = report(repo)
    second = report(repo)
    assert first == second
    assert list(repo.conn.iterdump()) == before
    assert first["analysis"]["sleep_observed_episodes"]
    repo.conn.close()


def test_fingerprint_tampering_cannot_supply_watch_identity():
    repo = make_repo()
    repo.conn.execute("UPDATE health_os_tuanhdo_records SET fingerprint='invalid' WHERE metric='heart_rate'")
    repo.conn.commit()
    identities = staging.accepted_watch_identity_by_key(repo, NOW, APPROVED)
    health = repo._load_health(NOW)
    assert identities
    assert not any(health._observations[key].metric == "heart_rate" for key in identities)
    repo.conn.close()


def test_no_cache_survives_a_new_read_after_batch_changes():
    repo = make_repo(batches=1)
    assert staging.accepted_watch_identity_by_key(repo, NOW, APPROVED)
    repo.conn.execute("UPDATE health_os_tuanhdo_batches SET payload_enc=?",
                      (store.encrypt(KEY, json.dumps({"aggregation": "raw", "records": [], "workouts": [], "units": {}})),))
    repo.conn.commit()
    assert staging.accepted_watch_identity_by_key(repo, NOW, APPROVED) == {}
    assert staging.accepted_sleep_episodes(repo, NOW) == []
    repo.conn.close()


def test_missing_batch_provides_no_proof():
    repo = make_repo()
    assert staging._read_batch_samples(repo, "nonexistent-synthetic-batch") == {}
    repo.conn.close()


@pytest.mark.parametrize("change", ["remove", "replace"])
def test_sleep_batch_changed_after_metadata_read_fails_closed(change):
    repo = make_repo(batches=1)
    connection = repo.conn

    class InterleavedConnection:
        changed = False

        def execute(self, sql, parameters=()):
            if sql.startswith("SELECT payload_enc FROM health_os_tuanhdo_batches") and not self.changed:
                self.changed = True
                if change == "remove":
                    connection.execute("DELETE FROM health_os_tuanhdo_batches WHERE batch_key=?", parameters)
                else:
                    empty = {"aggregation": "raw", "records": [], "workouts": [], "units": {}}
                    connection.execute("UPDATE health_os_tuanhdo_batches SET payload_enc=? WHERE batch_key=?",
                                       (store.encrypt(KEY, json.dumps(empty)), parameters[0]))
                connection.commit()
            return connection.execute(sql, parameters)

    interleaved = InterleavedConnection()
    repo.conn = interleaved
    try:
        assert staging.accepted_sleep_episodes(repo, NOW) == []
        assert interleaved.changed
    finally:
        repo.conn = connection
        connection.close()


def test_corrupt_ciphertext_is_not_silently_replaced_with_numbers():
    repo = make_repo()
    repo.conn.execute("UPDATE health_os_tuanhdo_batches SET payload_enc='invalid-ciphertext'")
    repo.conn.commit()
    with pytest.raises(ValueError):
        staging.accepted_watch_identity_by_key(repo, NOW, APPROVED)
    with pytest.raises(ValueError):
        staging.accepted_sleep_episodes(repo, NOW)
    repo.conn.close()


def test_revoked_watch_approval_does_not_reuse_prior_identity():
    repo = make_repo()
    assert staging.accepted_watch_identity_by_key(repo, NOW, APPROVED)
    assert staging.accepted_watch_identity_by_key(repo, NOW, frozenset()) == {}
    repo.conn.close()


def test_private_report_http_auth_contract_and_no_store(tmp_path, monkeypatch):
    for name in ("HEALTH_OS_TUANHDO_WATCH_PROVENANCE", "HEALTH_OS_TUANHDO_DERIVED_WATCH_SOURCES",
                 "HEALTH_OS_TUANHDO_PROMOTE_TYPES", "HEALTH_OS_TUANHDO_ADDITIVE_WATCH_SELECTION"):
        monkeypatch.delenv(name, raising=False)
    config = load_config({"GATEWAY_SECRET": "g" * 40, "HEALTH_OS_INGEST_KEY": "i" * 40,
                          "HEALTH_OS_READ_KEY": "r" * 40, "HEALTH_OS_DATA_KEY": "d" * 40,
                          "DB_PATH": str(tmp_path / "synthetic.db"), "DATA_DIR": str(tmp_path)})
    with TestClient(build_health_os_app(config)) as client:
        assert client.get("/health-os/private-report").status_code == 401
        response = client.get("/health-os/private-report", headers={"Authorization": "Bearer " + "r" * 40})
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["status"] == "OK"
        assert response.json()["report"]["scope"] == "private_app_only_approved_watch"
