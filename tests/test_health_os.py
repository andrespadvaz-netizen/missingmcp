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
    assert client.get("/healthz").json() == {"status": "OK", "service": "health-os", "commit": "unknown"}
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
    # Política Operativa v0.5: a closed-list alarm symptom is URGENTE; the
    # transcription neither blocks nor changes that outcome.
    assert health.assess_readiness(when(29), ["dolor de pecho"]).state == DecisionState.URGENTE
    assert health.assess_readiness(when(29)).state == DecisionState.INFORMATIVO
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
    # The checkpoint legitimately holds the archive mtime in nanoseconds, so a
    # bare "55" can appear by chance; check for the sample value as a value.
    assert '"55"' not in saved and ": 55," not in saved and ": 55}" not in saved
    assert "Apple Watch" not in saved and "secret" not in saved
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
    # Only the operator-approved closed list (Política Operativa v0.5, 6.0)
    # yields URGENTE; every other self-report abstains.
    assert health.assess_readiness(when(29), ["dizziness"]).state == DecisionState.ABSTENERSE
    assert health.assess_readiness(when(29), ["dolor de pecho"]).state == DecisionState.URGENTE
    assert health.assess_readiness(when(29), ["fatiga"]).state == DecisionState.ABSTENERSE
    assert health.assess_readiness(when(29), ["dolor"]).state == DecisionState.ABSTENERSE


def test_baseline_allows_the_documented_two_day_contiguous_extension():
    health = make_health()
    now = datetime(2026, 10, 11, 8, tzinfo=MX)
    # 26 days live in the ordinary 42-day window; the immediately preceding
    # two days complete the operator-approved, 44-day historical baseline.
    health.set_apple_transition(now - timedelta(days=60))
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
    health.set_apple_transition(datetime(2026, 8, 1, tzinfo=MX))
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
    # Isolated: a model-interpreted value cannot alter the state at all.
    assert health.assess_readiness(when(29)).state == DecisionState.INFORMATIVO


# --- DoD closing requirements (audit of b35f09b, Política Operativa v0.5) ---

def _mature(health, start_day=1):
    for day in range(start_day, start_day + 28):
        health.ingest_observations([
            observation("sleep_duration", 7, "hr", at=when(day), pid=f"sleep:{day}"),
            observation("resting_heart_rate", 55, "bpm", at=when(day), pid=f"rhr:{day}"),
        ])


def test_without_apple_era_start_the_state_is_abstain_never_informative():
    health = make_health()
    _mature(health)
    assert health.assess_readiness(when(29)).state == DecisionState.ABSTENERSE


def test_baseline_counts_only_evidence_after_the_apple_era_start():
    health = make_health()
    _mature(health)
    health.set_apple_transition(when(20))
    assert health.metric_baseline_days("sleep_duration", when(29)) == 9
    assert health.assess_readiness(when(29)).state == DecisionState.ABSTENERSE


@pytest.mark.parametrize("report", ["Dolor de PECHO", "presión en el pecho", "chest pain", "I can\u2019t breathe", "Pérdida   del equilibrio"])
def test_closed_alarm_list_is_bilingual_and_overrides_everything(report):
    health = make_health()  # no data at all: urgency never waits for metrics
    result = health.assess_readiness(when(29), [report])
    assert result.state == DecisionState.URGENTE
    assert "911" in result.factors[0]


def test_symptoms_outside_the_closed_list_abstain():
    health = make_health()
    health.set_apple_transition(when(1))
    _mature(health)
    for report in ("mareo", "palpitaciones", "algo raro", "fatiga"):
        assert health.assess_readiness(when(29), [report]).state == DecisionState.ABSTENERSE


def test_metrics_alone_can_never_produce_urgency():
    health = make_health()
    health.set_apple_transition(when(1))
    _mature(health)
    health.ingest_observations([observation("resting_heart_rate", 140, "bpm", at=when(28, 9), pid="high")])
    assert health.assess_readiness(when(29)).state != DecisionState.URGENTE


