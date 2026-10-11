import json
from datetime import datetime, timedelta, timezone

import pytest

from missingmcp.health_os.core import APPLE_HEALTH, GARMIN_LEGACY, SPEEDIANCE, DecisionState, HealthOS, MetricObservation, WorkoutSession
from missingmcp.health_os.importers import observations_from_export, workout_from_export
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp import store
from missingmcp.app import build_app
from missingmcp.health_os.service import build_health_os_app
from missingmcp.health_os.apple_export import parse_apple_health_export
from missingmcp.config import load_config
from starlette.testclient import TestClient

MX = timezone(timedelta(hours=-6))


def make_health() -> HealthOS:
    return HealthOS(MX)


def when(day: int, hour: int = 8) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=MX)


def observation(metric="heart_rate", value=60, unit="bpm", source=APPLE_HEALTH, at=None, pid="a"):
    return MetricObservation(metric, value, unit, at or when(1), source, pid)


def test_observation_preserves_provenance_and_is_idempotent():
    health = make_health()
    item = observation(pid="healthkit-sample-1")
    first = health.ingest_observations([item])
    second = health.ingest_observations([item])
    assert (first.accepted, second.duplicates) == (1, 1)


def test_equivalent_instants_with_different_offsets_are_not_double_counted():
    health = make_health()
    local = observation(at=when(1), pid="hk:offset")
    utc = observation(at=datetime(2026, 10, 1, 14, tzinfo=timezone.utc), pid="hk:offset")
    assert health.ingest_observations([local, utc]).duplicates == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, 400])
def test_nonfinite_or_negative_sensor_values_are_rejected(value):
    with pytest.raises(ValueError):
        observation(value=value)


def test_units_normalize_and_missing_is_not_zero():
    health = make_health()
    result = health.ingest_observations([observation("weight", 220, "lb")])
    assert result.accepted == 1
    assert health.data_quality(when(2))["stale"] is False


def test_invalid_units_are_rejected():
    health = make_health()
    result = health.ingest_observations([observation("hrv", 4, "kg")])
    assert result.accepted == 0 and result.rejected


def test_extended_apple_health_catalog_normalizes_decision_metrics():
    health = make_health()
    records = [
        observation("oxygen_saturation", 97, "%", pid="spo2"),
        observation("systolic_blood_pressure", 118, "mmHg", pid="bp"),
        observation("dietary_protein", 120, "g", pid="protein"),
        observation("walking_speed", 4.8, "km/h", pid="walk"),
    ]
    assert health.ingest_observations(records).accepted == 4


def test_speediance_and_apple_workout_are_one_logical_session():
    health = make_health()
    apple = WorkoutSession("apple-1", when(1, 18), when(1, 19), APPLE_HEALTH, "strength")
    speediance = WorkoutSession("speed-1", when(1, 18, ), when(1, 19), SPEEDIANCE, "strength", exercises=({"sets": 3},))
    health.ingest_workout(apple)
    health.ingest_workout(speediance)
    assert len(health.logical_workouts()) == 1
    assert health.data_quality(when(2))["raw_workouts"] == 2


def test_strength_synonyms_link_apple_and_speediance_sessions():
    health = make_health()
    health.ingest_workout(WorkoutSession("apple-1", when(1, 18), when(1, 19), APPLE_HEALTH, "functional strength training"))
    health.ingest_workout(WorkoutSession("speed-1", when(1, 18), when(1, 19), SPEEDIANCE, "strength"))
    assert len(health.logical_workouts()) == 1


def test_non_overlapping_workouts_never_merge():
    health = make_health()
    health.ingest_workout(WorkoutSession("apple-1", when(1, 8), when(1, 9), APPLE_HEALTH, "strength"))
    health.ingest_workout(WorkoutSession("speed-1", when(1, 12), when(1, 13), SPEEDIANCE, "strength"))
    assert len(health.logical_workouts()) == 2


