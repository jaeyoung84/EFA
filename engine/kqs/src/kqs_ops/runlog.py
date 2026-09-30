"""Immutable daily-pipeline run log and an atomically replaced latest-status file."""

import json
import os
import re
import sqlite3
from pathlib import Path

TABLE = "kqs_pipeline_runs"
STATUSES = frozenset({
    "published", "already_published", "collect_failed", "coverage_rejected", "quality_rejected",
    "blocked_quality", "writer_busy", "publish_failed", "failed",
})
SUCCESS = frozenset({"published", "already_published"})


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def record_run(path: Path, run: dict) -> dict:
    if run.get("status") not in STATUSES:
        raise ValueError("Pipeline run status is invalid.")
    if not isinstance(run.get("run_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", run["run_id"]):
        raise ValueError("Pipeline run_id is invalid.")
    if run.get("orders_enabled") is not False:
        raise ValueError("Pipeline runs must be order-disabled.")
    encoded = _encode(run)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {TABLE}:
            raise ValueError("Pipeline run log must be a separate database.")
        connection.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (run_id TEXT PRIMARY KEY, session TEXT, "
                           "status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
                           "run_json TEXT NOT NULL)")
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS kqs_pipeline_no_{operation.lower()} BEFORE "
                               f"{operation} ON {TABLE} BEGIN SELECT RAISE(ABORT, 'KQS pipeline runs are immutable'); END")
        connection.execute(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?)",
                           (run["run_id"], run.get("session"), run["status"], run["started_at"],
                            run["finished_at"], encoded))
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return run


def read_runs(path: Path, limit: int = 50) -> list[dict]:
    if not path.is_file():
        return []
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(f"SELECT run_json FROM {TABLE} ORDER BY started_at DESC, run_id DESC LIMIT ?",
                                  (limit,)).fetchall()
    finally:
        connection.close()
    return [json.loads(row[0]) for row in rows]


def write_status(path: Path, run: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_encode(run), encoding="utf-8")
    os.replace(temporary, path)
