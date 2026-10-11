"""Metis Health OS domain core.

This package deliberately has no Garmin runtime dependency.  Producers (an
authorized Apple Health exporter or Speediance adapter) submit normalized,
provenance-bearing observations to this layer.
"""

from .core import BaselineEra, HealthOS, MetricObservation, WorkoutSession

__all__ = ["BaselineEra", "HealthOS", "MetricObservation", "WorkoutSession"]
from .core import APPLE_HEALTH, GARMIN_LEGACY, SPEEDIANCE, BaselineEra, DecisionAssessment, DecisionState, HealthOS, MetricObservation, WorkoutSession
from .importers import observations_from_export, workout_from_export
from .apple_export import AppleExportResult, parse_apple_health_export

__all__ = [
    "APPLE_HEALTH", "GARMIN_LEGACY", "SPEEDIANCE", "BaselineEra", "DecisionAssessment", "DecisionState", "HealthOS",
    "MetricObservation", "WorkoutSession", "observations_from_export", "workout_from_export", "AppleExportResult", "parse_apple_health_export",
]