def test_native_healthkit_strength_names_link_apple_and_speediance():
    health = make_health()
    health.ingest_workout(WorkoutSession("hk-1", when(1, 18), when(1, 19), APPLE_HEALTH, "HKWorkoutActivityTypeTraditionalStrengthTraining"))
    health.ingest_workout(WorkoutSession("sp-1", when(1, 18, ), when(1, 19), SPEEDIANCE, "strength"))
    health.ingest_workout(WorkoutSession("hk-2", when(1, 18), when(1, 19), APPLE_HEALTH, "traditionalStrengthTraining"))
    assert len(health.logical_workouts()) == 1


def test_v05_integrity_limits_and_spo2_fraction():
    with pytest.raises(ValueError):
        observation("respiratory_rate", 80, "breaths/min", pid="rr")
    with pytest.raises(ValueError):
        observation("resting_heart_rate", 290, "bpm", pid="hr")
    health = make_health()
    assert health.ingest_observations([observation("oxygen_saturation", 0.97, "%", pid="spo2")]).accepted == 1
    assert [o.value for o in health._observations.values()] == [97.0]


def _service(tmp_path):
    keys = {"GATEWAY_SECRET": "g" * 40, "HEALTH_OS_INGEST_KEY": "i" * 40, "HEALTH_OS_READ_KEY": "r" * 40,
            "HEALTH_OS_DATA_KEY": "d" * 40, "DB_PATH": str(tmp_path / "h.db"), "DATA_DIR": str(tmp_path)}
    return TestClient(build_health_os_app(load_config(keys)), raise_server_exceptions=False)


def test_undecryptable_rows_are_quarantined_not_a_500_or_a_crash_loop(tmp_path):
    db = store.init_db(str(tmp_path / "h.db"))
    HealthOSRepository(db, "d" * 40)
    alien = store.encrypt("z" * 40, "{}")
    db.execute("INSERT INTO health_os_observations(event_key, source, payload_enc) VALUES ('x', 'APPLE_HEALTH', ?)", (alien,))
    db.execute("INSERT INTO health_os_state(state_key, payload_enc) VALUES ('apple_transition_started_at', ?)", (alien,))
    db.commit()
    # Startup migration must survive a row unreadable with both keys.
    HealthOSRepository(db, "d" * 40, legacy_key="g" * 40)
    client = _service(tmp_path)
    read = {"Authorization": "Bearer " + "r" * 40}
    brief = client.post("/health-os/brief", headers=read, json={})
    assert brief.status_code == 200
    body = brief.json()["brief"]
    assert body["decision"] == "REVISAR_TECNICO" and body["quality"]["pass"] is False
    assert client.get("/health-os/quality", headers=read).status_code == 200


def test_ingest_rejects_non_evidence_and_never_echoes_values(tmp_path):
    client = _service(tmp_path)
    ingest = {"Authorization": "Bearer " + "i" * 40}
    base = {"metric": "heart_rate", "unit": "bpm", "observed_at": "2026-10-11T08:00:00-06:00"}
    response = client.post("/health-os/ingest", headers=ingest, json={"source": "APPLE_HEALTH", "observations": [
        {**base, "value": 60, "provenance_id": "t", "semantic": "chatgpt_health_transcription"},
        {**base, "value": 61, "provenance_id": "m", "semantic": "model_interpreted"},
        {**base, "value": "SECRETO-123", "provenance_id": "s"},
    ]}).json()
    assert response["accepted"] == 0 and len(response["rejected"]) == 3
    assert "SECRETO-123" not in json.dumps(response)


def test_brief_always_carries_the_legend_and_urgency_instruction(tmp_path):
    client = _service(tmp_path)
    read = {"Authorization": "Bearer " + "r" * 40}
    calm = client.post("/health-os/brief", headers=read, json={}).json()["brief"]
    assert "911" in calm["legend"] and "instruction" not in calm
    urgent = client.post("/health-os/brief", headers=read, json={"subjective_flags": ["chest pain"]}).json()["brief"]
    assert urgent["decision"] == "URGENTE" and "911" in urgent["instruction"] and "911" in urgent["legend"]


