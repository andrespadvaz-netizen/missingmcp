"""Synthetic request-local reuse with full evidence checks."""
from datetime import timedelta
import sqlite3
from test_health_firmware_evolution import NOW, NEW, sample
from test_tuanhdo_staging import export, row
from missingmcp.health_os.persistence import HealthOSRepository
from missingmcp.health_os import tuanhdo_staging as s
from missingmcp.health_os.private_report import build_private_report

def test_shared_parse_equivalent_and_request_local(monkeypatch):
    conn=sqlite3.connect(":memory:");conn.row_factory=sqlite3.Row
    repo=HealthOSRepository(conn,"synthetic-only"*4,sleep_series_mode="active")
    repo.set_apple_transition(NOW-timedelta(days=30))
    sleep=row("sleepAnalysis",3,NOW,NOW+timedelta(minutes=40))
    sleep["deviceDetail"]={"model":"Watch","softwareVersion":"new"}
    approved=frozenset({NEW})
    assert s.ingest_tuanhdo(repo,export([sleep,sample("heartRate",60,"count/min")]),approved,
        approved_types=frozenset({"sleepAnalysis","heartRate"}))["accepted"]==2
    at=NOW+timedelta(days=1)
    reads=[];original=s._read_batch_samples
    def counted(*args):
        reads.append(args[1]);return original(*args)
    monkeypatch.setattr(s,"_read_batch_samples",counted)
    def report(shared):
        kwargs={"batch_samples":s.RequestBatchSamples(repo)} if shared else {}
        episodes=s.accepted_sleep_episodes(repo,at,**kwargs)
        proof=s.accepted_watch_identity_by_key(repo,at,approved,**kwargs)
        return build_private_report(repo._load_health(at),at,sleep_episodes=episodes,watch_identity_by_key=proof)
    original_report=report(False)
    assert len(reads)==2
    reads.clear()
    assert report(True)==original_report
    assert len(reads)==1
    reads.clear()
    assert report(True)==original_report
    assert len(reads)==1 # each request independently rereads
    conn.execute("UPDATE health_os_tuanhdo_records SET fingerprint='tampered' WHERE metric='heart_rate'")
    conn.commit()
    changed=report(True)
    hr=next(x for x in changed["catalog"] if x["metric"]=="heart_rate")
    assert hr["availability"]=="missing"


def test_intermediate_write_aborts_shared_proof():
    import pytest
    conn=sqlite3.connect(":memory:");conn.row_factory=sqlite3.Row
    repo=HealthOSRepository(conn,"synthetic-only"*4,sleep_series_mode="active")
    repo.set_apple_transition(NOW-timedelta(days=30))
    cache=s.RequestBatchSamples(repo)
    conn.execute("CREATE TABLE synthetic_write (id INTEGER)")
    conn.execute("INSERT INTO synthetic_write VALUES (1)")
    conn.commit()
    with pytest.raises(ValueError,match="evidence_changed"):
        cache.check(repo)

def test_external_commit_invalidates_epoch(tmp_path):
    import pytest
    path=tmp_path/"shared.db"
    conn=sqlite3.connect(path);conn.row_factory=sqlite3.Row
    repo=HealthOSRepository(conn,"synthetic-only"*4)
    conn.execute("CREATE TABLE synthetic_write (id INTEGER)");conn.commit()
    cache=s.RequestBatchSamples(repo)
    other=sqlite3.connect(path)
    other.execute("INSERT INTO synthetic_write VALUES (1)");other.commit()
    with pytest.raises(ValueError,match="evidence_changed"):
        cache.check(repo)
    other.close()

def test_cache_cannot_cross_repository():
    import pytest
    a=sqlite3.connect(":memory:");a.row_factory=sqlite3.Row
    b=sqlite3.connect(":memory:");b.row_factory=sqlite3.Row
    ra=HealthOSRepository(a,"synthetic-only"*4)
    rb=HealthOSRepository(b,"synthetic-other"*4)
    with pytest.raises(ValueError,match="evidence_changed"):
        s.RequestBatchSamples(ra).check(rb)


def test_payload_corruption_between_consumers_aborts():
    import pytest
    conn=sqlite3.connect(":memory:");conn.row_factory=sqlite3.Row
    repo=HealthOSRepository(conn,"synthetic-only"*4,sleep_series_mode="active")
    repo.set_apple_transition(NOW-timedelta(days=30))
    sleep=row("sleepAnalysis",3,NOW,NOW+timedelta(minutes=40))
    sleep["deviceDetail"]={"model":"Watch","softwareVersion":"new"}
    approved=frozenset({NEW})
    s.ingest_tuanhdo(repo,export([sleep,sample("heartRate",60,"count/min")]),approved,
        approved_types=frozenset({"sleepAnalysis","heartRate"}))
    at=NOW+timedelta(days=1)
    cache=s.RequestBatchSamples(repo)
    assert s.accepted_sleep_episodes(repo,at,batch_samples=cache)
    conn.execute("UPDATE health_os_tuanhdo_batches SET payload_enc='corrupt'");conn.commit()
    with pytest.raises(ValueError,match="evidence_changed"):
        s.accepted_watch_identity_by_key(repo,at,approved,batch_samples=cache)


def test_service_checks_after_final_consumer(tmp_path, monkeypatch):
    from test_tuanhdo_staging import client, READ
    from missingmcp.health_os import service
    app=client(tmp_path,monkeypatch,allow_watch=True,sleep_mode="active")
    def mutate(repo,now,window_days):
        repo.conn.execute("CREATE TABLE final_consumer_write (id INTEGER)")
        repo.conn.execute("INSERT INTO final_consumer_write VALUES (1)")
        repo.conn.commit()
        return {}
    monkeypatch.setattr(service,"activity_cross_day_exclusions",mutate)
    response=app.get("/health-os/private-report",headers=READ)
    assert response.status_code==500
    assert "catalog" not in response.text
