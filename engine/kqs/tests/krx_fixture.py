"""Synthetic market DB in the original krx_daily.sqlite3 schema plus a fake KRX transport."""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from test_runtime import synthetic_inputs
from kqs_collection.krx import SCHEMA_SQL


def _krx_row(trade_date: str, symbol: str, name: str, open_, high, low, close, volume, value, cap) -> dict:
    return {"BAS_DD": trade_date.replace("-", ""), "ISU_CD": symbol, "ISU_NM": name, "SECT_TP_NM": "",
            "TDD_OPNPRC": str(open_), "TDD_HGPRC": str(high), "TDD_LWPRC": str(low), "TDD_CLSPRC": str(close),
            "ACC_TRDVOL": str(volume), "ACC_TRDVAL": str(value), "MKTCAP": str(cap), "LIST_SHRS": "1000"}


def build_market(path: Path, *, previous_counts: dict | None = None):
    """Write all sessions except the last into the real schema; return (as_of, rows_by_market)."""
    history, benchmarks, as_of = synthetic_inputs()
    rows_by_market: dict[str, list[dict]] = {"KOSPI": [], "KOSDAQ": [], "ETF": []}
    stored = []
    for _, row in history.iterrows():
        day = row["Date"].date().isoformat()
        values = (int(row["Open"]), int(row["High"]), int(row["Low"]), int(row["Close"]),
                  int(row["Volume"]), int(row["TradingValue"]), int(row["MarketCap"]))
        if day == as_of:
            rows_by_market[row["Market"]].append(_krx_row(day, row["Symbol"], row["Name"], *values))
        else:
            stored.append((day, row["Symbol"], row["Name"], row["Market"], "", *values, 1000, "fixture"))
    for market, symbol in (("KOSPI", "069500"), ("KOSDAQ", "229200")):
        for session, close in benchmarks[market].items():
            day = session.date().isoformat()
            values = (int(close), int(close), int(close), int(close), 1000, 6_000_000_000, 1_000_000_000_000)
            if day == as_of:
                rows_by_market["ETF"].append(_krx_row(day, symbol, "합성ETF", *values))
            else:
                stored.append((day, symbol, "합성ETF", "ETF", "", *values, 1000, "fixture"))
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_SQL)
        connection.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", stored)
        previous = (date.fromisoformat(as_of) - timedelta(days=1)).isoformat()
        for market, count in (previous_counts or {"KOSPI": 1, "KOSDAQ": 1, "ETF": 2}).items():
            connection.execute("INSERT INTO collection_log VALUES (?,?,?,?)", (previous, market, count, "fixture"))
        connection.commit()
    finally:
        connection.close()
    return as_of, rows_by_market


def available_time(as_of: str) -> datetime:
    return datetime.combine(date.fromisoformat(as_of) + timedelta(days=1), datetime.min.time(),
                            tzinfo=ZoneInfo("Asia/Seoul")).replace(hour=7)


class FakeKrx:
    """Returns prepared rows per (market, basDd); records requested URLs; never sees the network."""

    endpoints = {"stk_bydd_trd": "KOSPI", "ksq_bydd_trd": "KOSDAQ", "etf_bydd_trd": "ETF"}

    def __init__(self, rows_by_market: dict):
        self.rows_by_market = rows_by_market
        self.urls: list[str] = []
        self.headers: list[dict] = []

    def __call__(self, url: str, headers: dict) -> dict:
        self.urls.append(url)
        self.headers.append(headers)
        market = next(value for key, value in self.endpoints.items() if key in url)
        return {"OutBlock_1": list(self.rows_by_market.get(market, []))}
