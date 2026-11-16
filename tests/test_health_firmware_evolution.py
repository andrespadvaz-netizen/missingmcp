"""Synthetic-only firmware evolution; no production data."""
from datetime import datetime, timedelta, timezone
import sqlite3
from test_tuanhdo_staging import row, export, local_repo, WATCH
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp.health_os.tuanhdo_staging import ingest_tuanhdo, review_held_tuanhdo, accepted_watch_identity_by_key

OLD = tuple(WATCH[:2] + ["Watch", "old"])
NEW = tuple(WATCH[:2] + ["Watch", "new"])
NOW = datetime(2026, 11, 16, 18, tzinfo=timezone.utc)

def sample(kind, value, unit):
    r = row(kind, value, NOW, NOW + timedelta(minutes=1), unit=unit)
    r["deviceDetail"] = {"model": "Watch", "softwareVersion": "new"}
    r["device"] = "Apple Watch"
    return r

def test_derived_identity_survives_explicit_second_firmware():
    repo = local_repo()
    item = row("restingHeartRate", 60, NOW, unit="count/min", detail=False)
    derived = frozenset({OLD[:2]})
    assert ingest_tuanhdo(repo, export([item]), frozenset({OLD}),
                         approved_types=frozenset({"restingHeartRate"}),
                         derived_watch=derived)["accepted"] == 1
    proof = accepted_watch_identity_by_key(repo, NOW, frozenset({OLD, NEW}), derived)
    assert proof and all(v is not None for v in proof.values())

def test_anchored_activity_accepts_only_explicit_same_watch_firmware():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    repo = HealthOSRepository(conn, "synthetic-key"*4,
        activity_series_mode="active", activity_watch_identity=OLD)
    selection = {m: OLD for m in ["steps","distance","active_energy","exercise_time"]}
    item = sample("stepCount", 10, "count")
    args = dict(approved_types=frozenset({"stepCount"}), additive_watch_selection=selection)
    first = ingest_tuanhdo(repo, export([item]), frozenset({OLD}), **args)
    assert first["accepted"] == 0
    key = conn.execute("select batch_key from health_os_tuanhdo_batches").fetchone()[0]
    result = review_held_tuanhdo(repo, frozenset({OLD,NEW}), batch_key=key, **args)
    assert result["promoted"] == 1
    assert review_held_tuanhdo(repo, frozenset({OLD,NEW}), batch_key=key, **args)["promoted"] == 0
    repo._assert_activity_series_mode_current()
    item["sourceBundleId"] = "example.other"
    assert ingest_tuanhdo(repo, export([item],date="other"), frozenset({OLD,NEW}), **args)["accepted"] == 0

def test_unknown_firmware_stays_held():
    repo = local_repo()
    assert ingest_tuanhdo(repo, export([sample("heartRate",60,"count/min")]),
        frozenset({OLD}), approved_types=frozenset({"heartRate"}))["accepted"] == 0


def test_staging_to_private_report_across_firmware_is_not_empty_or_ambiguous():
    from missingmcp.health_os.private_report import build_private_report
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    repo = HealthOSRepository(conn, "synthetic-key"*4,
        activity_series_mode="active", activity_watch_identity=OLD)
    repo.set_apple_transition(NOW - timedelta(days=30))
    selection = {m: OLD for m in ["steps","distance","active_energy","exercise_time"]}
    approved = frozenset({OLD, NEW})
    for version, offset in [("old",0),("new",10)]:
        records=[]
        for kind,value,unit in [("stepCount",10,"count"),("heartRate",60,"count/min")]:
            item=sample(kind,value,unit)
            item["startDate"]=(NOW+timedelta(minutes=offset)).isoformat()
            item["endDate"]=(NOW+timedelta(minutes=offset+1)).isoformat()
            item["deviceDetail"]["softwareVersion"]=version
            records.append(item)
        assert ingest_tuanhdo(repo,export(records,date=version),approved,
            approved_types=frozenset({"stepCount","heartRate"}),
            additive_watch_selection=selection)["accepted"] == 2
    report_at=NOW+timedelta(days=1)
    proof=accepted_watch_identity_by_key(repo,report_at,approved)
    report=build_private_report(repo._load_health(report_at),report_at,
        watch_identity_by_key=proof)
    catalog={x["metric"]:x for x in report["catalog"]}
    for metric in ["heart_rate","steps"]:
        assert catalog[metric]["availability"]=="available"
        assert catalog[metric]["valid_days_28"]==1
        assert catalog[metric]["excluded_unverified_days_28"]==0
    assert len([x for x in report["analysis"]["observed_daily"]
                if x["metric"] in {"heart_rate","steps"}])==2


def test_held_review_uses_bounded_interval_index_and_no_unrelated_scan(monkeypatch):
    import missingmcp.health_os.tuanhdo_staging as staging
    conn=sqlite3.connect(":memory:");conn.row_factory=sqlite3.Row
    repo=HealthOSRepository(conn,"synthetic-key"*4,
        activity_series_mode="active",activity_watch_identity=OLD)
    selection={m:OLD for m in ["steps","distance","active_energy","exercise_time"]}
    records=[]
    for i in range(25):
        item=sample("stepCount",10,"count")
        item["startDate"]=(NOW+timedelta(minutes=2*i)).isoformat()
        item["endDate"]=(NOW+timedelta(minutes=2*i+1)).isoformat()
        records.append(item)
    args=dict(approved_types=frozenset({"stepCount"}),additive_watch_selection=selection)
    assert ingest_tuanhdo(repo,export(records),frozenset({OLD}),**args)["accepted"]==0
    key=conn.execute("select batch_key from health_os_tuanhdo_batches").fetchone()[0]
    calls=[]
    original=staging._unknown_interval_metrics
    def guard(repo,*,required_metrics):
        calls.append(required_metrics)
        assert not required_metrics
        return original(repo,required_metrics=required_metrics)
    monkeypatch.setattr(staging,"_unknown_interval_metrics",guard)
    index_creations=[]
    original_index=staging._AcceptedIntervals
    class CountedIndex(original_index):
        def __init__(self,*a,**kw):
            index_creations.append(1)
            super().__init__(*a,**kw)
    monkeypatch.setattr(staging,"_AcceptedIntervals",CountedIndex)
    traces=[]
    conn.set_trace_callback(traces.append)
    result=review_held_tuanhdo(repo,frozenset({OLD,NEW}),batch_key=key,**args)
    conn.set_trace_callback(None)
    assert result["promoted"]==25 and calls==[frozenset()]
    interval_reads=[q for q in traces if q.startswith("SELECT interval_enc")]
    assert len(interval_reads)<=1
    assert len(index_creations)==1
