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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
LOOKBACK_DAYS = 400           # 50일선·연속일 계산 여유분까지 받아 온다.
HIGH_WINDOW_DAYS = 365        # 낙폭·고점 경과일은 화면·규칙 표기대로 52주(365일) 고점 기준.
MAX_MISSING_SESSIONS = 1      # 마지막 일봉 뒤로 NYSE 거래일이 2개 이상 비면 오래된 시세로 보고 판정을 보류한다.
FRED_SERIES = {"fed_funds": "FEDFUNDS", "ten_year": "DGS10", "nfp": "PAYEMS", "unemployment": "UNRATE"}
_NEW_YORK = ZoneInfo("America/New_York")


async def _series(ticker: str) -> tuple[list[str], list[float]]:
    """판정마다 공급자에서 새로 받아 검증·저장한다. 받지 못하면 get_daily_ohlcv가 마지막 저장본으로 대신한다.

    2026-10-06: 저장본 우선(prefer_cached) 조회로 SOXL·VIX가 2주 넘게 9월 18·21일 값에 멈춰
    SOXL 낙폭이 -58.9%(실제 -45.4%)로 계산되고 SOXL 첫 진입이 막힌 사고의 재발 방지.
    """
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=LOOKBACK_DAYS)
    result = await get_daily_ohlcv(ticker, start, end, adjusted=True)
    dates = [str(bar["timestamp"])[:10] for bar in result["bars"]]
    closes = [float(bar["close"]) for bar in result["bars"]]
    return dates, closes


def last_52_weeks(dates: list[str], closes: list[float], reference: date) -> tuple[list[str], list[float]]:
    """기준일로부터 365일 안의 일봉만 남긴다(52주 고점 계산용)."""
    cutoff = (reference - timedelta(days=HIGH_WINDOW_DAYS)).isoformat()
    pairs = [(day, close) for day, close in zip(dates, closes) if day >= cutoff]
    return [day for day, _ in pairs], [close for _, close in pairs]


def nyse_sessions_after(last_bar: str, reference: date) -> int:
    """마지막 일봉 다음 날부터 기준일(뉴욕 날짜)까지의 NYSE 거래일 수. 기준일 당일 장은 아직 진행 중일 수 있다."""
    start = date.fromisoformat(last_bar[:10]) + timedelta(days=1)
    if start > reference:
        return 0
    try:  # pandas_market_calendars 로딩은 실제 판정 때만
        from .market_calendar import _NYSE
    except ImportError:
        from market_calendar import _NYSE  # type: ignore
    return len(_NYSE.valid_days(start_date=start.isoformat(), end_date=reference.isoformat()))


def freshness(series: dict[str, tuple[list[str], list[float]]], reference: date, sessions_after=nyse_sessions_after) -> dict:
    """종목별 마지막 일봉 날짜와 오래된 종목. as_of는 네 종목 중 가장 오래된 마지막 일봉 날짜다."""
    data_dates = {SIGNAL_TICKERS[key]: dates[-1] for key, (dates, _closes) in series.items() if dates}
    stale = sorted(ticker for ticker, last in data_dates.items() if sessions_after(last, reference) > MAX_MISSING_SESSIONS)
    return {"as_of": min(data_dates.values()) if data_dates else None, "data_dates": data_dates,
            "stale": bool(stale), "stale_tickers": stale}


async def fetch_market_signals(policy: dict = DEFAULT_LEVERAGE_DCA_POLICY, *, now: datetime | None = None) -> dict:
    """S&P/VIX/UPRO/SOXL 일봉으로 매수 중지·재개 판정에 쓰는 신호를 계산한다(낙폭은 52주 고점 대비)."""
    errors: list[str] = []
    series: dict[str, tuple[list[str], list[float]]] = {}
    for key, ticker in SIGNAL_TICKERS.items():
        try:
            series[key] = await _series(ticker)
        except MarketDataError as exc:
            errors.append(f"{ticker}: {exc}")
            series[key] = ([], [])
    reference = (now or datetime.now(_NEW_YORK)).astimezone(_NEW_YORK).date()
    if not all(series[key][1] for key in SIGNAL_TICKERS):
        return {"available": False, "as_of": None, "checked_on": reference.isoformat(), "errors": errors}
    fresh = freshness(series, reference)
    if fresh["stale"]:
        errors.append("오래된 시세: " + ", ".join(f"{ticker} 마지막 일봉 {fresh['data_dates'][ticker]}" for ticker in fresh["stale_tickers"]))
    spx_dates, spx_closes = series["spx"]
    high_dates, high_closes = last_52_weeks(spx_dates, spx_closes, reference)
    return {
        "available": True,
        **fresh,
        "checked_on": reference.isoformat(),
        "spx_drawdown_pct": compute_drawdown_from_high_pct(high_closes),
        "vix": series["vix"][1][-1],
        "soxl_drawdown_pct": compute_drawdown_from_high_pct(last_52_weeks(*series["soxl"], reference)[1]),
        "upro_drawdown_pct": compute_drawdown_from_high_pct(last_52_weeks(*series["upro"], reference)[1]),
        "spx_above_ma_consecutive_days": compute_ma_above_streak(spx_closes, policy["resume_ma_days"]),
        "spx_drawdown_days": compute_days_since_high(high_dates, high_closes),
        "spx_dates": high_dates, "spx_closes": high_closes,
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
