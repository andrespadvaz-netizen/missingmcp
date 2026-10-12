"""Import a user-authorized Speediance JSON export into Health OS.

The device/vendor remains the producer of exercise detail.  This client does
not scrape a private API or infer sets, loads, or repetitions: it forwards
only an explicit export with stable workout identifiers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from .core import SPEEDIANCE
from .importers import workout_from_export


def import_export(export_path: Path, endpoint: str, key: str) -> dict[str, int | str]:
    """Validate and submit a JSON object containing a ``workouts`` list.

    Values are not logged.  The response exposes only acknowledgement counts.
    """
    payload: Any = json.loads(export_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("workouts"), list):
        raise ValueError("Speediance export must be an object with a workouts list")
    workouts = payload["workouts"]
    # Validate before any transmission, including required stable identity and
    # chronological session bounds.
    for workout in workouts:
        if not isinstance(workout, dict):
            raise ValueError("Speediance workout must be an object")
        workout_from_export(SPEEDIANCE, workout)
    response = httpx.post(
        endpoint.rstrip("/") + "/health-os/ingest",
        json={"source": SPEEDIANCE, "observations": [], "workouts": workouts},
        headers={"Authorization": "Bearer " + key}, timeout=90.0,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("status") != "OK":
        raise RuntimeError("Health OS rejected Speediance export")
    return {"status": "OK", "accepted": int(result.get("accepted", 0)),
            "duplicates": int(result.get("duplicates", 0)),
            "rejected": len(result.get("rejected", []))}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a Speediance JSON export into private Health OS")
    parser.add_argument("export_path", type=Path)
    parser.add_argument("--endpoint", required=True, help="Health OS base HTTPS URL")
    arguments = parser.parse_args()
    key = os.environ.get("HEALTH_OS_INGEST_KEY")
    if not key:
        raise SystemExit("HEALTH_OS_INGEST_KEY must be provided through the environment")
    try:
        print(json.dumps(import_export(arguments.export_path, arguments.endpoint, key), sort_keys=True))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, httpx.HTTPError, RuntimeError) as exc:
        print(json.dumps({"status": "FAILED", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
