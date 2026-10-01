"""Immutable append-only publications, separate from market data and human/auto ledgers."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .contract import KqsContractError, _parse_datetime, canonical_payload_digest, validate_recommendation_payload
from .publisher import build_payload, encode, sha256, validate_run
from .writer import WriterLock

MAX_PAYLOAD_BYTES = 512 * 1024
MAX_RUN_BYTES = 8 * 1024 * 1024
TABLE = "kqs_recommendation_snapshots"


def _decode(row: sqlite3.Row, *, now: datetime) -> dict:
    raw = row["payload_json"]
    if len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise KqsContractError("추천 응답이512 KiB 제한을 넘었습니다.")
    payload = json.loads(raw)
    if payload["integrity"]["payload_sha256"] != row["payload_sha256"]:
        raise KqsContractError("저장된 추천 무결성 지문이 다릅니다.")
    if (payload["publication"]["snapshot_id"], payload["engine"]["run_id"],
        payload["engine"]["data_as_of"], payload["generated_at"], payload["expires_at"]) != (
        row["snapshot_id"], row["run_id"], row["data_as_of"], row["generated_at"], row["expires_at"]):
        raise KqsContractError("게시 색인과 추천 내용이 일치하지 않습니다.")
    validate_recommendation_payload(payload, now=now)
    return payload


def read_latest(path: Path, *, now: datetime | None = None) -> dict:
    """Read the newest DATA session, never renew or fall back to older valid rows."""
    if not path.is_file():
        raise KqsContractError("게시된 추천 스냅샷이 없습니다. 별도 추천 작업의 최초 게시가 필요합니다.")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(f"SELECT snapshot_id,run_id,data_as_of,generated_at,expires_at,"
                                 f"payload_sha256,payload_json FROM {TABLE} "
                                 "ORDER BY data_as_of DESC,generated_at DESC,snapshot_id DESC LIMIT 1").fetchone()
        if row is None:
            raise KqsContractError("게시된 추천 스냅샷이 없습니다.")
        return _decode(row, now=now or datetime.now(timezone.utc))
    finally:
        connection.close()


def recommended_symbols(path: Path, *, since: dict[str, str], before: str) -> list[tuple[str, str]]:
    """(symbol, signal_type) pairs this engine published from ``since[signal_type]`` up to, not including,
    the ``before`` session (YYYY-MM-DD) -- so rerunning a session sees the same set (idempotent run_id).

    Feeds the EINT-017 exit monitor. Reads only the engine's own immutable snapshots -- never a
    user ledger -- and skips rows whose stored digest does not match their payload.
    """
    if not path.is_file():
        return []
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return []
        oldest = min(since.values())
        pairs = set()
        for row in connection.execute(f"SELECT data_as_of, payload_sha256, payload_json FROM {TABLE} "
                                      "WHERE data_as_of >= ? AND data_as_of < ?", (oldest, before)):
            payload = json.loads(row["payload_json"])
            if canonical_payload_digest(payload) != row["payload_sha256"]:
                continue
            for signal in payload.get("signals", []):
                kind = signal.get("signal_type")
                if (signal.get("action") == "entry" and signal.get("selection_outcome") == "selected"
                        and kind in since and row["data_as_of"] >= since[kind]):
                    pairs.add((str(signal["symbol"]), kind))
        return sorted(pairs)
    finally:
        connection.close()


def published_payloads(path: Path, *, schema: str, through: str) -> list[dict]:
    """Every stored payload of ``schema`` with ``data_as_of <= through``, oldest first.

    Feeds the EINT-019 forward outcome tracker. Read-only; rows whose stored digest does not
    match their payload are skipped, as in ``recommended_symbols``. One payload per session: a
    rerun that published again keeps the first (earliest generated) row of that session.
    """
    if not path.is_file():
        return []
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return []
        payloads = {}
        for row in connection.execute(f"SELECT data_as_of, payload_sha256, payload_json FROM {TABLE} "
                                      "WHERE data_as_of <= ? ORDER BY data_as_of, generated_at, snapshot_id",
                                      (through,)):
            payload = json.loads(row["payload_json"])
            if (payload.get("schema_version") != schema or row["data_as_of"] in payloads
                    or canonical_payload_digest(payload) != row["payload_sha256"]):
                continue
            payloads[row["data_as_of"]] = payload
        return list(payloads.values())
    finally:
        connection.close()


def is_published(path: Path, run_id: str) -> bool:
    """Read-only check used by the writer while it holds the lock; never creates the DB."""
    if not path.is_file():
        return False
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only = ON")
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return False
        return connection.execute(f"SELECT 1 FROM {TABLE} WHERE run_id=?", (run_id,)).fetchone() is not None
    finally:
        connection.close()


def publish(path: Path, runtime: dict, *, expected_as_of: str, expires_at: datetime,
            writer_lock: WriterLock, generated_at: datetime | None = None) -> dict:
    """Explicit worker-only write. Same run keeps its FIRST timestamps, even after expiry."""
    if not isinstance(writer_lock, WriterLock):
        raise KqsContractError("게시는 단일 writer 잠금(WriterLock)을 보유한 경우에만 가능합니다.")
    writer_lock.require(path)
    validate_run(runtime)
    if runtime["as_of"] != expected_as_of:
        raise KqsContractError("예상 자료 기준일이 실제 자료와 다릅니다.")
    runtime_json = encode(runtime)
    if len(runtime_json.encode()) > MAX_RUN_BYTES:
        raise KqsContractError("엔진 감사 결과가8 MiB 제한을 넘었습니다.")
    runtime_sha = sha256(runtime)
    now = generated_at or datetime.now(timezone.utc)
    # Check the publication before creating a file; no implicit time-to-live computation.
    incoming = build_payload(runtime, generated_at=now, expires_at=expires_at, expected_as_of=expected_as_of)
    incoming_json = encode(incoming)
    if len(incoming_json.encode()) > MAX_PAYLOAD_BYTES:
        raise KqsContractError("게시 추천이512 KiB 제한을 넘었습니다.")
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
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables - {TABLE}:
            raise KqsContractError("가격 DB/개인 원장/다른 DB에는 추천을 게시할 수 없습니다.")
        connection.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (snapshot_id TEXT PRIMARY KEY, "
                           "run_id TEXT NOT NULL UNIQUE, data_as_of TEXT NOT NULL, generated_at TEXT NOT NULL, "
                           "expires_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, "
                           "runtime_sha256 TEXT NOT NULL, runtime_json TEXT NOT NULL)")
        for operation in ("UPDATE", "DELETE"):
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS kqs_no_{operation.lower()} "
                               f"BEFORE {operation} ON {TABLE} BEGIN "
                               "SELECT RAISE(ABORT, 'KQS snapshots are immutable'); END")
        existing = connection.execute(f"SELECT * FROM {TABLE} WHERE run_id=?", (runtime["run_id"],)).fetchone()
        if existing:
            if existing["runtime_sha256"] != runtime_sha:
                raise KqsContractError("같은 실행 ID에 다른 추천/감사 내용을 게시할 수 없습니다.")
            # Validation at original generation preserves expired history without reactivation.
            payload = _decode(existing, now=_parse_datetime(existing["generated_at"], "generated_at"))
        else:
            latest = connection.execute(f"SELECT MAX(data_as_of) FROM {TABLE}").fetchone()[0]
            if latest and runtime["as_of"] < latest:
                raise KqsContractError("더 오래된 자료로 현재 게시 기준일을 되돌릴 수 없습니다.")
            connection.execute(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?)",
                               (incoming["publication"]["snapshot_id"], runtime["run_id"], runtime["as_of"],
                                incoming["generated_at"], incoming["expires_at"],
                                incoming["integrity"]["payload_sha256"], incoming_json, runtime_sha, runtime_json))
            payload = incoming
        connection.execute("COMMIT")
        return payload
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
