from datetime import datetime
from zoneinfo import ZoneInfo

from app.backend import market_calendar

KST = ZoneInfo("Asia/Seoul")


def test_execution_window_is_ready_right_after_kst_midnight_for_previous_us_session(monkeypatch):
    """NYSE 마감(16:00 ET)을 KST로 바꾸면 항상 미국 거래일의 다음 KST 날짜가 된다.

    KST 자정을 넘긴 새벽 시간대에는 실제로 마감 중인 세션이 ``now.date() - 1``의
    미국 거래일이어야 하며, 이 시각을 놓치면 레버리지 DCA 자동 매수·매도가 하루도
    실행되지 못한다(실서버에서 실제로 발생한 회귀).
    """
    def _fake_close(for_date=None):
        # 2026-09-22(미국 거래일) 세션 마감 = KST 2026-09-23 05:00
        if str(for_date) == "2026-09-22":
            return datetime(2026, 9, 23, 5, 0, tzinfo=KST)
        # 2026-09-23(미국 거래일) 세션 마감 = KST 2026-09-24 05:00
        if str(for_date) == "2026-09-23":
            return datetime(2026, 9, 24, 5, 0, tzinfo=KST)
        return None

    monkeypatch.setattr(market_calendar, "get_market_close_kst", _fake_close)

    # 마감 20분 전(04:40)~마감(05:00) 사이, KST 날짜는 이미 09-23으로 넘어간 상태다.
    now = datetime(2026, 9, 23, 4, 45, tzinfo=KST)
    result = market_calendar.is_within_execution_window(now=now)

    assert result["ready"] is True
    assert result["market_close_kst"] == "2026-09-23T05:00:00+09:00"
    assert result["buy_time_kst"] == "2026-09-23T04:40:00+09:00"


def test_execution_window_not_ready_before_buy_time(monkeypatch):
    def _fake_close(for_date=None):
        if str(for_date) == "2026-09-22":
            return datetime(2026, 9, 23, 5, 0, tzinfo=KST)
        if str(for_date) == "2026-09-23":
            return datetime(2026, 9, 24, 5, 0, tzinfo=KST)
        return None

    monkeypatch.setattr(market_calendar, "get_market_close_kst", _fake_close)

    # 낮 시간대 — 어제(09-22) 세션 마감은 이미 지났고, 다음 실행 창은 오늘 밤(09-23 세션 마감)이다.
    now = datetime(2026, 9, 23, 15, 0, tzinfo=KST)
    result = market_calendar.is_within_execution_window(now=now)

    assert result["ready"] is False
    assert result["reason"] == "장마감 20분 전이 아직 되지 않았습니다."
    assert result["market_close_kst"] == "2026-09-24T05:00:00+09:00"


def test_execution_window_already_closed_when_no_future_session_pending(monkeypatch):
    """다가올 세션이 전혀 없을 때만 '이미 마감' 사유를 보고한다(휴장 전날 등)."""
    def _fake_close(for_date=None):
        if str(for_date) == "2026-09-22":
            return datetime(2026, 9, 23, 5, 0, tzinfo=KST)
        return None  # 09-23은 휴장일이라 다음 세션 정보가 없다고 가정

    monkeypatch.setattr(market_calendar, "get_market_close_kst", _fake_close)

    now = datetime(2026, 9, 23, 15, 0, tzinfo=KST)
    result = market_calendar.is_within_execution_window(now=now)

    assert result["ready"] is False
    assert result["reason"] == "이미 장이 마감되었습니다."
    assert result["market_close_kst"] == "2026-09-23T05:00:00+09:00"


def test_execution_window_not_ready_long_before_close(monkeypatch):
    def _fake_close(for_date=None):
        if str(for_date) == "2026-09-22":
            return datetime(2026, 9, 23, 5, 0, tzinfo=KST)
        if str(for_date) == "2026-09-23":
            return datetime(2026, 9, 24, 5, 0, tzinfo=KST)
        return None

    monkeypatch.setattr(market_calendar, "get_market_close_kst", _fake_close)

    now = datetime(2026, 9, 23, 10, 0, tzinfo=KST)
    result = market_calendar.is_within_execution_window(now=now)

    assert result["ready"] is False
    assert result["reason"] == "장마감 20분 전이 아직 되지 않았습니다."
    assert result["market_close_kst"] == "2026-09-24T05:00:00+09:00"


def test_execution_window_reports_holiday_when_no_session_either_side(monkeypatch):
    monkeypatch.setattr(market_calendar, "get_market_close_kst", lambda for_date=None: None)
    now = datetime(2026, 9, 23, 10, 0, tzinfo=KST)
    result = market_calendar.is_within_execution_window(now=now)
    assert result == {"ready": False, "reason": "오늘은 NYSE 휴장일입니다.", "market_close_kst": None, "buy_time_kst": None}


def test_next_execution_schedule_shows_upcoming_close_during_the_day(monkeypatch):
    def _fake_close(for_date=None):
        if str(for_date) == "2026-09-22":
            return datetime(2026, 9, 23, 5, 0, tzinfo=KST)
        if str(for_date) == "2026-09-23":
            return datetime(2026, 9, 24, 5, 0, tzinfo=KST)
        return None

    monkeypatch.setattr(market_calendar, "get_market_close_kst", _fake_close)

    now = datetime(2026, 9, 23, 15, 0, tzinfo=KST)
    schedule = market_calendar.get_next_execution_schedule(now=now)

    assert schedule["market_open_today"] is True
    assert schedule["market_close_kst"] == "2026-09-24T05:00:00+09:00"
    assert schedule["buy_time_kst"] == "2026-09-24T04:40:00+09:00"
    assert schedule["sell_check_time_kst"] == "2026-09-24T05:30:00+09:00"


def test_next_execution_schedule_still_shows_recent_close_during_sell_check_window(monkeypatch):
    def _fake_close(for_date=None):
        if str(for_date) == "2026-09-22":
            return datetime(2026, 9, 23, 5, 0, tzinfo=KST)
        if str(for_date) == "2026-09-23":
            return datetime(2026, 9, 24, 5, 0, tzinfo=KST)
        return None

    monkeypatch.setattr(market_calendar, "get_market_close_kst", _fake_close)

    # 마감 직후(05:10) — 매도 체크(05:30)는 아직 안 지났다.
    now = datetime(2026, 9, 23, 5, 10, tzinfo=KST)
    schedule = market_calendar.get_next_execution_schedule(now=now)

    assert schedule["market_close_kst"] == "2026-09-23T05:00:00+09:00"
