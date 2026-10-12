"""Strict reader for Apple's native Health export.xml format.

This is deliberately a local/raw evidence parser: it never consumes a model
summary and derives a deterministic provenance id from Apple's sample fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    "HKQuantityTypeIdentifierBloodPressureSystolic": ("systolic_blood_pressure", "mmHg"),
    "HKQuantityTypeIdentifierBloodPressureDiastolic": ("diastolic_blood_pressure", "mmHg"),
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
    "HKQuantityTypeIdentifierAppleStandTime": ("stand_time", "min"),
    "HKQuantityTypeIdentifierWalkingSpeed": ("walking_speed", "km/h"),
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": ("walking_asymmetry", "%"),
    "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage": ("double_support_percentage", "%"),
    "HKQuantityTypeIdentifierWalkingStepLength": ("step_length", "cm"),
    "HKQuantityTypeIdentifierStairAscentSpeed": ("stair_ascent_speed", "km/h"),
    "HKQuantityTypeIdentifierStairDescentSpeed": ("stair_descent_speed", "km/h"),
    # This is a functional-test result, not daily locomotion.  Keeping it
    # distinct prevents it from inflating walking/running distance.
    "HKQuantityTypeIdentifierSixMinuteWalkTestDistance": ("six_minute_walk_distance", "km"),
    "HKQuantityTypeIdentifierEnvironmentalAudioExposure": ("environmental_noise", "dBA"),
    "HKQuantityTypeIdentifierHeadphoneAudioExposure": ("headphone_noise", "dBA"),
    "HKQuantityTypeIdentifierDietaryProtein": ("dietary_protein", "g"),
    "HKQuantityTypeIdentifierDietaryCarbohydrates": ("dietary_carbohydrates", "g"),
    "HKQuantityTypeIdentifierDietaryFatTotal": ("dietary_fat", "g"),
    "HKQuantityTypeIdentifierDietaryFiber": ("dietary_fiber", "g"),
    "HKQuantityTypeIdentifierDietarySugar": ("dietary_sugar", "g"),
    "HKQuantityTypeIdentifierDietaryFatSaturated": ("dietary_saturated_fat", "g"),
    "HKQuantityTypeIdentifierDietarySodium": ("dietary_sodium", "mg"),
    "HKQuantityTypeIdentifierDietaryPotassium": ("dietary_potassium", "mg"),
    "HKQuantityTypeIdentifierDietaryCalcium": ("dietary_calcium", "mg"),
    "HKQuantityTypeIdentifierDietaryMagnesium": ("dietary_magnesium", "mg"),
    "HKQuantityTypeIdentifierDietaryPhosphorus": ("dietary_phosphorus", "mg"),
    "HKQuantityTypeIdentifierDietaryIron": ("dietary_iron", "mg"),
    "HKQuantityTypeIdentifierDietaryVitaminC": ("dietary_vitamin_c", "mg"),
}

_SLEEP_ANALYSIS = "HKCategoryTypeIdentifierSleepAnalysis"
_MINDFUL_SESSION = "HKCategoryTypeIdentifierMindfulSession"


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


def observation_from_apple_record(attributes: dict[str, str]) -> dict[str, Any] | None:
    """Map one original Apple record without treating model text as evidence."""
    apple_type = attributes.get("type", "")
    spec = _METRICS.get(apple_type)
    if spec is not None:
        metric, canonical_unit = spec
        return {
            # Preserve the unit declared in the native export.  The core
            # converts only explicit, known units; silently relabelling lb as
            # kg (or kJ as kcal) would create false clinical-looking data.
            "metric": metric, "value": _value(attributes["value"], apple_type),
            "unit": attributes.get("unit", canonical_unit),
            "observed_at": attributes["endDate"], "provenance_id": _provenance(attributes), "semantic": "measured",
        }
    # HealthKit sleep analysis is a category interval rather than a numerical
    # quantity.  Only original periods marked asleep qualify; time-in-bed and
    # awake intervals are preserved by Apple but must not inflate sleep.
    if apple_type == _SLEEP_ANALYSIS and "asleep" in attributes.get("value", "").lower():
        start = datetime.fromisoformat(attributes["startDate"])
        end = datetime.fromisoformat(attributes["endDate"])
        minutes = (end - start).total_seconds() / 60
        if minutes > 0:
            return {
                "metric": "sleep_duration", "value": minutes, "unit": "min",
                "observed_at": attributes["endDate"], "provenance_id": _provenance(attributes), "semantic": "measured",
            }
    if apple_type == _MINDFUL_SESSION:
        start = datetime.fromisoformat(attributes["startDate"])
        end = datetime.fromisoformat(attributes["endDate"])
        minutes = (end - start).total_seconds() / 60
        if minutes > 0:
            return {
                "metric": "mindfulness_duration", "value": minutes, "unit": "min",
                "observed_at": attributes["endDate"], "provenance_id": _provenance(attributes), "semantic": "measured",
            }
    return None


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
                observation = observation_from_apple_record(attrs)
                if observation is None:
                    ignored.add(attrs.get("type", "unknown"))
                else:
                    observations.append(observation)
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
