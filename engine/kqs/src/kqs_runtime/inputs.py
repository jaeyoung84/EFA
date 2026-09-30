"""Validate bounded inputs before the unchanged research core is called."""

from datetime import date

import numpy as np
import pandas as pd

from korea_quant.eligibility import DEFAULT_MARKET_BENCHMARKS, SUPPORTED_MARKETS

COLUMNS = ["Date", "Symbol", "Name", "Market", "Open", "High", "Low", "Close",
           "Volume", "TradingValue", "MarketCap"]


def parse_date(value: str) -> pd.Timestamp:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Dates must use YYYY-MM-DD.")
    return pd.Timestamp(parsed)


def normalize_symbol(value: object) -> str:
    """Six ASCII letters/digits, as the archived `validate_krx_symbol` (e.g. 005930, 00088K, 0030R0).

    Only purely numeric codes get the CSV zero-fill (5930.0 -> 005930); alphanumeric KRX codes
    for newer listings must already be six characters and are upper-cased.
    """
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isascii() and text.isdigit():
        text = text.zfill(6)
    text = text.upper()
    if len(text) != 6 or not text.isascii() or not text.isalnum():
        raise ValueError(f"Invalid KRX short code: {value!r}")
    return text


def _dates(values: object, as_of: pd.Timestamp) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(values, errors="raise"))
    if dates.tz is not None or dates.hasnans or not dates.equals(dates.normalize()):
        raise ValueError("Market dates must be timezone-free daily sessions.")
    if (dates > as_of).any():
        raise ValueError("Future data beyond as_of is not allowed.")
    return dates


def validated_inputs(history: pd.DataFrame, benchmarks: dict[str, pd.Series],
                     as_of: str) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    cutoff = parse_date(as_of)
    if history.empty or not set(COLUMNS).issubset(history.columns):
        raise ValueError("History must be nonempty and contain the required OHLCV columns.")
    data = history[COLUMNS].copy()
    data["Date"] = _dates(data["Date"], cutoff)
    data["Symbol"] = data["Symbol"].map(normalize_symbol)
    if not data["Market"].isin(SUPPORTED_MARKETS).all():
        raise ValueError("Only KOSPI/KOSDAQ equity history is supported.")
    if data.duplicated(["Symbol", "Date"]).any():
        raise ValueError("Duplicate symbol/session rows are not allowed.")
    if data.groupby("Symbol")["Market"].nunique().gt(1).any():
        raise ValueError("A symbol must not change market inside the input window.")
    if data["Name"].isna().any() or data["Name"].astype(str).str.strip().eq("").any():
        raise ValueError("Security names must not be blank.")
    for column in COLUMNS[4:]:
        data[column] = pd.to_numeric(data[column], errors="raise").astype(float)
        if not np.isfinite(data[column]).all():
            raise ValueError(f"{column} must be finite.")
        if (data[column] < 0).any() or (column in COLUMNS[4:8] and data[column].eq(0).any()):
            raise ValueError(f"Invalid {column}; unfilled halt rows must not enter the scanner.")
    if (data["High"] < data[["Open", "Close", "Low"]].max(axis=1)).any() or (
        data["Low"] > data[["Open", "Close", "High"]].min(axis=1)
    ).any():
        raise ValueError("OHLC ranges are inconsistent.")
    for market in SUPPORTED_MARKETS:
        if data.loc[data["Market"].eq(market), "Date"].max() != cutoff:
            raise ValueError(f"{market} equity history is not current at {as_of}.")
    validated = {}
    for market, _symbol in DEFAULT_MARKET_BENCHMARKS:
        if market not in benchmarks:
            raise ValueError(f"Missing benchmark: {market}")
        series = benchmarks[market].copy()
        series.index = _dates(series.index, cutoff)
        series = pd.to_numeric(series, errors="raise").astype(float).sort_index()
        if len(series) < 200 or series.index.has_duplicates or not np.isfinite(series).all():
            raise ValueError(f"{market} benchmark requires 200 unique finite observations.")
        if series.le(0).any() or series.index.max() != cutoff:
            raise ValueError(f"{market} benchmark must be positive and current at {as_of}.")
        validated[market] = series
    return data.sort_values(["Symbol", "Date"]).reset_index(drop=True), validated
