"""Pre-registered prospective observation B-1: Bollinger-RSI entry after a confirmed rebound.

docs/engine/PREREGISTRATION_B_rebound_confirmation_v1.md fixes every choice below; changing one
requires a new registration (v2) and a fresh sample. For each published swing pick on or after
REGISTERED_FROM it replays, from collected KRX daily bars only, two paired arms:

- A (current rule): buy at the open of the first session after the signal (S1);
- B (confirmed): buy at the open of S2 only if S1's close is above S1's lower Bollinger band and
  above the signal-day close; otherwise B does not enter (net return 0).

Both arms exit with the live native rule (close >= middle band or RSI >= 50 on a close, sell at the
next open) and the audited 2026 cost model. Read-only: no orders, fills, positions or ledger rows,
kept apart from the EINT-019 outcomes and the filter-off prospective sample. It never changes a
recommendation; the verdict is computed only once the registered sample size or deadline is reached.
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

from korea_quant.strategy import bollinger_rsi_signals
from kqs_publication.contract import RECOMMENDATION_SCHEMA_V2
from kqs_publication.store import published_payloads

SCHEMA = "kqs-rebound-shadow/v1"
PREREGISTRATION = "docs/engine/PREREGISTRATION_B_rebound_confirmation_v1.md"
PREREGISTRATION_SHA256 = "cb3b3ddbe301d43a2d1de02c3bcf378220ab1ec782430a422654f17a3b163189"
REGISTERED_FROM = "2026-10-07"
DEADLINE = "2028-10-07"
TARGET_CLOSED_PAIRS = 100
HISTORY_DAYS = 730
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20261007
BUY_COST = 0.00015 + 0.0005            # commission + slippage
SELL_COST = 0.00015 + 0.0020 + 0.0005  # commission + 2026 transaction tax + slippage
PRICE_COLUMNS = ("open", "high", "low", "close")


class ReboundShadowError(ValueError):
    """The observation could not be built."""


def net_return_pct(entry: float, exit_: float) -> float:
    return round((exit_ * (1 - SELL_COST) / (entry * (1 + BUY_COST)) - 1) * 100, 4)


def _halted(bar: dict) -> bool:
    return any(bar[column] <= 0 for column in PRICE_COLUMNS)


def _history(connection: sqlite3.Connection, symbol: str, start: str, through: str) -> pd.DataFrame:
    rows = connection.execute(
        "SELECT trade_date, open, high, low, close FROM daily_prices WHERE symbol = ? AND trade_date >= ? "
        "AND trade_date <= ? ORDER BY trade_date", (symbol, start, through)).fetchall()
    frame = pd.DataFrame(rows, columns=["date", *PRICE_COLUMNS]).set_index("date")
    return frame


def _sessions_after(connection: sqlite3.Connection, day: str, through: str) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT DISTINCT trade_date FROM daily_prices WHERE market IN ('KOSPI', 'KOSDAQ') "
        "AND trade_date > ? AND trade_date <= ? ORDER BY trade_date", (day, through))]


def _arm(bars: pd.DataFrame, signals: pd.DataFrame, sessions: list[str], entry_day: str) -> dict:
    """Enter at entry_day's open, exit at the open after the first close meeting the native exit."""
    if entry_day not in bars.index:
        return {"status": "missing_bar", "entry_date": entry_day}
    entry_bar = bars.loc[entry_day].to_dict()
    if _halted(entry_bar):
        return {"status": "entry_halted", "entry_date": entry_day}
    entry = float(entry_bar["open"])
    arm = {"entry_date": entry_day, "entry_price": entry, "exit_signal_date": None, "exit_date": None,
           "exit_price": None, "net_return_pct": None, "holding_sessions": None}
    start = sessions.index(entry_day)
    exit_observed = None
    for index in range(start, len(sessions)):
        day = sessions[index]
        if day not in bars.index:
            return {**arm, "status": "missing_bar"}
        bar = bars.loc[day].to_dict()
        if exit_observed is not None:
            if _halted(bar):
                continue                         # sell at the next tradable open
            arm.update(exit_date=day, exit_price=float(bar["open"]), net_return_pct=net_return_pct(entry, float(bar["open"])),
                       holding_sessions=index - start)
            return {**arm, "status": "closed"}
        if _halted(bar):
            continue
        latest = signals.loc[day]
        if float(bar["close"]) >= float(latest["bb_middle"]) or float(latest["rsi"]) >= 50:
            exit_observed = day
            arm["exit_signal_date"] = day
    return {**arm, "status": "open"}


