"""US-MOM-v1 one-shot historical evaluation (PREREGISTRATION_US_MOM_v1.md). Offline research only.

Monthly-rebalanced, equal-weight multi-timeframe momentum on point-in-time S&P 500 members with daily
50-day-SMA and ATR-stop exits. Indicators come from the Korean v4 module (same formulas), the snapshot
from the US-PB-v1 loader. Refuses to run twice, on a lock mismatch or a snapshot hash mismatch.
No orders, fills or ledger rows.

usage: python research/us_momentum_v1/run.py <snapshot.sqlite3>
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("us_pb_v1_run", ROOT / "research/us_pullback_v1/run.py")
base = importlib.util.module_from_spec(_spec)   # US-PB-v1 snapshot loader (also puts engine/kqs/src on the path)
_spec.loader.exec_module(base)
from kqs_runtime import pullback_v4 as pv  # noqa: E402

PREREG = HERE / "PREREGISTRATION_US_MOM_v1.md"
LOCK = HERE / "LOCK.json"
RESULT = HERE / "RESULT.json"
LOCKED = {"pullback_v4": ROOT / "engine/kqs/src/kqs_runtime/pullback_v4.py", "snapshot_loader": ROOT / "research/us_pullback_v1/run.py",
          "run": HERE / "run.py", "prereg": PREREG}

WINDOW = ("2017-03-01", "2026-09-30")
CAPITAL = 100_000.0
BUY_COST, SELL_COST = 5.0 / 10_000, 5.3 / 10_000
R6_MIN, R3_MIN, HIGH_BAND, RSI_RANGE, ADX_MIN, ATR_STOP = 0.15, 0.05, 0.95, (50, 70), 20, 2.0
BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, BOOTSTRAP_QUANTILE = 10_000, 20261013, 0.0125
GOAL = {"trades": 30, "payoff": 2.0, "profit_factor": 1.3, "expectancy": 0.0, "window_share": 0.5}
DELISTING_SHARE_LIMIT, COVERAGE_FLOOR = 0.02, 95.0
STRESS = {"covid": ("2020-02-20", "2020-06-30"), "bear_2022_23": ("2022-01-03", "2023-12-29")}


class RunRefused(RuntimeError):
    pass


def check_lock() -> dict:
    if not LOCK.exists():
        raise RunRefused("LOCK.json is missing")
    current = {name: base.sha256(path) for name, path in LOCKED.items()}
    if json.loads(LOCK.read_text()) != current:
        raise RunRefused("lock mismatch")
    return current


def prepare(data: dict) -> tuple[dict, pd.DataFrame, dict]:
    spy = base.market_frame(data["spy"])
    frames = pv.frames_from_history(base.split_adjusted(data["bars"]), spy)
    prepared = {}
    for ticker, frame in frames.items():
        df = pv.compute_indicators(frame)
        df["R3"] = df["Close"] / df["Close"].shift(63) - 1
        df["UP1W"] = df["Close"] > df["Close"].shift(5)
        df["HIGH252"] = df["High"].rolling(252, min_periods=252).max()
        df["MEMBER"] = base.membership_mask(data["intervals"], ticker, df.index)
        prepared[ticker] = df
    last_bar = {ticker: df.index.max() for ticker, df in prepared.items()}
    return prepared, spy, last_bar


def month_ends(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    frame = pd.Series(index, index=index)
    return list(frame.groupby([index.year, index.month]).max())


def qualifies(df: pd.DataFrame, day: pd.Timestamp, six_months_ago: pd.Timestamp) -> bool:
    if day not in df.index:
        return False
    row = df.loc[day]
    past = df["Close"].loc[:six_months_ago]
    if past.empty or not bool(row["MEMBER"]):
        return False
    r6 = row["Close"] / past.iloc[-1] - 1
    checks = (r6 >= R6_MIN, row["R3"] >= R3_MIN, bool(row["UP1W"]), row["Close"] >= HIGH_BAND * row["HIGH252"],
              RSI_RANGE[0] <= row["RSI"] <= RSI_RANGE[1], row["ADX"] > ADX_MIN, row["ATR"] > 0)
    return all(bool(c) for c in checks)          # NaN 비교는 False라 예열 부족 종목은 자동 탈락


def simulate(prepared: dict, spy: pd.DataFrame, last_bar: dict, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    calendar = spy.index[(spy.index <= end)]
    ends = month_ends(calendar)
    first_signal = max(d for d in ends if d < start)          # 2017-02-28 close -> buys at the window's first open
    rebalance_days = {d for d in ends if first_signal <= d < end}
    days = [d for d in calendar if d >= first_signal]
    cash, positions, trades, equity = CAPITAL, {}, [], []
    pending_exit: dict[str, str] = {}
    pending_target: dict | None = None
    months: list[dict] = []

    def sell(ticker: str, value: float, reason: str | None, day) -> None:
        nonlocal cash
        pos = positions[ticker]
        cash += value * (1 - SELL_COST)
        pos["proceeds"] += value * (1 - SELL_COST)
        if reason is not None:
            trades.append({"ticker": ticker, "entry_date": pos["entry_date"], "exit_date": day, "cost": pos["cost"],
                           "proceeds": pos["proceeds"], "pnl_net": pos["proceeds"] - pos["cost"],
                           "ret": (pos["proceeds"] - pos["cost"]) / pos["cost"] * 100, "exit_reason": reason})
            del positions[ticker]

    for i, day in enumerate(days):
        bar = lambda t: prepared[t].loc[day] if day in prepared[t].index else None  # noqa: E731
        if day >= start:
            # 1) open: pending exits, then the pending rebalance
            for ticker, reason in list(pending_exit.items()):
                row = bar(ticker)
                if ticker in positions and row is not None:
                    sell(ticker, positions[ticker]["shares"] * row["Open"], reason, day)
                    del pending_exit[ticker]
            if pending_target is not None:
                target, atr = pending_target["tickers"], pending_target["atr"]
                for ticker in [t for t in positions if t not in target]:
                    row = bar(ticker)
                    if row is not None:
                        sell(ticker, positions[ticker]["shares"] * row["Open"], "rebalance", day)
                opens = {t: bar(t)["Open"] for t in set(positions) | set(target) if bar(t) is not None}
                value = cash + sum(p["shares"] * opens.get(t, p["last_close"]) for t, p in positions.items())
                buyable = [t for t in target if t in opens]
                if buyable:
                    each = value / len(target)
                    for ticker in buyable:
                        if ticker in positions:
                            excess = positions[ticker]["shares"] * opens[ticker] - each
                            if excess > 0:
                                positions[ticker]["shares"] -= excess / opens[ticker]
                                sell(ticker, excess, None, day)
                    wants = {t: each - (positions[t]["shares"] * opens[t] if t in positions else 0.0) for t in buyable}
                    wants = {t: w for t, w in wants.items() if w > 0}
                    scale = min(1.0, (cash / (1 + BUY_COST)) / sum(wants.values())) if wants else 0.0
                    for ticker, want in wants.items():
                        spend = want * scale
                        if ticker not in positions:
                            positions[ticker] = {"shares": 0.0, "cost": 0.0, "proceeds": 0.0, "entry_date": day,
                                                 "stop": opens[ticker] - ATR_STOP * atr[ticker], "last_close": opens[ticker]}
                        positions[ticker]["shares"] += spend / opens[ticker]
                        positions[ticker]["cost"] += spend * (1 + BUY_COST)
                        cash -= spend * (1 + BUY_COST)
                months.append({"date": day, "target": len(target), "held": len(positions)})
                pending_target = None
        # 2) close: marks, delisting, exit checks, month-end signal
        for ticker in list(positions):
            row = bar(ticker)
            if row is None:
                continue
            positions[ticker]["last_close"] = row["Close"]
            if last_bar[ticker] == day and day < end:
                sell(ticker, positions[ticker]["shares"] * row["Close"], "forced_close", day)
                pending_exit.pop(ticker, None)
                continue
            if ticker not in pending_exit and day >= start:
                if row["Close"] <= positions[ticker]["stop"]:
                    pending_exit[ticker] = "atr_stop"
                elif row["Close"] < row["SMA50"]:
                    pending_exit[ticker] = "sma50"
        if day in rebalance_days:
            six = ends[ends.index(day) - 6] if ends.index(day) >= 6 else None
            picks = sorted(t for t, df in prepared.items() if six is not None and t not in pending_exit and qualifies(df, day, six))
            pending_target = {"tickers": picks, "atr": {t: float(prepared[t].loc[day, "ATR"]) for t in picks}}
        if day >= start:
            equity.append({"date": day, "equity": cash + sum(p["shares"] * p["last_close"] for p in positions.values()),
                           "positions": len(positions)})
        if day == days[-1]:
            for ticker in list(positions):
                sell(ticker, positions[ticker]["shares"] * positions[ticker]["last_close"], "window_end", day)
            equity[-1]["equity"] = cash
    return {"trades": pd.DataFrame(trades), "equity": pd.DataFrame(equity).set_index("date"), "months": pd.DataFrame(months)}


def trade_metrics(trades: pd.DataFrame, quantile: float = BOOTSTRAP_QUANTILE) -> dict:
    r = trades["ret"].to_numpy() if len(trades) else np.array([])
    wins, losses = r[r > 0], r[r <= 0]
    n = len(r)
    win_rate = len(wins) / n if n else 0.0
    avg_win, avg_loss = (float(wins.mean()) if len(wins) else 0.0), (float(-losses.mean()) if len(losses) else 0.0)
    halves = trades.groupby(base.half_year(trades["exit_date"]))["ret"].sum() if n else pd.Series(dtype=float)
    rng = random.Random(BOOTSTRAP_SEED)
    draws = sorted(base.profit_factor(np.array(rng.choices(list(r), k=n))) for _ in range(BOOTSTRAP_DRAWS)) if n else [0.0]
    return {"trades": n, "win_rate": round(win_rate, 4), "mean_net_return_pct": round(float(r.mean()), 4) if n else None,
            "expectancy": round(win_rate * avg_win - (1 - win_rate) * avg_loss, 4),
            "profit_factor": round(base.profit_factor(r), 4) if n else None,
            "payoff": round(avg_win / avg_loss, 4) if avg_loss > 0 else math.inf,
            "window_share": round(float((halves > 0).mean()), 4) if len(halves) else None, "window_count": len(halves),
            "bootstrap_pf_q": round(draws[int(quantile * len(draws))], 4)}


def portfolio_stats(values: pd.Series) -> dict:
    daily = values.pct_change().dropna()
    years = len(daily) / 252
    return {"cagr_pct": round(((values.iloc[-1] / values.iloc[0]) ** (1 / years) - 1) * 100, 3),
            "max_drawdown_pct": round(float((values / values.cummax() - 1).min() * 100), 3),
            "sharpe": round(float(daily.mean() / daily.std() * math.sqrt(252)), 3)}


def annualized(daily: pd.Series) -> float:
    return round(((1 + daily).prod() ** (252 / len(daily)) - 1) * 100, 3)


def evaluate(sim: dict, spy: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    trades, equity = sim["trades"], sim["equity"]["equity"]
    before = spy.loc[spy.index < start, "Close"].iloc[-1]
    port = pd.concat([pd.Series([CAPITAL], index=[spy.index[spy.index < start][-1]]), equity])
    bench = spy.loc[(spy.index >= port.index[0]) & (spy.index <= end), "Close"] / before * CAPITAL
    metrics, portfolio, spy_stats = trade_metrics(trades), portfolio_stats(port), portfolio_stats(bench)
    checks = {"trades": metrics["trades"] >= GOAL["trades"], "expectancy": metrics["expectancy"] > GOAL["expectancy"],
              "profit_factor": (metrics["profit_factor"] or 0) >= GOAL["profit_factor"], "payoff": metrics["payoff"] >= GOAL["payoff"],
              "window_share": metrics["window_count"] == 0 or metrics["window_share"] >= GOAL["window_share"],
              "bootstrap_pf_q": metrics["bootstrap_pf_q"] > 1.0, "cagr_vs_spy": portfolio["cagr_pct"] > spy_stats["cagr_pct"]}
    stress_days = pd.Series(False, index=port.index[1:])
    stress_trades = pd.Series(False, index=trades.index)
    for low, high in STRESS.values():
        stress_days |= (stress_days.index >= low) & (stress_days.index <= high)
        stress_trades |= (trades["entry_date"] <= high) & (trades["exit_date"] >= low)
    pdaily, bdaily = port.pct_change().dropna(), bench.pct_change().dropna()
    months = sim["months"]
    diagnostics = {
        "portfolio": portfolio, "spy": spy_stats,
        "avg_target_per_month": round(float(months["target"].mean()), 2), "cash_months": int((months["target"] == 0).sum()),
        "rebalances": len(months), "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
        "trades_per_year": trades.groupby(trades["exit_date"].dt.year).size().to_dict(),
        "pnl_per_year": {int(k): round(float(v)) for k, v in trades.groupby(trades["exit_date"].dt.year)["pnl_net"].sum().items()},
        "delisting_forced_close": int((trades["exit_reason"] == "forced_close").sum()),
        "without_stress": {"trades": trade_metrics(trades[~stress_trades]),
                           "portfolio_ann_pct": annualized(pdaily[~stress_days.values]),
                           "spy_ann_pct": annualized(bdaily.reindex(pdaily.index)[~stress_days.values])},
        "stress_only_trades": trade_metrics(trades[stress_trades]),
    }
    return {"metrics": metrics, "checks": checks, "diagnostics": diagnostics}


def verdict(result: dict, coverage_low: list[str]) -> str:
    m, d = result["metrics"], result["diagnostics"]
    if coverage_low or (m["trades"] and d["delisting_forced_close"] / m["trades"] > DELISTING_SHARE_LIMIT):
        return "INVALID"
    if not m["trades"] or m["mean_net_return_pct"] <= 0 or d["portfolio"]["cagr_pct"] <= 0:
        return "REJECT"
    return "PASS" if all(result["checks"].values()) else "FAIL"


def main(snapshot: str) -> dict:
    if RESULT.exists():
        raise RunRefused("RESULT.json exists: this window has been used (prereg §3)")
    lock = check_lock()
    data = base.load_snapshot(Path(snapshot))
    start, end = pd.Timestamp(WINDOW[0]), pd.Timestamp(WINDOW[1])
    coverage_low = [r.date for r in data["coverage"].itertuples() if WINDOW[0] <= r.date <= WINDOW[1] and r.pct < COVERAGE_FLOOR]
    prepared, spy, last_bar = prepare(data)
    sim = simulate(prepared, spy, last_bar, start, end)
    result = evaluate(sim, spy, start, end)
    trades = sim["trades"]
    result.update(verdict=verdict(result, coverage_low), coverage_below_floor=coverage_low, window=list(WINDOW), lock=lock,
                  snapshot={"table_sha256": data["hashes"], "membership_id": data["meta"].get("membership_id"),
                            "source_run_id": data["meta"].get("source_run_id")},
                  costs={"buy": BUY_COST, "sell": SELL_COST}, capital=CAPITAL,
                  trades_sha256=hashlib.sha256(trades.to_csv(index=False).encode()).hexdigest(),
                  executed_at=datetime.now(timezone.utc).isoformat())
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    trades.to_csv(HERE / "TRADES.csv", index=False)
    sim["months"].to_csv(HERE / "REBALANCES.csv", index=False)
    return result


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False, indent=1, default=str))
