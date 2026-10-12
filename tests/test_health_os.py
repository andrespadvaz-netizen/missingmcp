import json
import io
import zipfile
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from missingmcp.health_os.core import APPLE_HEALTH, GARMIN_LEGACY, SPEEDIANCE, DecisionState, HealthOS, MetricObservation, WorkoutSession
from missingmcp.health_os.importers import observations_from_export, workout_from_export
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp import store
from missingmcp.app import build_app
from missingmcp.health_os.service import build_health_os_app
from missingmcp.health_os.apple_export import parse_apple_health_export
from missingmcp.health_os.apple_import_client import _records, _export_entry, _stream_records, import_zip
from missingmcp.health_os import apple_import_client
from missingmcp.health_os.speediance_import_client import import_export as import_speediance_export
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
    repo = HealthOSRepository(conn, "d" * 40)
    payload = {"metric": "heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-01T08:00:00-06:00", "provenance_id": "hk:1"}
    assert repo.ingest(APPLE_HEALTH, [payload], []) == {"accepted": 1, "duplicates": 0, "rejected": []}
    assert repo.ingest(APPLE_HEALTH, [payload], [])["duplicates"] == 1
    encrypted = conn.execute("SELECT payload_enc FROM health_os_observations").fetchone()[0]
    assert encrypted != json.dumps(payload, sort_keys=True)
    assert store.decrypt("d" * 40, encrypted) == json.dumps(payload, sort_keys=True)
    with pytest.raises(Exception):
        store.decrypt("s" * 40, encrypted)