def _signal_row(connection: sqlite3.Connection, payload: dict, signal: dict, through: str) -> dict:
    as_of = payload["engine"]["data_as_of"]
    row = {"as_of": as_of, "snapshot_id": payload["publication"]["snapshot_id"], "symbol": signal["symbol"],
           "name": signal["name"], "market": signal["market"], "rank": signal["selection_rank"],
           "s1": None, "s2": None, "confirmation": None, "arm_a": None, "arm_b": None}
    sessions = _sessions_after(connection, as_of, through)
    if not sessions:
        return {**row, "status": "awaiting_s1"}
    start = (date.fromisoformat(as_of) - timedelta(days=HISTORY_DAYS)).isoformat()
    bars = _history(connection, signal["symbol"], start, through)
    if as_of not in bars.index:
        return {**row, "status": "missing_bar"}
    signals = bollinger_rsi_signals(bars["close"].astype(float))
    row["s1"] = sessions[0]
    row["arm_a"] = _arm(bars, signals, sessions, sessions[0])
    if sessions[0] not in bars.index:
        return {**row, "status": "missing_bar"}
    s1_close = float(bars.loc[sessions[0], "close"])
    lower = float(signals.loc[sessions[0], "bb_lower"])
    signal_close = float(bars.loc[as_of, "close"])
    confirmed = (not _halted(bars.loc[sessions[0]].to_dict())) and s1_close > lower and s1_close > signal_close
    row["confirmation"] = {"s1_close": s1_close, "s1_bb_lower": round(lower, 4), "signal_close": signal_close,
                           "confirmed": bool(confirmed)}
    if not confirmed:
        row["arm_b"] = {"status": "not_entered", "net_return_pct": 0.0}
    elif len(sessions) < 2:
        row["arm_b"] = {"status": "awaiting_s2"}
    else:
        row["s2"] = sessions[1]
        row["arm_b"] = _arm(bars, signals, sessions, sessions[1])
    return {**row, "status": _pair_status(row)}


def _pair_status(row: dict) -> str:
    a, b = row["arm_a"]["status"], row["arm_b"]["status"]
    if "missing_bar" in (a, b) or "entry_halted" in (a, b):
        return "excluded"
    if a == "closed" and b in {"closed", "not_entered"}:
        return "closed_pair"
    return "open_pair"


def _bootstrap_ci(values: list[float]) -> tuple[float, float]:
    rng = random.Random(BOOTSTRAP_SEED)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(BOOTSTRAP_DRAWS))
    return round(means[int(0.025 * BOOTSTRAP_DRAWS)], 4), round(means[int(0.975 * BOOTSTRAP_DRAWS) - 1], 4)


def summarize(rows: list[dict], through: str) -> dict:
    closed = [row for row in rows if row["status"] == "closed_pair"]
    diffs = [round(row["arm_b"]["net_return_pct"] - row["arm_a"]["net_return_pct"], 4) for row in closed]
    a_returns = [row["arm_a"]["net_return_pct"] for row in closed]
    b_entered = [row["arm_b"]["net_return_pct"] for row in closed if row["arm_b"]["status"] == "closed"]
    due = len(closed) >= TARGET_CLOSED_PAIRS or through >= DEADLINE
    summary = {
        "signals": len(rows), "closed_pairs": len(closed), "target_closed_pairs": TARGET_CLOSED_PAIRS, "deadline": DEADLINE,
        "b_entry_rate_pct": round(len(b_entered) / len(closed) * 100, 2) if closed else None,
        "mean_net_a_pct": round(statistics.fmean(a_returns), 4) if a_returns else None,
        "mean_net_b_entered_pct": round(statistics.fmean(b_entered), 4) if b_entered else None,
        "mean_diff_pct": round(statistics.fmean(diffs), 4) if diffs else None,
        "verdict": "not_due",
    }
    if due and len(diffs) >= 2:
        low, high = _bootstrap_ci(diffs)
        summary.update(ci95=[low, high], verdict="PASS" if summary["mean_diff_pct"] > 0 and low > 0
                       else "FAIL" if high < 0 else "INCONCLUSIVE")
    elif due:
        summary["verdict"] = "INCONCLUSIVE"
    return summary


def build_rebound_shadow(snapshot_db: Path, market_db: Path, through: str) -> dict:
    if date.fromisoformat(through).isoformat() != through:
        raise ReboundShadowError("through must be YYYY-MM-DD.")
    try:
        resolved = Path(market_db).resolve(strict=True)
    except OSError as exc:
        raise ReboundShadowError("Market DB does not exist.") from exc
    payloads = [payload for payload in published_payloads(Path(snapshot_db), schema=RECOMMENDATION_SCHEMA_V2, through=through)
                if payload["engine"]["data_as_of"] >= REGISTERED_FROM]
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        rows = [_signal_row(connection, payload, signal, through) for payload in payloads for signal in payload["signals"]
                if signal.get("action") == "entry" and signal.get("selection_outcome") == "selected"
                and signal.get("signal_type") == "swing"]
    except sqlite3.Error as exc:
        raise ReboundShadowError(f"Rebound shadow query failed: {exc}") from exc
    finally:
        connection.close()
    rows.sort(key=lambda row: (row["as_of"], row["rank"] or 0, row["symbol"]))
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema": SCHEMA, "through": through, "registered_from": REGISTERED_FROM,
            "preregistration": PREREGISTRATION, "preregistration_sha256": PREREGISTRATION_SHA256,
            "costs": {"buy": BUY_COST, "sell": SELL_COST}, "orders_enabled": False,
            "summary": summarize(rows, through), "rows_sha256": digest, "rows": rows}


def write_rebound_shadow(path: Path, report: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".rebound-shadow-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
