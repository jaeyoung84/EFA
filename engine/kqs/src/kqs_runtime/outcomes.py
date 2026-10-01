"""Forward outcomes of published v2 recommendations (EINT-019).

Each pick says "buy at the next session". This module looks up, after the fact, what that would
have returned at the close of the 1st, 5th, 10th and 20th session counting the entry session as
the 1st. It is a read-only observation of the engine's own immutable snapshots against the
collected KRX daily bars:

- no orders, fills, positions or ledger rows -- it does not apply any item's exit rule;
- no costs, taxes or slippage (gross returns from the entry-session open);
- a horizon whose session has not been collected yet stays ``observed: false`` (never filled);
- it never selects, promotes or rejects a strategy. Per-item sample gates mark summaries below
  ``MIN_SAMPLE`` observations as ``insufficient_sample``.

Written atomically to the state dir for the website, like the instrument catalog.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import statistics
import tempfile
from datetime import date
from pathlib import Path

from korea_quant.eligibility import DEFAULT_MARKET_BENCHMARKS
from kqs_publication.contract import RECOMMENDATION_SCHEMA_V2
from kqs_publication.store import published_payloads
from kqs_runtime.recommendations import ITEMS

SCHEMA = "kqs-recommendation-outcomes/v1"
HORIZONS = (1, 5, 10, 20)
MIN_SAMPLE = 20
BENCHMARKS = dict(DEFAULT_MARKET_BENCHMARKS)
PRICE_COLUMNS = ("open", "high", "low", "close")


class OutcomeError(ValueError):
    """The outcome report could not be built."""


def _pct(start: float, end: float) -> float:
    return round((end / start - 1) * 100, 4)


def _halted(bar: dict) -> bool:
    return any(bar[column] <= 0 for column in PRICE_COLUMNS)


def _bars(connection: sqlite3.Connection, symbol: str, sessions: list[str]) -> dict[str, dict]:
    if not sessions:
        return {}
    rows = connection.execute(
        "SELECT trade_date, open, high, low, close FROM daily_prices WHERE symbol = ? "
        "AND trade_date >= ? AND trade_date <= ?", (symbol, sessions[0], sessions[-1])).fetchall()
    return {day: dict(zip(PRICE_COLUMNS, values)) for day, *values in rows}


def _sessions_after(connection: sqlite3.Connection, as_of: str, through: str) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT DISTINCT trade_date FROM daily_prices WHERE market IN ('KOSPI', 'KOSDAQ') "
        "AND trade_date > ? AND trade_date <= ? ORDER BY trade_date LIMIT ?",
        (as_of, through, max(HORIZONS)))]


def _pick_outcome(connection: sqlite3.Connection, payload: dict, signal: dict, through: str) -> dict:
    as_of = payload["engine"]["data_as_of"]
    row = {"as_of": as_of, "snapshot_id": payload["publication"]["snapshot_id"], "item": signal["signal_type"],
           "rank": signal["selection_rank"], "symbol": signal["symbol"], "name": signal["name"],
           "market": signal["market"], "entry_date": None, "entry_open": None, "horizons": {}}
    sessions = _sessions_after(connection, as_of, through)
    if not sessions:
        return {**row, "status": "awaiting_entry"}
    bars = _bars(connection, signal["symbol"], sessions)
    entry = bars.get(sessions[0])
    if entry is None:
        return {**row, "entry_date": sessions[0], "status": "missing_entry_bar"}
    if _halted(entry):
        return {**row, "entry_date": sessions[0], "status": "entry_halted"}
    benchmark = _bars(connection, BENCHMARKS[signal["market"]], sessions)
    bench_entry = benchmark.get(sessions[0])
    bench_open = bench_entry["open"] if bench_entry and not _halted(bench_entry) else None
    row.update(entry_date=sessions[0], entry_open=entry["open"])

    status = "observed"
    last_close, low, high, carried = entry["open"], entry["open"], entry["open"], False
    for index, day in enumerate(sessions, start=1):
        bar = bars.get(day)
        if bar is None:  # delisted or not collected: stop, never fill
            status = "missing_bar"
            break
        if _halted(bar):  # trading halt: the price stays at the last close, as in the engine's reader
            carried = True
        else:
            last_close, low, high = bar["close"], min(low, bar["low"]), max(high, bar["high"])
        if index in HORIZONS:
            bench_bar = benchmark.get(day)
            bench_return = (_pct(bench_open, bench_bar["close"])
                            if bench_open and bench_bar and not _halted(bench_bar) else None)
            stock_return = _pct(entry["open"], last_close)
            row["horizons"][str(index)] = {
                "target_date": day, "close": last_close, "return_pct": stock_return,
                "benchmark_return_pct": bench_return,
                "excess_return_pct": round(stock_return - bench_return, 4) if bench_return is not None else None,
                "max_drawdown_pct": _pct(entry["open"], low), "max_runup_pct": _pct(entry["open"], high),
                "halt_carried": carried}
    for horizon in HORIZONS:
        row["horizons"].setdefault(str(horizon), {"observed": False})
    for horizon in row["horizons"].values():
        horizon.setdefault("observed", True)
    return {**row, "status": status}


def _summary(rows: list[dict]) -> dict:
    items = {}
    for item in ITEMS:
        picks = [row for row in rows if row["item"] == item]
        horizons = {}
        for horizon in HORIZONS:
            returns = [row["horizons"][str(horizon)]["return_pct"] for row in picks
                       if row["horizons"].get(str(horizon), {}).get("observed")]
            excess = [row["horizons"][str(horizon)]["excess_return_pct"] for row in picks
                      if row["horizons"].get(str(horizon), {}).get("excess_return_pct") is not None]
            horizons[str(horizon)] = {
                "observed_count": len(returns),
                "mean_return_pct": round(statistics.fmean(returns), 4) if returns else None,
                "median_return_pct": round(statistics.median(returns), 4) if returns else None,
                "win_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 2) if returns else None,
                "mean_excess_return_pct": round(statistics.fmean(excess), 4) if excess else None,
                "sample_gate": "ready" if len(returns) >= MIN_SAMPLE else "insufficient_sample"}
        items[item] = {"pick_count": len(picks), "session_count": len({row["as_of"] for row in picks}),
                       "horizons": horizons}
    return items


def build_outcome_report(snapshot_db: Path, market_db: Path, through: str) -> dict:
    """Outcomes of every verified v2 publication up to ``through`` against bars up to ``through``."""
    if date.fromisoformat(through).isoformat() != through:
        raise OutcomeError("through must be YYYY-MM-DD.")
    try:
        resolved = Path(market_db).resolve(strict=True)
    except OSError as exc:
        raise OutcomeError("Market DB does not exist.") from exc
    payloads = published_payloads(Path(snapshot_db), schema=RECOMMENDATION_SCHEMA_V2, through=through)
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        rows = [_pick_outcome(connection, payload, signal, through)
                for payload in payloads for signal in payload["signals"]
                if signal.get("action") == "entry" and signal.get("selection_outcome") == "selected"]
    except sqlite3.Error as exc:
        raise OutcomeError(f"Outcome query failed: {exc}") from exc
    finally:
        connection.close()
    rows.sort(key=lambda row: (row["as_of"], ITEMS.index(row["item"]), row["rank"]))
    sources = [{"as_of": p["engine"]["data_as_of"], "snapshot_id": p["publication"]["snapshot_id"],
                "payload_sha256": p["integrity"]["payload_sha256"]} for p in payloads]
    digest = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":")).encode()).hexdigest()
    return {"schema": SCHEMA, "through": through, "horizons": list(HORIZONS), "min_sample": MIN_SAMPLE,
            "entry_basis": "next_session_open", "costs_included": False, "orders_enabled": False,
            "benchmarks": BENCHMARKS, "source_count": len(sources), "sources_sha256": digest(sources),
            "rows_sha256": digest(rows), "summary": _summary(rows), "rows": rows, "sources": sources}


def write_outcome_report(path: Path, report: dict) -> Path:
    """Replace the report atomically so the website never reads a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".recommendation-outcomes-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
