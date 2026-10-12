from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo, timezone
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Iterable
from zoneinfo import ZoneInfo


class BaselineEra(StrEnum):
    GARMIN = "ERA_GARMIN"
    APPLE_TRANSITION = "ERA_APPLE_TRANSITION"
    APPLE_BASELINE = "ERA_APPLE_BASELINE"


class DecisionState(StrEnum):
    ACTIVA = "ACTIVA"
    AJUSTA = "AJUSTA"
    DETEN = "DETEN"
    ABSTENERSE = "ABSTENERSE"
    ESCALAR = "ESCALAR"


APPLE_HEALTH = "APPLE_HEALTH"
SPEEDIANCE = "SPEEDIANCE"
GARMIN_LEGACY = "GARMIN_LEGACY"

# The operator approved a narrowly bounded completion extension: the current
# Apple sleep series has 26 distinct days in the standard 42-day window and
# may use the two immediately preceding local days.  This alters only whether
# a historical baseline is mature; it never authorizes a medical decision.
_BASELINE_DAYS = 28
_BASELINE_WINDOW_DAYS = 44


@dataclass(frozen=True, slots=True)
class MetricObservation:
    metric: str
    value: float
    unit: str
    observed_at: datetime
    source: str
    provenance_id: str
    semantic: str = "measured"
    confidence: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.semantic not in {"measured", "estimated", "manual", "derived", "model_interpreted", "chatgpt_health_transcription"}:
            raise ValueError("semantic must identify the kind of observation")
        if not self.source or not self.provenance_id or not self.unit:
            raise ValueError("source, provenance_id, and unit are required")
        if not isfinite(self.value) or self.value < 0:
            raise ValueError("observation value must be finite and non-negative")
        # These are data-integrity limits, not medical alert thresholds.  They
        # only discard values a sensor cannot plausibly encode; escalation
        # thresholds remain a clinician-owned policy.
        if self.metric in {"heart_rate", "resting_heart_rate", "walking_heart_rate_average"} and not 0 < self.value <= 300:
            raise ValueError("heart-rate observation is outside sensor bounds")
        if self.metric == "oxygen_saturation" and not 0 < self.value <= 100:
            raise ValueError("oxygen-saturation observation is outside sensor bounds")
        bounds = {
            "weight": (1, 500), "body_mass": (1, 500), "lean_body_mass": (1, 500),
            "respiratory_rate": (1, 100), "sleep_duration": (1, 24 * 60),
        }
        if self.metric in bounds and not bounds[self.metric][0] <= self.value <= bounds[self.metric][1]:
            raise ValueError(f"{self.metric} observation is outside sensor bounds")


@dataclass(frozen=True, slots=True)
class WorkoutSession:
    workout_id: str
    start_time: datetime
    end_time: datetime
    source: str
    activity_type: str
    exercises: tuple[dict, ...] = ()
    physiological_metrics: tuple[MetricObservation, ...] = ()

    def __post_init__(self) -> None:
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("workout times must be timezone-aware")
        if self.end_time <= self.start_time:
            raise ValueError("workout end_time must follow start_time")
        if not self.workout_id or not self.source or not self.activity_type:
            raise ValueError("workout identity, source, and activity_type are required")


@dataclass(slots=True)
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    rejected: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DecisionAssessment:
    state: DecisionState
    confidence: str
    factors: tuple[str, ...]
    missing: tuple[str, ...]
    automated_action: bool = False


