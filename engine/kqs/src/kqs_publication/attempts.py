"""Immutable publication-worker attempt log; separate from market, quality and snapshot DBs."""

import json
import os
import re
import sqlite3
from pathlib import Path

from .contract import KqsContractError
from .publisher import encode

TABLE = "kqs_publication_worker_attempts"
STATUSES = frozenset({"published", "already_published", "blocked_quality", "writer_busy", "failed"})
MAX_ATTEMPT_BYTES = 64 * 1024
_HEX = re.compile(r"[0-9a-f]{32}|[0-9a-f]{64}")


def _validate(attempt: dict) -> None:
    if attempt.get("status") not in STATUSES:
        raise KqsContractError("worker 시도 상태가 올바르지 않습니다.")
    if not isinstance(attempt.get("attempt_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", attempt["attempt_id"]):
        raise KqsContractError("worker 시도 ID 형식이 올바르지 않습니다.")
    for key in ("quality_run_id", "run_id"):
        value = attempt.get(key)
        if value is not None and (not isinstance(value, str) or not _HEX.fullmatch(value)):
            raise KqsContractError(f"worker 시도 {key} 형식이 올바르지 않습니다.")
    if attempt.get("orders_enabled") is not False:
        raise KqsContractError("worker 시도는 주문 비활성 상태여야 합니다.")


def record_attempt(path: Path, attempt: dict) -> dict:
    """Append one attempt. Every attempt, including failures, is an observation, never rewritten."""
    _validate(attempt)
    encoded = encode(attempt)
    if len(encoded.encode()) > MAX_ATTEMPT_BYTES:
        raise KqsContractError("worker 시도 기록이 64 KiB 제한을 넘었습니다.")
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
            raise KqsContractError("worker 시도 로그는 시장/품질/추천/개인 원장 DB와 분리해야 합니다.")
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} (attempt_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, "
            "status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
            "quality_run_id TEXT, run_id TEXT, snapshot_id TEXT, error TEXT, attempt_json TEXT NOT NULL)"
        )
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS kqs_attempt_no_{operation.lower()} BEFORE {operation} ON {TABLE} "
                "BEGIN SELECT RAISE(ABORT, 'KQS worker attempts are immutable'); END"
            )
        connection.execute(
            f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?,?)",
            (attempt["attempt_id"], attempt["as_of"], attempt["status"], attempt["started_at"],
             attempt["finished_at"], attempt.get("quality_run_id"), attempt.get("run_id"),
             attempt.get("snapshot_id"), attempt.get("error"), encoded),
        )
        connection.execute("COMMIT")
        return attempt
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def read_attempts(path: Path) -> list[dict]:
    """Read-only listing for operators and tests; never creates the log."""
    if not path.is_file():
        return []
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(f"SELECT attempt_json FROM {TABLE} ORDER BY started_at, attempt_id").fetchall()
    finally:
        connection.close()
    return [json.loads(row[0]) for row in rows]
