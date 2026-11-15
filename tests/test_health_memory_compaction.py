"""Synthetic-only regressions for bounded labels and scan graph lifetimes."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import weakref

import pytest

from missingmcp import store
from missingmcp.health_os.core import APPLE_HEALTH, HealthOS, MetricObservation
from missingmcp.health_os.persistence import HealthOSRepository


NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
LABEL_FIELDS = ("metric", "unit", "source", "semantic", "confidence", "device_source")


class UnsharedHealth(HealthOS):
    """Same validation/normalization, with the optimization disabled."""
    def _shared_observation_label(self, value):
        return value


class StringSubclass(str):
    pass


def fresh(value):
    return value.encode().decode()


def observation(index=0, **changes):
    values = dict(metric=fresh("heart_rate"), value=60.0, unit=fresh("bpm"),
                  observed_at=NOW + timedelta(seconds=index), source=fresh(APPLE_HEALTH),
                  provenance_id=fresh(f"synthetic-unique-provenance-{index}"),
                  semantic=fresh("measured"), confidence=fresh("device_metadata"),
                  device_source=fresh("apple_watch"))
    values.update(changes)
    return MetricObservation(**values)


def raw(index):
    return dict(metric="heart_rate", value=60, unit="bpm",
                observed_at=(NOW + timedelta(seconds=index)).isoformat(),
                provenance_id=f"synthetic-unique-provenance-{index}",
                confidence="device_metadata", device_source="apple_watch")


@pytest.fixture
def repo_pair(tmp_path):
    path = str(tmp_path / "synthetic-memory.db")
    repo = HealthOSRepository(store.init_db(path), "synthetic-key-only" * 3)
    external = sqlite3.connect(path)
    try:
        yield repo, external
    finally:
        external.close()
        repo.conn.close()


def external_insert(repo, external, index):
    external.execute(
        "INSERT INTO health_os_observations(event_key,source,payload_enc) VALUES (?,?,?)",
        (f"synthetic-row-{index}", APPLE_HEALTH, store.encrypt(repo.data_key, json.dumps(raw(index)))),
    )
    external.commit()


def test_repeated_labels_share_identity_but_preserve_all_values_and_keys():
    candidate, reference = HealthOS(), UnsharedHealth()
    rows = [observation(i) for i in range(50)]
    for field in LABEL_FIELDS:
        assert getattr(rows[0], field) is not getattr(rows[1], field)
    assert candidate.ingest_observations(rows) == reference.ingest_observations(rows)
    assert candidate._observations == reference._observations
    saved = list(candidate._observations.values())
    for field in LABEL_FIELDS:
        assert all(getattr(item, field) is getattr(saved[0], field) for item in saved)
    for original, item in zip(rows, saved):
        assert asdict(original) == asdict(item)
        assert item.provenance_id is original.provenance_id
        assert item.observed_at is original.observed_at
        assert item.provenance_id not in candidate._observation_labels
    assert set(candidate._observation_labels) == {getattr(rows[0], field) for field in LABEL_FIELDS}


@pytest.mark.parametrize("semantic", ["measured", "estimated", "manual", "derived",
                                      "model_interpreted", "chatgpt_health_transcription"])
def test_all_supported_semantics_preserve_result(semantic):
    a, b = HealthOS(), UnsharedHealth()
    rows = [observation(i, semantic=fresh(semantic)) for i in range(3)]
    assert a.ingest_observations(rows) == b.ingest_observations(rows)
    assert a._observations == b._observations
    assert a.data_quality(NOW + timedelta(days=1)) == b.data_quality(NOW + timedelta(days=1))


def test_unit_normalization_duplicate_and_metadata_upgrade_are_unchanged():
    a, b = HealthOS(), UnsharedHealth()
    rows = [observation(metric="weight", value=220, unit="lb", device_source=None),
            observation(metric="weight", value=220, unit="lb", device_source="apple_watch"),
            observation(2, metric="hrv", value=40, unit="kg")]
    for item in rows:
        assert a.ingest_observations([item]) == b.ingest_observations([item])
        assert a._observations == b._observations
    assert len(a._observations) == 1
    item = next(iter(a._observations.values()))
    assert (item.value, item.unit, item.device_source) == (99.790321, "kg", "apple_watch")


def test_pool_saturates_without_rejecting_or_changing_high_cardinality_values():
    a, b = HealthOS(), UnsharedHealth()
    rows = [observation(i, confidence=f"synthetic-confidence-{i}") for i in range(700)]
    assert a.ingest_observations(rows) == b.ingest_observations(rows)
    assert a._observations == b._observations
    assert len(a._observation_labels) == 256
    assert all(len(key) <= 128 for key in a._observation_labels)
    existing = next(iter(a._observation_labels))
    assert a._shared_observation_label(fresh(existing)) is a._observation_labels[existing]
    unseen = "new-label-after-capacity"
    assert a._shared_observation_label(unseen) is unseen
    assert unseen not in a._observation_labels


@pytest.mark.parametrize("length", [127, 128, 129, 10000])
def test_long_labels_preserve_values_without_unbounded_retention(length):
    a, b = HealthOS(), UnsharedHealth()
    label = "x" * length
    rows = [observation(i, confidence=fresh(label)) for i in range(2)]
    assert a.ingest_observations(rows) == b.ingest_observations(rows)
    assert a._observations == b._observations
    assert (label in a._observation_labels) is (length <= 128)
    if length > 128:
        assert next(iter(a._observations.values())).confidence is rows[0].confidence


@pytest.mark.parametrize("field", ["metric", "source", "semantic", "confidence", "device_source"])
def test_string_subclasses_are_not_replaced_by_plain_string_labels(field):
    a, b = HealthOS(), UnsharedHealth()
    original = observation()
    subclass_value = StringSubclass(getattr(original, field))
    row = replace(original, **{field: subclass_value})
    # Seed an equal plain string first; identity/type must still be untouched.
    a._shared_observation_label(str(subclass_value))
    assert a.ingest_observations([row]) == b.ingest_observations([row])
    item = next(iter(a._observations.values()))
    assert getattr(item, field) is subclass_value
    assert a._observations == b._observations


def test_helper_preserves_none_and_non_strings_and_pool_is_per_instance():
    a, b = HealthOS(), HealthOS()
    for value in (None, 7, 0.0, [], {}, StringSubclass("x")):
        assert a._shared_observation_label(value) is value
    assert a._observation_labels == {}
    left, right = fresh("distinct-instance-label"), fresh("distinct-instance-label")
    assert left is not right
    assert a._shared_observation_label(left) is left
    assert b._shared_observation_label(right) is right
    item = observation(confidence=None, device_source=None)
    a.ingest_observations([item])
    saved = next(iter(a._observations.values()))
    assert saved.confidence is None and saved.device_source is None


def test_scan_reuses_same_graph_until_local_write_and_releases_it(repo_pair):
    repo, _ = repo_pair
    graph = repo._scan()[0]
    ref = weakref.ref(graph)
    assert repo._scan()[0] is graph
    del graph
    assert repo.ingest(APPLE_HEALTH, [raw(1)], [])["accepted"] == 1
    assert repo._scan_cache is None and repo._scan_cache_version is None
    assert ref() is None
    assert len(repo._scan()[0]._observations) == 1


def test_external_write_releases_old_graph_before_rebuilding(repo_pair, monkeypatch):
    repo, external = repo_pair
    ref = weakref.ref(repo._scan()[0])
    external_insert(repo, external, 1)
    real_scan = repo._scan_uncached
    def scan():
        assert repo._scan_cache is None and repo._scan_cache_version is None
        assert ref() is None
        return real_scan()
    monkeypatch.setattr(repo, "_scan_uncached", scan)
    assert len(repo._scan()[0]._observations) == 1


def test_external_invalidation_keeps_independent_caller_reference_valid(repo_pair):
    repo, external = repo_pair
    old = repo._scan()[0]
    external_insert(repo, external, 1)
    new = repo._scan()[0]
    assert new is not old
    assert len(old._observations) == 0 and len(new._observations) == 1


def test_failed_rebuild_does_not_restore_stale_cache_and_can_retry(repo_pair, monkeypatch):
    repo, external = repo_pair
    ref = weakref.ref(repo._scan()[0])
    external_insert(repo, external, 1)
    real_scan = repo._scan_uncached
    def fail():
        assert ref() is None
        raise RuntimeError("synthetic scan failure")
    monkeypatch.setattr(repo, "_scan_uncached", fail)
    with pytest.raises(RuntimeError, match="synthetic scan failure"):
        repo._scan()
    assert repo._scan_cache is None and repo._scan_cache_version is None
    monkeypatch.setattr(repo, "_scan_uncached", real_scan)
    assert len(repo._scan()[0]._observations) == 1


def test_external_commit_during_scan_retries_and_releases_discarded_graph(repo_pair, monkeypatch):
    repo, external = repo_pair
    real_scan = repo._scan_uncached
    refs = []
    def scan():
        if refs:
            assert refs[0]() is None, "discarded graph survives into replacement scan"
        result = real_scan()
        refs.append(weakref.ref(result[0]))
        if len(refs) == 1:
            external_insert(repo, external, 1)
        return result
    monkeypatch.setattr(repo, "_scan_uncached", scan)
    final = repo._scan()[0]
    assert len(refs) == 2 and len(final._observations) == 1
    assert refs[0]() is None and refs[1]() is final
    assert repo._scan()[0] is final


def test_mode_guard_failure_never_returns_cached_graph(repo_pair, monkeypatch):
    repo, _ = repo_pair
    repo._scan()
    def revoked():
        raise ValueError("synthetic activation changed")
    monkeypatch.setattr(repo, "_assert_sleep_series_mode_current", revoked)
    with pytest.raises(ValueError, match="synthetic activation changed"):
        repo._scan()


def test_synthetic_read_does_not_modify_encrypted_storage(repo_pair):
    repo, external = repo_pair
    for index in range(3):
        external_insert(repo, external, index)
    before = list(repo.conn.iterdump())
    first = repo._scan()
    assert len(first[0]._observations) == 3
    assert repo._scan() is first
    assert list(repo.conn.iterdump()) == before