def _canonical_unit(metric: str, value: float, unit: str) -> tuple[float, str]:
    key = unit.strip().lower()
    if metric in {"body_mass", "weight"}:
        if key in {"lb", "lbs", "pound", "pounds"}:
            return round(value * 0.45359237, 6), "kg"
        if key == "kg":
            return value, "kg"
    if metric in {"distance", "six_minute_walk_distance"}:
        if key in {"mi", "mile", "miles"}:
            return round(value * 1.609344, 6), "km"
        if key == "km":
            return value, "km"
    if metric in {"sleep_duration", "workout_duration"}:
        if key in {"h", "hr", "hour", "hours"}:
            return round(value * 60, 6), "min"
        if key in {"min", "minute", "minutes"}:
            return value, "min"
    if metric in {"heart_rate", "resting_heart_rate", "walking_heart_rate_average"} and key in {"bpm", "count/min"}:
        return value, "bpm"
    if metric == "hrv" and key == "ms":
        return value, "ms"
    if metric == "active_energy" and key in {"kcal", "cal"}:
        return value, "kcal"
    if metric == "active_energy" and key in {"kj", "kilojoule", "kilojoules"}:
        return round(value / 4.184, 6), "kcal"
    # Apple Health / ChatGPT Health catalog. Keep this explicit: accepting an
    # unknown unit would make a plausible-looking Health OS conclusion unsafe.
    if metric in {"oxygen_saturation", "body_fat_percentage", "walking_asymmetry", "double_support_percentage"} and key in {"%", "percent", "percentage"}:
        return value, "%"
    if metric in {"respiratory_rate"} and key in {"breaths/min", "breaths_per_minute", "brpm"}:
        return value, "breaths/min"
    if metric in {"systolic_blood_pressure", "diastolic_blood_pressure"} and key in {"mmhg", "mm hg"}:
        return value, "mmHg"
    if metric in {"steps", "flights_climbed"} and key in {"count", "steps", "flights"}:
        return value, "count"
    if metric in {"exercise_time", "stand_time", "mindfulness_duration"} and key in {"min", "minute", "minutes"}:
        return value, "min"
    if metric in {"lean_body_mass"} and key == "kg":
        return value, "kg"
    if metric in {"distance", "six_minute_walk_distance"} and key in {"m", "meter", "meters"}:
        return round(value / 1000, 6), "km"
    if metric == "height":
        if key in {"m", "meter", "meters"}:
            return round(value * 100, 6), "cm"
        if key in {"cm", "centimeter", "centimeters"}:
            return value, "cm"
    if metric == "bmi" and key in {"kg/m2", "kg/m²"}:
        return value, "kg/m2"
    if metric in {"walking_speed", "stair_ascent_speed", "stair_descent_speed"} and key in {"km/h", "kph"}:
        return value, "km/h"
    if metric == "step_length" and key in {"cm", "centimeter", "centimeters"}:
        return value, "cm"
    if metric in {"dietary_protein", "dietary_carbohydrates", "dietary_fat", "dietary_fiber", "dietary_sugar", "dietary_saturated_fat"} and key in {"g", "gram", "grams"}:
        return value, "g"
    if metric in {"dietary_sodium", "dietary_potassium", "dietary_calcium", "dietary_magnesium", "dietary_phosphorus", "dietary_iron", "dietary_vitamin_c"} and key in {"mg", "milligram", "milligrams"}:
        return value, "mg"
    if metric in {"environmental_noise", "headphone_noise"} and key in {"db", "dba", "db(a)"}:
        return value, "dBA"
    raise ValueError(f"unsupported unit {unit!r} for metric {metric!r}")


