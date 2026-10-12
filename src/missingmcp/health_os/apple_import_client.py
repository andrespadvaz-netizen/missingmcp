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
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Iterable
from xml.etree import ElementTree as ET
from xml.sax import handler, make_parser

import httpx

from .apple_export import _provenance, observation_from_apple_record
from .core import APPLE_HEALTH

# Keep each request comfortably below the API limit and short enough for the
# encrypted SQLite writer to remain responsive on a single Railway replica.
_BATCH_BYTES = 80_000


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


class _StreamingAppleHealthHandler(handler.ContentHandler):
    """SAX handler used for large exports; it never retains the XML tree."""

    def __init__(self, receive) -> None:
        super().__init__()
        self._receive = receive

    def startElement(self, name: str, attrs) -> None:  # noqa: N802 - SAX API name
        attributes = dict(attrs.items())
        if name == "Record":
            observation = observation_from_apple_record(attributes)
            if observation is None:
                self._receive("ignored", attributes.get("type", "") or "unknown")
                return
            self._receive("observation", observation)
        elif name == "Workout":
            self._receive("workout", {
                "workout_id": _provenance(attributes),
                "start_time": attributes["startDate"],
                "end_time": attributes["endDate"],
                "activity_type": attributes.get("workoutActivityType", "other"),
            })


def _stream_records(stream: BinaryIO, receive) -> None:
    """Parse an export in constant XML-memory space and invoke ``receive``."""
    parser = make_parser()
    parser.setFeature(handler.feature_external_ges, False)
    parser.setFeature(handler.feature_external_pes, False)
    parser.setContentHandler(_StreamingAppleHealthHandler(receive))
    parser.parse(stream)


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


def _checkpoint_identity(zip_path: Path, since: datetime | None,
                         metrics: Iterable[str] | None) -> dict[str, Any]:
    """Return non-medical identity data for a resumable local import.

    A checkpoint never contains sample values or provenance identifiers.  It
    only binds a completed sequence count to the exact archive revision and
    user-selected import scope.
    """
    stat = zip_path.stat()
    return {
        "archive_name": zip_path.name,
        "archive_bytes": stat.st_size,
        "archive_mtime_ns": stat.st_mtime_ns,
        "since": since.isoformat() if since else None,
        "metrics": sorted(metrics or ()),
    }


def _load_checkpoint(path: Path, identity: dict[str, Any]) -> int:
    if not path.exists():
        return 0
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid import checkpoint") from exc
    if saved.get("identity") != identity:
        raise ValueError("checkpoint belongs to a different archive or import scope")
    completed = saved.get("completed_records")
    if not isinstance(completed, int) or completed < 0:
        raise ValueError("invalid import checkpoint")
    return completed


def _write_checkpoint(path: Path, identity: dict[str, Any], completed: int) -> None:
    """Atomically persist only an acknowledged record count, never PHI."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"identity": identity, "completed_records": completed},
                                    sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def import_zip(zip_path: Path, endpoint: str, key: str, since: datetime | None = None,
               metrics: Iterable[str] | None = None, checkpoint: Path | None = None) -> dict[str, Any]:
    summary: Counter[str] = Counter()
    ignored: Counter[str] = Counter()
    observations: list[dict[str, Any]] = []
    workouts: list[dict[str, Any]] = []
    selected_metrics = set(metrics or ())
    identity = _checkpoint_identity(zip_path, since, selected_metrics)
    completed = _load_checkpoint(checkpoint, identity) if checkpoint else 0
    eligible = 0

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
        if checkpoint:
            _write_checkpoint(checkpoint, identity, eligible)

    with zipfile.ZipFile(zip_path) as archive, httpx.Client(timeout=90.0) as client:
        entry = _export_entry(archive)
        with archive.open(entry) as stream:
            def receive(kind: str, item: dict[str, Any] | str) -> None:
                nonlocal observations, workouts, eligible
                if kind == "ignored":
                    ignored[str(item)] += 1
                    return
                observed_at = item["observed_at"] if kind == "observation" else item["end_time"]  # type: ignore[index]
                if since is not None and datetime.fromisoformat(observed_at) < since:
                    summary["outside_window"] += 1
                    return
                if kind == "observation" and selected_metrics and item["metric"] not in selected_metrics:  # type: ignore[index]
                    summary["outside_metrics"] += 1
                    return
                eligible += 1
                if eligible <= completed:
                    summary["checkpoint_skipped"] += 1
                    return
                if kind == "observation":
                    observations.append(item)  # type: ignore[arg-type]
                    summary["observations_seen"] += 1
                else:
                    workouts.append(item)  # type: ignore[arg-type]
                    summary["workouts_seen"] += 1
                estimated = len(json.dumps({"source": APPLE_HEALTH, "observations": observations, "workouts": workouts}, separators=(",", ":")))
                if estimated >= _BATCH_BYTES:
                    flush(client)
            _stream_records(stream, receive)
            flush(client)
    return {
        "status": "OK",
        "batches": summary["batches"],
        "accepted": summary["accepted"],
        "duplicates": summary["duplicates"],
        "rejected": summary["rejected"],
        "observations_seen": summary["observations_seen"],
        "workouts_seen": summary["workouts_seen"],
        "outside_window": summary["outside_window"],
        "outside_metrics": summary["outside_metrics"],
        "checkpoint_skipped": summary["checkpoint_skipped"],
        "ignored_apple_types": sorted(ignored),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a native Apple Health ZIP into private Health OS")
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--endpoint", required=True, help="Health OS base HTTPS URL")
    parser.add_argument("--since", help="Import samples at or after an ISO-8601 instant")
    parser.add_argument("--metric", action="append", default=[],
                        help="Import one canonical metric; repeat to include several")
    parser.add_argument("--checkpoint", type=Path,
                        help="Local non-PHI resume checkpoint; created after each acknowledged batch")
    arguments = parser.parse_args()
    key = os.environ.get("HEALTH_OS_INGEST_KEY")
    if not key:
        raise SystemExit("HEALTH_OS_INGEST_KEY must be provided through the environment")
    try:
        since = datetime.fromisoformat(arguments.since) if arguments.since else None
        if since is not None and since.tzinfo is None:
            raise ValueError("--since must include a UTC offset")
        print(json.dumps(import_zip(arguments.zip_path, arguments.endpoint, key, since,
                                   arguments.metric, arguments.checkpoint), sort_keys=True))
    except (OSError, ValueError, ET.ParseError, httpx.HTTPError, RuntimeError) as exc:
        print(json.dumps({"status": "FAILED", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
