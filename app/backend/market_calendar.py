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


def _candidate_session_dates(now):
    """NYSE 정규장 마감(16:00 ET)을 KST로 바꾸면 항상 미국 거래일의 다음 KST 날짜가 된다.

    그래서 KST 새벽 시간대의 "지금 막 마감된 장"은 거래일이 ``now.date() - 1``인
    세션이다. 이를 무시하고 ``now.date()``의 세션만 보면 마감 20분 전~마감 구간이
    KST 자정 이후에는 영영 감지되지 않는다 — 항상 하루 뒤(다음 KST 날) 마감으로
    계산돼 실행 창이 열리지 않는다.
    """
    return (now.date() - timedelta(days=1), now.date())


def is_within_execution_window(*, now=None) -> dict:
    """오늘 매수 실행 시각(장마감 20분 전)이 이미 지났고 장중(마감 전)인지 판정한다.

    반환값의 ``ready``가 True면 지금 실행해도 되는 시각이라는 뜻이며, 실제 실행
    여부는 호출부의 1일 1회 가드(``last_run_date``)가 최종 결정한다.
    """
    now = now or _kst_now()
    checked: list[tuple] = []
    for session_date in _candidate_session_dates(now):
        close = get_market_close_kst(session_date)
        if close is None:
            continue
        buy_time = close - timedelta(minutes=20)
        if buy_time <= now < close:
            return {"ready": True, "reason": None, "market_close_kst": close.isoformat(), "buy_time_kst": buy_time.isoformat()}
        checked.append((close, buy_time))
    if not checked:
        return {"ready": False, "reason": "오늘은 NYSE 휴장일입니다.", "market_close_kst": None, "buy_time_kst": None}
    upcoming = [pair for pair in checked if pair[0] > now]
    if upcoming:
        close, buy_time = min(upcoming, key=lambda pair: pair[0])
        reason = "장마감 20분 전이 아직 되지 않았습니다."
    else:
        close, buy_time = max(checked, key=lambda pair: pair[0])
        reason = "이미 장이 마감되었습니다."
    return {"ready": False, "reason": reason, "market_close_kst": close.isoformat(), "buy_time_kst": buy_time.isoformat()}


def get_next_execution_schedule(*, now=None) -> dict:
    """표시용으로 가장 가까운(진행 중이거나 다가오는) 실행 창의 매수·마감·매도 시각을 반환한다."""
    now = now or _kst_now()
    for session_date in _candidate_session_dates(now):
        close = get_market_close_kst(session_date)
        if close is None:
            continue
        sell_check = close + timedelta(minutes=30)
        if now < sell_check:
            return {"market_open_today": True, "buy_time_kst": (close - timedelta(minutes=20)).isoformat(),
                    "market_close_kst": close.isoformat(), "sell_check_time_kst": sell_check.isoformat()}
    return {"market_open_today": False, "buy_time_kst": None, "market_close_kst": None, "sell_check_time_kst": None}
