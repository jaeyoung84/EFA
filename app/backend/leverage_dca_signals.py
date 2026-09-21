"""레버리지 DCA 전략 판정에 쓰는 시장·거시 신호를 실제 데이터에서 계산한다.

가격 I/O(저장 일봉)와 FRED 거시지표 I/O는 이 모듈에만 두고, 판정 로직은
``leverage_dca_strategy``의 순수 함수가 맡는다. 이 모듈은 신호를 읽기만 하며
주문이나 자금 이동을 만들지 않는다.
"""
from __future__ import annotations

import asyncio
import csv
import io
import urllib.parse
import urllib.request
from datetime import date, timedelta

try:
    from .market_data import MarketDataError, get_daily_ohlcv
    from .leverage_dca_strategy import (
        DEFAULT_LEVERAGE_DCA_POLICY, compute_days_since_high, compute_drawdown_from_high_pct,
        compute_ma_above_streak, compute_series_trend,
    )
except ImportError:
    from market_data import MarketDataError, get_daily_ohlcv  # type: ignore
    from leverage_dca_strategy import (  # type: ignore
        DEFAULT_LEVERAGE_DCA_POLICY, compute_days_since_high, compute_drawdown_from_high_pct,
        compute_ma_above_streak, compute_series_trend,
    )

SIGNAL_TICKERS = {"spx": "^GSPC", "vix": "^VIX", "upro": "UPRO", "soxl": "SOXL"}
LOOKBACK_DAYS = 400
FRED_SERIES = {"fed_funds": "FEDFUNDS", "ten_year": "DGS10", "nfp": "PAYEMS", "unemployment": "UNRATE"}


async def _series(ticker: str) -> tuple[list[str], list[float]]:
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=LOOKBACK_DAYS)
    result = await get_daily_ohlcv(ticker, start, end, adjusted=True, prefer_cached=True)
    dates = [str(bar["timestamp"])[:10] for bar in result["bars"]]
    closes = [float(bar["close"]) for bar in result["bars"]]
    return dates, closes


async def fetch_market_signals(policy: dict = DEFAULT_LEVERAGE_DCA_POLICY) -> dict:
    """S&P/VIX/UPRO/SOXL 저장 일봉으로 매수 중지·재개 판정에 쓰는 신호를 계산한다."""
    errors: list[str] = []
    series: dict[str, tuple[list[str], list[float]]] = {}
    for key, ticker in SIGNAL_TICKERS.items():
        try:
            series[key] = await _series(ticker)
        except MarketDataError as exc:
            errors.append(f"{ticker}: {exc}")
            series[key] = ([], [])
    if not all(series[key][1] for key in SIGNAL_TICKERS):
        return {"available": False, "as_of": date.today().isoformat(), "errors": errors}
    spx_dates, spx_closes = series["spx"]
    return {
        "available": True,
        "as_of": date.today().isoformat(),
        "spx_drawdown_pct": compute_drawdown_from_high_pct(spx_closes),
        "vix": series["vix"][1][-1],
        "soxl_drawdown_pct": compute_drawdown_from_high_pct(series["soxl"][1]),
        "upro_drawdown_pct": compute_drawdown_from_high_pct(series["upro"][1]),
        "spx_above_ma_consecutive_days": compute_ma_above_streak(spx_closes, policy["resume_ma_days"]),
        "spx_drawdown_days": compute_days_since_high(spx_dates, spx_closes),
        "spx_dates": spx_dates, "spx_closes": spx_closes,
        "errors": errors,
    }


def _fred_csv_url(series_id: str, start: date, end: date) -> str:
    params = urllib.parse.urlencode({"id": series_id, "cos": start.isoformat(), "coe": end.isoformat()})
    return f"https://fred.stlouisfed.org/graph/fredgraph.csv?{params}"


def _fetch_fred_series_sync(series_id: str, start: date, end: date) -> list[float]:
    request = urllib.request.Request(_fred_csv_url(series_id, start, end), headers={"User-Agent": "JY-Investing/2.0"})
    with urllib.request.urlopen(request, timeout=12) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    values: list[float] = []
    for row in rows:
        try:
            values.append(float(row.get(series_id)))
        except (TypeError, ValueError):
            continue
    return values


async def _fred_series(series_id: str, lookback_days: int) -> list[float]:
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    return await asyncio.to_thread(_fetch_fred_series_sync, series_id, start, end)


async def fetch_reserve_signals(spx_dates: list[str], spx_closes: list[float]) -> dict:
    """예비비 단계 투입 판정에 쓰는 FRED 거시지표(연준금리·국채금리·고용)를 계산한다.

    API 키 없이 공개 CSV(``fredgraph.csv``)로 조회한다. 일부 시리즈 조회가
    실패해도 다른 시리즈는 계속 시도하고, 오류는 ``errors``에 모아 반환한다.
    """
    errors: list[str] = []

    async def _safe(series_id: str, lookback_days: int) -> list[float]:
        try:
            return await _fred_series(series_id, lookback_days)
        except Exception as exc:
            errors.append(f"{series_id}: {exc}")
            return []

    fed_funds_values = await _safe(FRED_SERIES["fed_funds"], 200)
    ten_year_values = await _safe(FRED_SERIES["ten_year"], 40)
    nfp_values = await _safe(FRED_SERIES["nfp"], 200)
    unemployment_values = await _safe(FRED_SERIES["unemployment"], 200)

    if not (fed_funds_values and ten_year_values and nfp_values and unemployment_values):
        return {"available": False, "errors": errors}

    # PAYEMS는 천 명 단위 누적 고용자 수다. 월간 증감분(명 단위)의 최근 3개월 평균이 "NFP".
    nfp_diffs = [(nfp_values[index] - nfp_values[index - 1]) * 1000 for index in range(1, len(nfp_values))]
    nfp_3m_avg = sum(nfp_diffs[-3:]) / len(nfp_diffs[-3:]) if len(nfp_diffs) >= 3 else None
    return {
        "available": True,
        "fed_funds_trend": compute_series_trend(fed_funds_values[-3:]),
        "ten_year_yield_trend": compute_series_trend(ten_year_values[-10:]),
        "nfp_3m_avg": nfp_3m_avg,
        "unemployment_rate": unemployment_values[-1],
        "drawdown_days": compute_days_since_high(spx_dates, spx_closes),
        "errors": errors,
    }
