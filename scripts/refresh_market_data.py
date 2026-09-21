"""로컬 운영자가 정기 실행할 일봉 OHLCV 수집 명령."""
from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backend.market_data import MarketDataError, ensure_market_data_indexes, get_daily_ohlcv


async def main() -> int:
    tickers = [ticker.strip().upper() for ticker in os.getenv(
        "MARKET_DATA_REFRESH_TICKERS", "^KS11,^KQ11,^GSPC,^IXIC,KRW=X"
    ).split(",") if ticker.strip()]
    days = max(1, min(3650, int(os.getenv("MARKET_DATA_REFRESH_DAYS", "370"))))
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days)
    await ensure_market_data_indexes()
    failed = False
    for ticker in tickers:
        try:
            result = await get_daily_ohlcv(ticker, start, end)
            print(f"[OK] {ticker}: {len(result['bars'])} bars, {result['source']}, {result['dataset_version']}")
        except MarketDataError as exc:
            failed = True
            print(f"[FAIL] {ticker}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
