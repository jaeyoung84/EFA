"""Immutable single-host quality-run evidence store; separate from market/recommendation DBs."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .quality import QualityError, canonical_digest, validate_report

TABLE = "kqs_market_input_quality_runs"
MAX_REPORT_BYTES = 2 * 1024 * 1024


def read_run(path: Path, run_id: str) -> dict:
    """Read one recorded quality run read-only; never creates the evidence DB."""
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise QualityError("Quality evidence DB does not exist.") from exc
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(f"SELECT report_json,report_sha256 FROM {TABLE} WHERE run_id=?",
                                 (run_id,)).fetchone()
    except sqlite3.Error as exc:
        raise QualityError(f"Quality evidence DB read failed: {exc}") from exc
    finally:
        connection.close()
    if row is None:
        raise QualityError("Quality run is not recorded in the evidence DB.")
    report = validate_report(json.loads(row[0]))
    if canonical_digest(report) != row[1]:
        raise QualityError("Recorded quality run digest mismatch.")
    return report


def publish(path: Path, report: dict) -> dict:
    validate_report(report)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > MAX_REPORT_BYTES:
        raise QualityError("Quality report exceeds 2 MiB.")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {TABLE}:
            raise QualityError("Quality evidence must not share a market, recommendation, or personal ledger DB.")
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} (run_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, "
            "observed_at TEXT NOT NULL, report_sha256 TEXT NOT NULL, report_json TEXT NOT NULL)"
        )
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS kqs_quality_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
                "BEGIN SELECT RAISE(ABORT, 'KQS quality runs are immutable'); END"
            )
        existing = connection.execute(f"SELECT report_json,report_sha256 FROM {TABLE} WHERE run_id=?",
                                      (report["run_id"],)).fetchone()
        report_sha = canonical_digest(report)
        if existing:
            if existing[1] != report_sha:
                raise QualityError("The same quality run_id cannot record different evidence.")
            stored = json.loads(existing[0])
            validate_report(stored)
            connection.execute("COMMIT")
            return stored
        connection.execute(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?)",
                           (report["run_id"], report["as_of"], report["observed_at"], report_sha, encoded))
        connection.execute("COMMIT")
        return report
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
