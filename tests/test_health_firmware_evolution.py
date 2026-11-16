"""Synthetic-only firmware evolution; no production data."""
from datetime import datetime, timedelta, timezone
import sqlite3
from test_tuanhdo_staging import row, export, local_repo, WATCH
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp.health_os.tuanhdo_staging import ingest_tuanhdo, review_held_tuanhdo, accepted_watch_identity_by_key

OLD = tuple(WATCH[:2] + ["Watch", "old"])
NEW = tuple(WATCH[:2] + ["Watch", "new"])
NOW = datetime(2026, 11, 16, 18, tzinfo=timezone.utc)

def sample(kind, value, unit):
    r = row(kind, value, NOW, NOW + timedelta(minutes=1), unit=unit)
    r["deviceDetail"] = {"model": "Watch", "softwareVersion": "new"}
    r["device"] = "Apple Watch"
    return r

def test_derived_identity_survives_explicit_second_firmware():
    repo = local_repo()
    item = row("restingHeartRate", 60, NOW, unit="count/min", detail=False)
    derived = frozenset({OLD[:2]})
    assert ingest_tuanhdo(repo, export([item]), frozenset({OLD}),
                         approved_types=frozenset({"restingHeartRate"}),
                         derived_watch=derived)["accepted"] == 1
    proof = accepted_watch_identity_by_key(repo, NOW, frozenset({OLD, NEW}), derived)
    assert proof and all(v is not None for v in proof.values())

def test_anchored_activity_accepts_only_explicit_same_watch_firmware():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    repo = HealthOSRepository(conn, "synthetic-key"*4,
        activity_series_mode="active", activity_watch_identity=OLD)
    selection = {m: OLD for m in ["steps","distance","active_energy","exercise_time"]}
    item = sample("stepCount", 10, "count")
    args = dict(approved_types=frozenset({"stepCount"}), additive_watch_selection=selection)
    first = ingest_tuanhdo(repo, export([item]), frozenset({OLD}), **args)
    assert first["accepted"] == 0
    key = conn.execute("select batch_key from health_os_tuanhdo_batches").fetchone()[0]
    result = review_held_tuanhdo(repo, frozenset({OLD,NEW}), batch_key=key, **args)
    assert result["promoted"] == 1
    assert review_held_tuanhdo(repo, frozenset({OLD,NEW}), batch_key=key, **args)["promoted"] == 0
    repo._assert_activity_series_mode_current()
    item["sourceBundleId"] = "example.other"
    assert ingest_tuanhdo(repo, export([item],date="other"), frozenset({OLD,NEW}), **args)["accepted"] == 0

def test_unknown_firmware_stays_held():
    repo = local_repo()
    assert ingest_tuanhdo(repo, export([sample("heartRate",60,"count/min")]),
        frozenset({OLD}), approved_types=frozenset({"heartRate"}))["accepted"] == 0
