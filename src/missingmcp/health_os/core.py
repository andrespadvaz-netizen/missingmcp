from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo, timezone
from enum import StrEnum
from hashlib import sha256
import re
import unicodedata
from math import isfinite
from typing import Iterable
from zoneinfo import ZoneInfo


class BaselineEra(StrEnum):
    GARMIN = "ERA_GARMIN"
    APPLE_TRANSITION = "ERA_APPLE_TRANSITION"
    APPLE_BASELINE = "ERA_APPLE_BASELINE"


class DecisionState(StrEnum):
    # These are operational data states, not medical or training directives.
    INFORMATIVO = "INFORMATIVO"
    REVISAR_TECNICO = "REVISAR_TECNICO"
    ABSTENERSE = "ABSTENERSE"
    # Política Operativa v0.5, section 6.0: emitted ONLY when the operator
    # self-reports a symptom from the closed list below.  Never from metrics,
    # never from model inference.  The system calls and notifies no one.
    URGENTE = "URGENTE"


# Política Operativa v0.5, sections 6.0 and 7.  Fixed operator-approved text.
EMERGENCY_INSTRUCTION = ("Detén la actividad y llama al 911 ahora. No esperes una lectura del "
                         "dispositivo ni respuesta de tu médico.")
EMERGENCY_LEGEND = ("Health OS apoya la decisión; no diagnostica ni sustituye el juicio clínico ni los "
                    "servicios de urgencia (911). Ante dolor o presión en el pecho, debilidad súbita, "
                    "alteración del habla, falta de aire intensa o pérdida de respuesta, llama al 911 "
                    "sin esperar al sistema.")
NON_EVIDENCE_SEMANTICS = frozenset({"model_interpreted", "chatgpt_health_transcription"})
# Producer families written into Apple Health.  Política Operativa v0.5, 4.3:
# a device change restarts the baseline, so only Apple Watch (or legacy rows
# without a family) may feed Apple-era baselines and decisions.
DEVICE_FAMILIES = frozenset({"apple_watch", "garmin", "iphone", "other_app"})
_NON_WATCH_FAMILIES = frozenset({"garmin", "iphone", "other_app"})

# Closed, bilingual alarm list (Política Operativa v0.5, section 6.0).  Matching
# is case-, accent- and whitespace-insensitive on the operator's self-report.
_ALARM_TERMS = (
    # Chest pain or pressure
    "dolor en el pecho", "dolor de pecho", "presion en el pecho", "opresion en el pecho",
    "chest pain", "chest pressure", "chest tightness",
    # Sudden one-sided weakness, facial droop, speech, vision or balance change
    "debilidad subita", "cara caida", "alteracion del habla", "no puedo hablar",
    "perdida de vision", "perdida del equilibrio",
    "sudden weakness", "face drooping", "slurred speech", "speech difficulty",
    "vision loss", "loss of balance",
    # Loss of response with absent or abnormal breathing
    "perdida de respuesta", "no responde", "inconsciente",
    "unresponsive", "loss of consciousness", "not breathing",
    # Severe or worsening shortness of breath
    "falta de aire intensa", "no puedo respirar", "ahogo",
    "severe shortness of breath", "can't breathe", "cant breathe", "cannot breathe",
)


def _normalize_report(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain.replace("\u2019", "'")).strip().lower()


