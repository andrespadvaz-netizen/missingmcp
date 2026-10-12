"""Validated boundary adapters for Apple Health and Speediance exports.

Apple Health has no general server-to-server API.  A user-authorized iOS
producer/exporter must submit this normalized, provenance-preserving shape to
the deployment adapter.  This module deliberately accepts no Garmin payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from .core import APPLE_HEALTH, SPEEDIANCE, MetricObservation, WorkoutSession


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include an offset")
    return parsed


def observations_from_export(source: str, records: Iterable[Mapping[str, Any]]) -> list[MetricObservation]:
    """Convert a normalized producer export without inventing measurements.

    Every record requires its original source identifier, value, unit and an
    offset-aware observation time.  Missing values are rejected rather than
    silently mapped to zero.
    """
    if source not in {APPLE_HEALTH, SPEEDIANCE}:
        raise ValueError("Health OS accepts only Apple Health or Speediance producers")
    converted: list[MetricObservation] = []
    for record in records:
        try:
            converted.append(MetricObservation(
                metric=str(record["metric"]), value=float(record["value"]),
                unit=str(record["unit"]), observed_at=_timestamp(record["observed_at"], "observed_at"),
                source=source, provenance_id=str(record["provenance_id"]),
                semantic=str(record.get("semantic", "measured")),
                confidence=(str(record["confidence"]) if record.get("confidence") is not None else None),
                device_source=(str(record["device_source"]) if record.get("device_source") is not None else None),
            ))
        except KeyError as exc:
            raise ValueError(f"missing required observation field {exc.args[0]!r}") from exc
    return converted


def workout_from_export(source: str, record: Mapping[str, Any]) -> WorkoutSession:
    """Convert a normalized workout export while retaining the producer ID."""
    if source not in {APPLE_HEALTH, SPEEDIANCE}:
        raise ValueError("Health OS accepts only Apple Health or Speediance producers")
    try:
        exercises = record.get("exercises", ())
        if not isinstance(exercises, (list, tuple)):
            raise ValueError("exercises must be a list when supplied")
        return WorkoutSession(
            workout_id=str(record["workout_id"]),
            start_time=_timestamp(record["start_time"], "start_time"),
            end_time=_timestamp(record["end_time"], "end_time"),
            source=source,
            activity_type=str(record["activity_type"]),
            exercises=tuple(exercises),
        )
    except KeyError as exc:
        raise ValueError(f"missing required workout field {exc.args[0]!r}") from exc
