"""슈퍼트렌드 독립 차트 계산·API 회귀 테스트(합성 데이터).

설계 문서(docs/handoff/codex/2026-10-19-supertrend-chart-design.md) 8절의 필수 검증:
초기값·전환·동률, 직전 확정 주봉 정렬(주중·금요일·휴장 주), 주봉 상승 중 SELL 보존,
이력 추가 불변성, 미확정 봉, 결측을 0으로 오인하지 않기.
"""
import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.backend import supertrend_chart as stc
from app.backend.routers import charts as charts_router

FAR_FUTURE = datetime(2100, 1, 1, tzinfo=ZoneInfo("Asia/Seoul"))


def _weekdays(start: date, count: int, skip: set[date] = frozenset()) -> list[str]:
    out, day = [], start
    while len(out) < count:
        if day.weekday() < 5 and day not in skip:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _bars(closes: list[float], days: list[str] | None = None, spread: float = 1.0, volume: float | None = 1000.0) -> list[dict]:
    days = days or _weekdays(date(2024, 1, 1), len(closes))
    out = []
    for i, (day, close) in enumerate(zip(days, closes)):
        open_ = closes[i - 1] if i else close
        out.append({"timestamp": day, "open": open_, "high": max(open_, close) + spread,
                    "low": min(open_, close) - spread, "close": close, "volume": volume})
    return out


def _pine_reference(highs, lows, closes, length, factor):
    """Pine 공식 스크립트를 na=NaN 그대로 옮긴 대조용 구현."""
    nan = float("nan")
    n = len(closes)
    tr = [highs[0] - lows[0]] + [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, n)]
    atr = [nan] * n
    for i in range(n):
        if i == length - 1:
            atr[i] = sum(tr[:length]) / length
        elif i >= length:
            atr[i] = (tr[i] + (length - 1) * atr[i - 1]) / length
    nz = lambda x: 0.0 if math.isnan(x) else x
    upper = [nan] * n; lower = [nan] * n; st = [nan] * n; dirs = [None] * n
    for i in range(n):
        src = (highs[i] + lows[i]) / 2
        up, lo = src + factor * atr[i], src - factor * atr[i]
        p_lo, p_up = nz(lower[i - 1]) if i else 0.0, nz(upper[i - 1]) if i else 0.0
        c1 = closes[i - 1] if i else nan
        lo = lo if (lo > p_lo or c1 < p_lo) else p_lo
        up = up if (up < p_up or c1 > p_up) else p_up
        lower[i], upper[i] = lo, up
        if i == 0 or math.isnan(atr[i - 1]):
            d = 1
        elif st[i - 1] == p_up:
            d = -1 if closes[i] > up else 1
        else:
            d = 1 if closes[i] < lo else -1
        st[i] = lo if d == -1 else up
        dirs[i] = d
    return st, dirs, atr


def test_supertrend_matches_literal_pine_transcription_on_random_walk():
    rng = random.Random(7)
    closes = [100.0]
    for _ in range(400):
        closes.append(max(1.0, closes[-1] * (1 + rng.gauss(0, 0.02))))
    bars = _bars(closes, spread=0.8)
    highs = [b["high"] for b in bars]; lows = [b["low"] for b in bars]
    ours = stc.supertrend(highs, lows, closes, atr_length=10, factor=3.0)
    ref_st, ref_dir, ref_atr = _pine_reference(highs, lows, closes, 10, 3.0)
    for i in range(len(closes)):
        if math.isnan(ref_atr[i]):
            assert ours["line"][i] is None and ours["direction"][i] is None
        else:
            assert ours["line"][i] == pytest.approx(ref_st[i]) and ours["direction"][i] == ref_dir[i]
    assert len({d for d in ours["direction"] if d is not None}) == 2  # 상승·하락이 모두 나오는 표본


def test_warmup_starts_down_on_upper_band_then_flips_up():
    closes = [100 + i for i in range(30)]
    bars = _bars(closes)
    st = stc.supertrend([b["high"] for b in bars], [b["low"] for b in bars], closes, atr_length=10, factor=3.0)
    assert all(d is None for d in st["direction"][:9])
    assert st["direction"][9] == 1 and st["line"][9] == pytest.approx(st["upper"][9])
    assert st["direction"][-1] == -1


def test_flips_require_previous_direction_and_close_beyond_band():
    closes = [100.0] * 15 + [100 + 3 * i for i in range(1, 25)] + [172 - 6 * i for i in range(1, 20)]
    result = stc.compute_chart(_bars(closes), ticker="AAA", now=FAR_FUTURE)
    types = [flip["type"] for flip in result["flips"]]
    assert "up" in types and "down" in types
    for flip in result["flips"]:
        row = next(r for r in result["bars"] if r["date"] == flip["date"])
        assert row["prev_dir"] is not None and row["prev_dir"] != row["dir"]


