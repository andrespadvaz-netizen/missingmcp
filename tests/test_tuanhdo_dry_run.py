"""Fictitious year-2000 inputs only; no Apple Health export is read."""
from copy import deepcopy
import math
import pytest

from missingmcp.health_os import tuanhdo_dry_run
from missingmcp.health_os.tuanhdo_dry_run import parse_health_export


def record(kind, value, unit=None, *, start="2000-01-01T00:00:00-06:00",
           end="2000-01-01T00:01:00-06:00", source="example.synthetic",
           bundle="example.synthetic"):
    item = {
        "type": kind, "startDate": start, "endDate": end, "value": value,
        "source": source, "sourceBundleId": bundle, "device": None,
        "deviceDetail": {"model": "example.synthetic", "softwareVersion": "0"},
    }
    if unit is not None:
        item["unit"] = unit
    if kind == "sleepAnalysis":
        item["valueName"] = {
            2: "awake", 3: "asleepCore", 4: "asleepDeep", 5: "asleepREM",
        }.get(value)
    return item


def payload(records, **extra):
    return {"exportDate": "2000-01-02T00:00:00-06:00",
            "source": "example.synthetic", "app": "example.synthetic",
            "timeZone": "America/Mexico_City", "dateFormat": "ISO8601",
            "dateRange": {"start": "2000-01-01", "end": "2000-01-02"},
            "units": {}, "aggregation": "raw", "records": records,
            "workouts": [], "summary": {}, **extra}


def test_explicit_quantity_maps_and_sleep_interval_are_held():
    rows = [
        record("stepCount", 100, "count"),
        record("distanceWalkingRunning", 0.1, "km"),
        record("activeEnergyBurned", 10, "kcal"),
        record("heartRate", 60, "count/min"),
        record("bodyMass", 70, "kg"),
        record("sleepAnalysis", 3),
    ]
    result = parse_health_export(payload(rows))
    assert [(s.metric, s.value, s.unit) for s in result.samples] == [
        ("steps", 100, "count"), ("distance", 0.1, "km"),
        ("active_energy", 10, "kcal"), ("heart_rate", 60, "bpm"),
        ("weight", 70, "kg"), ("sleep_duration", 1, "min"),
    ]
    assert all(s.start_at.isoformat() == "2000-01-01T00:00:00-06:00" for s in result.samples)
    assert all(s.end_at.isoformat() == "2000-01-01T00:01:00-06:00" for s in result.samples)
    assert all(not s.computable for s in result.samples)
    assert all(s.source_bundle_id == "example.synthetic" for s in result.samples)
    assert all(s.fingerprint.startswith("derived-health-export:v1:") for s in result.samples)
    assert result.receipt()["computable"] == 0
    assert result.receipt()["issues"] == {}


def test_point_quantities_are_retained_while_zero_sleep_and_reverse_intervals_are_rejected():
    instant = "2000-01-01T00:00:00-06:00"
    rows = [
        record("heartRate", 60, "count/min", start=instant, end=instant),
        record("bodyMass", 70, "kg", start=instant, end=instant),
        record("sleepAnalysis", 3, start=instant, end=instant),
        record("stepCount", 100, "count", start="2000-01-01T00:01:00-06:00", end=instant),
    ]
    result = parse_health_export(payload(rows))
    assert [sample.metric for sample in result.samples] == ["heart_rate", "weight"]
    assert all(sample.start_at == sample.end_at for sample in result.samples)
    assert all(not sample.computable for sample in result.samples)
    assert result.receipt()["issues"] == {"interval_invalid": 2}


def test_overlapping_additive_quantities_are_held_in_one_batch():
    first = record("stepCount", 10, "count", start="2000-01-01T00:00:00-06:00",
                   end="2000-01-01T01:00:00-06:00")
    second = record("stepCount", 11, "count", start="2000-01-01T00:30:00-06:00",
                    end="2000-01-01T01:30:00-06:00")
    result = parse_health_export(payload([first, second]))
    assert result.receipt()["issues"] == {"quantity_overlap": 1}
    assert [sample.hold_reason for sample in result.samples] == ["quantity_overlap"] * 2