def test_oversized_bodies_return_413_not_500(tmp_path):
    client = _service(tmp_path)
    assert client.post("/health-os/transition", headers={"Authorization": "Bearer " + "i" * 40}, content=b"x" * 9000).status_code == 413
    assert client.post("/health-os/brief", headers={"Authorization": "Bearer " + "r" * 40}, content=b"x" * 9000).status_code == 413


# --- Sanitation of historical evidence and weekly trends ---

def _seed_invalid(tmp_path):
    db = store.init_db(str(tmp_path / "h.db"))
    repo = HealthOSRepository(db, "d" * 40)
    def put(key, source, payload):
        db.execute("INSERT INTO health_os_observations(event_key, source, payload_enc) VALUES (?, ?, ?)",
                   (key, source, store.encrypt("d" * 40, json.dumps(payload))))
    good = {"metric": "resting_heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-05T08:00:00-06:00", "provenance_id": "ok"}
    put("good", "APPLE_HEALTH", good)
    put("garmin", "GARMIN_LEGACY", good)
    put("zero", "APPLE_HEALTH", {**good, "metric": "weight", "value": 0, "unit": "kg", "provenance_id": "w"})
    put("badunit", "APPLE_HEALTH", {**good, "unit": "beats", "provenance_id": "u"})
    db.execute("INSERT INTO health_os_observations(event_key, source, payload_enc) VALUES ('alien', 'APPLE_HEALTH', ?)",
               (store.encrypt("z" * 40, "{}"),))
    db.commit()
    return db, repo


def test_quarantine_report_names_reasons_without_values(tmp_path):
    _, repo = _seed_invalid(tmp_path)
    report = repo.quarantine_report()
    assert report["invalid_records"] == 4
    reasons = {item["reason"] for item in report["by_reason"]}
    assert "unsupported_source:GARMIN_LEGACY" in reasons and "undecryptable" in reasons
    assert any(r.startswith("failed_validation:unsupported unit") for r in reasons)
    assert report["earliest_apple_day_by_metric"] == {"resting_heart_rate": "2026-10-05"}
    assert "55" not in json.dumps(report)


def test_silent_core_rejections_now_count_as_invalid(tmp_path):
    _, repo = _seed_invalid(tmp_path)
    assert repo.quality(when(6))["persisted_invalid_records"] == 4


def test_quarantine_apply_moves_rows_atomically_and_restores_quality(tmp_path):
    db, repo = _seed_invalid(tmp_path)
    with pytest.raises(ValueError):
        repo.quarantine_invalid(expected=3)
    assert repo.quarantine_invalid(expected=4) == {"quarantined": 4}
    assert db.execute("SELECT COUNT(*) FROM health_os_quarantine").fetchone()[0] == 4
    assert db.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0] == 1
    assert repo.quality(when(6))["persisted_invalid_records"] == 0
    assert repo.quarantine_invalid(expected=0) == {"quarantined": 0}


def test_weekly_trends_are_aggregates_against_a_provisional_baseline():
    health = make_health()
    start = datetime(2026, 9, 1, tzinfo=MX)
    health.set_apple_transition(start)
    for day in range(40):
        at = start + timedelta(days=day, hours=7)
        health.ingest_observations([
            MetricObservation("sleep_duration", 200, "min", at, APPLE_HEALTH, f"s1:{day}"),
            MetricObservation("sleep_duration", 220, "min", at + timedelta(minutes=1), APPLE_HEALTH, f"s2:{day}"),
            MetricObservation("resting_heart_rate", 55 if day < 34 else 58, "bpm", at, APPLE_HEALTH, f"r:{day}"),
        ])
    health.ingest_workout(WorkoutSession("hk", start + timedelta(days=36, hours=18), start + timedelta(days=36, hours=19), APPLE_HEALTH, "HKWorkoutActivityTypeTraditionalStrengthTraining"))
    health.ingest_workout(WorkoutSession("sp", start + timedelta(days=36, hours=18), start + timedelta(days=36, hours=19), SPEEDIANCE, "strength"))
    trends = health.weekly_trends(start + timedelta(days=39, hours=12))
    assert trends["available"] is True
    sleep = [r for r in trends["weeks"] if r["metric"] == "sleep_duration"][-1]
    assert sleep["value"] == 420.0 and sleep["baseline_value"] == 420.0
    rhr = [r for r in trends["weeks"] if r["metric"] == "resting_heart_rate"][-1]
    assert rhr["change_vs_baseline"] == 3.0
    workouts = [r for r in trends["weeks"] if r["metric"] == "workouts"]
    assert workouts[-1]["value"] == 1 and workouts[-1]["strength_sessions"] == 1
    assert all("observed_at" not in r and "provenance_id" not in r for r in trends["weeks"])