def test_baseline_has_explicit_transition_and_apple_era():
    health = make_health()
    health.set_apple_transition(when(1))
    assert health.baseline_era(when(1)).value == "ERA_APPLE_TRANSITION"
    for day in range(1, 15):
        health.ingest_observations([observation(at=when(day), pid=str(day))])
    assert health.baseline_era(when(15)).value == "ERA_APPLE_BASELINE"


def test_garmin_after_transition_fails_quality_without_deleting_history():
    health = make_health()
    health.set_apple_transition(when(1))
    health.ingest_observations([observation(source=GARMIN_LEGACY, at=when(2), pid="old")])
    assert health.data_quality(when(3))["legacy_after_transition"] == ["heart_rate"]


def test_timezone_maps_sleep_to_operator_local_day():
    health = make_health()
    assert health.local_day(datetime(2026, 10, 2, 5, tzinfo=timezone.utc)) == "2026-10-01"


def test_apple_export_requires_provenance_and_an_offset_aware_timestamp():
    record = {"metric": "heart_rate", "value": 57, "unit": "bpm", "observed_at": "2026-10-01T08:00:00-06:00", "provenance_id": "HKQuantityTypeIdentifierHeartRate:42"}
    parsed = observations_from_export(APPLE_HEALTH, [record])
    assert parsed[0].source == APPLE_HEALTH
    with pytest.raises(ValueError, match="offset"):
        observations_from_export(APPLE_HEALTH, [{**record, "observed_at": "2026-10-01T08:00:00"}])


def test_speediance_workout_export_keeps_exercises_and_rejects_garmin():
    record = {"workout_id": "speediance:77", "start_time": "2026-10-01T18:00:00-06:00", "end_time": "2026-10-01T19:00:00-06:00", "activity_type": "strength", "exercises": [{"name": "squat", "sets": 3}]}
    parsed = workout_from_export(SPEEDIANCE, record)
    assert parsed.exercises[0]["name"] == "squat"
    with pytest.raises(ValueError, match="only Apple Health or Speediance"):
        workout_from_export(GARMIN_LEGACY, record)


def test_repository_encrypts_and_deduplicates_records():
    conn = store.init_db(":memory:")
    repo = HealthOSRepository(conn, "s" * 40)
    payload = {"metric": "heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-01T08:00:00-06:00", "provenance_id": "hk:1"}
    assert repo.ingest(APPLE_HEALTH, [payload], []) == {"accepted": 1, "duplicates": 0, "rejected": []}
    assert repo.ingest(APPLE_HEALTH, [payload], [])["duplicates"] == 1
    encrypted = conn.execute("SELECT payload_enc FROM health_os_observations").fetchone()[0]
    assert encrypted != json.dumps(payload, sort_keys=True)
    assert store.decrypt("s" * 40, encrypted) == json.dumps(payload, sort_keys=True)


