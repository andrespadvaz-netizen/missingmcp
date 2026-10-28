"""Private, descriptive Health OS report over already accepted Apple Watch data.

This module does not promote records or write storage. Its result belongs to the
read-key app surface; the operational e-mail Brief must never consume it.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Mapping

from .core import APPLE_HEALTH, HealthOS


# Only metrics already approved for the TuAnhDo Watch path. No retained type is
# made available by adding it to this presentation catalog.
CATALOG = {
    "heart_rate": "bpm", "resting_heart_rate": "bpm", "hrv": "ms",
    "sleep_duration": "min", "steps": "count", "distance": "km",
    "active_energy": "kcal", "exercise_time": "min",
    "respiratory_rate": "breaths/min",
}
ADDITIVE = frozenset({"sleep_duration", "steps", "distance", "active_energy", "exercise_time"})
COMPARABLE = frozenset({"resting_heart_rate", "hrv",
                        "steps", "distance", "active_energy", "exercise_time",
                        "respiratory_rate"})


def _accepted_days(health: HealthOS, now: datetime,
                   watch_identity_by_key: Mapping[str, tuple[str, ...] | None]) -> tuple[
        dict[str, dict[str, float]], dict[tuple[str, str], set[str]],
        dict[tuple[str, str], set[tuple[str, ...]]],
        set[tuple[str, str]]]:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    methods: dict[tuple[str, str], set[str]] = defaultdict(set)
    identities: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    unverified_days: set[tuple[str, str]] = set()
    for item in health._observations.values():
        if (item.metric not in CATALOG or item.source != APPLE_HEALTH
                or item.device_source != "apple_watch" or item.observed_at > now
                or not health._current_evidence(item)):
            continue
        if (health._apple_baseline_started is None
                or item.observed_at < health._apple_baseline_started):
            continue
        if (item.metric == "sleep_duration" and (health._sleep_series_start is None
                or item.observed_at < health._sleep_series_start)
                or item.metric in health.ACTIVITY_SERIES_METRICS
                and health._activity_series_start is None):
            continue
        if item.metric in health.ACTIVITY_SERIES_METRICS:
            interval = health._activity_series_intervals.get(health._observation_key(item))
            if interval is None:
                continue
            start, end = interval
            if start < health._activity_series_start:
                continue
            day = start.astimezone(health.timezone).date()
            if day != (end - timedelta(microseconds=1)).astimezone(health.timezone).date():
                continue
        else:
            day = item.observed_at.astimezone(health.timezone).date()
        metric_day = (item.metric, day.isoformat())
        # Only an accepted, fingerprint-checked staging link can supply Watch
        # proof. A core-only row must not produce a number.
        identity = watch_identity_by_key.get(health._observation_key(item))
        exact_watch = (isinstance(identity, tuple) and len(identity) == 4
                       and identity[2] == "Watch")
        derived_watch = (isinstance(identity, tuple) and len(identity) == 2
                         and item.metric in {"resting_heart_rate", "sleep_duration"}
                         and item.confidence == "watch_source_only")
        if (not (exact_watch or derived_watch)
                or not all(isinstance(value, str) and value for value in identity)):
            unverified_days.add(metric_day)
            continue
        buckets[metric_day].append(item.value)
        methods[metric_day].add(item.confidence or "unspecified")
        identities[metric_day].add(identity)
    result: dict[str, dict[str, float]] = defaultdict(dict)
    for (metric, day), values in buckets.items():
        if (metric, day) in unverified_days or len(identities[(metric, day)]) != 1:
            unverified_days.add((metric, day))
            continue
        result[metric][day] = sum(values) if metric in ADDITIVE else sum(values) / len(values)
    return result, methods, identities, unverified_days


def _window_days(end_day, n: int) -> list[str]:
    return [(end_day - timedelta(days=offset)).isoformat() for offset in range(n - 1, -1, -1)]


def build_private_report(health: HealthOS, now: datetime, *,
                         watch_identity_by_key: Mapping[str, tuple[str, ...] | None] | None = None,
                         sleep_episodes: list[dict[str, Any]] | None = None,
                         sleep_nights: list[dict[str, Any]] | None = None,
                         cross_day_exclusions: Mapping[tuple[str, str], int] | None = None) -> dict[str, object]:
    """One coherent read-only report; sparse coverage is never treated as zero."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(health.timezone).date()
    # Exclude today: the current local day is incomplete. Equal seven-day
    # windows have the same weekdays, source and per-metric aggregation.
    current_days = _window_days(today - timedelta(days=1), 7)
    prior_days = _window_days(today - timedelta(days=8), 7)
    recent_28 = set(_window_days(today, 28))
    daily, methods, identities, unverified_days = _accepted_days(
        health, now, watch_identity_by_key or {})
    exclusions = cross_day_exclusions or {}
    catalog = []
    analyses = []
    observed_daily = []
    for metric, unit in CATALOG.items():
        by_day = daily.get(metric, {})
        covered = sorted(recent_28.intersection(by_day))
        unverified_28 = len([day for day in recent_28 if (metric, day) in unverified_days])
        current_partial = [d for d in current_days if exclusions.get((metric, d), 0)]
        prior_partial = [d for d in prior_days if exclusions.get((metric, d), 0)]
        current = [by_day[d] for d in current_days if d in by_day and d not in current_partial]
        prior = [by_day[d] for d in prior_days if d in by_day and d not in prior_partial]
        excluded = {"excluded_partial_days_current": len(current_partial),
                    "excluded_partial_days_prior": len(prior_partial)}
        series = ("validated_activity_2026_10_16" if metric in health.ACTIVITY_SERIES_METRICS
                  else "validated_2026_10_16" if metric == "sleep_duration"
                  else "approved_watch")
        for day in current_days:
            excluded_count = (exclusions.get((metric, day), 0)
                              if metric in health.ACTIVITY_SERIES_METRICS else 0)
            unverified = (metric, day) in unverified_days
            if day not in by_day and not excluded_count and not unverified:
                continue
            observed_daily.append({
                "metric": metric, "local_day": day,
                "observed_value": round(by_day[day], 1) if day in by_day else None,
                "unit": unit, "source": "APPLE_HEALTH/apple_watch", "series": series,
                "aggregation": ("sum_of_accepted_intervals"
                                if metric in health.ACTIVITY_SERIES_METRICS else
                                "sum_of_observed_sleep_stage_minutes"
                                if metric == "sleep_duration" else "mean_of_accepted_samples"),
                "coverage": ("unverified_watch_identity" if unverified else
                             "partial" if excluded_count else
                             "accepted_intervals_only" if metric in health.ACTIVITY_SERIES_METRICS
                             else "observed_stages_only" if metric == "sleep_duration"
                             else "accepted_samples_only"),
                "excluded_cross_midnight": excluded_count,
            })
        catalog.append({
            "metric": metric, "unit": unit, "source": "APPLE_HEALTH/apple_watch",
            "series": series, "availability": "available" if by_day else "missing",
            "latest_local_day": max(by_day) if by_day else None,
            "valid_days_28": len(covered), "coverage": "observed_days_only",
            "excluded_unverified_days_28": unverified_28,
            "required_days_for_comparison": 5 if metric in COMPARABLE else None,
            "missing": ([] if covered else ["watch_identity_unverified_or_mixed"] if unverified_28
                        else ["no_observation_in_28_days"] if by_day
                        else ["no_accepted_approved_watch_observation"]),
        })
        if by_day and not covered:
            catalog[-1]["availability"] = "stale"
        if metric not in COMPARABLE:
            analyses.append({"metric": metric, "status": "abstain",
                             "reason": ("night_completeness_unverifiable" if metric == "sleep_duration"
                                        else "sampling_pattern_not_comparable"),
                             "current_days": len(current),
                             "prior_days": len(prior), **excluded})
            continue
        comparison_methods = set().union(*(methods.get((metric, day), set())
                                           for day in current_days + prior_days
                                           if not exclusions.get((metric, day), 0)))
        if len(comparison_methods) > 1:
            analyses.append({"metric": metric, "status": "abstain",
                             "reason": "mixed_provenance_methods",
                             "current_days": len(current), "prior_days": len(prior), **excluded})
            continue
        if len(current) < 5 or len(prior) < 5:
            analyses.append({"metric": metric, "status": "abstain",
                             "reason": "insufficient_equivalent_days",
                             "current_days": len(current), "prior_days": len(prior),
                             "required_days_each": 5, **excluded})
            continue
        comparison_identities = set().union(*(identities.get((metric, day), set())
                                              for day in current_days + prior_days
                                              if not exclusions.get((metric, day), 0)))
        if len(comparison_identities) != 1 or None in comparison_identities:
            analyses.append({"metric": metric, "status": "abstain",
                             "reason": "technical_watch_identity_unverified_or_changed",
                             "current_days": len(current), "prior_days": len(prior), **excluded})
            continue
        current_mean = sum(current) / len(current)
        prior_mean = sum(prior) / len(prior)
        analyses.append({"metric": metric, "status": "descriptive",
                         "unit": unit, "series": series,
                         "current_window": [current_days[0], current_days[-1]],
                         "prior_window": [prior_days[0], prior_days[-1]],
                         "current_days": len(current), "prior_days": len(prior),
                         "aggregate": "mean_of_daily_totals" if metric in ADDITIVE else "mean_of_daily_means",
                         "current_mean": round(current_mean, 1),
                         "prior_mean": round(prior_mean, 1),
                         "difference": round(current_mean - prior_mean, 1),
                         "interpretation": "descriptive_only", **excluded})

    # The legacy keyword is accepted for local probes, but these inputs are
    # episodes of observed phases, never proof of complete nights.
    observed = sleep_episodes if sleep_episodes is not None else sleep_nights or []
    episodes = [episode for episode in observed
                if datetime.fromisoformat(episode["last_end"]).astimezone(health.timezone).date().isoformat() in recent_28
                and health._apple_baseline_started is not None
                and datetime.fromisoformat(episode["last_end"]) >= health._apple_baseline_started]
    episode_rows = [{"first_start": episode["first_start"], "last_end": episode["last_end"],
                     "observed_phase_minutes": episode.get("observed_phase_minutes"),
                     "unobserved_gap_minutes": episode.get("unobserved_gap_minutes"),
                     "segment_count": episode.get("segment_count"),
                     "coverage": "observed_stages_only"}
                    for episode in episodes[-28:]]
    sleep_regularity = {"status": "abstain", "reason": "night_completeness_unverifiable",
                        "observed_episode_count_28": len(episodes)}

    recommendations = [
        {"kind": "general_habit", "text": "Mantén horarios de descanso que te resulten sostenibles; registra cómo te sientes antes de interpretar cambios."},
    ]
    if any(row["status"] == "abstain" and row["metric"] in COMPARABLE
           for row in analyses):
        recommendations.append({"kind": "general_planning", "text": "Deja margen para ajustar tus actividades según cómo te sientas; estos registros aún no sostienen una conclusión personal."})
    limitations = ["observed_days_are_not_full_day_coverage",
                   "night_completeness_unverifiable",
                   "no_medical_or_automatic_schedule_decision"]
    if unverified_days:
        limitations.append("unverified_watch_identity_days_excluded")
    if any(row["availability"] != "available" for row in catalog):
        limitations.append("missing_or_stale_metric_coverage")
    if any(row["excluded_partial_days_current"] or row["excluded_partial_days_prior"]
           for row in analyses):
        limitations.append("partial_activity_days_excluded_from_comparison")
    activity_window = set(_window_days(today, 7))
    activity_daily = [{
        "metric": metric, "local_day": day,
        "observed_sum": round(daily.get(metric, {})[day], 1)
        if day in daily.get(metric, {}) else None,
        "unit": CATALOG[metric], "series": "validated_activity_2026_10_16",
        "coverage": ("unverified_watch_identity" if (metric, day) in unverified_days
                     else "partial" if exclusions.get((metric, day), 0)
                     else "accepted_intervals_only"),
        "excluded_cross_midnight": exclusions.get((metric, day), 0),
    } for metric in sorted(health.ACTIVITY_SERIES_METRICS)
      for day in sorted(activity_window)
      if (day in daily.get(metric, {}) or (metric, day) in unverified_days
          or exclusions.get((metric, day), 0))]
    return {"as_of_utc": now.isoformat(), "timezone": str(health.timezone),
            "scope": "private_app_only_approved_watch", "catalog": catalog,
            "analysis": {"comparison": analyses, "observed_daily": observed_daily,
                         "sleep_regularity": sleep_regularity,
                         "sleep_observed_episodes": episode_rows,
                         "activity_daily": activity_daily},
            "recommendations": recommendations[:3],
            "limitations": limitations}
