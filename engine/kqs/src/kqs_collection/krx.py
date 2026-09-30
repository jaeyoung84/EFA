"""KRX Open API daily collection into the market SQLite (standard library only).

Ported for operations from the archived `korea_quant/krx.py` (EINT-001 snapshot): the same
endpoints, row normalization, table schema and D-31 key handling, so an uploaded original
`krx_daily.sqlite3` keeps working. Research-only parts (pandas history/universe panels,
weekday backfills) were not ported. Differences, all fail-closed:

- only reviewed KRX calendar sessions are requested (no weekday guessing, no holiday calls);
- a reviewed session with zero returned rows is a provider/readiness failure, never stored;
- the authentication key is excluded from repr and never included in messages.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from kqs_publication.calendar import calendar_for_session

MARKETS = ("KOSPI", "KOSDAQ", "ETF")
MARKET_ENDPOINTS = {
    "KOSPI": "sto/stk_bydd_trd",
    "KOSDAQ": "sto/ksq_bydd_trd",
    "ETF": "etp/etf_bydd_trd",
}
NUMERIC_FIELDS = {
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "TDD_CLSPRC": "close",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "trading_value",
    "MKTCAP": "market_cap",
    "LIST_SHRS": "listed_shares",
}
ZERO_WHEN_BLANK_FIELDS = {"TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC"}
Transport = Callable[[str, dict[str, str]], dict[str, Any]]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_prices (
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ', 'ETF')),
    section TEXT NOT NULL,
    open INTEGER NOT NULL CHECK (open >= 0),
    high INTEGER NOT NULL CHECK (high >= 0),
    low INTEGER NOT NULL CHECK (low >= 0),
    close INTEGER NOT NULL CHECK (close >= 0),
    volume INTEGER NOT NULL CHECK (volume >= 0),
    trading_value INTEGER NOT NULL CHECK (trading_value >= 0),
    market_cap INTEGER NOT NULL CHECK (market_cap >= 0),
    listed_shares INTEGER NOT NULL CHECK (listed_shares >= 0),
    collected_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_daily_symbol_date ON daily_prices(symbol, trade_date);
CREATE TABLE IF NOT EXISTS collection_log (
    trade_date TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('KOSPI', 'KOSDAQ', 'ETF')),
    row_count INTEGER NOT NULL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (trade_date, market)
);
"""


class KrxApiError(RuntimeError):
    """KRX did not return a usable response for a reviewed session."""


def _dotenv_value(path: Path, key: str) -> str:
    """D-31: read one plain KEY=value line without sourcing/evaluating the dotenv file."""
    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