class HealthOS:
    """In-memory reference implementation of the policy-bearing Health OS core.

    Persistence and producer authentication belong to the deployment adapter;
    this class owns semantics, idempotence, provenance, and safety checks.
    """

    def __init__(self, timezone: str | tzinfo = "America/Mexico_City") -> None:
        # Deployments use the IANA name; accepting a tzinfo keeps tests and
        # offline import tools deterministic even on hosts without a zoneinfo DB.
        self.timezone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
        self._observations: dict[str, MetricObservation] = {}
        self._workouts: dict[str, WorkoutSession] = {}
        self._workout_links: dict[str, set[str]] = {}
        self._apple_baseline_started: datetime | None = None

    @staticmethod
    def _observation_key(item: MetricObservation) -> str:
        # Apple may mirror an identical sample from iPhone and Watch with
        # distinct provenance IDs.  Idempotence belongs to the measurement,
        # not the device label; source remains part of the key to avoid
        # collapsing independently produced Speediance evidence.
        raw = "|".join((item.source, item.metric, str(item.value), item.unit,
                        item.observed_at.astimezone(timezone.utc).isoformat()))
        return sha256(raw.encode()).hexdigest()

    def ingest_observations(self, observations: Iterable[MetricObservation]) -> IngestResult:
        result = IngestResult()
        for original in observations:
            try:
                value, unit = _canonical_unit(original.metric, original.value, original.unit)
                item = MetricObservation(
                    metric=original.metric, value=value, unit=unit,
                    observed_at=original.observed_at, source=original.source,
                    provenance_id=original.provenance_id, semantic=original.semantic,
                    confidence=original.confidence,
                )
            except ValueError as exc:
                result.rejected.append(str(exc))
                continue
            key = self._observation_key(item)
            if key in self._observations:
                result.duplicates += 1
            else:
                self._observations[key] = item
                result.accepted += 1
        return result

    @staticmethod
    def _overlap(a: WorkoutSession, b: WorkoutSession) -> timedelta:
        return max(timedelta(), min(a.end_time, b.end_time) - max(a.start_time, b.start_time))

    @staticmethod
    def _workout_key(session: WorkoutSession) -> str:
        return sha256(f"{session.source}|{session.workout_id}".encode()).hexdigest()

    @staticmethod
    def _activity_family(value: str) -> str:
        normalized = value.lower().replace("-", "_").replace(" ", "_")
        if normalized in {"strength", "strength_training", "functional_strength_training", "traditional_strength_training", "weight_training"}:
            return "strength"
        return normalized

    def ingest_workout(self, session: WorkoutSession) -> str:
        """Store a session or link it to its matching Apple/Speediance session.

        A Speediance strength session and an Apple Health strength workout are
        one logical workout only if their activity type agrees and they overlap
        by at least five minutes.  Near timestamps alone never merge sessions.
        """
        key = self._workout_key(session)
        if key in self._workouts:
            return key
        for existing_key, existing in self._workouts.items():
            compatible_sources = {existing.source, session.source} == {APPLE_HEALTH, SPEEDIANCE}
            overlap = self._overlap(existing, session)
            shorter = min(existing.end_time - existing.start_time, session.end_time - session.start_time)
            starts_close = abs(existing.start_time - session.start_time) <= timedelta(minutes=15)
            if compatible_sources and self._activity_family(existing.activity_type) == self._activity_family(session.activity_type) and starts_close and overlap >= shorter / 2:
                self._workouts[key] = session
                linked = self._workout_links.setdefault(existing_key, {existing_key})
                linked.add(key)
                self._workout_links[key] = linked
                return key
        self._workouts[key] = session
        self._workout_links[key] = {key}
        return key

    def logical_workouts(self) -> list[tuple[WorkoutSession, ...]]:
        emitted: set[str] = set()
        groups: list[tuple[WorkoutSession, ...]] = []
        for key, links in self._workout_links.items():
            if key in emitted:
                continue
            emitted.update(links)
            groups.append(tuple(self._workouts[item] for item in sorted(links)))
        return groups

    def set_apple_transition(self, started_at: datetime) -> None:
        if started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        self._apple_baseline_started = started_at

    def baseline_era(self, at: datetime) -> BaselineEra:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        if self._apple_baseline_started is None or at < self._apple_baseline_started:
            return BaselineEra.GARMIN
        coverage = {o.observed_at.astimezone(self.timezone).date() for o in self._observations.values()
                    if o.source == APPLE_HEALTH and o.observed_at >= self._apple_baseline_started
                    and o.semantic not in {"model_interpreted", "chatgpt_health_transcription"}}
        if len(coverage) < 14:
            return BaselineEra.APPLE_TRANSITION
        return BaselineEra.APPLE_BASELINE

    def metric_baseline_days(self, metric: str, at: datetime,
                             window_days: int = _BASELINE_WINDOW_DAYS) -> int:
        """Number of Apple local days supporting one metric's current baseline.

        Baselines belong to individual metrics; a long steps history cannot make
        a seven-day HRV series look mature.
        """
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        # Baselines are defined in operator-local *calendar days*, not a
        # rolling number of 24-hour instants.  A night ending at 06:00 on the
        # first eligible local day must not be discarded merely because the
        # Brief runs later that afternoon.
        lower_day = (at.astimezone(self.timezone).date() - timedelta(days=window_days)).isoformat()
        return len({self.local_day(x.observed_at) for x in self._observations.values()
                    if x.source == APPLE_HEALTH and x.metric == metric
                    and self.local_day(x.observed_at) >= lower_day
                    and x.semantic not in {"model_interpreted", "chatgpt_health_transcription"}})

    def metric_has_stable_baseline(self, metric: str, at: datetime) -> bool:
        return self.metric_baseline_days(metric, at) >= _BASELINE_DAYS

    def assess_readiness(self, now: datetime, subjective_flags: Iterable[str] = ()) -> DecisionAssessment:
        """Return a conservative, explainable state; never changes a plan itself."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        # Subjective language is not a clinician-approved symptom triage
        # policy.  Do not make a medical decision from developer-owned keyword
        # lists; the service remains conservative until a policy is supplied.
        flags_present = any(str(flag).strip() for flag in subjective_flags)
        quality = self.data_quality(now)
        if quality["stale"] or quality["legacy_after_transition"]:
            return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("data quality does not support a readiness decision",), ("recent Apple Health coverage",), False)
        required = ("sleep_duration", "resting_heart_rate")
        missing = tuple(metric for metric in required if not self.metric_has_stable_baseline(metric, now))
        if missing:
            return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("Apple baseline is still metric-specific and incomplete",), missing, False)
        non_evidence = [x for x in self._observations.values()
                        if x.semantic in {"model_interpreted", "chatgpt_health_transcription"}
                        and x.observed_at >= now - timedelta(days=3)]
        if non_evidence:
            return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("non-evidence values cannot drive readiness",), (), False)
        if flags_present:
            return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("subjective flags require a clinician-reviewed policy",), (), False)
        # A stable personal baseline alone does not establish a safe medical
        # range.  A clinician-reviewed policy is intentionally required before
        # ACTIVA can be emitted from physiological data.
        return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("no clinician-reviewed safety policy is configured",), (), False)

    def local_day(self, at: datetime) -> str:
        return at.astimezone(self.timezone).date().isoformat()

    def data_quality(self, now: datetime) -> dict[str, object]:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        apple = [x for x in self._observations.values()
                 if x.source == APPLE_HEALTH and x.semantic not in {"model_interpreted", "chatgpt_health_transcription"}]
        days = {self.local_day(x.observed_at) for x in apple}
        stale = not apple or max(x.observed_at for x in apple) < now - timedelta(days=2)
        mixed = [x.metric for x in self._observations.values() if x.source == GARMIN_LEGACY and self.baseline_era(x.observed_at) != BaselineEra.GARMIN]
        return {
            "coverage_days": len(days),
            "stale": stale,
            "legacy_after_transition": sorted(set(mixed)),
            "logical_workouts": len(self.logical_workouts()),
            "raw_workouts": len(self._workouts),
            "pass": not stale and not mixed,
        }
