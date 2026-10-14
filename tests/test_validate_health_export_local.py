"""Count-only CLI regression using fictional records."""
import json
import os
from pathlib import Path
import subprocess
import sys

from test_tuanhdo_dry_run import payload, record


def test_report_preserves_distinct_hold_reasons_and_totals(tmp_path):
    rows = [
        record("physicalEffort", 1, "kcal/(kg*hr)"),
        record("oxygenSaturation", 1, "%"),
        record("appleSleepingWristTemperature", -1, "degC"),
        record("appleStandHour", 0),
        record("appleStandHour", 1, start="2000-01-01T00:02:00-06:00",
               end="2000-01-01T00:03:00-06:00"),
    ]
    rows[3]["valueName"] = "stood"
    rows[4]["valueName"] = "idle"
    export = tmp_path / "fictional.json"
    export.write_text(json.dumps(payload(rows)), encoding="utf-8")
    project = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["HEALTH_EXPORT_LOCAL_PATH"] = str(export)
    env["PYTHONPATH"] = str(project / "src")
    completed = subprocess.run(
        [sys.executable, "-B", str(project / "validate_health_export_local.py")],
        capture_output=True, text=True, check=True, env=env,
    )
    report = json.loads(completed.stdout)
    assert report["records"] == report["recognized_samples"] == 5
    assert report["hold_reasons"] == {
        "category_unmapped": 2,
        "manual_temperature_policy": 1,
        "metric_not_supported": 1,
        "percentage_semantics_unverified": 1,
    }
    assert sum(report["hold_reasons"].values()) == report["recognized_samples"]
    assert report["unmapped_records"] == 0


def test_report_type_name_fallback_and_missing_source(tmp_path):
    row = record("heartRate", 60, "count/min")
    row["type"] = None
    row["name"] = "heartRate"
    row["source"] = None
    export = tmp_path / "fictional-fallback.json"
    export.write_text(json.dumps(payload([row])), encoding="utf-8")
    project = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["HEALTH_EXPORT_LOCAL_PATH"] = str(export)
    env["PYTHONPATH"] = str(project / "src")
    completed = subprocess.run(
        [sys.executable, "-B", str(project / "validate_health_export_local.py")],
        capture_output=True, text=True, check=True, env=env,
    )
    report = json.loads(completed.stdout)
    assert report["record_types"] == {"heartRate": 1}
    assert report["recognized_samples"] == 1
    assert report["provenance_gaps"]["heartRate"] == {"source_missing": 1}