@dataclass(frozen=True)
class KrxOpenApiClient:
    auth_key: str = field(repr=False)
    base_url: str = "https://data-dbg.krx.co.kr/svc/apis"
    timeout: float = 30.0
    retries: int = 3
    transport: Transport | None = field(default=None, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    @classmethod
    def from_env(cls, env_name: str = "KRX_AUTH_KEY", *, env_file: Path = Path(".env"), **kwargs: Any) -> "KrxOpenApiClient":
        auth_key = os.environ.get(env_name, "").strip() or _dotenv_value(env_file, env_name)
        if not auth_key:
            raise ValueError(f"Set {env_name} to the approved KRX Open API authentication key.")
        return cls(auth_key=auth_key, **kwargs)

    def fetch_market_day(self, market: str, trading_day: date) -> list[dict[str, Any]]:
        if market not in MARKET_ENDPOINTS:
            raise ValueError(f"Unsupported market: {market}")
        query = urllib.parse.urlencode({"basDd": trading_day.strftime("%Y%m%d")})
        url = f"{self.base_url.rstrip('/')}/{MARKET_ENDPOINTS[market]}?{query}"
        rows = self._request(url).get("OutBlock_1")
        if not isinstance(rows, list):
            raise KrxApiError("KRX response is missing the OutBlock_1 list.")
        return [_normalize_row(row, market, trading_day) for row in rows if not _skip_unusable_etf_row(row, market)]

    def _request(self, url: str) -> dict[str, Any]:
        headers = {"AUTH_KEY": self.auth_key, "Accept": "application/json"}
        if self.transport is not None:
            payload = self.transport(url, headers)
            if not isinstance(payload, dict):
                raise KrxApiError("KRX response must be a JSON object.")
            return payload
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise KrxApiError("KRX response must be a JSON object.")
                return payload
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.retries:
                    self.sleep(2 ** attempt)
        # The URL carries only the date; the key is in headers and is never formatted into errors.
        raise KrxApiError(f"KRX request failed after retries: {type(last_error).__name__}") from None


def _normalize_row(row: Any, market: str, requested_day: date) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise KrxApiError("KRX row must be a JSON object.")
    symbol = str(row.get("ISU_CD", "")).strip().upper()
    if len(symbol) != 6 or not symbol.isascii() or not symbol.isalnum():
        raise KrxApiError(f"Invalid KRX symbol in response: {symbol!r}")
    raw_date = str(row.get("BAS_DD", "")).strip()
    try:
        response_day = date.fromisoformat(f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}")
    except ValueError as exc:
        raise KrxApiError(f"Invalid KRX date in response: {raw_date!r}") from exc
    if response_day != requested_day:
        raise KrxApiError(f"KRX returned {response_day} for requested date {requested_day}.")
    normalized: dict[str, Any] = {
        "trade_date": response_day.isoformat(), "symbol": symbol, "name": str(row.get("ISU_NM", "")).strip(),
        "market": market, "section": str(row.get("SECT_TP_NM", "")).strip(),
    }
    for source, target in NUMERIC_FIELDS.items():
        value = str(row.get(source, "")).replace(",", "").strip()
        if not value and source in ZERO_WHEN_BLANK_FIELDS:
            value = "0"
        try:
            normalized[target] = int(value)
        except ValueError as exc:
            raise KrxApiError(f"Invalid integer in {source}: {value!r}") from exc
        if normalized[target] < 0:
            raise KrxApiError(f"Negative value in {source}: {value!r}")
    return normalized


def _skip_unusable_etf_row(row: Any, market: str) -> bool:
    """Exclude ETF response rows without a close, which cannot form a daily bar."""
    return market == "ETF" and isinstance(row, dict) and not str(row.get("TDD_CLSPRC", "")).replace(",", "").strip()


class MarketStore:
    """Writer for the market SQLite. Compatible with the original `krx_daily.sqlite3` schema."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        schemas = {row[0]: row[1] or "" for row in
                   connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
        for table in ("daily_prices", "collection_log"):
            if table in schemas and "'ETF'" not in schemas[table]:
                connection.close()
                raise ValueError(f"Legacy {table} schema without ETF; migrate with the archived collector first.")
        connection.executescript(SCHEMA_SQL)
        return connection

    def collected_rows(self, market: str, trading_day: date) -> int | None:
        if not self.path.is_file():
            return None
        connection = self._connect()
        try:
            row = connection.execute("SELECT row_count FROM collection_log WHERE trade_date=? AND market=?",
                                     (trading_day.isoformat(), market)).fetchone()
        finally:
            connection.close()
        return None if row is None else int(row[0])

    def write_session(self, market: str, trading_day: date, rows: Iterable[dict[str, Any]]) -> int:
        materialized = list(rows)
        if not materialized:
            raise KrxApiError(f"KRX returned no {market} rows for reviewed session {trading_day.isoformat()}.")
        if any(row["trade_date"] != trading_day.isoformat() or row["market"] != market for row in materialized):
            raise KrxApiError("Rows do not belong to the requested session/market.")
        now = datetime.now(timezone.utc).isoformat()
        values = [(row["trade_date"], row["symbol"], row["name"], row["market"], row["section"], row["open"],
                   row["high"], row["low"], row["close"], row["volume"], row["trading_value"], row["market_cap"],
                   row["listed_shares"], now) for row in materialized]
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                """INSERT INTO daily_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, symbol) DO UPDATE SET
                    name=excluded.name, market=excluded.market, section=excluded.section,
                    open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                    volume=excluded.volume, trading_value=excluded.trading_value,
                    market_cap=excluded.market_cap, listed_shares=excluded.listed_shares,
                    collected_at=excluded.collected_at""", values)
            connection.execute(
                """INSERT INTO collection_log VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date, market) DO UPDATE SET row_count=excluded.row_count,
                    collected_at=excluded.collected_at""",
                (trading_day.isoformat(), market, len(materialized), now))
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        return len(materialized)


def collect_session(client: KrxOpenApiClient, store: MarketStore, session: date, *,
                    markets: tuple[str, ...] = MARKETS, refresh: bool = False, delay_seconds: float = 0.1) -> dict:
    """Collect one reviewed KRX session. Markets already logged are skipped unless refresh=True."""
    calendar_for_session(session).require_session(session)
    if not markets or any(market not in MARKET_ENDPOINTS for market in markets):
        raise ValueError("markets must contain supported KRX market values.")
    result: dict[str, dict] = {}
    for market in markets:
        existing = store.collected_rows(market, session)
        if existing and not refresh:
            result[market] = {"status": "skipped", "rows": existing}
            continue
        rows = client.fetch_market_day(market, session)
        result[market] = {"status": "collected", "rows": store.write_session(market, session, rows)}
        if delay_seconds:
            client.sleep(delay_seconds)
    return {"session": session.isoformat(), "markets": result}
