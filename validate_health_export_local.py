"""Local count-only validation. Run only against an explicitly authorized file."""
from collections import Counter
from datetime import datetime
import json
import os
import sys

from missingmcp.health_os.tuanhdo_dry_run import parse_health_export


ALLOWED_TYPES = {
    "stepCount", "distanceWalkingRunning", "activeEnergyBurned",
    "heartRate", "bodyMass", "sleepAnalysis",
    "appleExerciseTime", "appleSleepingBreathingDisturbances",
    "appleSleepingWristTemperature", "appleStandHour", "appleStandTime",
    "basalEnergyBurned", "environmentalAudioExposure", "flightsClimbed",
    "heartRateVariabilitySDNN", "oxygenSaturation", "physicalEffort",
    "respiratoryRate", "restingHeartRate", "stairAscentSpeed",
    "stairDescentSpeed", "timeInDaylight", "walkingAsymmetryPercentage",
    "walkingDoubleSupportPercentage", "walkingHeartRateAverage",
    "walkingSpeed", "walkingStepLength",
}
ALLOWED_ISSUES = {
    "aggregation_not_raw", "record_invalid", "type_missing", "type_unsupported",
    "timestamp_invalid", "timestamp_missing_offset", "interval_invalid",
    "additive_point_no_duration",
    "category_value_invalid", "category_value_unsupported",
    "category_label_missing", "category_label_mismatch", "quantity_overlap",
    "device_detail_invalid", "sleep_stage_unsupported", "sleep_label_missing",
    "sleep_label_mismatch", "unit_unsupported", "value_invalid",
    "indistinguishable_records", "core_key_collision", "sleep_overlap",
    "provenance_incomplete", "workouts_unmapped",
}
ALLOWED_HOLD_REASONS = ALLOWED_ISSUES | {
    "source_unverified", "awake_not_sleep", "category_unmapped",
    "manual_temperature_policy", "metric_not_supported",
    "percentage_semantics_unverified",
}


def main() -> int:
    path = os.environ.get("HEALTH_EXPORT_LOCAL_PATH")
    if not path:
        print(json.dumps({"error": "path_not_set"}))
        return 2
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            print(json.dumps({"error": "root_not_object", "bytes": size}))
            return 2
        records = payload.get("records")
        if not isinstance(records, list):
            print(json.dumps({"error": "records_not_array", "bytes": size}))
            return 2
        type_counts = Counter()
        interval_shapes = {}
        provenance_gaps = {}
        for row in records:
            raw_type = (row.get("type") or row.get("name")) if isinstance(row, dict) else None
            safe_type = raw_type if raw_type in ALLOWED_TYPES else "other"
            type_counts[safe_type] += 1
            interval_shapes.setdefault(safe_type, Counter())
            try:
                start = datetime.fromisoformat(row["startDate"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(row["endDate"].replace("Z", "+00:00"))
                shape = "equal" if start == end else "reverse" if end < start else "positive"
            except (KeyError, TypeError, ValueError, AttributeError):
                shape = "invalid_timestamp"
            interval_shapes[safe_type][shape] += 1
            gaps = provenance_gaps.setdefault(safe_type, Counter())
            if isinstance(row, dict):
                if not isinstance(row.get("source"), str) or not row.get("source"):
                    gaps["source_missing"] += 1
                if not row.get("sourceBundleId"):
                    gaps["bundle_missing"] += 1
                detail = row.get("deviceDetail")
                if not isinstance(detail, dict) or not detail.get("model"):
                    gaps["model_missing"] += 1
                if not isinstance(detail, dict) or not detail.get("softwareVersion"):
                    gaps["software_version_missing"] += 1
        result = parse_health_export(payload)
        issues = Counter()
        for code, count in result.issues.items():
            issues[code if code in ALLOWED_ISSUES else "other_issue"] += count
        hold_reasons = Counter(sample.hold_reason for sample in result.samples)
        safe_holds = Counter()
        for key, count in hold_reasons.items():
            safe_holds[key if key in ALLOWED_HOLD_REASONS else "other_hold"] += count
        print(json.dumps({
            "bytes": size,
            "json_readable": True,
            "raw_aggregation": payload.get("aggregation") == "raw",
            "records": len(records),
            "record_types": dict(sorted(type_counts.items())),
            "interval_shapes": {key: dict(sorted(value.items())) for key, value in sorted(interval_shapes.items())},
            "provenance_gaps": {key: dict(sorted(value.items())) for key, value in sorted(provenance_gaps.items())},
            "recognized_samples": len(result.samples),
            "unmapped_records": len(result.raw_records) - len(result.samples),
            "computable_samples": sum(sample.computable for sample in result.samples),
            "workouts_held": len(result.workouts),
            "issues": dict(sorted(issues.items())),
            "hold_reasons": dict(sorted(safe_holds.items())),
        }, sort_keys=True))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({"error": "file_unreadable_or_invalid_json"}))
        return 2
    except Exception as exc:
        # Only the exception class is emitted; messages may contain input.
        print(json.dumps({"error": "validation_failed", "exception": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
