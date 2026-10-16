"""Prospective shadow observation of one hypothesis: RSI-OBV plus rising SMA60.

Codex 수정: CODX-MA-002 (2026-10-16), 사전등록된 단일 가설만 추적한다.

This module never changes recommendations or creates orders.  It pairs every published
``long_term`` pick with a shadow arm that enters only when ``SMA60[t] > SMA60[t-1]``.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
import statistics
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from korea_quant.strategy import rsi_obv_signals
from kqs_publication.contract import RECOMMENDATION_SCHEMA_V2
from kqs_publication.store import published_payloads

SCHEMA = "kqs-rsi-obv-sma60-slope-shadow/v1"
PREREGISTRATION = "docs/engine/PREREGISTRATION_RSI_OBV_SMA60_SLOPE_v1.md"
PREREGISTRATION_SHA256 = "a89582aab23e4e858b6a06deacce0a5ef8fc026287e0f38cbeabd23cfbf437e7"
REGISTERED_FROM = "2026-10-16"
DEADLINE = "2028-10-16"
SMA_WINDOW = 60
MIN_B_CLOSED = 100
MIN_COVERAGE = 0.20
HISTORY_DAYS = 730
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20261016
BUY_COST = 0.00015 + 0.0005
SELL_COST = 0.00015 + 0.0018 + 0.0005
PRICE_COLUMNS = ("open", "high", "low", "close")


class SmaSlopeShadowError(ValueError):
    """The prospective observation could not be built."""


def net_return_pct(entry: float, exit_: float) -> float:
    return round((exit_ * (1 - SELL_COST) / (entry * (1 + BUY_COST)) - 1) * 100, 4)


def _halted(bar: dict) -> bool:
    return any(float(bar[column]) <= 0 for column in PRICE_COLUMNS)


def _history(connection: sqlite3.Connection, symbol: str, start: str, through: str) -> pd.DataFrame:
    rows = connection.execute(
        "SELECT trade_date, open, high, low, close, volume FROM daily_prices WHERE symbol = ? "
        "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date", (symbol, start, through)).fetchall()
    if not rows:
        return pd.DataFrame(columns=[*PRICE_COLUMNS, "volume"])
    return pd.DataFrame(rows, columns=["date", *PRICE_COLUMNS, "volume"]).set_index("date")


def _sessions_after(connection: sqlite3.Connection, day: str, through: str) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT DISTINCT trade_date FROM daily_prices WHERE market IN ('KOSPI', 'KOSDAQ') "
        "AND trade_date > ? AND trade_date <= ? ORDER BY trade_date", (day, through))]


def _arm(bars: pd.DataFrame, indicators: pd.DataFrame, sessions: list[str], entry_day: str) -> dict:
    if entry_day not in bars.index:
        return {"status": "missing_bar", "entry_date": entry_day}
    entry_bar = bars.loc[entry_day].to_dict()
    if _halted(entry_bar):
        return {"status": "entry_halted", "entry_date": entry_day}
    entry = float(entry_bar["open"])
    arm = {"entry_date": entry_day, "entry_price": entry, "exit_signal_date": None, "exit_date": None,
           "exit_price": None, "net_return_pct": None, "holding_sessions": None}
    start = sessions.index(entry_day)
    exit_observed = False
    for index in range(start, len(sessions)):
        day = sessions[index]
        if day not in bars.index:
            return {**arm, "status": "missing_bar"}
        bar = bars.loc[day].to_dict()
        if exit_observed:
            if _halted(bar):
                continue
            price = float(bar["open"])
            arm.update(exit_date=day, exit_price=price, net_return_pct=net_return_pct(entry, price),
                       holding_sessions=index - start)
            return {**arm, "status": "closed"}
        if not _halted(bar) and float(indicators.loc[day, "rsi"]) >= 50:
            exit_observed = True
            arm["exit_signal_date"] = day
    return {**arm, "status": "open"}


def _pair_status(row: dict) -> str:
    a = row["arm_a"]["status"]
    b = row["arm_b"]["status"]
    if a in {"missing_bar", "entry_halted"} or b in {"missing_bar", "entry_halted"}:
        return "excluded"
    if a == "closed" and b in {"closed", "not_entered"}:
        return "closed_pair"
    return "open_pair"


def _signal_row(connection: sqlite3.Connection, payload: dict, signal: dict, through: str) -> dict:
    as_of = payload["engine"]["data_as_of"]
    row = {"as_of": as_of, "snapshot_id": payload["publication"]["snapshot_id"],
           "symbol": signal["symbol"], "name": signal["name"], "market": signal["market"],
           "rank": signal["selection_rank"], "s1": None, "filter": None, "arm_a": None, "arm_b": None}
    start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
    bars = _history(connection, signal["symbol"], start, through)
    if as_of not in bars.index:
        return {**row, "status": "missing_signal_bar"}
    close = bars["close"].astype(float)
    sma = close.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean()
    position = int(bars.index.get_loc(as_of))
    if position < SMA_WINDOW:
        return {**row, "status": "insufficient_sma_history"}
    current_sma, previous_sma = float(sma.iloc[position]), float(sma.iloc[position - 1])
    rising = current_sma > previous_sma
    row["filter"] = {"window": SMA_WINDOW, "sma": round(current_sma, 6),
                     "previous_sma": round(previous_sma, 6), "rising": bool(rising)}
    sessions = _sessions_after(connection, as_of, through)
    if not sessions:
        return {**row, "status": "awaiting_s1"}
    prices = pd.DataFrame({"Close": close, "Volume": bars["volume"].astype(float)}, index=bars.index)
    indicators = rsi_obv_signals(prices)
    row["s1"] = sessions[0]
    row["arm_a"] = _arm(bars, indicators, sessions, sessions[0])
    row["arm_b"] = (_arm(bars, indicators, sessions, sessions[0]) if rising
                    else {"status": "not_entered", "net_return_pct": 0.0})
    return {**row, "status": _pair_status(row)}


def _profit_factor(values: list[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses == 0:
        return None if gains == 0 else float("inf")
    return round(gains / losses, 4)


def _q05(values: list[float]) -> float | None:
    return round(float(pd.Series(values).quantile(0.05)), 4) if values else None


def _bootstrap_ci(values: list[float]) -> tuple[float, float]:
    rng = random.Random(BOOTSTRAP_SEED)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(BOOTSTRAP_DRAWS))
    return round(means[int(0.025 * BOOTSTRAP_DRAWS)], 4), round(means[int(0.975 * BOOTSTRAP_DRAWS) - 1], 4)


def summarize(rows: list[dict], through: str) -> dict:
    closed = [row for row in rows if row["status"] == "closed_pair"]
    a_returns = [row["arm_a"]["net_return_pct"] for row in closed]
    b_all = [row["arm_b"]["net_return_pct"] for row in closed]
    b_entered = [row["arm_b"]["net_return_pct"] for row in closed if row["arm_b"]["status"] == "closed"]
    diffs = [round(b - a, 4) for a, b in zip(a_returns, b_all, strict=True)]
    coverage = len(b_entered) / len(closed) if closed else 0.0
    target_reached = len(b_entered) >= MIN_B_CLOSED
    deadline_reached = through >= DEADLINE
    summary = {
        "signals": len(rows), "a_closed": len(closed), "b_closed": len(b_entered),
        "min_b_closed": MIN_B_CLOSED, "min_coverage_pct": MIN_COVERAGE * 100, "deadline": DEADLINE,
        "b_coverage_pct": round(coverage * 100, 2) if closed else None,
        "mean_net_a_pct": round(statistics.fmean(a_returns), 4) if a_returns else None,
        "mean_net_b_all_signals_pct": round(statistics.fmean(b_all), 4) if b_all else None,
        "mean_net_b_entered_pct": round(statistics.fmean(b_entered), 4) if b_entered else None,
        "profit_factor_a": _profit_factor(a_returns), "profit_factor_b_entered": _profit_factor(b_entered),
        "q05_a_pct": _q05(a_returns), "q05_b_entered_pct": _q05(b_entered),
        "mean_diff_pct": round(statistics.fmean(diffs), 4) if diffs else None, "verdict": "not_due",
    }
    if not (target_reached or deadline_reached):
        return summary
    if len(b_entered) < MIN_B_CLOSED or coverage < MIN_COVERAGE:
        summary["verdict"] = "REJECT_TOO_SPARSE"
        return summary
    low, high = _bootstrap_ci(diffs)
    summary["ci95"] = [low, high]
    pf_a, pf_b = summary["profit_factor_a"], summary["profit_factor_b_entered"]
    pf_b_better = pf_b is not None and (pf_a is None or pf_b > pf_a)
    tail_not_worse = summary["q05_b_entered_pct"] >= summary["q05_a_pct"]
    if low > 0 and pf_b_better and tail_not_worse:
        summary["verdict"] = "PASS"
    elif high < 0 or not pf_b_better or not tail_not_worse:
        summary["verdict"] = "FAIL"
    else:
        summary["verdict"] = "INCONCLUSIVE"
    return summary


def build_sma_slope_shadow(snapshot_db: Path, market_db: Path, through: str) -> dict:
    if date.fromisoformat(through).isoformat() != through:
        raise SmaSlopeShadowError("through must be YYYY-MM-DD.")
    try:
        resolved = Path(market_db).resolve(strict=True)
    except OSError as exc:
        raise SmaSlopeShadowError("Market DB does not exist.") from exc
    payloads = [payload for payload in published_payloads(Path(snapshot_db), schema=RECOMMENDATION_SCHEMA_V2,
                                                          through=through)
                if payload["engine"]["data_as_of"] >= REGISTERED_FROM]
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        rows = [_signal_row(connection, payload, signal, through) for payload in payloads
                for signal in payload["signals"] if signal.get("action") == "entry"
                and signal.get("selection_outcome") == "selected" and signal.get("signal_type") == "long_term"]
    except sqlite3.Error as exc:
        raise SmaSlopeShadowError(f"SMA slope shadow query failed: {exc}") from exc
    finally:
        connection.close()
    rows.sort(key=lambda row: (row["as_of"], row["rank"] or 0, row["symbol"]))
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    return {"schema": SCHEMA, "through": through, "registered_from": REGISTERED_FROM,
            "preregistration": PREREGISTRATION, "preregistration_sha256": PREREGISTRATION_SHA256,
            "hypothesis": "signal_day_sma60_gt_previous_sma60", "costs": {"buy": BUY_COST, "sell": SELL_COST},
            "recommendations_changed": False, "orders_enabled": False, "summary": summarize(rows, through),
            "rows_sha256": digest, "rows": rows}


def write_sma_slope_shadow(path: Path, report: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".rsi-obv-sma60-slope-shadow-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
