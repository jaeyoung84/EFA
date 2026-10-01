"""Read-only Pullback v4 backtest over an existing KRX DB copy (EINT-017).

Loads the DB with the archived v4 loader rules (universe = KOSPI/KOSDAQ 6-digit symbols minus
069500/229200, market map with transfer segments, prices cleaned and aligned to the 069500 calendar)
and runs ``kqs_runtime.pullback_v4.run_period``. Prints the observation #5 fingerprint (trade count,
exit distribution, trades/equity hash) so the port can be compared with the archived run, then
optional performance figures. Never writes to the DB; never places orders or fills.

    PYTHONPATH=engine/kqs/src python engine/kqs/tools/pullback_v4_backtest.py --db PATH \
        [--start 2022-01-01 --end 2026-09-08] [--json OUT]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from kqs_runtime import pullback_v4 as v4

DB_START = "2016-08-29"
EXCLUDE = tuple(v4.MARKET_BENCHMARKS.values())


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve(strict=True)}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def load_universe_and_markets(connection) -> tuple[list[str], dict]:
    markets = ("KOSPI", "KOSDAQ")
    glob6 = "'[0-9][0-9][0-9][0-9][0-9][0-9]'"
    tickers = [str(r[0]) for r in connection.execute(
        f"SELECT DISTINCT symbol FROM daily_prices WHERE market IN (?,?) AND symbol GLOB {glob6} ORDER BY symbol",
        markets).fetchall() if str(r[0]) not in EXCLUDE]
    market_map: dict = {}
    for sym, market in connection.execute(
            f"SELECT symbol, MIN(market) FROM daily_prices WHERE market IN (?,?) AND symbol GLOB {glob6} "
            "GROUP BY symbol HAVING COUNT(DISTINCT market) = 1 ORDER BY symbol", markets).fetchall():
        market_map[str(sym)] = str(market)
    multi = [str(r[0]) for r in connection.execute(
        f"SELECT symbol FROM daily_prices WHERE market IN (?,?) AND symbol GLOB {glob6} "
        "GROUP BY symbol HAVING COUNT(DISTINCT market) > 1 ORDER BY symbol", markets).fetchall()]
    for sym in multi:
        segs = []
        for d, m in connection.execute("SELECT trade_date, market FROM daily_prices WHERE symbol = ? "
                                       "AND market IN (?,?) ORDER BY trade_date", [sym, *markets]).fetchall():
            if not segs or segs[-1][1] != str(m):
                segs.append((str(d)[:10], str(m)))
        if len({m for _, m in segs}) != len(segs):
            raise RuntimeError(f"{sym}: market flip-flop {segs}")
        market_map[sym] = segs
    for ex in EXCLUDE:
        market_map.pop(ex, None)
    if set(tickers) != set(market_map):
        raise RuntimeError("universe and market map disagree")
    return tickers, market_map


def load_frames(connection, symbols: list[str], end: str, market_df: pd.DataFrame | None) -> dict:
    frames = []
    for start in range(0, len(symbols), 500):
        batch = symbols[start:start + 500]
        frames.append(pd.read_sql_query(
            f"SELECT trade_date AS Date, symbol AS Symbol, open AS Open, high AS High, low AS Low, "
            f"close AS Close, volume AS Volume, trading_value AS TradingValue FROM daily_prices "
            f"WHERE symbol IN ({','.join('?' * len(batch))}) AND trade_date >= ? AND trade_date <= ? "
            f"ORDER BY symbol, trade_date", connection, params=[*batch, DB_START, end]))
    history = pd.concat(frames, ignore_index=True)
    return v4.frames_from_history(history, market_df)


def load_benchmarks(connection, end: str) -> dict:
    out = {}
    for market, symbol in v4.MARKET_BENCHMARKS.items():
        frame = load_frames(connection, [symbol], end, None).get(symbol)
        if frame is None or frame.empty:
            raise RuntimeError(f"benchmark {symbol} has no data")
        out[market] = frame
    return out


def fingerprint(equity: pd.DataFrame, trades: pd.DataFrame) -> dict:
    return {
        "trades": int(len(trades)),
        "tickers": int(trades["ticker"].nunique()) if len(trades) else 0,
        "exit_reasons": {k: int(v) for k, v in trades["exit_reason"].value_counts().items()},
        "holding_days_mean": round(float(trades["holding_days"].mean()), 2) if len(trades) else None,
        "wins_losses": [int((trades["pnl_net"] > 0).sum()), int((trades["pnl_net"] <= 0).sum())],
        "trades_by_year": {int(k): int(v) for k, v in trades["entry_date"].dt.year.value_counts().sort_index().items()},
        "equity_rows": int(len(equity)),
        "trades_sha16": hashlib.sha256(trades[v4.TRADE_COLUMNS].to_csv(index=False).encode()).hexdigest()[:16],
        "equity_sha16": hashlib.sha256(equity.to_csv().encode()).hexdigest()[:16],
    }


def performance(equity: pd.DataFrame, trades: pd.DataFrame, capital: float) -> dict:
    curve = equity["equity"]
    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-9)
    drawdown = curve / curve.cummax() - 1
    gains, losses = trades.loc[trades["pnl_net"] > 0, "pnl_net"].sum(), -trades.loc[trades["pnl_net"] <= 0, "pnl_net"].sum()
    return {
        "final_equity": round(float(curve.iloc[-1]), 0),
        "total_return_pct": round((float(curve.iloc[-1]) / capital - 1) * 100, 2),
        "cagr_pct": round(((float(curve.iloc[-1]) / capital) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
        "profit_factor": round(float(gains / losses), 3) if losses > 0 else None,
        "win_rate_pct": round(float((trades["pnl_net"] > 0).mean()) * 100, 1) if len(trades) else None,
        "avg_net_return_pct": round(float((trades["pnl_net"] / (trades["entry_price"] * trades["shares"]
                                                                  * (1 + v4.ENTRY_COST))).mean()) * 100, 3)
        if len(trades) else None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2026-09-08")
    parser.add_argument("--capital", type=float, default=100_000_000)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    started = time.time()
    with connect(args.db) as connection:
        benchmarks = load_benchmarks(connection, args.end)
        tickers, market_map = load_universe_and_markets(connection)
        ohlcv = load_frames(connection, tickers, args.end, benchmarks["KOSPI"])
    equity, trades = v4.run_period(ohlcv, benchmarks["KOSPI"], pd.Timestamp(args.start), pd.Timestamp(args.end),
                                   v4.FIXED_CONFIG, args.capital, benchmarks=benchmarks, market_map=market_map)
    result = {"window": [args.start, args.end], "universe": len(tickers), "loaded": len(ohlcv),
              "fingerprint": fingerprint(equity, trades), "performance": performance(equity, trades, args.capital),
              "seconds": round(time.time() - started, 1)}
    text = json.dumps(result, ensure_ascii=False, indent=1, default=lambda v: v.item() if isinstance(v, np.generic) else str(v))
    print(text)
    if args.json:
        args.json.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