def test_trends_require_the_apple_era_start():
    assert make_health().weekly_trends(when(10))["available"] is False


def test_new_routes_enforce_their_keys(tmp_path):
    client = _service(tmp_path)
    read, ingest = {"Authorization": "Bearer " + "r" * 40}, {"Authorization": "Bearer " + "i" * 40}
    assert client.get("/health-os/trends", headers=ingest).status_code == 401
    assert client.get("/health-os/quarantine", headers=ingest).status_code == 401
    assert client.post("/health-os/quarantine/apply", headers=read, json={"confirm": "QUARANTINE", "expected": 0}).status_code == 401
    assert client.get("/health-os/trends", headers=read).json()["trends"]["available"] is False
    assert client.get("/health-os/quarantine", headers=read).json()["report"]["invalid_records"] == 0
    assert client.post("/health-os/quarantine/apply", headers=ingest, json={"expected": 0}).status_code == 400
    assert client.post("/health-os/quarantine/apply", headers=ingest, json={"confirm": "QUARANTINE", "expected": 0}).json()["quarantined"] == 0


def test_short_apple_watch_sleep_stage_segments_are_valid_evidence():
    health = make_health()
    assert health.ingest_observations([observation("sleep_duration", 0.5, "min", pid="stage")]).accepted == 1
    with pytest.raises(ValueError):
        observation("sleep_duration", 1441, "min", pid="too-long")


def test_admin_cli_runs_sanitation_and_transition_with_env_keys(tmp_path, monkeypatch):
    from missingmcp.health_os import admin_client
    db, _ = _seed_invalid(tmp_path)
    client = _service(tmp_path)
    monkeypatch.setattr(admin_client.httpx, "request",
                        lambda method, url, json=None, headers=None, timeout=None:
                        client.request(method, url.replace("https://h.example", ""), json=json, headers=headers))
    monkeypatch.delenv("HEALTH_OS_INGEST_KEY", raising=False)
    monkeypatch.setenv("HEALTH_OS_READ_KEY", "r" * 40)
    base = ["--base-url", "https://h.example"]
    assert admin_client.run(base + ["report"])["report"]["invalid_records"] == 4
    with pytest.raises(SystemExit):
        admin_client.run(base + ["quarantine-apply", "--expected", "4"])
    monkeypatch.setenv("HEALTH_OS_INGEST_KEY", "i" * 40)
    assert admin_client.run(base + ["quarantine-apply", "--expected", "3"])["http"] == 400
    assert admin_client.run(base + ["quarantine-apply", "--expected", "4"])["quarantined"] == 4
    with pytest.raises(SystemExit):
        admin_client.run(base + ["transition", "--started-at", "2026-10-04T00:00:00"])
    assert admin_client.run(base + ["transition", "--started-at", "2026-10-04T00:00:00-06:00"])["http"] == 200
    verify = admin_client.run(base + ["verify"])
    assert verify["persisted_invalid_records"] == 0 and verify["legend_present"] is True
    assert set(verify) == {"http", "status", "decision", "quality_pass", "stale", "persisted_invalid_records", "legend_present"}


# --- Producer family (device) tagging and long-horizon history ---

@pytest.mark.parametrize("attrs, family", [
    ({"sourceName": "Apple Watch de Andrés"}, "apple_watch"),
    ({"sourceName": "Connect"}, "garmin"),
    ({"sourceName": "Garmin Connect"}, "garmin"),
    ({"sourceName": "iPhone de Andrés"}, "iphone"),
    ({"sourceName": "Salud", "device": "<<HKDevice>, name:iPhone, model:iPhone>"}, "iphone"),
    ({"sourceName": "MyFitnessPal"}, "other_app"),
])
def test_device_family_is_a_category_never_free_text(attrs, family):
    from missingmcp.health_os.apple_export import device_family
    assert device_family(attrs) == family