@pytest.mark.parametrize("kind,unit", [
    ("stepCount", "count"),
    ("distanceWalkingRunning", "km"),
    ("activeEnergyBurned", "kcal"),
    ("appleExerciseTime", "min"),
    ("appleStandTime", "min"),
    ("flightsClimbed", "count"),
])
def test_additive_point_is_retained_as_noncomputable(kind, unit):
    instant = "2000-01-01T00:00:00-06:00"
    result = parse_health_export(payload([record(kind, 100, unit, start=instant, end=instant)]))
    assert len(result.samples) == 1
    assert result.samples[0].hold_reason == "additive_point_no_duration"
    assert not result.samples[0].computable
    assert result.receipt()["issues"] == {"additive_point_no_duration": 1}
    assert result.receipt()["records_unmapped"] == 0


def test_diary_catalog_recognizes_all_26_known_types_without_inventing_metrics():
    quantities = [
        ("activeEnergyBurned", 10, "kcal"),
        ("appleExerciseTime", 3, "min"),
        ("appleSleepingBreathingDisturbances", 1, "count"),
        ("appleSleepingWristTemperature", -0.2, "degC"),
        ("appleStandTime", 5, "min"),
        ("basalEnergyBurned", 40, "kcal"),
        ("distanceWalkingRunning", 0.3, "km"),
        ("environmentalAudioExposure", 70, "dBASPL"),
        ("flightsClimbed", 1, "count"),
        ("heartRate", 60, "count/min"),
        ("heartRateVariabilitySDNN", 45, "ms"),
        ("oxygenSaturation", 0.97, "%"),
        ("physicalEffort", 4, "kcal/(kg*hr)"),
        ("respiratoryRate", 14, "count/min"),
        ("restingHeartRate", 55, "count/min"),
        ("stairAscentSpeed", 2, "km/h"),
        ("stairDescentSpeed", 2, "km/h"),
        ("stepCount", 100, "count"),
        ("timeInDaylight", 8, "min"),
        ("walkingAsymmetryPercentage", 0.04, "%"),
        ("walkingDoubleSupportPercentage", 22, "%"),
        ("walkingHeartRateAverage", 90, "count/min"),
        ("walkingSpeed", 4, "km/h"),
        ("walkingStepLength", 0.75, "m"),
    ]
    rows = [record(kind, value, unit) for kind, value, unit in quantities]
    stand = record("appleStandHour", 0)
    stand["valueName"] = "HKCategoryValueAppleStandHourStood"
    rows.extend([stand, record("sleepAnalysis", 3)])
    result = parse_health_export(payload(rows))
    assert len(rows) == len(result.samples) == 26
    assert result.receipt()["records_unmapped"] == 0
    assert result.receipt()["issues"] == {}
    by_type = {sample.exported_type: sample for sample in result.samples}
    assert by_type["heartRate"].metric == "heart_rate"
    assert by_type["restingHeartRate"].metric == "resting_heart_rate"
    assert by_type["heartRateVariabilitySDNN"].metric == "hrv"
    assert by_type["walkingStepLength"].value == 75
    assert by_type["walkingStepLength"].unit == "cm"
    assert by_type["environmentalAudioExposure"].unit == "dBA"
    assert by_type["respiratoryRate"].unit == "breaths/min"
    for kind in ("oxygenSaturation", "walkingAsymmetryPercentage",
                 "walkingDoubleSupportPercentage"):
        assert by_type[kind].hold_reason == "percentage_semantics_unverified"
    assert by_type["appleStandHour"].hold_reason == "category_unmapped"
    assert all(not sample.computable for sample in result.samples)


def test_diary_percentages_and_unknown_categories_are_held_without_scale_guessing():
    rows = [record("oxygenSaturation", 0.97, "%"),
            record("oxygenSaturation", 97, "%", end="2000-01-01T00:02:00-06:00"),
            record("walkingAsymmetryPercentage", 0.04, "%"),
            record("walkingAsymmetryPercentage", 4, "%", end="2000-01-01T00:02:00-06:00"),
            record("walkingDoubleSupportPercentage", 0.22, "%"),
            record("walkingDoubleSupportPercentage", 22, "%", end="2000-01-01T00:02:00-06:00"),
            record("oxygenSaturation", 0.97, "fraction", end="2000-01-01T00:03:00-06:00")]
    unknown_stand = record("appleStandHour", 9)
    unknown_stand["valueName"] = "unknown"
    unknown_sleep = record("sleepAnalysis", 9)
    rows.extend([unknown_stand, unknown_sleep])
    result = parse_health_export(payload(rows))
    held = [sample for sample in result.samples if sample.hold_reason == "percentage_semantics_unverified"]
    assert len(held) == 6
    assert {sample.value for sample in held if sample.exported_type == "oxygenSaturation"} == {0.97, 97.0}
    assert any(sample.hold_reason == "unit_unsupported" for sample in result.samples)
    assert any(sample.hold_reason == "category_value_unsupported" for sample in result.samples)
    assert result.receipt()["records_unmapped"] == 1  # unknown sleep code, raw row retained
    assert result.receipt()["issues"] == {
        "unit_unsupported": 1, "category_value_unsupported": 1, "sleep_stage_unsupported": 1,
    }


