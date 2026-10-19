"""기술 차트 API: 슈퍼트렌드 독립 차트(읽기 전용, 로그인 불필요).

계산은 supertrend_chart.py가 맡고, 여기서는 저장된 검증 일봉을 읽어 넘긴다. 주문·알림·원장·
엔진 추천과 연결하지 않는다. 시세가 없으면 가짜 캔들 대신 503과 원인을 돌려준다.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

try:
    from ..market_data import get_daily_ohlcv
    from .. import supertrend_chart as stc
except ImportError:  # Allows `uvicorn main:app` from app/backend.
    from market_data import get_daily_ohlcv  # type: ignore
    import supertrend_chart as stc  # type: ignore

router = APIRouter(prefix="/api/chart", tags=["기술 차트"])

TICKER_RE = re.compile(r"^[A-Za-z0-9.^=\-]{1,24}$")
# 표시 구간(최대 3년)보다 길게 계산해 ATR·주봉 초기값 영향을 줄인다.
HISTORY_DAYS = 365 * 5 + 30


async def _load(ticker: str) -> dict:
    end = date.today() + timedelta(days=1)
    return await get_daily_ohlcv(ticker, end - timedelta(days=HISTORY_DAYS), end, adjusted=True, prefer_cached=True)


@router.get("/supertrend")
async def supertrend_chart(
    ticker: str = Query(..., description="예: 005930.KS, AAPL"),
    atr: int = Query(default=stc.DEFAULT_ATR_LENGTH, ge=1, le=100, description="ATR 기간"),
    factor: float = Query(default=stc.DEFAULT_FACTOR, ge=0.1, le=20, description="ATR 배수"),
    benchmark: bool = Query(default=True, description="비교 지수 방향을 함께 계산"),
) -> dict:
    if not TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=422, detail="종목 코드 형식이 올바르지 않습니다.")
    ticker = ticker.upper()
    try:
        stored = await _load(ticker)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"{ticker} 일봉 데이터 없음: {type(exc).__name__}: {exc}") from exc
    if not stored.get("bars"):
        raise HTTPException(status_code=503, detail=f"{ticker} 일봉 데이터 없음: 공급자가 빈 응답을 반환했습니다.")

    bench_ticker = stc.benchmark_ticker(ticker)
    bench_bars = None
    bench_note = None
    if benchmark and bench_ticker != ticker:
        try:
            bench_bars = (await _load(bench_ticker)).get("bars") or None
        except Exception as exc:  # 비교 지수가 없어도 본 차트는 표시한다.
            bench_note = f"{type(exc).__name__}: {exc}"[:200]

    result = stc.compute_chart(stored["bars"], ticker=ticker, atr_length=atr, factor=factor, benchmark_bars=bench_bars)
    last_day = result["bars"][-1]["date"]
    return {
        "ticker": ticker, "settings": {"atr": atr, "factor": factor, "adx": stc.ADX_LENGTH},
        **result,
        "benchmark": {"ticker": bench_ticker if bench_bars else None, "error": bench_note},
        "data": {"source": stored.get("source"), "dataset_version": stored.get("dataset_version"),
                 "quality": stored.get("quality"), "adjusted": True, "last_bar_date": last_day,
                 "first_bar_date": result["bars"][0]["date"], "bar_count": len(result["bars"]),
                 "stale": (date.today() - date.fromisoformat(last_day)).days > 5},
    }
