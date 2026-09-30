"""The single publication writer: lock, quality gate, compute, publish, record the attempt.

This is the only installed entry point that writes recommendation snapshots. It never
collects market data, creates orders or fills, or touches personal ledgers.
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pandas.errors import DatabaseError

from kqs_collection.quality import QualityError, inspect_market_inputs
from kqs_collection.store import read_run
from kqs_publication.attempts import record_attempt
from kqs_publication.contract import _parse_datetime
from kqs_publication.store import is_published, publish
from kqs_publication.writer import WriterBusy, WriterLock
from kqs_runtime.recommendations import generate_recommendations
from kqs_runtime.sqlite_reader import read_inputs

EXIT_OK, EXIT_FAILED, EXIT_BUSY = 0, 2, 3


def _same_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    return left.exists() and right.exists() and left.samefile(right)


def require_separate_paths(**paths: Path) -> None:
    names = list(paths)
    for index, first in enumerate(names):
        for second in names[index + 1:]:
            if _same_file(paths[first], paths[second]):
                raise ValueError(f"{first} and {second} must be separate files.")


def require_recorded_ready_quality(*, krx_db: Path, as_of: str, quality_db: Path, quality_run_id: str,
                                   coverage_manifest: Path) -> dict:
    """Use only a ready run recorded in the immutable evidence DB, reproduced unchanged now."""
    stored = read_run(quality_db, quality_run_id)
    if stored["as_of"] != as_of:
        raise QualityError("Recorded quality run as_of must equal the requested data session.")
    if stored["status"] != "ready":
        raise QualityError("Recorded quality run is not ready; publication is blocked.")
    fresh = inspect_market_inputs(krx_db, as_of=as_of, observed_at=stored["observed_at"],
                                  coverage_manifest=coverage_manifest)
    if fresh["run_id"] != stored["run_id"] or fresh["status"] != "ready":
        raise QualityError("Market inputs no longer match the recorded ready quality run; publication is blocked.")
    return stored


def run_publication(*, krx_db: Path, history_start: str, as_of: str, expected_as_of: str,
                    expires_at: datetime, snapshot_db: Path, quality_db: Path, quality_run_id: str,
                    coverage_manifest: Path, attempts_db: Path, now: datetime | None = None) -> dict:
    require_separate_paths(krx_db=krx_db, snapshot_db=snapshot_db, quality_db=quality_db, attempts_db=attempts_db)
    started = now or datetime.now(timezone.utc)
    attempt = {"attempt_id": uuid.uuid4().hex, "as_of": as_of, "expected_as_of": expected_as_of,
               "started_at": started.isoformat(), "quality_run_id": quality_run_id,
               "run_id": None, "snapshot_id": None, "error": None, "orders_enabled": False}

    def finish(status: str, *, payload: dict | None = None, error: str | None = None) -> dict:
        attempt.update(status=status, error=error, finished_at=datetime.now(timezone.utc).isoformat())
        return {"attempt": record_attempt(attempts_db, attempt), "payload": payload}

    lock = WriterLock(snapshot_db)
    try:
        lock.acquire()
    except WriterBusy as exc:
        return finish("writer_busy", error=str(exc))
    try:
        try:
            require_recorded_ready_quality(krx_db=krx_db, as_of=as_of, quality_db=quality_db,
                                           quality_run_id=quality_run_id, coverage_manifest=coverage_manifest)
        except (QualityError, OSError) as exc:
            return finish("blocked_quality", error=str(exc))
        try:
            history, benchmarks = read_inputs(krx_db, history_start=history_start, as_of=as_of)
            dropped = history.attrs.get("dropped_unfillable_halted_rows", {"rows": 0, "symbols": []})
            attempt["input_adjustments"] = {"dropped_unfillable_halted_rows": dropped["rows"],
                                            "dropped_symbol_count": len(dropped["symbols"])}
            runtime = generate_recommendations(history, benchmarks, as_of=as_of, excluded_symbols=())
            attempt["run_id"] = runtime["run_id"]
            existed = is_published(snapshot_db, runtime["run_id"])
            payload = publish(snapshot_db, runtime, expected_as_of=expected_as_of, expires_at=expires_at,
                              generated_at=started, writer_lock=lock)
        except (ValueError, OSError, sqlite3.Error, DatabaseError) as exc:
            return finish("failed", error=str(exc))
        attempt["snapshot_id"] = payload["publication"]["snapshot_id"]
        return finish("already_published" if existed else "published", payload=payload)
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Single KQS recommendation publication writer; no collection, orders or fills.")
    parser.add_argument("--krx-db", required=True, type=Path, help="Existing market SQLite; opened mode=ro")
    parser.add_argument("--history-start", required=True, help="Explicit history boundary, YYYY-MM-DD")
    parser.add_argument("--as-of", required=True, help="Data session, not today's wall-clock date")
    parser.add_argument("--expected-as-of", required=True, help="Explicit expected data session")
    parser.add_argument("--expires-at", required=True, help="Absolute timezone-aware expiry")
    parser.add_argument("--snapshot-db", required=True, type=Path, help="Separate publication DB")
    parser.add_argument("--quality-db", required=True, type=Path, help="Immutable quality evidence DB")
    parser.add_argument("--quality-run-id", required=True, help="Recorded ready quality run_id")
    parser.add_argument("--coverage-manifest", required=True, type=Path, help="Manifest of that quality run")
    parser.add_argument("--attempts-db", required=True, type=Path, help="Separate immutable worker attempt log")
    args = parser.parse_args(argv)
    try:
        result = run_publication(
            krx_db=args.krx_db, history_start=args.history_start, as_of=args.as_of,
            expected_as_of=args.expected_as_of, expires_at=_parse_datetime(args.expires_at, "expires_at"),
            snapshot_db=args.snapshot_db, quality_db=args.quality_db, quality_run_id=args.quality_run_id,
            coverage_manifest=args.coverage_manifest, attempts_db=args.attempts_db)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc), "orders_enabled": False}, ensure_ascii=False), file=sys.stderr)
        return EXIT_FAILED
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    status = result["attempt"]["status"]
    if status in {"published", "already_published"}:
        return EXIT_OK
    return EXIT_BUSY if status == "writer_busy" else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