@pytest.mark.parametrize("kind,value,wrong_unit", [
    ("heartRateVariabilitySDNN", 45, "bpm"),
    ("environmentalAudioExposure", 70, "kcal"),
    ("walkingStepLength", 0.75, "km"),
    ("appleExerciseTime", 5, "h"),
    ("activeEnergyBurned", 1000, "cal"),
    ("activeEnergyBurned", 1, "Cal"),
    ("activeEnergyBurned", 10, "CAL"),
    ("activeEnergyBurned", 10, "kJ"),
    ("activeEnergyBurned", 10, "Calories"),
])
def test_diary_mapped_types_reject_unverified_units(kind, value, wrong_unit):
    result = parse_health_export(payload([record(kind, value, wrong_unit)]))
    assert result.samples == ()
    assert result.receipt()["records_unmapped"] == 1
    assert result.receipt()["issues"] == {"unit_unsupported": 1}


def test_missing_invalid_and_nonfinite_units_or_values_are_rejected():
    rows = [
        record("heartRate", 60),
        record("heartRate", 60, True),
        record("heartRate", None, "bpm"),
        record("heartRate", True, "bpm"),
        record("heartRate", math.nan, "bpm"),
        record("heartRate", math.inf, "bpm"),
    ]
    result = parse_health_export(payload(rows))
    assert result.samples == ()
    assert result.issues == {"unit_unsupported": 2, "value_invalid": 4}


def test_offsets_order_and_reexport_preserve_derived_fingerprints():
    first = record("stepCount", 100, "count")
    same_in_utc = record("stepCount", 100, "count",
                         start="2000-01-01T06:00:00+00:00",
                         end="2000-01-01T06:01:00+00:00")
    other = record("bodyMass", 70, "kg")
    original = parse_health_export(payload([first, other]))
    reordered = parse_health_export(payload([dict(reversed(list(other.items()))),
                                              dict(reversed(list(same_in_utc.items())))]))
    assert {s.metric: s.fingerprint for s in original.samples} == {
        s.metric: s.fingerprint for s in reordered.samples
    }


def test_indistinguishable_export_rows_and_distinct_sources_surface_collisions():
    first = record("heartRate", 60, "bpm")
    same_fields = deepcopy(first)
    different_source = record("heartRate", 60, "bpm",
                              source="another.synthetic", bundle="another.synthetic")
    result = parse_health_export(payload([first, same_fields, different_source]))
    assert result.issues["indistinguishable_records"] == 1
    assert result.issues["core_key_collision"] == 1
    assert len({s.fingerprint for s in result.samples}) == 2
    assert [s.record_index for s in result.samples] == [0, 1, 2]
    assert result.samples[0].fingerprint == result.samples[1].fingerprint
    assert result.samples[0].fingerprint != result.samples[2].fingerprint
    assert all(not s.computable for s in result.samples)


def test_unknown_types_sleep_codes_awake_and_overlaps_are_not_summed():
    rows = [
        record("unknownType", 1, "count"),
        record("sleepAnalysis", 99),
        record("sleepAnalysis", 2),
        record("sleepAnalysis", 3),
        record("sleepAnalysis", 4, start="2000-01-01T00:00:30-06:00",
               end="2000-01-01T00:01:30-06:00"),
        record("sleepAnalysis", 5, start="2000-01-01T00:02:00-06:00",
               end="2000-01-01T00:03:00-06:00"),
    ]
    result = parse_health_export(payload(rows))
    assert result.issues["type_unsupported"] == 1
    assert result.issues["sleep_stage_unsupported"] == 1
    assert len(result.raw_records) == len(rows)
    assert result.raw_records[0]["type"] == "unknownType"
    assert result.receipt()["records_unmapped"] == 2
    assert result.issues["sleep_overlap"] == 1
    assert [s.sleep_stage for s in result.samples] == ["awake", "core", "deep", "rem"]
    assert result.samples[0].hold_reason == "awake_not_sleep"
    assert all(not s.computable for s in result.samples)


