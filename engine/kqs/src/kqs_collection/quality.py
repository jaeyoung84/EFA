"""Validate one existing KRX SQLite snapshot against an explicit coverage manifest."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from kqs_collection import PROTOCOL
from kqs_publication.calendar import CalendarError, calendar_for_session, validate_observation_calendar

MARKETS = ("KOSPI", "KOSDAQ")
BENCHMARKS = {"KOSPI": "069500", "KOSDAQ": "229200"}
REQUIRED_COLUMNS = {
    "trade_date", "symbol", "name", "market", "open", "high", "low", "close",
    "volume", "trading_value", "market_cap",
}


class QualityError(ValueError):
    """Raised for an invalid coverage manifest or market-input quality report."""


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def parse_observed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise QualityError("observed_at is required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QualityError("observed_at must be ISO-8601 with timezone.") from exc
    if parsed.tzinfo is None:
        raise QualityError("observed_at must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise QualityError(f"{field} must be YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise QualityError(f"{field} must be YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise QualityError(f"{field} must be canonical YYYY-MM-DD.")
    return parsed


def load_coverage_manifest(path: Path, *, as_of: str) -> tuple[dict, str]:
    try:
        raw_bytes = path.resolve(strict=True).read_bytes()
        manifest = json.loads(raw_bytes)
    except (OSError, ValueError) as exc:
        raise QualityError("Coverage manifest must be an existing valid JSON file.") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "kqs-market-coverage/v1":
        raise QualityError("Coverage manifest schema must be kqs-market-coverage/v1.")
    if manifest.get("as_of") != as_of:
        raise QualityError("Coverage manifest as_of must equal requested as_of.")
    source_id = manifest.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 160:
        raise QualityError("Coverage manifest source_id is required.")
    markets = manifest.get("markets")
    if not isinstance(markets, dict) or set(markets) != set(MARKETS):
        raise QualityError("Coverage manifest must specify exactly KOSPI and KOSDAQ.")
    for market in MARKETS:
        expected = markets[market]
        if not isinstance(expected, dict) or type(expected.get("expected_symbol_count")) is not int:
            raise QualityError(f"Coverage manifest {market} expected_symbol_count must be an integer.")
        if not 1 <= expected["expected_symbol_count"] <= 10000:
            raise QualityError(f"Coverage manifest {market} expected_symbol_count is out of range.")
    if manifest.get("benchmarks") != BENCHMARKS:
        raise QualityError("Coverage manifest must use the approved KOSPI/KOSDAQ benchmark symbols.")
    return manifest, hashlib.sha256(raw_bytes).hexdigest()


def _code_identity() -> dict:
    root = Path(__file__).resolve().parents[1]
    paths = list((root / "kqs_collection").glob("*.py"))
    paths += [root / "kqs_publication/calendar.py", root / "kqs_publication/calendars/krx_equity_2026.json"]
    components = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(paths)}
    return {"code_sha256": canonical_digest(components), "component_sha256": components}


def _schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA table_info(daily_prices)").fetchall()
    columns = {row[1] for row in rows}
    if not REQUIRED_COLUMNS <= columns:
        missing = ", ".join(sorted(REQUIRED_COLUMNS - columns))
        raise QualityError(f"daily_prices is missing required columns: {missing}")


def _rows(connection: sqlite3.Connection, as_of: str) -> list[tuple]:
    return connection.execute(
        "SELECT trade_date,symbol,name,market,open,high,low,close,volume,trading_value,market_cap "
        "FROM daily_prices WHERE trade_date=? "
        "AND (market IN ('KOSPI','KOSDAQ') OR symbol IN ('069500','229200')) "
        "ORDER BY market,symbol,name,open,high,low,close,volume,trading_value,market_cap",
        (as_of,),
    ).fetchall()


def _finite_positive(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value)) and float(value) > 0


def _finite_non_negative(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value)) and float(value) >= 0


def _valid_bar(row: tuple) -> bool:
    """Accept traded bars and KRX halted/no-trade bars; reject anything else.

    KRX reports halted or untraded symbols with open/high/low 0 and zero volume/value while
    close (reference price) and market cap stay positive. Real sessions contain dozens of
    these rows, so requiring every OHLCV field to be positive would reject every real day.
    """
    open_, high, low, close, volume, value, cap = row[4:11]
    if not (_finite_positive(close) and _finite_positive(cap)):
        return False
    if not all(_finite_non_negative(item) for item in (open_, high, low, volume, value)):
        return False
    if volume == 0:
        return value == 0 and open_ == high == low == 0
    return (all(_finite_positive(item) for item in (open_, high, low, value))
            and low <= min(open_, close) and max(open_, close) <= high)


def inspect_market_inputs(path: Path, *, as_of: str, observed_at: str, coverage_manifest: Path) -> dict:
    """Read a snapshot once; never create/migrate/write the market database."""
    session = _date(as_of, "as_of")
    observed = parse_observed_at(observed_at)
    calendar = calendar_for_session(session)
    try:
        calendar_metadata = validate_observation_calendar(
            calendar.public_metadata(), data_as_of=session, observed_at=observed
        )
    except CalendarError as exc:
        raise QualityError(f"KRX session policy rejected observation: {exc}") from exc
    manifest, manifest_sha = load_coverage_manifest(coverage_manifest, as_of=as_of)
    path = path.resolve(strict=True)
    if not path.is_file():
        raise QualityError("KRX DB must be an existing regular file.")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        _schema(connection)
        rows = _rows(connection, as_of)
    except sqlite3.Error as exc:
        raise QualityError(f"Read-only KRX DB inspection failed: {exc}") from exc
    finally:
        connection.close()

    market_rows = {market: [row for row in rows if row[3] == market] for market in MARKETS}
    benchmark_rows = {market: [row for row in rows if row[1] == symbol]
                      for market, symbol in BENCHMARKS.items()}
    issues: list[str] = []
    coverage: dict[str, Any] = {}
    for market in MARKETS:
        rows_for_market = market_rows[market]
        symbols = [str(row[1]) for row in rows_for_market]
        expected = manifest["markets"][market]["expected_symbol_count"]
        duplicates = len(symbols) - len(set(symbols))
        invalid = sum(not _valid_bar(row) for row in rows_for_market)
        no_trade = sum(_valid_bar(row) and row[8] == 0 for row in rows_for_market)
        coverage[market] = {"expected_symbol_count": expected, "observed_row_count": len(rows_for_market),
                            "observed_symbol_count": len(set(symbols)), "duplicate_symbol_rows": duplicates,
                            "invalid_numeric_rows": invalid, "no_trade_rows": no_trade}
        if len(rows_for_market) != expected or len(set(symbols)) != expected:
            issues.append(f"{market}: expected {expected} unique rows, observed {len(rows_for_market)} rows/{len(set(symbols))} symbols")
        if duplicates:
            issues.append(f"{market}: duplicate symbols at as_of")
        if invalid:
            issues.append(f"{market}: invalid bar rows (non-positive close/cap, negative/non-finite, "
                          "inconsistent OHLC or traded volume without prices)")
    benchmark_report: dict[str, Any] = {}
    for market, symbol in BENCHMARKS.items():
        candidates = benchmark_rows[market]
        valid = len(candidates) == 1 and _finite_positive(candidates[0][7])
        benchmark_report[market] = {"symbol": symbol, "observed_rows": len(candidates), "positive_close": valid}
        if not valid:
            issues.append(f"{market}: benchmark {symbol} must have exactly one positive close")

    fingerprint_rows = [[str(value) if value is not None else None for value in row] for row in rows]
    code = _code_identity()
    input_fingerprint = canonical_digest({"as_of": as_of, "coverage_manifest_sha256": manifest_sha,
                                          "rows": fingerprint_rows})
    identity = {"protocol": PROTOCOL, "as_of": as_of, "observed_at": observed.isoformat(),
                "coverage_manifest_sha256": manifest_sha, "input_sha256": input_fingerprint,
                "code_sha256": code["code_sha256"]}
    report = {
        "schema": PROTOCOL, "run_id": canonical_digest(identity), "as_of": as_of,
        "observed_at": observed.isoformat(), "read_only": True, "orders_enabled": False,
        "status": "ready" if not issues else "rejected", "issues": issues,
        "calendar": calendar_metadata, "coverage_manifest": {
            "source_id": manifest["source_id"], "sha256": manifest_sha,
            "schema": manifest["schema"], "as_of": manifest["as_of"],
        }, "coverage": coverage, "benchmarks": benchmark_report,
        "input_sha256": input_fingerprint, "engine": code,
    }
    report["integrity"] = {"report_sha256": canonical_digest(report)}
    return report


def validate_report(report: object) -> dict:
    if not isinstance(report, dict) or report.get("schema") != PROTOCOL:
        raise QualityError("Unsupported market-input quality report.")
    integrity = report.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("report_sha256") != canonical_digest(
            {key: value for key, value in report.items() if key != "integrity"}):
        raise QualityError("Market-input quality report integrity mismatch.")
    if report.get("orders_enabled") is not False or report.get("read_only") is not True:
        raise QualityError("Quality report must be read-only and order-disabled.")
    if report.get("status") not in {"ready", "rejected"} or not isinstance(report.get("issues"), list):
        raise QualityError("Market-input quality report status is invalid.")
    _date(report.get("as_of"), "report.as_of")
    observed = parse_observed_at(report.get("observed_at"))
    try:
        validate_observation_calendar(report.get("calendar"), data_as_of=date.fromisoformat(report["as_of"]),
                                      observed_at=observed)
    except CalendarError as exc:
        raise QualityError(f"Market-input quality calendar is invalid: {exc}") from exc
    if not isinstance(report.get("run_id"), str) or len(report["run_id"]) != 64:
        raise QualityError("Market-input quality run_id is invalid.")
    return report
