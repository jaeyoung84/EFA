"""EINT-035 미국 일봉 경로: 완성 세션만 저장, 원시 일봉 불변·사후 수정 기록, 조정 해시, 미국 섹터 순환, 읽기 전용 API."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.backend import us_market_data as us
from app.backend.db import get_db

AFTER_CLOSE = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)      # 10/9(금) 정규장 마감 뒤
DURING_SESSION = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)   # 10/9 장중


def _weekdays(end: date, count: int) -> list[date]:
    days, current = [], end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return days[::-1]


def fake_fetch(overrides=None, drift=None):
    overrides, drift = overrides or {}, drift or {}

    def fetch(symbols, start, end, adjustment):
        out = {}
        for index, symbol in enumerate(symbols):
            slope = drift.get(symbol, 0.02 * (index - 6))
            bars = []
            for i, day in enumerate(_weekdays(date(2026, 10, 9), 320)):
                close = 100 + slope * i + (i % 7) * 0.3
                if adjustment == "all":
                    close *= 0.98
                close = overrides.get((symbol, day.isoformat(), adjustment), close)
                bars.append({"t": f"{day.isoformat()}T04:00:00Z", "o": close, "h": close * 1.01, "l": close * 0.99, "c": close, "v": 1000})
            out[symbol] = bars
        return out
    return fetch


def verified(symbol, rows, through):
    return {"status": "verified", "overlap_count": 60, "max_deviation_pct": 0.01, "verified": True}


@pytest.mark.asyncio
async def test_collect_stores_completed_sessions_and_builds_rotation():
    result = await us.collect_us_market_data(now=AFTER_CLOSE, fetch=fake_fetch(), crosscheck=verified)
    assert result["status"] == "ok" and result["last_session"] == "2026-10-09"
    db = get_db()
    assert await db[us.BARS].count_documents({"symbol": "SPY"}) == 320
    bar = await db[us.BARS].find_one({"_id": "SPY:2026-10-09"})
    assert bar["source"] == "alpaca-sip" and abs(bar["adj"]["close"] / bar["raw"]["close"] - 0.98) < 1e-9
    run = await db[us.RUNS].find_one({"run_id": result["run_id"]})
    assert len(run["adjusted_sha256"]) == 64 and run["stale_symbols"] == [] and run["rotation"]["sectors"] == 12

    data = await us.read_us_sector_rotation()
    assert data["available"] and data["market"] == "us" and data["as_of"] == "2026-10-09"
    assert data["benchmark"]["symbol"] == "SPY" and {s["symbol"] for s in data["sectors"]} >= {"XLK", "SMH", "XLRE"}
    assert data["smoothing_weeks"] == {"short": 4, "long": 13, "momentum": 4}
    assert "매수·매도 신호" in data["disclaimer"]


@pytest.mark.asyncio
async def test_raw_bars_are_immutable_and_revisions_are_recorded():
    await us.collect_us_market_data(now=AFTER_CLOSE, fetch=fake_fetch(), crosscheck=verified)
    db = get_db()
    before = await db[us.BARS].find_one({"_id": "XLK:2026-10-01"})
    revised = fake_fetch({("XLK", "2026-10-01", "raw"): 1.0, ("XLK", "2026-10-01", "all"): 2.0})
    result = await us.collect_us_market_data(now=AFTER_CLOSE, fetch=revised, crosscheck=verified)
    after = await db[us.BARS].find_one({"_id": "XLK:2026-10-01"})
    assert after["raw"] == before["raw"] and after["first_run_id"] == before["first_run_id"]
    assert after["adj"]["close"] == 2.0 and after["adj_run_id"] == result["run_id"]
    run = await db[us.RUNS].find_one({"run_id": result["run_id"]})
    assert run["symbols"]["XLK"]["raw_revisions"] == 1 and run["symbols"]["XLK"]["inserted"] == 0
    assert run["symbols"]["XLK"]["raw_revision_samples"][0]["new_close"] == 1.0


@pytest.mark.asyncio
async def test_unfinished_session_is_not_stored():
    result = await us.collect_us_market_data(now=DURING_SESSION, fetch=fake_fetch(), crosscheck=verified)
    assert result["last_session"] == "2026-10-08"
    assert await get_db()[us.BARS].find_one({"date": "2026-10-09"}) is None


@pytest.mark.asyncio
async def test_provider_failure_is_recorded_without_partial_writes():
    def broken(*args):
        raise us.UsMarketDataError("Alpaca 시장데이터 오류 403")
    result = await us.collect_us_market_data(now=AFTER_CLOSE, fetch=broken, crosscheck=verified)
    assert result["status"] == "failed"
    db = get_db()
    assert await db[us.BARS].count_documents({}) == 0
    assert (await db[us.RUNS].find_one({}))["errors"][0].startswith("UsMarketDataError")
    assert (await us.read_us_sector_rotation())["available"] is False


@pytest.mark.asyncio
async def test_crosscheck_review_marks_run_partial():
    def suspicious(symbol, rows, through):
        return {"status": "review_required" if symbol == "XLE" else "verified", "verified": symbol != "XLE"}
    result = await us.collect_us_market_data(now=AFTER_CLOSE, fetch=fake_fetch(), crosscheck=suspicious)
    run = await get_db()[us.RUNS].find_one({"run_id": result["run_id"]})
    assert result["status"] == "partial" and run["crosscheck_review"] == ["XLE"]


def test_rotation_quadrants_follow_relative_strength():
    days = [d.isoformat() for d in _weekdays(date(2026, 10, 9), 320)]
    closes = {"SPY": {d: 100 + i * 0.05 for i, d in enumerate(days)}}
    for _, _, symbol in us.SECTORS:
        closes[symbol] = {d: 100 + i * 0.05 for i, d in enumerate(days)}
    closes["SMH"] = {d: 100 + i * 0.05 + (i - 250) * 0.6 * (i > 250) for i, d in enumerate(days)}   # 최근 급격히 시장 초과
    report = us.build_us_sector_rotation(closes, "2026-10-09")
    semis = next(s for s in report["sectors"] if s["symbol"] == "SMH")
    assert semis["rs_ratio"] > 100 and semis["quadrant"] in {"leading", "weakening"}
    with pytest.raises(us.UsMarketDataError):
        us.build_us_sector_rotation(closes, "2026-10-12")


@pytest.mark.asyncio
async def test_api_is_read_only_and_status_requires_login(client):
    response = await client.get("/api/market/us-sector-rotation")
    assert response.status_code == 200 and response.json()["available"] is False
    assert (await client.get("/api/market/us-data-status")).status_code == 401
    await us.collect_us_market_data(now=AFTER_CLOSE, fetch=fake_fetch(), crosscheck=verified)
    body = (await client.get("/api/market/us-sector-rotation")).json()
    assert body["available"] and body["as_of"] == "2026-10-09" and "generated_at" in body
