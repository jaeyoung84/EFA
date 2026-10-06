"""레버리지 DCA 신호(2026-10-06): 판정마다 새 시세, 오래된 시세 판정 보류, 52주 고점, 실제 실행 기록 보존."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.backend import leverage_dca_signals as signals_module
from app.backend.db import get_db
from app.backend.routers import strategy


def test_nyse_sessions_after_counts_trading_days_only():
    # 2026-09-18(금) 이후 10/6(화)까지: 9/21~10/2 열흘 + 10/5·10/6 = 12거래일
    assert signals_module.nyse_sessions_after("2026-09-18", date(2026, 10, 6)) == 12
    assert signals_module.nyse_sessions_after("2026-10-02", date(2026, 10, 5)) == 1   # 주말 건너 월요일 장 진행 중
    assert signals_module.nyse_sessions_after("2026-10-02", date(2026, 10, 6)) == 2
    assert signals_module.nyse_sessions_after("2026-10-06", date(2026, 10, 6)) == 0


def test_freshness_reports_oldest_bar_and_stale_tickers():
    series = {"spx": (["2026-10-05"], [1.0]), "vix": (["2026-09-21"], [1.0]),
              "upro": (["2026-10-05"], [1.0]), "soxl": (["2026-09-18"], [1.0])}
    fresh = signals_module.freshness(series, date(2026, 10, 6))
    assert fresh["as_of"] == "2026-09-18"
    assert fresh["stale"] is True and fresh["stale_tickers"] == ["SOXL", "^VIX"]
    assert fresh["data_dates"]["^GSPC"] == "2026-10-05"
    ok = signals_module.freshness({key: (["2026-10-05"], [1.0]) for key in series}, date(2026, 10, 6))
    assert ok["stale"] is False and ok["as_of"] == "2026-10-05"


def _bars(last: date, closes: list[float]) -> dict:
    days = [last - timedelta(days=len(closes) - 1 - index) for index in range(len(closes))]
    return {"bars": [{"timestamp": day.isoformat(), "close": close} for day, close in zip(days, closes)]}


@pytest.mark.asyncio
async def test_signals_fetch_fresh_data_and_use_a_52_week_high(monkeypatch):
    calls = []
    last = date(2026, 10, 5)

    async def fake_ohlcv(ticker, start, end, *, adjusted=True, prefer_cached=False):
        calls.append((ticker, prefer_cached))
        closes = [100.0] * 400
        if ticker == "SOXL":
            closes[0] = 500.0          # 395일 전 고점: 52주 밖이라 무시
            closes[200] = 300.0        # 52주 안 고점
            closes[-1] = 164.0
        return _bars(last, closes)

    monkeypatch.setattr(signals_module, "get_daily_ohlcv", fake_ohlcv)
    result = await signals_module.fetch_market_signals(now=datetime(2026, 10, 6, 19, 40, tzinfo=timezone.utc))
    assert all(prefer_cached is False for _ticker, prefer_cached in calls)       # 저장본 우선 조회를 쓰지 않음
    assert result["soxl_drawdown_pct"] == round((164 / 300 - 1) * 100, 4)          # -45.33%, 400일 고점(500) 아님
    assert result["stale"] is False and result["as_of"] == "2026-10-05"
    assert min(result["spx_dates"]) >= "2025-10-06"                               # 예비비 판정도 52주 구간


@pytest.mark.asyncio
async def test_signals_mark_stale_when_a_ticker_stopped_updating(monkeypatch):
    async def fake_ohlcv(ticker, start, end, *, adjusted=True, prefer_cached=False):
        return _bars(date(2026, 9, 18) if ticker == "SOXL" else date(2026, 10, 5), [100.0] * 300)

    monkeypatch.setattr(signals_module, "get_daily_ohlcv", fake_ohlcv)
    result = await signals_module.fetch_market_signals(now=datetime(2026, 10, 6, 19, 40, tzinfo=timezone.utc))
    assert result["available"] is True and result["stale"] is True
    assert result["stale_tickers"] == ["SOXL"] and result["as_of"] == "2026-09-18"
    assert any("SOXL 마지막 일봉 2026-09-18" in error for error in result["errors"])


def _signals(**overrides) -> dict:
    return {"available": True, "as_of": "2026-10-05", "stale": False, "stale_tickers": [],
            "data_dates": {"SOXL": "2026-10-05"}, "spx_drawdown_pct": -2.0, "vix": 15.0,
            "soxl_drawdown_pct": -5.0, "upro_drawdown_pct": -2.0, "spx_above_ma_consecutive_days": 5,
            "errors": [], **overrides}


@pytest.mark.asyncio
async def test_stale_signals_hold_the_day_and_real_run_log_is_kept(client, make_user, monkeypatch):
    prices = {"UPRO": 100.0, "SOXL": 20.0, "KRW=X": 1300.0}
    monkeypatch.setattr(strategy, "fetch_quote_cached", lambda ticker: {
        "status": "ok", "price": prices[ticker], "change_pct": 0.0, "quoted_at": "2026-10-05T00:00:00Z"})
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    current = {"signals": _signals(stale=True, stale_tickers=["SOXL"], data_dates={"SOXL": "2026-09-18"})}

    async def fake_signals():
        return current["signals"]

    monkeypatch.setattr(strategy, "fetch_market_signals", fake_signals)
    headers = await make_user("lvdca-stale@example.com")
    await client.put("/api/strategy/leverage-dca/config", json={
        "enabled": True, "daily_buy_krw": 5_000_000, "max_position_krw": 100_000_000,
        "sell_net_profit_target_krw": 5_000_000, "alpaca_enabled": False,
    }, headers=headers)
    user = await get_db().users.find_one({"email": "lvdca-stale@example.com"})

    stale = (await client.post("/api/strategy/leverage-dca/run-now", headers=headers)).json()["result"]
    assert stale["status"] == "signals_stale" and stale["stale_tickers"] == ["SOXL"]
    config = await get_db().leverage_dca_configs.find_one({"user_id": user["_id"]})
    assert config.get("last_run_date") is None                                  # 오늘 실행을 선점하지 않음
    assert await get_db().paper_fills.count_documents({"user_id": user["_id"]}) == 0

    current["signals"] = _signals()
    bought = (await client.post("/api/strategy/leverage-dca/run-now", headers=headers)).json()["result"]
    assert bought["status"] == "bought"
    again = (await client.post("/api/strategy/leverage-dca/run-now", headers=headers)).json()["result"]
    assert again["status"] == "already_run_today"
    log = await get_db().leverage_dca_daily_logs.find_one({"user_id": user["_id"]})
    assert log["result"]["status"] == "bought"                                  # 재확인이 실제 판정 기록을 덮어쓰지 않음
    assert log.get("last_checked_at") is not None
