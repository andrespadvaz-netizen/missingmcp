"""Read-only parser for TuAnhDo HealthExport JSON.

This module performs no I/O and has no route to HealthOSRepository.  Its
fingerprints are derived from exported fields, not HealthKit sample UUIDs.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Any, Mapping


# Export type -> (Health OS metric, accepted unit -> canonical unit).
_QUANTITIES = {
    "stepCount": ("steps", {"count": "count"}),
    "distanceWalkingRunning": ("distance", {"km": "km"}),
    "activeEnergyBurned": ("active_energy", {"kcal": "kcal"}),
    "appleExerciseTime": ("exercise_time", {"min": "min"}),
    "appleStandTime": ("stand_time", {"min": "min"}),
    "environmentalAudioExposure": ("environmental_noise", {"dBASPL": "dBA"}),
    "flightsClimbed": ("flights_climbed", {"count": "count"}),
    "heartRate": ("heart_rate", {"count/min": "bpm", "bpm": "bpm"}),
    "heartRateVariabilitySDNN": ("hrv", {"ms": "ms"}),
    "respiratoryRate": ("respiratory_rate", {"count/min": "breaths/min"}),
    "restingHeartRate": ("resting_heart_rate", {"count/min": "bpm"}),
    "stairAscentSpeed": ("stair_ascent_speed", {"km/h": "km/h"}),
    "stairDescentSpeed": ("stair_descent_speed", {"km/h": "km/h"}),
    "walkingHeartRateAverage": ("walking_heart_rate_average", {"count/min": "bpm"}),
    "walkingSpeed": ("walking_speed", {"km/h": "km/h"}),
    "walkingStepLength": ("step_length", {"m": "cm"}),
    "bodyMass": ("weight", {"kg": "kg"}),
}
_HELD_QUANTITIES = {
    "appleSleepingBreathingDisturbances": ("unmapped_breathing_disturbances", "count", "metric_not_supported"),
    "appleSleepingWristTemperature": ("unmapped_wrist_temperature", "degC", "manual_temperature_policy"),
    "basalEnergyBurned": ("unmapped_basal_energy", "kcal", "metric_not_supported"),
    "physicalEffort": ("unmapped_physical_effort", "kcal/(kg*hr)", "metric_not_supported"),
    "oxygenSaturation": ("oxygen_saturation", "%", "percentage_semantics_unverified"),
    "timeInDaylight": ("unmapped_daylight", "min", "metric_not_supported"),
    "walkingAsymmetryPercentage": ("walking_asymmetry", "%", "percentage_semantics_unverified"),
    "walkingDoubleSupportPercentage": ("double_support_percentage", "%", "percentage_semantics_unverified"),
}
_ADDITIVE_EXPORT_TYPES = frozenset({
    "stepCount", "distanceWalkingRunning", "activeEnergyBurned",
    "appleExerciseTime", "appleStandTime", "flightsClimbed",
})
_ADDITIVE_METRICS = frozenset({
    "sleep_duration", "steps", "distance", "active_energy",
    "exercise_time", "stand_time", "flights_climbed",
})
_SLEEP_STAGES = {
    2: "awake", 3: "core", 4: "deep", 5: "rem",
}
_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
_MAX_RECORDS = 50_000
_MAX_WORKOUTS = 10_000


@dataclass(frozen=True)
class Sample:
    metric: str
    start_at: datetime = field(repr=False)
    end_at: datetime = field(repr=False)
    unit: str
    fingerprint: str = field(repr=False)
    # Health values and source names must never appear in a diagnostic repr.
    value: float = field(repr=False)
    exported_type: str = field(repr=False)
    source: str | None = field(repr=False)
    source_bundle_id: str | None = field(repr=False)
    device: str | None = field(repr=False)
    device_detail: Mapping[str, Any] | None = field(repr=False)
    sleep_stage: str | None = None
    computable: bool = False
    hold_reason: str = "source_unverified"
    sleep_value_name: str | None = field(default=None, repr=False)
    # Distinguishes identical rows within one export in future immutable staging.
    # It is not stable across re-exports and is never a sample identity.
    record_index: int = -1


@dataclass(frozen=True)
class DryRun:
    samples: tuple[Sample, ...]
    issues: Mapping[str, int]
    # Every original row stays in memory, including unsupported/invalid ones.
    raw_records: tuple[Any, ...] = field(repr=False)
    # Workouts are retained in memory for later schema work, never interpreted.
    workouts: tuple[Mapping[str, Any], ...] = field(repr=False)
    aggregation: str | None = field(default=None, repr=False)
    rejected_by_index: Mapping[int, str] = field(default_factory=dict, repr=False)

    def receipt(self) -> dict[str, Any]:
        """No values, source strings, dates, arbitrary keys, or fingerprints."""
        return {
            "samples": len(self.samples),
            "records_received": len(self.raw_records),
            "records_unmapped": len(self.raw_records) - len(self.samples),
            "computable": sum(sample.computable for sample in self.samples),
            "held": sum(not sample.computable for sample in self.samples),
            "workouts_held": len(self.workouts),
            "issues": dict(self.issues),
        }


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp_invalid")
    try:
        result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp_invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp_missing_offset")
    return result


def _number(value: Any, *, allow_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value_invalid")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("value_invalid") from exc
    if not isfinite(number) or (number < 0 and not allow_negative):
        raise ValueError("value_invalid")
    return number


def _source_field(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _unit(record: Mapping[str, Any], root_units: Any, exported_type: str) -> str | None:
    candidate = record.get("unit", record.get("units"))
    if candidate is None and isinstance(root_units, Mapping):
        candidate = root_units.get(exported_type)
    return candidate if isinstance(candidate, str) else None


def _fingerprint(record: Mapping[str, Any], exported_type: str, start: datetime, end: datetime,
                 raw_unit: str | None) -> str:
    # Preserve the distinction between an explicit device, bundle, interval,
    # and source.  Sorting fields makes re-exports and JSON key order stable.
    identity = {
        "type": exported_type,
        "start": start.astimezone(timezone.utc).isoformat(),
        "end": end.astimezone(timezone.utc).isoformat(),
        "value": record.get("value"),
        "valueName": record.get("valueName"),
        "unit": raw_unit,
        "source": record.get("source"),
        "sourceBundleId": record.get("sourceBundleId"),
        "device": record.get("device"),
        "deviceDetail": record.get("deviceDetail"),
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return "derived-health-export:v1:" + sha256(encoded.encode("utf-8")).hexdigest()


def parse_health_export(payload: Mapping[str, Any]) -> DryRun:
    """Keep recognized records separate; do not calculate totals or readiness."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload_invalid")
    try:
        encoded_size = len(json.dumps(payload, ensure_ascii=False, allow_nan=True).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("payload_invalid") from exc
    if encoded_size > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload_too_large")
    records = payload.get("records", [])
    workouts = payload.get("workouts", [])
    if not isinstance(records, list) or not isinstance(workouts, list):
        raise ValueError("collections_invalid")
    if len(records) > _MAX_RECORDS or len(workouts) > _MAX_WORKOUTS:
        raise ValueError("collection_limit_exceeded")
    aggregation = payload.get("aggregation")
    issues: dict[str, int] = {}
    rejected_by_index: dict[int, str] = {}
    samples: list[Sample] = []

    def issue(code: str) -> None:
        issues[code] = issues.get(code, 0) + 1

    if aggregation != "raw":
        issue("aggregation_not_raw")

    for record_index, record in enumerate(records):
        if not isinstance(record, Mapping):
            issue("record_invalid")
            rejected_by_index[record_index] = "record_invalid"
            continue
        exported_type = record.get("type") or record.get("name")
        if not isinstance(exported_type, str):
            issue("type_missing")
            rejected_by_index[record_index] = "type_missing"
            continue
        spec = _QUANTITIES.get(exported_type)
        held_spec = _HELD_QUANTITIES.get(exported_type)
        is_sleep = exported_type == "sleepAnalysis"
        is_stand_hour = exported_type == "appleStandHour"
        if spec is None and held_spec is None and not is_sleep and not is_stand_hour:
            issue("type_unsupported")
            rejected_by_index[record_index] = "type_unsupported"
            continue
        try:
            start = _timestamp(record.get("startDate"))
            end = _timestamp(record.get("endDate"))
            if end < start or (is_sleep and end == start):
                raise ValueError("interval_invalid")
            source = _source_field(record.get("source"))
            bundle = _source_field(record.get("sourceBundleId"))
            device = _source_field(record.get("device"))
            detail = record.get("deviceDetail")
            if detail is not None and not isinstance(detail, Mapping):
                raise ValueError("device_detail_invalid")
            if is_sleep:
                stage_code = record.get("value")
                if isinstance(stage_code, bool) or not isinstance(stage_code, int) or stage_code not in _SLEEP_STAGES:
                    raise ValueError("sleep_stage_unsupported")
                stage = _SLEEP_STAGES[stage_code]
                value_name = record.get("valueName")
                if value_name is None:
                    issue("sleep_label_missing")
                    hold = "sleep_label_missing"
                elif not isinstance(value_name, str):
                    issue("sleep_label_mismatch")
                    hold = "sleep_label_mismatch"
                else:
                    normalized = re.sub(r"[^a-z0-9]", "", value_name.lower())
                    normalized = normalized.removeprefix("hkcategoryvaluesleepanalysis")
                    expected = "awake" if stage == "awake" else "asleep" + stage
                    if normalized != expected:
                        issue("sleep_label_mismatch")
                        hold = "sleep_label_mismatch"
                    else:
                        hold = "awake_not_sleep" if stage == "awake" else "source_unverified"
                value = (end - start).total_seconds() / 60
                metric, unit = "sleep_duration", "min"
                raw_unit = None
            elif is_stand_hour:
                category_code = record.get("value")
                if isinstance(category_code, bool) or not isinstance(category_code, int):
                    raise ValueError("category_value_invalid")
                metric, unit, raw_unit = "unmapped_stand_hour", "category", None
                value, stage = float(category_code), None
                value_name = record.get("valueName")
                if category_code not in (0, 1):
                    issue("category_value_unsupported")
                    hold = "category_value_unsupported"
                elif not isinstance(value_name, str):
                    issue("category_label_missing")
                    hold = "category_label_missing"
                elif (re.sub(r"[^a-z0-9]", "", value_name.lower())
                      .removeprefix("hkcategoryvalueapplestandhour")
                      != {0: "stood", 1: "idle"}[category_code]):
                    issue("category_label_mismatch")
                    hold = "category_label_mismatch"
                else:
                    hold = "category_unmapped"
            elif held_spec is not None:
                metric, expected_unit, hold = held_spec
                raw_unit = _unit(record, payload.get("units"), exported_type)
                unit = expected_unit if raw_unit == expected_unit else "unverified"
                value = _number(record.get("value"),
                                allow_negative=exported_type == "appleSleepingWristTemperature")
                if raw_unit != expected_unit:
                    issue("unit_unsupported")
                    hold = "unit_unsupported"
                stage = None
                value_name = None
            else:
                metric, allowed_units = spec
                raw_unit = _unit(record, payload.get("units"), exported_type)
                if raw_unit not in allowed_units:
                    raise ValueError("unit_unsupported")
                unit = allowed_units[raw_unit]
                value = _number(record.get("value"))
                if exported_type == "walkingStepLength":
                    value *= 100  # The export uses m; Health OS stores cm.
                stage = None
                value_name = None
                if exported_type in _ADDITIVE_EXPORT_TYPES and start == end:
                    issue("additive_point_no_duration")
                    hold = "additive_point_no_duration"
                else:
                    hold = "source_unverified"
            fingerprint = _fingerprint(record, exported_type, start, end, raw_unit)
            samples.append(Sample(
                metric, start, end, unit, fingerprint, value,
                exported_type, source, bundle, device, detail, stage,
                False, hold, value_name if isinstance(value_name, str) else None, record_index,
            ))
        except ValueError as exc:
            code = str(exc)
            issue(code)
            rejected_by_index[record_index] = code

    # Identical visible records cannot be distinguished without an origin UUID.
    by_fingerprint: dict[str, list[int]] = {}
    for index, sample in enumerate(samples):
        by_fingerprint.setdefault(sample.fingerprint, []).append(index)
    for indexes in by_fingerprint.values():
        if len(indexes) > 1:
            issue("indistinguishable_records")
            for index in indexes:
                samples[index] = replace(samples[index], hold_reason="indistinguishable_records")

    # The existing core key ignores interval start and producer metadata.
    # Surface would-be collisions even when the derived fingerprints differ.
    by_core_key: dict[tuple[str, float, str, datetime], list[int]] = {}
    for index, sample in enumerate(samples):
        if sample.sleep_stage == "awake":
            continue
        key = (sample.metric, sample.value, sample.unit, sample.end_at.astimezone(timezone.utc))
        by_core_key.setdefault(key, []).append(index)
    for indexes in by_core_key.values():
        if len(indexes) > 1 and len({samples[i].fingerprint for i in indexes}) > 1:
            issue("core_key_collision")
            for index in indexes:
                samples[index] = replace(samples[index], hold_reason="core_key_collision")

    # Additive intervals cannot be safely summed when they overlap, even if
    # their producers differ. A sorted sweep avoids quadratic payload work.
    for metric in _ADDITIVE_METRICS:
        indexes = sorted(
            (i for i, sample in enumerate(samples)
             if sample.metric == metric and sample.start_at < sample.end_at
             and sample.sleep_stage != "awake"),
            key=lambda i: (samples[i].start_at, samples[i].end_at),
        )
        longest_index = None
        for index in indexes:
            if longest_index is not None and samples[index].start_at < samples[longest_index].end_at:
                code = "sleep_overlap" if metric == "sleep_duration" else "quantity_overlap"
                issue(code)
                samples[index] = replace(samples[index], hold_reason=code)
                samples[longest_index] = replace(samples[longest_index], hold_reason=code)
            if longest_index is None or samples[index].end_at > samples[longest_index].end_at:
                longest_index = index

    for sample in samples:
        detail = sample.device_detail or {}
        if (sample.source_bundle_id is None
                or not isinstance(detail.get("model"), str) or not detail.get("model")
                or not isinstance(detail.get("softwareVersion"), str)
                or not detail.get("softwareVersion")):
            issue("provenance_incomplete")
    if workouts:
        issue("workouts_unmapped")
    return DryRun(tuple(samples), issues, tuple(records), tuple(workouts),
                  aggregation if isinstance(aggregation, str) else None,
                  rejected_by_index)
