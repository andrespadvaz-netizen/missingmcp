"""Adapter for the "Health Auto Export" iOS app (REST API automation, JSON).

The app reads Apple Health on the iPhone and POSTs JSON on a schedule.  This
module maps that payload onto the normalized Apple Health observation shape the
repository already validates.  It never invents values: unknown metrics are
reported by name, aggregated sleep is refused (it would double count the
segments), and every unit goes through the core's explicit unit table.

Payload (help.healthyapps.dev, export format):
    {"data": {"metrics": [{"name", "units", "data": [{"date", "qty", "source"}]}],
              "workouts": [{"id", "name", "start", "end", "duration"}]}}
Dates use ``yyyy-MM-dd HH:mm:ss Z``.  Sleep must be exported unaggregated
("Summarize Data" off): one entry per segment with startDate, endDate, value.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Mapping

from .apple_export import device_family

# App metric identifier -> Health OS metric.  Only metrics with an explicit
# meaning here are accepted; everything else is reported as ignored.
_METRICS = {
    "resting_heart_rate": "resting_heart_rate",
    "heart_rate_variability": "hrv",
    "heart_rate_variability_sdnn": "hrv",
    "respiratory_rate": "respiratory_rate",
    "blood_oxygen_saturation": "oxygen_saturation",
    "weight_&_body_mass": "weight",
    "weight_body_mass": "weight",
    "body_mass": "weight",
    "body_fat_percentage": "body_fat_percentage",
    "lean_body_mass": "lean_body_mass",
    "walking_heart_rate_average": "walking_heart_rate_average",
    "step_count": "steps",
    "active_energy": "active_energy",
    "walking_running_distance": "distance",
    "apple_exercise_time": "exercise_time",
    "blood_pressure_systolic": "systolic_blood_pressure",
    "blood_pressure_diastolic": "diastolic_blood_pressure",
}
_ASLEEP_STAGES = {"asleep", "core", "deep", "rem"}


def _date(value: Any) -> datetime:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("date without UTC offset")
    return parsed


def _provenance(*parts: Any) -> str:
    return "health-auto-export:" + sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def _family(source: Any) -> str | None:
    return device_family({"sourceName": str(source)}) if source else None


def parse_health_auto_export(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Return (observations, workouts, skipped) where skipped counts reasons by name only."""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("payload must contain a data object")
    observations: list[dict[str, Any]] = []
    workouts: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str, n: int = 1) -> None:
        skipped[reason] = skipped.get(reason, 0) + n

    for metric in data.get("metrics") or []:
        name = str(metric.get("name", "")).strip().lower()
        points = metric.get("data") or []
        units = str(metric.get("units", ""))
        if name == "sleep_analysis":
            for point in points:
                stage = str(point.get("value", "")).strip().lower()
                if "startDate" not in point or "endDate" not in point:
                    skip("sleep_aggregated_not_supported")
                    continue
                if stage not in _ASLEEP_STAGES:
                    continue  # awake / in bed: preserved by Apple, never sleep
                start, end = _date(point["startDate"]), _date(point["endDate"])
                minutes = (end - start).total_seconds() / 60
                if minutes <= 0:
                    skip("sleep_non_positive_interval")
                    continue
                # Same shape as the native export importer (minutes, observed at
                # the segment end) so a segment already imported deduplicates.
                observations.append({
                    "metric": "sleep_duration", "value": minutes, "unit": "min",
                    "observed_at": end.isoformat(), "semantic": "measured",
                    "provenance_id": _provenance("sleep", point["startDate"], point["endDate"], stage, point.get("source", "")),
                    "device_source": _family(point.get("source")),
                })
            continue
        target = _METRICS.get(name)
        if target is None:
            skip("ignored_metric:" + name[:48], len(points))
            continue
        for point in points:
            if "qty" not in point or point.get("qty") is None:
                skip("missing_qty")
                continue
            observations.append({
                "metric": target, "value": float(point["qty"]), "unit": units,
                "observed_at": _date(point["date"]).isoformat(), "semantic": "measured",
                "provenance_id": _provenance(name, point["date"], point["qty"], units, point.get("source", "")),
                "device_source": _family(point.get("source")),
            })

    for workout in data.get("workouts") or []:
        try:
            workouts.append({
                "workout_id": "health-auto-export:" + str(workout["id"]),
                "start_time": _date(workout["start"]).isoformat(),
                "end_time": _date(workout["end"]).isoformat(),
                "activity_type": str(workout.get("name", "other")),
            })
        except (KeyError, ValueError):
            skip("workout_invalid")
    return observations, workouts, skipped