def test_repository_uses_operator_timezone_and_persists_apple_transition():
    conn = store.init_db(":memory:")
    repo = HealthOSRepository(conn, "s" * 40, "America/Mexico_City")
    repo.set_apple_transition(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))
    payload = {"metric": "heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-02T05:00:00+00:00", "provenance_id": "hk:local-day"}
    repo.ingest(APPLE_HEALTH, [payload], [])
    health = repo._load_health(datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    assert health.local_day(datetime(2026, 10, 2, 5, tzinfo=timezone.utc)) == "2026-10-01"
    assert health.baseline_era(datetime(2026, 10, 2, 12, tzinfo=timezone.utc)).value == "ERA_APPLE_TRANSITION"


def test_private_ingest_endpoint_requires_dedicated_key_and_persists(tmp_path):
    key = "h" * 40
    config = load_config({"GATEWAY_SECRET": "s" * 40, "HEALTH_OS_INGEST_KEY": key, "DB_PATH": str(tmp_path / "health.db"), "DATA_DIR": str(tmp_path)})
    client = TestClient(build_app(config))
    payload = {"source": APPLE_HEALTH, "observations": [{"metric": "heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-01T08:00:00-06:00", "provenance_id": "hk:1"}]}
    assert client.post("/health-os/ingest", json=payload).status_code == 401
    response = client.post("/health-os/ingest", json=payload, headers={"Authorization": f"Bearer {key}"})
    assert response.json() == {"status": "OK", "accepted": 1, "duplicates": 0, "rejected": []}
    assert client.get("/health-os/quality", headers={"Authorization": f"Bearer {key}"}).json()["status"] == "OK"
    brief = client.post("/health-os/brief", json={}, headers={"Authorization": f"Bearer {key}"}).json()
    assert brief["status"] == "OK"
    assert brief["brief"]["decision"] == "ABSTENERSE"


def test_isolated_service_has_no_garmin_surface(tmp_path):
    key = "h" * 40
    config = load_config({"GATEWAY_SECRET": "s" * 40, "HEALTH_OS_INGEST_KEY": key, "DB_PATH": str(tmp_path / "health.db"), "DATA_DIR": str(tmp_path)})
    client = TestClient(build_health_os_app(config))
    assert client.get("/healthz").json() == {"status": "OK"}
    assert client.get("/garmin").status_code == 404
    transition = client.post("/health-os/transition", json={"started_at": "2026-10-01T08:00:00-06:00"}, headers={"Authorization": f"Bearer {key}"})
    assert transition.json()["baseline_era"] == "ERA_APPLE_TRANSITION"


def test_native_apple_export_is_raw_provenance_bearing_input(tmp_path):
    xml = b'''<?xml version="1.0"?><HealthData>
      <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" value="55" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600" creationDate="2026-10-01 08:00:01 -0600"/>
      <Record type="HKQuantityTypeIdentifierOxygenSaturation" sourceName="Apple Watch" unit="%" value="0.97" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600" creationDate="2026-10-01 08:00:01 -0600"/>
      <Record type="HKQuantityTypeIdentifierUnknown" value="1" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
    </HealthData>'''
    parsed = parse_apple_health_export(xml)
    assert parsed.observations[0]["provenance_id"].startswith("apple-export:")
    assert parsed.observations[1]["value"] == 97
    assert parsed.ignored_types == ("HKQuantityTypeIdentifierUnknown",)
    key = "h" * 40
    config = load_config({"GATEWAY_SECRET": "s" * 40, "HEALTH_OS_INGEST_KEY": key, "DB_PATH": str(tmp_path / "health.db"), "DATA_DIR": str(tmp_path)})
    response = TestClient(build_health_os_app(config)).post("/health-os/import/apple-export", content=xml, headers={"Authorization": f"Bearer {key}"})
    assert response.json()["accepted"] == 2


def test_readiness_abstains_until_metric_specific_baselines_are_stable():
    health = make_health()
    health.set_apple_transition(when(1))
    for day in range(1, 29):
        health.ingest_observations([
            observation("sleep_duration", 7, "hr", at=when(day), pid=f"sleep:{day}"),
            observation("resting_heart_rate", 55, "bpm", at=when(day), pid=f"rhr:{day}"),
        ])
    assert health.assess_readiness(when(29)).state == DecisionState.ABSTENERSE
    assert health.assess_readiness(when(29), ["dizziness"]).state == DecisionState.ESCALAR
    assert health.assess_readiness(when(29), ["dolor de pecho"]).state == DecisionState.ESCALAR
    assert health.assess_readiness(when(29), ["fatiga"]).state == DecisionState.AJUSTA
    assert health.assess_readiness(when(29), ["dolor"]).state == DecisionState.DETEN


def test_model_interpreted_value_never_drives_readiness():
    health = make_health()
    health.set_apple_transition(when(1))
    for day in range(1, 29):
        health.ingest_observations([
            observation("sleep_duration", 7, "hr", at=when(day), pid=f"sleep:{day}"),
            observation("resting_heart_rate", 55, "bpm", at=when(day), pid=f"rhr:{day}"),
        ])
    health.ingest_observations([MetricObservation("heart_rate", 60, "bpm", when(29), APPLE_HEALTH, "summary", semantic="model_interpreted")])
    assert health.assess_readiness(when(29)).state == DecisionState.ABSTENERSE