def test_weekly_direction_uses_only_previous_completed_week():
    days = _weekdays(date(2024, 1, 1), 300)
    rng = random.Random(3)
    closes = [100.0]
    for _ in range(299):
        closes.append(closes[-1] * (1 + rng.gauss(0.001, 0.02)))
    result = stc.compute_chart(_bars(closes, days), ticker="AAA", now=FAR_FUTURE)
    weeks = stc.weekly_bars(days, *[[b[k] for b in _bars(closes, days)] for k in ("open", "high", "low", "close")])
    wst = stc.supertrend([w["high"] for w in weeks], [w["low"] for w in weeks], [w["close"] for w in weeks])
    for row in result["bars"]:
        w = next(i for i, wk in enumerate(weeks) if wk["key"] == stc.week_key(row["date"]))
        expected = wst["direction"][w - 1] if w else None
        assert row["weekly_dir"] == expected
        if row["weekly_ref_end"]:
            assert row["weekly_ref_end"] < row["date"]  # 같은 주의 진행 중인 값을 쓰지 않음
    # 월요일과 금요일 모두 같은 직전 주를 본다.
    monday = next(r for r in result["bars"][100:] if date.fromisoformat(r["date"]).weekday() == 0)
    friday = next(r for r in result["bars"] if r["date"] > monday["date"] and date.fromisoformat(r["date"]).weekday() == 4)
    assert monday["weekly_ref_end"] == friday["weekly_ref_end"]


def test_holiday_week_groups_only_trading_days_and_maps_next_week_to_it():
    holiday_week = {date(2024, 5, 6) + timedelta(days=k) for k in range(4)}  # 월~목 휴장, 금요일만 거래
    days = _weekdays(date(2024, 1, 1), 120, skip=holiday_week)
    result = stc.compute_chart(_bars([100 + i * 0.5 for i in range(120)], days), ticker="AAA", now=FAR_FUTURE)
    next_monday = next(r for r in result["bars"] if r["date"] == "2024-05-13")
    assert next_monday["weekly_ref_start"] == "2024-05-10" == next_monday["weekly_ref_end"]


def test_sell_is_kept_when_weekly_trend_is_up():
    closes = [100 * 1.004 ** i for i in range(200)] + [100 * 1.004 ** 199 * 0.97 ** k for k in range(1, 8)]
    result = stc.compute_chart(_bars(closes, spread=0.3), ticker="AAA", now=FAR_FUTURE)
    sells = [f for f in result["flips"] if f["type"] == "down"]
    assert sells and sells[-1]["weekly_dir"] == -1 and sells[-1]["weekly_aligned"] is False


def test_appending_history_does_not_change_existing_rows():
    rng = random.Random(11)
    closes = [100.0]
    for _ in range(259):
        closes.append(closes[-1] * (1 + rng.gauss(0, 0.02)))
    bars = _bars(closes)
    short = stc.compute_chart(bars[:200], ticker="AAA", now=FAR_FUTURE)["bars"]
    full = stc.compute_chart(bars, ticker="AAA", now=FAR_FUTURE)["bars"]
    assert short == full[:200]


