"""US-PB-v1 one-shot historical evaluation (PREREGISTRATION_US_PB_v1.md). Offline research only.

Calls the Korean v4 engine functions unchanged. The only US adaptations (prereg §2), applied inside
this process: the module's cost constants are set to the US values, and the entry signal is AND-ed
with point-in-time S&P 500 membership (intervals flagged by EINT-036 excluded). SPY is the single
market gate. Refuses to run twice (RESULT.json exists), on a lock mismatch or on a snapshot hash
mismatch. No orders, fills or ledger rows.

usage: python research/us_pullback_v1/run.py <snapshot.sqlite3>
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "engine/kqs/src"))
from kqs_runtime import pullback_v4 as pv  # noqa: E402

PREREG = HERE / "PREREGISTRATION_US_PB_v1.md"
LOCK = HERE / "LOCK.json"
RESULT = HERE / "RESULT.json"
LOCKED = {"pullback_v4": ROOT / "engine/kqs/src/kqs_runtime/pullback_v4.py", "run": HERE / "run.py", "prereg": PREREG}

WINDOW = ("2017-03-01", "2026-09-30")
CAPITAL = 100_000.0
US_ENTRY_COST = 5.0 / 10_000
US_EXIT_COST = 5.3 / 10_000
CONFIG = {**pv.FIXED_CONFIG, "min_turnover": 0}
EXCLUDED_KINDS = {"starts_late", "no_bars"}
BOOTSTRAP_DRAWS, BOOTSTRAP_SEED = 10_000, 20261012
DELISTING_SHARE_LIMIT = 0.02
COVERAGE_FLOOR = 95.0
GOAL = {"trades": 30, "payoff": 2.0, "profit_factor": 1.3, "expectancy": 0.0, "window_share": 0.5}
SNAPSHOT_TABLES = ("bars", "spy", "intervals", "coverage")


class RunRefused(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_lock() -> dict:
    if not LOCK.exists():
        raise RunRefused("LOCK.json is missing")
    recorded = json.loads(LOCK.read_text())
    current = {name: sha256(path) for name, path in LOCKED.items()}
    if recorded != current:
        raise RunRefused(f"lock mismatch: {sorted(k for k in current if recorded.get(k) != current[k])}")
    return current


def table_sha(connection: sqlite3.Connection, table: str) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2"):
        digest.update(json.dumps(row, separators=(",", ":")).encode())
    return digest.hexdigest()


def load_snapshot(path: Path) -> dict:
    connection = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    try:
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        hashes = {table: table_sha(connection, table) for table in SNAPSHOT_TABLES}
        recorded = json.loads(meta["table_sha256"])
        if hashes != recorded:
            raise RunRefused("snapshot hash mismatch")
        bars = pd.read_sql_query("SELECT symbol, date, open, high, low, close, volume, split FROM bars", connection)
        spy = pd.read_sql_query("SELECT date, open, high, low, close, volume FROM spy ORDER BY date", connection)
        intervals = pd.read_sql_query("SELECT symbol, start, end, excluded FROM intervals", connection)
        coverage = pd.read_sql_query("SELECT date, pct FROM coverage", connection)
    finally:
        connection.close()
    return {"meta": meta, "hashes": hashes, "bars": bars, "spy": spy, "intervals": intervals, "coverage": coverage}


def market_frame(spy: pd.DataFrame) -> pd.DataFrame:
    frame = spy.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    frame.index = pd.to_datetime(frame.pop("date"))
    frame.index.name = "date"
    return pv.clean_ohlcv(frame)


def split_adjusted(bars: pd.DataFrame) -> pd.DataFrame:
    """Raw x split factor for prices, raw / factor for volume (prereg §2). Factor 1 when missing."""
    factor = bars["split"].fillna(1.0)
    out = pd.DataFrame({"Date": bars["date"], "Symbol": bars["symbol"]})
    for column in ("open", "high", "low", "close"):
        out[column.capitalize()] = bars[column] * factor
    out["Volume"] = bars["volume"] / factor
    out["TradingValue"] = out["Close"] * out["Volume"]
    return out


def membership_mask(intervals: pd.DataFrame, ticker: str, index: pd.DatetimeIndex) -> pd.Series:
    mask = pd.Series(False, index=index)
    for row in intervals.loc[(intervals["symbol"] == ticker) & (intervals["excluded"] == 0)].itertuples():
        end = pd.Timestamp(row.end) if row.end else pd.Timestamp.max
        mask |= (index >= pd.Timestamp(row.start)) & (index < end)
    return mask


def install_us_adaptations(intervals: pd.DataFrame) -> None:
    """Prereg §2, this process only: US costs and point-in-time membership on the entry signal."""
    pv.ENTRY_COST, pv.EXIT_COST = US_ENTRY_COST, US_EXIT_COST
    original = pv.entry_signals

    def entry_signals_us(prepared: dict, config: dict) -> dict:
        signals = original(prepared, config)
        for ticker, sig in signals.items():
            sig["full"] = sig["full"] & membership_mask(intervals, ticker, sig["full"].index)
        return signals

    pv.entry_signals = entry_signals_us


def net_return_pct(trades: pd.DataFrame) -> pd.Series:
    return trades["pnl_net"] / (trades["entry_price"] * trades["shares"] * (1 + pv.ENTRY_COST)) * 100


def half_year(dates: pd.Series) -> pd.Series:
    return dates.dt.year.astype(str) + np.where(dates.dt.month <= 6, "H1", "H2")


def profit_factor(values: np.ndarray) -> float:
    gains, losses = values[values > 0].sum(), -values[values <= 0].sum()
    return float(gains / losses) if losses > 0 else math.inf


def evaluate(trades: pd.DataFrame, equity: pd.DataFrame, spy: pd.DataFrame, last_bar: dict, window_end: pd.Timestamp) -> dict:
    closed = trades.copy()
    closed["NetReturnPct"] = net_return_pct(closed)
    returns = closed["NetReturnPct"].to_numpy()
    wins, losses = returns[returns > 0], returns[returns <= 0]
    count = len(returns)
    win_rate = len(wins) / count if count else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(-losses.mean()) if len(losses) else 0.0
    per_window = closed.groupby(half_year(closed["exit_date"]))["NetReturnPct"].sum() if count else pd.Series(dtype=float)
    rng = random.Random(BOOTSTRAP_SEED)
    draws = sorted(profit_factor(np.array(rng.choices(list(returns), k=count))) for _ in range(BOOTSTRAP_DRAWS)) if count else [0.0]
    metrics = {
        "trades": count, "win_rate": round(win_rate, 4), "mean_net_return_pct": round(float(returns.mean()), 4) if count else None,
        "expectancy": round(win_rate * avg_win - (1 - win_rate) * avg_loss, 4),
        "profit_factor": round(profit_factor(returns), 4) if count else None,
        "payoff": round(avg_win / avg_loss, 4) if avg_loss > 0 else math.inf,
        "window_share": round(float((per_window > 0).mean()), 4) if len(per_window) else None, "window_count": len(per_window),
        "bootstrap_pf_lower95": round(draws[int(0.025 * len(draws))], 4),
    }
    checks = {
        "trades": count >= GOAL["trades"], "expectancy": metrics["expectancy"] > GOAL["expectancy"],
        "profit_factor": (metrics["profit_factor"] or 0) >= GOAL["profit_factor"],
        "payoff": metrics["payoff"] >= GOAL["payoff"],
        "window_share": metrics["window_count"] == 0 or metrics["window_share"] >= GOAL["window_share"],
        "bootstrap_pf_lower95": metrics["bootstrap_pf_lower95"] > 1.0,
    }
    delisted = closed.loc[(closed["exit_reason"] == "forced_close")
                          & closed["ticker"].map(lambda t: pd.Timestamp(last_bar[t]) < window_end)]
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    peak = equity["equity"].cummax()
    spy_window = spy.loc[(spy.index >= equity.index[0]) & (spy.index <= equity.index[-1]), "Close"]
    pnl_sorted = closed["pnl_net"].sort_values(ascending=False)
    top = pnl_sorted.head(max(1, int(round(0.05 * count)))).sum() if count else 0.0
    diagnostics = {
        "cagr_pct": round(((equity["equity"].iloc[-1] / CAPITAL) ** (1 / years) - 1) * 100, 3),
        "max_drawdown_pct": round(float((equity["equity"] / peak - 1).min() * 100), 3),
        "spy_cagr_pct": round(((spy_window.iloc[-1] / spy_window.iloc[0]) ** (1 / years) - 1) * 100, 3),
        "exit_reasons": closed["exit_reason"].value_counts().to_dict(),
        "trades_per_year": closed.groupby(closed["exit_date"].dt.year).size().to_dict(),
        "tail_share_top5pct": round(float(top / closed["pnl_net"].sum()), 4) if count and closed["pnl_net"].sum() > 0 else None,
        "delisting_forced_close": len(delisted),
    }
    return {"metrics": metrics, "checks": checks, "diagnostics": diagnostics}


def verdict(result: dict, coverage_low: list[str]) -> str:
    trades = result["metrics"]["trades"]
    if coverage_low or (trades and result["diagnostics"]["delisting_forced_close"] / trades > DELISTING_SHARE_LIMIT):
        return "INVALID"
    if not trades or result["metrics"]["mean_net_return_pct"] <= 0:
        return "REJECT"
    return "PASS" if all(result["checks"].values()) else "FAIL"


def main(snapshot: str) -> dict:
    if RESULT.exists():
        raise RunRefused("RESULT.json exists: this window has been used (prereg §3)")
    lock = check_lock()
    data = load_snapshot(Path(snapshot))
    start, end = pd.Timestamp(WINDOW[0]), pd.Timestamp(WINDOW[1])
    coverage_low = [row.date for row in data["coverage"].itertuples() if WINDOW[0] <= row.date <= WINDOW[1] and row.pct < COVERAGE_FLOOR]
    spy = market_frame(data["spy"])
    install_us_adaptations(data["intervals"])
    ohlcv = pv.frames_from_history(split_adjusted(data["bars"]), spy)
    last_bar = {ticker: frame.index.max() for ticker, frame in ohlcv.items()}
    equity, trades = pv.run_period(ohlcv, spy, start, end, CONFIG, initial_capital=CAPITAL)
    result = evaluate(trades, equity, spy, last_bar, end)
    result.update(
        verdict=verdict(result, coverage_low), coverage_below_floor=coverage_low, window=list(WINDOW),
        lock=lock, snapshot={"path_name": Path(snapshot).name, "table_sha256": data["hashes"],
                             "membership_id": data["meta"].get("membership_id"), "source_run_id": data["meta"].get("source_run_id")},
        costs={"entry": US_ENTRY_COST, "exit": US_EXIT_COST}, config=CONFIG, capital=CAPITAL,
        trades_sha256=hashlib.sha256(trades.to_csv(index=False).encode()).hexdigest(),
        executed_at=datetime.now(timezone.utc).isoformat(),
    )
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    trades.to_csv(HERE / "TRADES.csv", index=False)
    return result


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False, indent=1, default=str))