def test_sleep_value_name_conflict_is_held_without_interpretation():
    row = record("sleepAnalysis", 3)
    row["valueName"] = "asleepDeep"
    result = parse_health_export(payload([row]))
    assert result.issues == {"sleep_label_mismatch": 1}
    assert result.samples[0].sleep_stage == "core"
    assert result.samples[0].sleep_value_name == "asleepDeep"
    assert result.samples[0].hold_reason == "sleep_label_mismatch"


def test_same_stage_sleep_overlap_is_reported_without_total():
    rows = [
        record("sleepAnalysis", 3),
        record("sleepAnalysis", 3, start="2000-01-01T00:00:30-06:00",
               end="2000-01-01T00:01:30-06:00"),
    ]
    result = parse_health_export(payload(rows))
    assert result.issues == {"sleep_overlap": 1}
    assert all(s.hold_reason == "sleep_overlap" for s in result.samples)
    assert "total_sleep" not in result.receipt()


def test_root_unit_fallback_preserves_device_detail_without_exposing_it():
    row = record("bodyMass", 70)
    row["deviceDetail"] = {"manufacturer": "example.synthetic",
                           "model": "synthetic-scale", "softwareVersion": "0"}
    result = parse_health_export(payload([row], units={"bodyMass": "kg"}))
    assert result.samples[0].unit == "kg"
    assert result.samples[0].device_detail == row["deviceDetail"]
    assert "synthetic-scale" not in repr(result)
    assert "synthetic-scale" not in repr(result.receipt())


def test_nonraw_unknown_source_workouts_and_receipt_are_noncomputable_and_redacted():
    row = record("heartRate", 60, "bpm", source=None, bundle=None)
    result = parse_health_export(payload([row], aggregation="sensitive-free-text",
                                        workouts=[{"synthetic_value": 123456}]))
    assert result.issues == {
        "aggregation_not_raw": 1, "provenance_incomplete": 1,
        "workouts_unmapped": 1,
    }
    assert result.receipt() == {
        "samples": 1, "records_received": 1, "records_unmapped": 0,
        "computable": 0, "held": 1, "workouts_held": 1,
        "issues": dict(result.issues),
    }
    assert "123456" not in repr(result)
    assert "60.0" not in repr(result)
    assert "example.synthetic" not in repr(result)
    assert "2000-01-01" not in repr(result)
    assert "sensitive-free-text" not in repr(result)


def test_rejects_missing_offsets_and_invalid_collections():
    row = record("stepCount", 100, "count", start="2000-01-01T00:00:00")
    result = parse_health_export(payload([row]))
    assert result.samples == ()
    assert result.issues == {"timestamp_missing_offset": 1}
    invalid = payload([])
    invalid["records"] = None
    try:
        parse_health_export(invalid)
    except ValueError as exc:
        assert str(exc) == "collections_invalid"
    else:
        raise AssertionError("non-list records must fail")


def test_missing_device_provenance_stays_held():
    row = record("stepCount", 100, "count")
    row["deviceDetail"] = None
    result = parse_health_export(payload([row]))
    assert result.issues == {"provenance_incomplete": 1}
    assert result.receipt()["computable"] == 0


def test_payload_byte_and_row_limits_fail_with_fixed_errors(monkeypatch):
    row = record("stepCount", 100, "count")
    monkeypatch.setattr(tuanhdo_dry_run, "_MAX_RECORDS", 1)
    with pytest.raises(ValueError, match="^collection_limit_exceeded$"):
        parse_health_export(payload([row, row]))
    monkeypatch.setattr(tuanhdo_dry_run, "_MAX_PAYLOAD_BYTES", 100)
    with pytest.raises(ValueError, match="^payload_too_large$"):
        parse_health_export(payload([row]))


def test_malformed_nested_input_cannot_become_a_dynamic_diagnostic():
    invalid = payload([])
    invalid["summary"]["cycle"] = invalid["summary"]
    with pytest.raises(ValueError, match="^payload_invalid$"):
        parse_health_export(invalid)
