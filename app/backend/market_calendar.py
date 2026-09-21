"""NYSE 개장 캘린더로 장마감 전후 정밀 실행 시각을 계산한다.

서머타임(EDT/EST) 전환은 ``zoneinfo``의 America/New_York 규칙을 그대로 쓰므로
별도 분기 없이 자동 반영된다. 공휴일·휴장일은 ``pandas_market_calendars``의
NYSE 캘린더로 판정한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

_KST = ZoneInfo("Asia/Seoul")
_NYSE = mcal.get_calendar("NYSE")


def _kst_now() -> datetime:
    return datetime.now(_KST)


def get_market_close_kst(for_date=None):
    """지정한 날짜(KST 기준, 기본 오늘)의 NYSE 정규장 마감 시각을 KST로 반환한다.

    해당일이 휴장일이면 None을 반환한다.
    """
    day = for_date or _kst_now().date()
    schedule = _NYSE.schedule(start_date=str(day), end_date=str(day))
    if schedule.empty:
        return None
    close_utc = schedule.iloc[0]["market_close"]
    return close_utc.tz_convert(_KST)


def is_us_market_open_today(for_date=None) -> bool:
    return get_market_close_kst(for_date) is not None


def get_buy_time_kst(for_date=None, *, minutes_before_close: int = 20):
    close = get_market_close_kst(for_date)
    return None if close is None else close - timedelta(minutes=minutes_before_close)


def get_sell_check_time_kst(for_date=None, *, minutes_after_close: int = 30):
    close = get_market_close_kst(for_date)
    return None if close is None else close + timedelta(minutes=minutes_after_close)


def today_kst_iso() -> str:
    return _kst_now().date().isoformat()


def is_within_execution_window(*, now=None) -> dict:
    """오늘 매수 실행 시각(장마감 20분 전)이 이미 지났고 장중(마감 전)인지 판정한다.

    반환값의 ``ready``가 True면 지금 실행해도 되는 시각이라는 뜻이며, 실제 실행
    여부는 호출부의 1일 1회 가드(``last_run_date``)가 최종 결정한다.
    """
    now = now or _kst_now()
    close = get_market_close_kst(now.date())
    if close is None:
        return {"ready": False, "reason": "오늘은 NYSE 휴장일입니다.", "market_close_kst": None, "buy_time_kst": None}
    buy_time = close - timedelta(minutes=20)
    ready = buy_time <= now < close
    return {
        "ready": ready, "reason": None if ready else ("장마감 20분 전이 아직 되지 않았습니다." if now < buy_time else "이미 장이 마감되었습니다."),
        "market_close_kst": close.isoformat(), "buy_time_kst": buy_time.isoformat(),
    }
