"""Synthetic-only probes for the committed Health Auto Export adapter."""

from missingmcp.health_os.health_auto_export import parse_health_auto_export


def test_identical_replay_has_stable_derived_provenance():
    payload = {"data": {"metrics": [{
        "name": "resting_heart_rate", "units": "bpm",
        "data": [{"date": "2026-10-12 06:30:00 -0600", "qty": 61, "source": "Apple Watch"}],
    }]}}
    first = parse_health_auto_export(payload)[0]
    replay = parse_health_auto_export(payload)[0]
    assert first == replay


def test_two_distinct_entries_with_identical_exported_fields_collide():
    point = {"date": "2026-10-12 06:30:00 -0600", "qty": 61, "source": "Apple Watch"}
    payload = {"data": {"metrics": [{
        "name": "resting_heart_rate", "units": "bpm", "data": [point.copy(), point.copy()],
    }]}}
    observations, _, skipped = parse_health_auto_export(payload)
    assert len(observations) == 2
    assert observations[0]["provenance_id"] == observations[1]["provenance_id"]
    assert skipped == {}


def test_unmapped_category_and_aggregated_sleep_are_reported():
    payload = {"data": {
        "medications": [{"synthetic": True}],
        "metrics": [{"name": "sleep_analysis", "units": "hr",
                     "data": [{"date": "2026-10-12", "totalSleep": 7.0}]}],
    }}
    observations, workouts, skipped = parse_health_auto_export(payload)
    assert observations == [] and workouts == []
    assert skipped == {
        "ignored_category:medications": 1,
        "sleep_aggregated_not_supported": 1,
    }


def test_empty_data_object_and_top_level_metadata_do_not_claim_omissions():
    assert parse_health_auto_export({"data": {}}) == ([], [], {})
    assert parse_health_auto_export({
        "data": {"metrics": [], "workouts": [], "medications": []},
        "metadata": {"version": 2},
    }) == ([], [], {})


def test_multiple_known_and_unknown_categories_report_counts_without_names_or_values():
    payload = {"data": {
        "medications": [{"synthetic": "private-1"}, {"synthetic": "private-2"}],
        "ecg": [{"synthetic": "private-3"}],
        "free_text_sensitive_key": [{"synthetic": "private-4"}],
        "metadata": {"version": 2},
    }}
    observations, workouts, skipped = parse_health_auto_export(payload)
    assert observations == [] and workouts == []
    assert skipped == {
        "ignored_category:medications": 2,
        "ignored_category:ecg": 1,
        "ignored_category:other": 1,
    }
    assert "private" not in repr(skipped)
    assert "free_text_sensitive_key" not in repr(skipped)


def test_known_metrics_and_workouts_keep_their_existing_shape():
    payload = {"data": {
        "metrics": [{"name": "resting_heart_rate", "units": "bpm",
                     "data": [{"date": "2026-10-12 06:30:00 -0600", "qty": 61, "source": "Apple Watch"}]}],
        "workouts": [{"id": "synthetic-1", "name": "Walking",
                      "start": "2026-10-12 07:00:00 -0600",
                      "end": "2026-10-12 07:30:00 -0600", "duration": 1800}],
    }}
    observations, workouts, skipped = parse_health_auto_export(payload)
    assert len(observations) == 1 and observations[0]["metric"] == "resting_heart_rate"
    assert workouts == [{
        "workout_id": "health-auto-export:synthetic-1",
        "start_time": "2026-10-12T07:00:00-06:00",
        "end_time": "2026-10-12T07:30:00-06:00",
        "activity_type": "Walking",
    }]
    assert skipped == {}
