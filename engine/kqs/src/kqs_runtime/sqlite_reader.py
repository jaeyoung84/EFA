"""One read transaction over an existing KRX DB. Never create or migrate it."""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from korea_quant.eligibility import DEFAULT_MARKET_BENCHMARKS
from kqs_runtime.inputs import parse_date


def fill_halted_trading_days(history: pd.DataFrame) -> pd.DataFrame:
    """Extracted OHLC carry-forward from krx.py; see migration_manifest.json."""
    price_cols = ["Open", "High", "Low", "Close"]
    halted = history[price_cols].le(0).any(axis=1)
    if not halted.any():
        return history
    history = history.sort_values(["Symbol", "Date"]).reset_index(drop=True)
    halted = history[price_cols].le(0).any(axis=1)
    history.loc[halted, price_cols] = np.nan
    prior_close = history.groupby("Symbol")["Close"].ffill()
    history.loc[halted, price_cols] = np.repeat(prior_close.loc[halted].to_numpy()[:, None], 4, axis=1)
    return history.sort_values(["Date", "Symbol"]).reset_index(drop=True)


def drop_unfillable_halted_rows(history: pd.DataFrame) -> pd.DataFrame:
    """Remove halted rows that have no earlier close in the window to carry forward.

    These are the leading rows of symbols halted at the start of the input window (or halted
    throughout it). They must not enter the scanner, but one of them must not fail the whole
    session either. The count and symbols are kept in ``history.attrs`` for the audit trail.
    """
    price_cols = ["Open", "High", "Low", "Close"]
    unfilled = history[price_cols].isna().any(axis=1)
    kept = history.loc[~unfilled].reset_index(drop=True)
    kept.attrs["dropped_unfillable_halted_rows"] = {
        "rows": int(unfilled.sum()),
        "symbols": sorted(str(symbol) for symbol in history.loc[unfilled, "Symbol"].unique()),
    }
    return kept


def read_inputs(path: Path, *, history_start: str, as_of: str) -> tuple:
    if parse_date(history_start) > parse_date(as_of):
        raise ValueError("history_start must not be after as_of.")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("KRX DB must be an existing file.")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        history = pd.read_sql_query(
            "SELECT trade_date AS Date, symbol AS Symbol, name AS Name, market AS Market, "
            "open AS Open, high AS High, low AS Low, close AS Close, volume AS Volume, "
            "trading_value AS TradingValue, market_cap AS MarketCap FROM daily_prices "
            "WHERE trade_date >= ? AND trade_date <= ? AND market IN ('KOSPI', 'KOSDAQ') "
            "ORDER BY trade_date, symbol", connection, params=(history_start, as_of))
        history["Date"] = pd.to_datetime(history["Date"])
        benchmarks = {}
        for market, symbol in DEFAULT_MARKET_BENCHMARKS:
            frame = pd.read_sql_query(
                "SELECT trade_date, close FROM daily_prices "
                "WHERE symbol = ? AND trade_date <= ? ORDER BY trade_date",
                connection, params=(symbol, as_of))
            benchmarks[market] = pd.Series(frame["close"].to_numpy(),
                                           index=pd.to_datetime(frame["trade_date"]), name=symbol)
        return drop_unfillable_halted_rows(fill_halted_trading_days(history)), benchmarks
    finally:
        connection.close()