def test_apple_export_records_carry_the_producer_family():
    xml = b'''<HealthData>
      <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Connect" unit="count/min" value="52" startDate="2026-09-01 08:00:00 -0600" endDate="2026-09-01 08:00:00 -0600"/>
      <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Apple Watch" unit="count/min" value="56" startDate="2026-10-06 08:00:00 -0600" endDate="2026-10-06 08:00:00 -0600"/>
    </HealthData>'''
    families = [o["device_source"] for o in parse_apple_health_export(xml).observations]
    assert families == ["garmin", "apple_watch"]


def test_non_watch_families_never_feed_the_apple_era_baseline():
    health = make_health()
    health.set_apple_transition(when(1))
    for day in range(1, 29):
        health.ingest_observations([
            MetricObservation("sleep_duration", 420, "min", when(day), APPLE_HEALTH, f"s:{day}", device_source="garmin"),
            MetricObservation("resting_heart_rate", 55, "bpm", when(day), APPLE_HEALTH, f"r:{day}", device_source="iphone"),
        ])
    assert health.metric_baseline_days("sleep_duration", when(29)) == 0
    assert health.assess_readiness(when(29)).state == DecisionState.REVISAR_TECNICO  # no Watch data => stale


def test_reimport_refreshes_payload_with_device_source(tmp_path):
    db = store.init_db(str(tmp_path / "h.db"))
    repo = HealthOSRepository(db, "d" * 40)
    base = {"metric": "resting_heart_rate", "value": 55, "unit": "bpm", "observed_at": "2026-10-06T08:00:00-06:00", "provenance_id": "a"}
    assert repo.ingest("APPLE_HEALTH", [base], [])["accepted"] == 1
    assert repo.ingest("APPLE_HEALTH", [{**base, "provenance_id": "b", "device_source": "apple_watch"}], [])["duplicates"] == 1
    stored = json.loads(store.decrypt("d" * 40, db.execute("SELECT payload_enc FROM health_os_observations").fetchone()[0]))
    assert stored["device_source"] == "apple_watch"
    assert db.execute("SELECT COUNT(*) FROM health_os_observations").fetchone()[0] == 1


def test_monthly_history_keeps_families_apart_and_coverage_has_no_values(tmp_path):
    db = store.init_db(str(tmp_path / "h.db"))
    repo = HealthOSRepository(db, "d" * 40)
    rows = [{"metric": "weight", "value": 60, "unit": "kg", "observed_at": "2021-01-03T08:00:00-06:00", "provenance_id": "w", "device_source": "iphone"},
            {"metric": "resting_heart_rate", "value": 52, "unit": "bpm", "observed_at": "2026-09-01T08:00:00-06:00", "provenance_id": "g", "device_source": "garmin"},
            {"metric": "resting_heart_rate", "value": 56, "unit": "bpm", "observed_at": "2026-10-06T08:00:00-06:00", "provenance_id": "aw", "device_source": "apple_watch"}]
    assert repo.ingest("APPLE_HEALTH", rows, [])["accepted"] == 3
    months = repo.history()["months"]
    assert {(r["metric"], r["device_source"], r["month"]) for r in months} == {
        ("weight", "iphone", "2021-01"), ("resting_heart_rate", "garmin", "2026-09"), ("resting_heart_rate", "apple_watch", "2026-10")}
    coverage = repo.coverage()
    assert all(set(c) == {"metric", "device_source", "first_month", "last_month", "months"} for c in coverage)
    client = _service(tmp_path)
    read = {"Authorization": "Bearer " + "r" * 40}
    assert client.get("/health-os/history", headers={"Authorization": "Bearer " + "i" * 40}).status_code == 401
    assert client.get("/health-os/coverage", headers=read).json()["status"] == "OK"
