"""기술 차트 API: 슈퍼트렌드 독립 차트(읽기 전용, 로그인 불필요).

계산은 supertrend_chart.py가 맡고, 여기서는 저장된 검증 일봉을 읽어 넘긴다. 주문·알림·원장·
엔진 추천과 연결하지 않는다. 시세가 없으면 가짜 캔들 대신 503과 원인을 돌려준다.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from time import monotonic

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


REF_MAX_TICKERS = 30
REF_CACHE_SECONDS = 600
_ref_cache: dict[str, tuple[float, dict]] = {}
_ref_gate = asyncio.Semaphore(4)


def reference_summary(ticker: str, bars: list[dict], now=None) -> dict:
    """추천 카드용 요약: 마지막 확정 일봉 기준 방향·주봉·최근 전환·거리. 점수·신호가 아니다."""
    result = stc.compute_chart(bars, ticker=ticker, now=now, benchmark_bars=None)
    rows = result["bars"]
    confirmed = rows if result["last_confirmed"] else rows[:-1]
    if not confirmed or confirmed[-1]["dir"] is None:
        return {"ticker": ticker, "status": "insufficient", "bar_count": len(rows)}
    row = confirmed[-1]
    return {"ticker": ticker, "status": "ok", "as_of": row["date"], "dir": row["dir"], "weekly_dir": row["weekly_dir"],
            "weekly_ref_end": row["weekly_ref_end"], "flip_today": row["flip"], "last_flip_type": row["last_flip_type"],
            "last_flip_date": row["last_flip_date"], "days_since_flip": row["days_since_flip"],
            "distance_pct": row["distance_pct"], "atr_distance": row["atr_distance"], "st": row["st"],
            "flip_count": row["flip_count"], "adx": row["adx"],
            "intraday_break": rows[-1]["intraday_break"] if not result["last_confirmed"] else False}


@router.get("/supertrend-reference")
async def supertrend_reference(tickers: str = Query(..., description="쉼표로 구분, 최대 30개 (예: 005930.KS,035720.KQ)")) -> dict:
    """추천 종목 참고 칩용 일괄 요약(ATR 10 × 3 고정). 추천 규칙·순서·원장에 쓰지 않는다."""
    wanted = list(dict.fromkeys(t.strip().upper() for t in tickers.split(",") if TICKER_RE.fullmatch(t.strip())))[:REF_MAX_TICKERS]

    async def one(ticker: str) -> dict:
        hit = _ref_cache.get(ticker)
        if hit and monotonic() - hit[0] < REF_CACHE_SECONDS:
            return hit[1]
        async with _ref_gate:
            try:
                stored = await _load(ticker)
                summary = reference_summary(ticker, stored.get("bars") or []) if stored.get("bars") else {"ticker": ticker, "status": "unavailable"}
            except Exception as exc:
                return {"ticker": ticker, "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[:200]}
        _ref_cache[ticker] = (monotonic(), summary)
        return summary

    items = await asyncio.gather(*(one(ticker) for ticker in wanted))
    return {"settings": {"atr": stc.DEFAULT_ATR_LENGTH, "factor": stc.DEFAULT_FACTOR},
            "items": {item["ticker"]: item for item in items},
            "note": "슈퍼트렌드 참고 정보이며 추천 규칙·순서에 반영되지 않습니다. 확정 일봉 기준."}


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
