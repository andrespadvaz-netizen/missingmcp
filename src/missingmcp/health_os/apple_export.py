"""Strict reader for Apple's native Health export.xml format.

This is deliberately a local/raw evidence parser: it never consumes a model
summary and derives a deterministic provenance id from Apple's sample fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

from .core import APPLE_HEALTH

_METRICS: dict[str, tuple[str, str]] = {
    "HKQuantityTypeIdentifierHeartRate": ("heart_rate", "bpm"),
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_heart_rate", "bpm"),
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": ("walking_heart_rate_average", "bpm"),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv", "ms"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("oxygen_saturation", "%"),
    "HKQuantityTypeIdentifierRespiratoryRate": ("respiratory_rate", "breaths/min"),
    "HKQuantityTypeIdentifierBodyMass": ("weight", "kg"),
    "HKQuantityTypeIdentifierBodyFatPercentage": ("body_fat_percentage", "%"),
    "HKQuantityTypeIdentifierLeanBodyMass": ("lean_body_mass", "kg"),
    "HKQuantityTypeIdentifierBodyMassIndex": ("bmi", "kg/m2"),
    "HKQuantityTypeIdentifierHeight": ("height", "cm"),
    "HKQuantityTypeIdentifierStepCount": ("steps", "count"),
    "HKQuantityTypeIdentifierFlightsClimbed": ("flights_climbed", "count"),
    "HKQuantityTypeIdentifierDistanceWalkingRunning": ("distance", "km"),
    "HKQuantityTypeIdentifierActiveEnergyBurned": ("active_energy", "kcal"),
    "HKQuantityTypeIdentifierAppleExerciseTime": ("exercise_time", "min"),
    "HKQuantityTypeIdentifierWalkingSpeed": ("walking_speed", "km/h"),
    "HKQuantityTypeIdentifierSixMinuteWalkTestDistance": ("distance", "km"),
}


@dataclass(frozen=True, slots=True)
class AppleExportResult:
    observations: list[dict[str, Any]]
    workouts: list[dict[str, Any]]
    ignored_types: tuple[str, ...]


def _provenance(attributes: dict[str, str]) -> str:
    identity = "|".join(attributes.get(name, "") for name in
                        ("type", "startDate", "endDate", "value", "unit", "sourceName", "device", "creationDate"))
    return "apple-export:" + sha256(identity.encode("utf-8")).hexdigest()


def _value(value: str, apple_type: str) -> float:
    parsed = float(value)
    # Apple encodes proportions for these types, while the Health OS canonical
    # unit is percent.  This is a documented unit conversion, not inference.
    if apple_type in {"HKQuantityTypeIdentifierOxygenSaturation", "HKQuantityTypeIdentifierBodyFatPercentage"}:
        return parsed * 100 if parsed <= 1 else parsed
    return parsed


def parse_apple_health_export(xml: bytes) -> AppleExportResult:
    """Extract supported raw Records and Workouts from Apple `export.xml`.

    Unknown types are returned as a report, never coerced into a metric.
    """
    observations: list[dict[str, Any]] = []
    workouts: list[dict[str, Any]] = []
    ignored: set[str] = set()
    try:
        iterator = ET.iterparse(BytesIO(xml), events=("end",))
        for _, node in iterator:
            if node.tag == "Record":
                attrs = node.attrib
                spec = _METRICS.get(attrs.get("type", ""))
                if spec is None:
                    ignored.add(attrs.get("type", "unknown"))
                else:
                    metric, unit = spec
                    observations.append({
                        "metric": metric, "value": _value(attrs["value"], attrs["type"]), "unit": unit,
                        "observed_at": attrs["endDate"], "provenance_id": _provenance(attrs), "semantic": "measured",
                    })
                node.clear()
            elif node.tag == "Workout":
                attrs = node.attrib
                workouts.append({
                    "workout_id": _provenance(attrs), "start_time": attrs["startDate"], "end_time": attrs["endDate"],
                    "activity_type": attrs.get("workoutActivityType", "other"),
                })
                node.clear()
    except (ET.ParseError, KeyError, ValueError) as exc:
        raise ValueError(f"invalid Apple Health export.xml: {exc}") from exc
    return AppleExportResult(observations, workouts, tuple(sorted(ignored)))