def test_unconfirmed_last_bar_has_no_flip_and_marks_intraday_break():
    closes = [100 + i for i in range(40)] + [100]
    days = _weekdays(date(2024, 1, 1), 41)
    last = date.fromisoformat(days[-1])
    during_session = datetime(last.year, last.month, last.day, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    live = stc.compute_chart(_bars(closes, days), ticker="005930.KS", now=during_session)
    assert live["last_confirmed"] is False and live["bars"][-1]["flip"] is None and live["bars"][-1]["intraday_break"]
    assert live["bars"][-1]["volume_ratio"] is None and live["bars"][-2]["volume_ratio"] is not None
    assert all(f["date"] != days[-1] for f in live["flips"])
    after = stc.compute_chart(_bars(closes, days), ticker="005930.KS", now=during_session.replace(hour=16, minute=5))
    assert after["last_confirmed"] and after["bars"][-1]["flip"] == "down"


def test_confirmation_uses_exchange_session_close():
    kst = ZoneInfo("Asia/Seoul")
    assert not stc.last_bar_confirmed("005930.KS", "2026-10-19", datetime(2026, 10, 19, 15, 45, tzinfo=kst))
    assert stc.last_bar_confirmed("005930.KS", "2026-10-19", datetime(2026, 10, 19, 16, 0, tzinfo=kst))
    assert stc.last_bar_confirmed("005930.KS", "2026-10-16", datetime(2026, 10, 19, 9, 0, tzinfo=kst))
    ny = ZoneInfo("America/New_York")
    assert not stc.last_bar_confirmed("AAPL", "2026-10-19", datetime(2026, 10, 19, 16, 10, tzinfo=ny))
    assert stc.last_bar_confirmed("AAPL", "2026-10-19", datetime(2026, 10, 19, 16, 30, tzinfo=ny))


def test_volume_ratio_excludes_today_and_missing_volume_is_not_zero():
    bars = _bars([100 + (i % 3) for i in range(30)])
    for i, bar in enumerate(bars):
        bar["volume"] = 1000.0 if i < 29 else 3000.0
    rows = stc.compute_chart(bars, ticker="AAA", now=FAR_FUTURE)["bars"]
    assert rows[-1]["volume_ratio"] == pytest.approx(3.0)
    assert rows[19]["volume_ratio"] is None
    no_volume = stc.compute_chart(_bars([100 + i for i in range(30)], volume=None), ticker="^KS11", now=FAR_FUTURE)["bars"]
    assert all(row["volume_ratio"] is None for row in no_volume)


def test_levels_distance_and_adx_are_defined_on_prior_bars():
    rng = random.Random(5)
    closes = [100.0]
    for _ in range(149):
        closes.append(closes[-1] * (1 + rng.gauss(0, 0.015)))
    bars = _bars(closes)
    rows = stc.compute_chart(bars, ticker="AAA", now=FAR_FUTURE)["bars"]
    row = rows[100]
    assert row["high20"] == pytest.approx(max(b["high"] for b in bars[80:100]))
    assert row["low60"] == pytest.approx(min(b["low"] for b in bars[40:100]))
    expected = (row["c"] - row["st"]) / row["c"] * 100 if row["dir"] == -1 else (row["st"] - row["c"]) / row["c"] * 100
    assert row["distance_pct"] == pytest.approx(expected, abs=1e-3)
    assert all(0 <= r["adx"] <= 100 for r in rows if r["adx"] is not None)
    assert rows[26]["adx"] is None and rows[28]["adx"] is not None  # DI 14 + ADX 14 평활 준비 기간


@pytest.mark.asyncio
async def test_api_returns_rows_and_503_without_data(client, monkeypatch):
    bars = _bars([100 + i * 0.3 for i in range(120)])

    async def fake_load(ticker):
        if ticker == "^GSPC":
            raise RuntimeError("index down")
        return {"bars": bars, "source": "test", "dataset_version": "v1", "quality": {"status": "passed"}}

    monkeypatch.setattr(charts_router, "_load", fake_load)
    response = await client.get("/api/chart/supertrend?ticker=aaa&atr=10&factor=3")
    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AAA" and len(body["bars"]) == 120 and body["benchmark"]["error"]
    assert body["data"]["source"] == "test"

    async def no_data(_ticker):
        raise RuntimeError("provider down")

    monkeypatch.setattr(charts_router, "_load", no_data)
    response = await client.get("/api/chart/supertrend?ticker=AAA")
    assert response.status_code == 503 and "provider down" in response.json()["detail"]
    assert (await client.get("/api/chart/supertrend?ticker=AAA&atr=0")).status_code == 422


def test_reference_summary_uses_last_confirmed_bar():
    closes = [100 + i for i in range(40)] + [100]
    days = _weekdays(date(2024, 1, 1), 41)
    last = date.fromisoformat(days[-1])
    during_session = datetime(last.year, last.month, last.day, 11, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    summary = charts_router.reference_summary("005930.KS", _bars(closes, days), now=during_session)
    assert summary["status"] == "ok" and summary["as_of"] == days[-2] and summary["dir"] == -1
    assert summary["flip_today"] is None and summary["intraday_break"] is True
    short = charts_router.reference_summary("005930.KS", _bars([100.0] * 5, days[:5]), now=FAR_FUTURE)
    assert short["status"] == "insufficient"


@pytest.mark.asyncio
async def test_reference_api_batches_and_reports_unavailable(client, monkeypatch):
    bars = _bars([100 + i * 0.3 for i in range(120)])
    calls = []

    async def fake_load(ticker):
        calls.append(ticker)
        if ticker == "000000.KS":
            raise RuntimeError("no data")
        return {"bars": bars}

    monkeypatch.setattr(charts_router, "_load", fake_load)
    monkeypatch.setattr(charts_router, "_ref_cache", {})
    response = await client.get("/api/chart/supertrend-reference?tickers=005930.KS,000000.KS,bad ticker!,005930.KS")
    assert response.status_code == 200
    items = response.json()["items"]
    assert set(items) == {"005930.KS", "000000.KS"}
    assert items["005930.KS"]["status"] == "ok" and items["000000.KS"]["status"] == "unavailable"
    await client.get("/api/chart/supertrend-reference?tickers=005930.KS")
    assert calls.count("005930.KS") == 1  # 캐시 재사용