def is_alarm_report(text: str) -> bool:
    normalized = _normalize_report(text)
    return any(term in normalized for term in _ALARM_TERMS)


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
    device_source: str | None = None

    def __post_init__(self) -> None:
        if self.device_source is not None and self.device_source not in DEVICE_FAMILIES:
            raise ValueError("device_source must be a known producer family")
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
        if self.metric in {"heart_rate", "resting_heart_rate", "walking_heart_rate_average"} and not 20 <= self.value <= 250:
            raise ValueError("heart-rate observation is outside sensor bounds")
        if self.metric == "hrv" and not 0 < self.value <= 300:
            raise ValueError("hrv observation is outside sensor bounds")
        if self.metric == "oxygen_saturation" and not 0 < self.value <= 100:
            raise ValueError("oxygen-saturation observation is outside sensor bounds")
        bounds = {
            "weight": (1, 500), "body_mass": (1, 500), "lean_body_mass": (1, 500),
            "respiratory_rate": (4, 60),
        }
        if self.metric in bounds and not bounds[self.metric][0] <= self.value <= bounds[self.metric][1]:
            raise ValueError(f"{self.metric} observation is outside sensor bounds")
        # Apple Watch sleep stages arrive as short segments (often under a
        # minute).  Política Operativa v0.5: total >= 0 and <= 1,440 minutes,
        # so any positive segment up to a full day is valid evidence.
        if self.metric == "sleep_duration" and not 0 < self.value <= 24 * 60:
            raise ValueError("sleep_duration observation is outside sensor bounds")


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
    if metric in {"oxygen_saturation", "body_fat_percentage"} and key in {"%", "percent", "percentage"}:
        # Apple stores these proportions as 0.0-1.0; a real reading is never
        # at or below one percent, so a fraction is converted, not mixed in.
        return (round(value * 100, 6) if value <= 1 else value), "%"
    if metric in {"walking_asymmetry", "double_support_percentage"} and key in {"%", "percent", "percentage"}:
        return value, "%"
    if metric in {"respiratory_rate"} and key in {"breaths/min", "breaths_per_minute", "brpm", "count/min"}:
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
    # HealthKit stores BMI as a dimensionless "count".
    if metric == "bmi" and key in {"kg/m2", "kg/m²", "count"}:
        return value, "kg/m2"
    if metric in {"walking_speed", "stair_ascent_speed", "stair_descent_speed"} and key in {"km/h", "kph", "km/hr"}:
        return value, "km/h"
    if metric in {"walking_speed", "stair_ascent_speed", "stair_descent_speed"} and key in {"m/s", "m/sec"}:
        return round(value * 3.6, 6), "km/h"
    if metric == "step_length" and key in {"cm", "centimeter", "centimeters"}:
        return value, "cm"
    if metric in {"dietary_protein", "dietary_carbohydrates", "dietary_fat", "dietary_fiber", "dietary_sugar", "dietary_saturated_fat"} and key in {"g", "gram", "grams"}:
        return value, "g"
    if metric in {"dietary_sodium", "dietary_potassium", "dietary_calcium", "dietary_magnesium", "dietary_phosphorus", "dietary_iron", "dietary_vitamin_c"} and key in {"mg", "milligram", "milligrams"}:
        return value, "mg"
    # Apple writes audio exposure as "dBASPL" (A-weighted sound pressure level).
    if metric in {"environmental_noise", "headphone_noise"} and key in {"db", "dba", "db(a)", "dbaspl"}:
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
        # Historical rows may predate a stricter validation release.  They are
        # retained as encrypted evidence, but cannot silently influence the
        # operational snapshot.
        self._persisted_invalid_records = 0

    @staticmethod
    def _apple_era_evidence(item: "MetricObservation") -> bool:
        return (item.source == APPLE_HEALTH and item.semantic not in NON_EVIDENCE_SEMANTICS
                and item.device_source not in _NON_WATCH_FAMILIES)

    def record_persisted_invalid(self) -> None:
        self._persisted_invalid_records += 1

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
                    confidence=original.confidence, device_source=original.device_source,
                )
            except ValueError as exc:
                result.rejected.append(str(exc))
                continue
            key = self._observation_key(item)
            if key in self._observations:
                existing = self._observations[key]
                if existing.device_source is None and item.device_source is not None:
                    # The same measurement re-imported with its producer family:
                    # the tagged copy wins so device separation is not lost.
                    self._observations[key] = item
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
        raw = re.sub(r"^HKWorkoutActivityType", "", value.strip())
        snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
        normalized = re.sub(r"[\s\-]+", "_", snake).lower()
        if normalized in {"strength", "strength_training", "functional_strength_training",
                          "traditional_strength_training", "weight_training", "core_training"}:
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
            same_family = self._activity_family(existing.activity_type) == self._activity_family(session.activity_type)
            if (existing.source == session.source and same_family
                    and existing.start_time == session.start_time and existing.end_time == session.end_time):
                # The same producer re-sent one session under a new identifier.
                return existing_key
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
                    if self._apple_era_evidence(o) and o.observed_at >= self._apple_baseline_started}
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
        if self._apple_baseline_started is None:
            # Without an operator-confirmed Apple era there is no baseline.
            return 0
        lower_day = (at.astimezone(self.timezone).date() - timedelta(days=window_days)).isoformat()
        return len({self.local_day(x.observed_at) for x in self._observations.values()
                    if self._apple_era_evidence(x) and x.metric == metric
                    and x.observed_at >= self._apple_baseline_started
                    and self.local_day(x.observed_at) >= lower_day})

    def metric_has_stable_baseline(self, metric: str, at: datetime) -> bool:
        return self.metric_baseline_days(metric, at) >= _BASELINE_DAYS

    def assess_readiness(self, now: datetime, subjective_flags: Iterable[str] = ()) -> DecisionAssessment:
        """Return a conservative, explainable state; never changes a plan itself.

        Order (Política Operativa v0.5): an alarm symptom from the closed list
        overrides everything; any other self-reported symptom abstains; then
        data quality, the Apple era, and metric-specific baselines.  Values
        interpreted by a model or transcribed from ChatGPT Health are isolated:
        they never count as evidence and never change the state.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        flags = [str(flag) for flag in subjective_flags if str(flag).strip()]
        if any(is_alarm_report(flag) for flag in flags):
            return DecisionAssessment(DecisionState.URGENTE, "high", (EMERGENCY_INSTRUCTION,), (), False)
        if flags:
            return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("self-reported symptom outside the closed alarm list",), (), False)
        quality = self.data_quality(now)
        if quality["stale"] or quality["legacy_after_transition"] or quality["persisted_invalid_records"]:
            return DecisionAssessment(DecisionState.REVISAR_TECNICO, "low", ("data quality needs technical review",), ("recent Apple Health coverage",), False)
        if self._apple_baseline_started is None:
            return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("Apple era start is not recorded",), ("apple_transition_started_at",), False)
        required = ("sleep_duration", "resting_heart_rate")
        missing = tuple(metric for metric in required if not self.metric_has_stable_baseline(metric, now))
        if missing:
            return DecisionAssessment(DecisionState.ABSTENERSE, "low", ("Apple baseline is still metric-specific and incomplete",), missing, False)
        # A stable baseline allows only an informational presentation.  It is
        # not a medical clearance, diagnosis, prescription or training order.
        return DecisionAssessment(DecisionState.INFORMATIVO, "medium", ("Apple Health coverage and metric-specific baselines are stable",), (), False)

    # Descriptive weekly trends (Política Operativa v0.5: "tendencia-descriptiva",
    # app view only).  Daily values are aggregated first; nothing per-reading
    # ever leaves the core.  Steps and energy are excluded on purpose: iPhone
    # and Watch report overlapping intervals that cannot be de-duplicated from
    # exported samples without double counting.
    _TREND_METRICS = {
        "sleep_duration": ("sum", "min"),
        "resting_heart_rate": ("mean", "bpm"),
        "hrv": ("mean", "ms"),
        "respiratory_rate": ("mean", "breaths/min"),
        "oxygen_saturation": ("mean", "%"),
    }

    def _daily_values(self, metric: str) -> dict[str, float]:
        how, _ = self._TREND_METRICS[metric]
        start = self._apple_baseline_started
        buckets: dict[str, list[float]] = {}
        for item in self._observations.values():
            if (not self._apple_era_evidence(item) or item.metric != metric
                    or start is None or item.observed_at < start):
                continue
            buckets.setdefault(self.local_day(item.observed_at), []).append(item.value)
        return {day: (sum(v) if how == "sum" else sum(v) / len(v)) for day, v in buckets.items()}

    def weekly_trends(self, now: datetime, weeks: int = 4) -> dict[str, object]:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if self._apple_baseline_started is None:
            return {"available": False, "reason": "Apple era start is not recorded", "weeks": []}
        today = now.astimezone(self.timezone).date()
        this_monday = today - timedelta(days=today.weekday())
        rows: list[dict[str, object]] = []
        for metric, (how, unit) in self._TREND_METRICS.items():
            daily = self._daily_values(metric)
            for back in range(weeks - 1, -1, -1):
                week_start = this_monday - timedelta(weeks=back)
                days = [(week_start + timedelta(days=i)).isoformat() for i in range(7)]
                values = [daily[d] for d in days if d in daily]
                if not values:
                    continue
                prior = [daily[(week_start - timedelta(days=i)).isoformat()] for i in range(1, 29)
                         if (week_start - timedelta(days=i)).isoformat() in daily]
                mean = sum(values) / len(values)
                baseline = sum(prior) / len(prior) if len(prior) >= 14 else None
                rows.append({
                    "metric": metric, "unit": unit, "week_start": week_start.isoformat(),
                    "aggregate": "mean_of_nightly_totals" if how == "sum" else "mean_of_daily_means",
                    "value": round(mean, 1), "valid_days": len(values),
                    "baseline_value": round(baseline, 1) if baseline is not None else None,
                    "baseline_days": len(prior),
                    "change_vs_baseline": round(mean - baseline, 1) if baseline is not None else None,
                    "rule": "tendencia-descriptiva v0.1",
                })
        for back in range(weeks - 1, -1, -1):
            week_start = this_monday - timedelta(weeks=back)
            week_end = week_start + timedelta(days=7)
            sessions = [group for group in self.logical_workouts()
                        if week_start <= group[0].start_time.astimezone(self.timezone).date() < week_end
                        and group[0].start_time >= self._apple_baseline_started]
            if not sessions:
                continue
            minutes = sum(max(s.end_time - s.start_time for s in group).total_seconds() / 60 for group in sessions)
            strength = sum(1 for group in sessions if any(self._activity_family(s.activity_type) == "strength" for s in group))
            rows.append({
                "metric": "workouts", "unit": "sessions", "week_start": week_start.isoformat(),
                "aggregate": "weekly_count", "value": len(sessions), "valid_days": None,
                "baseline_value": None, "baseline_days": None, "change_vs_baseline": None,
                "total_minutes": round(minutes), "strength_sessions": strength,
                "rule": "tendencia-descriptiva v0.1",
            })
        return {"available": True, "reason": None, "weeks": rows}

    # Long-horizon monthly history, separated by producer family so Garmin,
    # iPhone and Apple Watch series are shown side by side, never blended.
    _HISTORY_METRICS = {
        "weight": "mean", "body_fat_percentage": "mean", "resting_heart_rate": "mean",
        "heart_rate": "mean", "hrv": "mean", "oxygen_saturation": "mean", "respiratory_rate": "mean",
        "sleep_duration": "sum", "steps": "sum", "active_energy": "sum", "distance": "sum",
    }

    def monthly_history(self) -> list[dict[str, object]]:
        daily: dict[tuple[str, str, str], list[float]] = {}
        for item in self._observations.values():
            if (item.source != APPLE_HEALTH or item.semantic in NON_EVIDENCE_SEMANTICS
                    or item.metric not in self._HISTORY_METRICS):
                continue
            key = (item.metric, item.device_source or "untagged", self.local_day(item.observed_at))
            daily.setdefault(key, []).append(item.value)
        monthly: dict[tuple[str, str, str], list[float]] = {}
        for (metric, family, day), values in daily.items():
            value = sum(values) if self._HISTORY_METRICS[metric] == "sum" else sum(values) / len(values)
            monthly.setdefault((metric, family, day[:7]), []).append(value)
        rows = []
        for (metric, family, month), values in sorted(monthly.items()):
            rows.append({
                "metric": metric, "device_source": family, "month": month,
                "aggregate": "mean_of_daily_totals" if self._HISTORY_METRICS[metric] == "sum" else "mean_of_daily_means",
                "value": round(sum(values) / len(values), 1), "valid_days": len(values),
                "rule": "tendencia-descriptiva v0.1",
            })
        return rows

    def local_day(self, at: datetime) -> str:
        return at.astimezone(self.timezone).date().isoformat()

    def data_quality(self, now: datetime) -> dict[str, object]:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        # Freshness and coverage follow Apple Watch (or untagged legacy) evidence:
        # iPhone or Garmin rows arriving cannot hide that the Watch stopped syncing.
        apple = [x for x in self._observations.values() if self._apple_era_evidence(x)]
        days = {self.local_day(x.observed_at) for x in apple}
        stale = not apple or max(x.observed_at for x in apple) < now - timedelta(days=2)
        mixed = [x.metric for x in self._observations.values() if x.source == GARMIN_LEGACY and self.baseline_era(x.observed_at) != BaselineEra.GARMIN]
        return {
            "coverage_days": len(days),
            "stale": stale,
            "legacy_after_transition": sorted(set(mixed)),
            "logical_workouts": len(self.logical_workouts()),
            "raw_workouts": len(self._workouts),
            "persisted_invalid_records": self._persisted_invalid_records,
            "pass": not stale and not mixed and not self._persisted_invalid_records,
        }
