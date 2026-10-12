"""Stream a native Apple Health ZIP to the private Health OS ingest API.

Apple's ``export.xml`` can be hundreds of megabytes.  The private API has a
small request limit deliberately, so this client parses the export locally and
sends bounded, provenance-bearing JSON batches over HTTPS.  It never prints
raw medical samples or the authentication key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from xml.etree import ElementTree as ET

import httpx

from .apple_export import _METRICS, _provenance, _value
from .core import APPLE_HEALTH

_BATCH_BYTES = 180_000


def _records(stream: BinaryIO) -> Iterator[tuple[str, dict[str, Any] | str]]:
    """Yield supported raw samples while preserving the XML parser's memory bound."""
    for _, node in ET.iterparse(stream, events=("end",)):
        if node.tag == "Record":
            attributes = node.attrib
            apple_type = attributes.get("type", "")
            spec = _METRICS.get(apple_type)
            if spec is None:
                yield "ignored", apple_type or "unknown"
            else:
                metric, unit = spec
                yield "observation", {
                    "metric": metric,
                    "value": _value(attributes["value"], apple_type),
                    "unit": unit,
                    "observed_at": attributes["endDate"],
                    "provenance_id": _provenance(attributes),
                    "semantic": "measured",
                }
            node.clear()
        elif node.tag == "Workout":
            attributes = node.attrib
            yield "workout", {
                "workout_id": _provenance(attributes),
                "start_time": attributes["startDate"],
                "end_time": attributes["endDate"],
                "activity_type": attributes.get("workoutActivityType", "other"),
            }
            node.clear()


def _export_entry(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    candidates = [
        item for item in archive.infolist()
        if item.filename.lower().endswith(".xml") and "export_cda" not in item.filename.lower()
    ]
    if not candidates:
        raise ValueError("the ZIP does not contain an Apple Health export XML")
    # Native exports use export.xml, but a renamed file is equally valid.
    return max(candidates, key=lambda item: item.file_size)


def _post_batch(client: httpx.Client, endpoint: str, key: str, observations: list[dict[str, Any]], workouts: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps({"source": APPLE_HEALTH, "observations": observations, "workouts": workouts}, separators=(",", ":")).encode("utf-8")
    response = client.post(
        endpoint.rstrip("/") + "/health-os/ingest",
        content=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "OK":
        raise RuntimeError("Health OS rejected an import batch")
    return payload


def import_zip(zip_path: Path, endpoint: str, key: str) -> dict[str, Any]:
    summary: Counter[str] = Counter()
    ignored: Counter[str] = Counter()
    observations: list[dict[str, Any]] = []
    workouts: list[dict[str, Any]] = []

    def flush(client: httpx.Client) -> None:
        nonlocal observations, workouts
        if not observations and not workouts:
            return
        result = _post_batch(client, endpoint, key, observations, workouts)
        summary["batches"] += 1
        summary["accepted"] += result.get("accepted", 0)
        summary["duplicates"] += result.get("duplicates", 0)
        summary["rejected"] += len(result.get("rejected", []))
        observations, workouts = [], []

    with zipfile.ZipFile(zip_path) as archive, httpx.Client(timeout=90.0) as client:
        entry = _export_entry(archive)
        with archive.open(entry) as stream:
            for kind, item in _records(stream):
                if kind == "ignored":
                    ignored[str(item)] += 1
                    continue
                if kind == "observation":
                    observations.append(item)  # type: ignore[arg-type]
                    summary["observations_seen"] += 1
                else:
                    workouts.append(item)  # type: ignore[arg-type]
                    summary["workouts_seen"] += 1
                estimated = len(json.dumps({"source": APPLE_HEALTH, "observations": observations, "workouts": workouts}, separators=(",", ":")))
                if estimated >= _BATCH_BYTES:
                    flush(client)
            flush(client)
    return {
        "status": "OK",
        "batches": summary["batches"],
        "accepted": summary["accepted"],
        "duplicates": summary["duplicates"],
        "rejected": summary["rejected"],
        "observations_seen": summary["observations_seen"],
        "workouts_seen": summary["workouts_seen"],
        "ignored_apple_types": sorted(ignored),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a native Apple Health ZIP into private Health OS")
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--endpoint", required=True, help="Health OS base HTTPS URL")
    arguments = parser.parse_args()
    key = os.environ.get("HEALTH_OS_INGEST_KEY")
    if not key:
        raise SystemExit("HEALTH_OS_INGEST_KEY must be provided through the environment")
    try:
        print(json.dumps(import_zip(arguments.zip_path, arguments.endpoint, key), sort_keys=True))
    except (OSError, ValueError, ET.ParseError, httpx.HTTPError, RuntimeError) as exc:
        print(json.dumps({"status": "FAILED", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from exc