def test_repository_uses_operator_timezone_and_persists_apple_transition():
    conn = store.init_db(":memory:")
    repo = HealthOSRepository(conn, "d" * 40, "America/Mexico_City")
    repo.set_apple_transition(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))
    payload = {"metric": "heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-02T05:00:00+00:00", "provenance_id": "hk:local-day"}
    repo.ingest(APPLE_HEALTH, [payload], [])
    health = repo._load_health(datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    assert health.local_day(datetime(2026, 10, 2, 5, tzinfo=timezone.utc)) == "2026-10-01"
    assert health.baseline_era(datetime(2026, 10, 2, 12, tzinfo=timezone.utc)).value == "ERA_APPLE_TRANSITION"


def test_repository_reencrypts_existing_health_rows_with_dedicated_data_key():
    conn = store.init_db(":memory:")
    legacy_key, data_key = "s" * 40, "d" * 40
    conn.executescript("""
        CREATE TABLE health_os_observations (
          event_key TEXT PRIMARY KEY, source TEXT NOT NULL, payload_enc TEXT NOT NULL,
          received_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE health_os_workouts (
          event_key TEXT PRIMARY KEY, source TEXT NOT NULL, payload_enc TEXT NOT NULL,
          received_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE health_os_state (
          state_key TEXT PRIMARY KEY, payload_enc TEXT NOT NULL,
          updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    payload = {"metric": "heart_rate", "value": 55, "unit": "bpm",
               "observed_at": "2026-10-01T08:00:00-06:00", "provenance_id": "hk:legacy"}
    conn.execute("INSERT INTO health_os_observations(event_key, source, payload_enc) VALUES (?, ?, ?)",
                 ("legacy", APPLE_HEALTH, store.encrypt(legacy_key, json.dumps(payload, sort_keys=True))))
    conn.execute("INSERT INTO health_os_state(state_key, payload_enc) VALUES (?, ?)",
                 ("apple_transition_started_at", store.encrypt(legacy_key, when(1).isoformat())))
    conn.commit()
    repo = HealthOSRepository(conn, data_key, legacy_key=legacy_key)
    encrypted = conn.execute("SELECT payload_enc FROM health_os_observations").fetchone()[0]
    assert store.decrypt(data_key, encrypted) == json.dumps(payload, sort_keys=True)
    with pytest.raises(Exception):
        store.decrypt(legacy_key, encrypted)
    assert repo._load_health(when(2)).baseline_era(when(2)).value == "ERA_APPLE_TRANSITION"


def test_historical_invalid_evidence_is_retained_but_forces_technical_review():
    conn = store.init_db(":memory:")
    repo = HealthOSRepository(conn, "d" * 40)
    raw = {"metric": "sleep_duration", "value": 1800, "unit": "min",
           "observed_at": "2026-10-01T08:00:00-06:00", "provenance_id": "legacy:invalid"}
    conn.execute("INSERT INTO health_os_observations(event_key, source, payload_enc) VALUES (?, ?, ?)",
                 ("legacy-invalid", APPLE_HEALTH, store.encrypt("d" * 40, json.dumps(raw, sort_keys=True))))
    conn.commit()
    snapshot = repo.brief(when(2))
    assert snapshot["decision"] == DecisionState.REVISAR_TECNICO
    assert snapshot["quality"]["persisted_invalid_records"] == 1


def test_private_ingest_endpoint_requires_dedicated_key_and_persists(tmp_path):
    key = "h" * 40
    read_key = "r" * 40
    config = load_config({"GATEWAY_SECRET": "s" * 40, "HEALTH_OS_INGEST_KEY": key, "HEALTH_OS_READ_KEY": read_key, "HEALTH_OS_DATA_KEY": "d" * 40, "DB_PATH": str(tmp_path / "health.db"), "DATA_DIR": str(tmp_path)})
    client = TestClient(build_app(config))
    payload = {"source": APPLE_HEALTH, "observations": [{"metric": "heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-01T08:00:00-06:00", "provenance_id": "hk:1"}]}
    assert client.post("/health-os/ingest", json=payload).status_code == 401
    response = client.post("/health-os/ingest", json=payload, headers={"Authorization": f"Bearer {key}"})
    assert response.json() == {"status": "OK", "accepted": 1, "duplicates": 0, "rejected": []}
    assert client.get("/health-os/quality", headers={"Authorization": f"Bearer {key}"}).status_code == 401
    assert client.get("/health-os/quality", headers={"Authorization": f"Bearer {read_key}"}).json()["status"] == "OK"
    assert client.post("/health-os/ingest", json=payload, headers={"Authorization": f"Bearer {read_key}"}).status_code == 401
    brief = client.post("/health-os/brief", json={}, headers={"Authorization": f"Bearer {read_key}"}).json()
    assert brief["status"] == "OK"
    assert brief["brief"]["decision"] == "REVISAR_TECNICO"


def test_isolated_service_has_no_garmin_surface(tmp_path):
    key = "h" * 40
    config = load_config({"GATEWAY_SECRET": "s" * 40, "HEALTH_OS_INGEST_KEY": key, "HEALTH_OS_READ_KEY": "r" * 40, "HEALTH_OS_DATA_KEY": "d" * 40, "DB_PATH": str(tmp_path / "health.db"), "DATA_DIR": str(tmp_path)})
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
    config = load_config({"GATEWAY_SECRET": "s" * 40, "HEALTH_OS_INGEST_KEY": key, "HEALTH_OS_READ_KEY": "r" * 40, "HEALTH_OS_DATA_KEY": "d" * 40, "DB_PATH": str(tmp_path / "health.db"), "DATA_DIR": str(tmp_path)})
    response = TestClient(build_health_os_app(config)).post("/health-os/import/apple-export", content=xml, headers={"Authorization": f"Bearer {key}"})
    assert response.json()["accepted"] == 2


def test_native_apple_sleep_category_uses_only_asleep_intervals():
    xml = b'''<HealthData>
      <Record type="HKCategoryTypeIdentifierSleepAnalysis" value="HKCategoryValueSleepAnalysisAsleepCore" startDate="2026-10-01 23:00:00 -0600" endDate="2026-10-02 06:00:00 -0600" sourceName="Apple Watch"/>
      <Record type="HKCategoryTypeIdentifierSleepAnalysis" value="HKCategoryValueSleepAnalysisInBed" startDate="2026-10-01 22:30:00 -0600" endDate="2026-10-02 06:30:00 -0600" sourceName="Apple Watch"/>
    </HealthData>'''
    parsed = parse_apple_health_export(xml)
    assert len(parsed.observations) == 1
    assert parsed.observations[0]["metric"] == "sleep_duration"
    assert parsed.observations[0]["value"] == 420


def test_native_apple_export_preserves_declared_units_for_safe_normalization():
    xml = b'''<HealthData>
      <Record type="HKQuantityTypeIdentifierBodyMass" value="180" unit="lb" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
      <Record type="HKQuantityTypeIdentifierDistanceWalkingRunning" value="3" unit="mi" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
      <Record type="HKQuantityTypeIdentifierActiveEnergyBurned" value="2000" unit="kJ" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
      <Record type="HKQuantityTypeIdentifierSixMinuteWalkTestDistance" value="0.5" unit="km" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
    </HealthData>'''
    parsed = parse_apple_health_export(xml)
    assert [(x["metric"], x["unit"]) for x in parsed.observations] == [
        ("weight", "lb"), ("distance", "mi"), ("active_energy", "kJ"), ("six_minute_walk_distance", "km"),
    ]
    health = make_health()
    assert health.ingest_observations(observations_from_export(APPLE_HEALTH, parsed.observations)).accepted == 4
    normalized = {(x.metric, x.unit): x.value for x in health._observations.values()}
    assert normalized[("weight", "kg")] == pytest.approx(81.646627)
    assert normalized[("distance", "km")] == pytest.approx(4.828032)
    assert normalized[("active_energy", "kcal")] == pytest.approx(478.011472)


def test_subjective_flags_and_chatgpt_transcriptions_cannot_drive_a_decision():
    health = make_health()
    health.set_apple_transition(when(1))
    for day in range(1, 29):
        health.ingest_observations([
            observation("sleep_duration", 7, "hr", at=when(day), pid=f"sleep:{day}"),
            observation("resting_heart_rate", 55, "bpm", at=when(day), pid=f"rhr:{day}"),
        ])
    health.ingest_observations([MetricObservation("heart_rate", 60, "bpm", when(29), APPLE_HEALTH, "transcript", semantic="chatgpt_health_transcription")])
    assert health.assess_readiness(when(29), ["dolor de pecho"]).state == DecisionState.ABSTENERSE
    assert health.metric_baseline_days("heart_rate", when(29)) == 0


def test_native_apple_catalog_includes_clinically_neutral_context_metrics():
    xml = b'''<HealthData>
      <Record type="HKQuantityTypeIdentifierBloodPressureSystolic" value="118" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
      <Record type="HKQuantityTypeIdentifierWalkingStepLength" value="62" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
      <Record type="HKQuantityTypeIdentifierDietaryProtein" value="120" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
      <Record type="HKCategoryTypeIdentifierMindfulSession" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:15:00 -0600"/>
    </HealthData>'''
    parsed = parse_apple_health_export(xml)
    assert {x["metric"] for x in parsed.observations} == {
        "systolic_blood_pressure", "step_length", "dietary_protein", "mindfulness_duration",
    }


def test_streaming_import_reader_accepts_renamed_apple_export(tmp_path):
    archive_path = tmp_path / "exportar.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("apple_health_export/exportar.xml", b'''<HealthData>
          <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" value="55" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600" creationDate="2026-10-01 08:00:01 -0600"/>
          <Record type="HKQuantityTypeIdentifierNotSupported" value="1" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
        </HealthData>''')
        archive.writestr("apple_health_export/export_cda.xml", b"<ClinicalDocument/>")
    with zipfile.ZipFile(archive_path) as archive:
        entry = _export_entry(archive)
        assert entry.filename.endswith("exportar.xml")
        records = list(_records(archive.open(entry)))
    assert records[0][0] == "observation"
    assert records[0][1]["provenance_id"].startswith("apple-export:")
    assert records[1] == ("ignored", "HKQuantityTypeIdentifierNotSupported")


def test_sax_reader_streams_records_without_a_document_tree():
    received = []
    _stream_records(io.BytesIO(b'''<HealthData>
      <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" value="55" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600" creationDate="2026-10-01 08:00:01 -0600"/>
      <Workout workoutActivityType="HKWorkoutActivityTypeRunning" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:30:00 -0600"/>
    </HealthData>'''), lambda kind, item: received.append((kind, item)))
    assert [kind for kind, _ in received] == ["observation", "workout"]


def test_resumable_import_filters_metrics_and_never_puts_phi_in_checkpoint(tmp_path, monkeypatch):
    archive_path = tmp_path / "exportar.zip"
    checkpoint = tmp_path / "resume.json"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("export.xml", b'''<HealthData>
          <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" value="55" startDate="2026-10-01 08:00:00 -0600" endDate="2026-10-01 08:00:00 -0600"/>
          <Record type="HKCategoryTypeIdentifierSleepAnalysis" value="HKCategoryValueSleepAnalysisAsleepCore" startDate="2026-10-01 23:00:00 -0600" endDate="2026-10-02 06:00:00 -0600"/>
        </HealthData>''')
    posted = []
    monkeypatch.setattr(apple_import_client, "_post_batch", lambda *args: posted.append(args[3:]) or {"status": "OK", "accepted": 1})
    result = import_zip(archive_path, "https://health.example", "secret", metrics=["sleep_duration"], checkpoint=checkpoint)
    assert result["observations_seen"] == 1 and result["outside_metrics"] == 1
    assert len(posted) == 1
    saved = checkpoint.read_text(encoding="utf-8")
    assert "55" not in saved and "Apple Watch" not in saved and "secret" not in saved
    second = import_zip(archive_path, "https://health.example", "secret", metrics=["sleep_duration"], checkpoint=checkpoint)
    assert second["checkpoint_skipped"] == 1 and len(posted) == 1


def test_speediance_export_requires_real_workout_identity_before_transport(tmp_path):
    export = tmp_path / "speediance.json"
    export.write_text(json.dumps({"workouts": [{
        "workout_id": "speediance:1", "start_time": "2026-10-01T18:00:00-06:00",
        "end_time": "2026-10-01T19:00:00-06:00", "activity_type": "strength",
        "exercises": [{"name": "squat", "sets": 3}],
    }]}), encoding="utf-8")
    with pytest.raises(httpx.HTTPError):
        import_speediance_export(export, "https://127.0.0.1:1", "key")
    export.write_text(json.dumps({"workouts": [{"activity_type": "strength"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="workout_id"):
        import_speediance_export(export, "https://127.0.0.1:1", "key")


def test_readiness_abstains_until_metric_specific_baselines_are_stable():
    health = make_health()
    health.set_apple_transition(when(1))
    for day in range(1, 29):
        health.ingest_observations([
            observation("sleep_duration", 7, "hr", at=when(day), pid=f"sleep:{day}"),
            observation("resting_heart_rate", 55, "bpm", at=when(day), pid=f"rhr:{day}"),
        ])
    assert health.assess_readiness(when(29)).state == DecisionState.INFORMATIVO
    # No developer-owned keyword list may emit a clinical state.  Subjective
    # flags stay outside the automated operational policy.
    assert health.assess_readiness(when(29), ["dizziness"]).state == DecisionState.ABSTENERSE
    assert health.assess_readiness(when(29), ["dolor de pecho"]).state == DecisionState.ABSTENERSE
    assert health.assess_readiness(when(29), ["fatiga"]).state == DecisionState.ABSTENERSE
    assert health.assess_readiness(when(29), ["dolor"]).state == DecisionState.ABSTENERSE


def test_baseline_allows_the_documented_two_day_contiguous_extension():
    health = make_health()
    now = datetime(2026, 10, 11, 8, tzinfo=MX)
    # 26 days live in the ordinary 42-day window; the immediately preceding
    # two days complete the operator-approved, 44-day historical baseline.
    for offset in range(17, 45):
        health.ingest_observations([observation("sleep_duration", 7, "hr", at=now - timedelta(days=offset), pid=f"sleep:{offset}")])
    assert health.metric_baseline_days("sleep_duration", now) == 28
    assert health.metric_has_stable_baseline("sleep_duration", now) is True


def test_baseline_window_counts_the_first_eligible_local_sleep_day():
    health = make_health()
    now = datetime(2026, 10, 11, 19, tzinfo=MX)
    # The observation ends early on the first calendar day in the 44-day
    # window.  It must count even though it predates the time-of-day of now.
    first_day = datetime(2026, 8, 28, 6, tzinfo=MX)
    health.ingest_observations([observation("sleep_duration", 7, "hr", at=first_day, pid="first-day")])
    assert health.metric_baseline_days("sleep_duration", now) == 1


def test_model_interpreted_value_never_drives_readiness():
    health = make_health()
    health.set_apple_transition(when(1))
    for day in range(1, 29):
        health.ingest_observations([
            observation("sleep_duration", 7, "hr", at=when(day), pid=f"sleep:{day}"),
            observation("resting_heart_rate", 55, "bpm", at=when(day), pid=f"rhr:{day}"),
        ])
    health.ingest_observations([MetricObservation("heart_rate", 60, "bpm", when(29), APPLE_HEALTH, "summary", semantic="model_interpreted")])
    assert health.assess_readiness(when(29)).state == DecisionState.REVISAR_TECNICO
