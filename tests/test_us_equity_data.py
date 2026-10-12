"""EINT-036 개별주 일봉: 구성 이력 저장·재사용, 편출·폐지 포함 백필, 원시 불변, 기업행위 계수 재수집, 품질 표시, 읽기 API."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.backend import us_equity_data as eq
from app.backend.db import get_db
from tests.test_us_universe import ALIASES, CHANGES, CONSTITUENTS

AFTER_CLOSE = datetime(2026, 10, 10, 1, 0, tzinfo=timezone.utc)
LAST_BAR = {"DDD": "2024-02-29", "EEE": "2020-05-29"}       # 편출 뒤 상장폐지된 종목
NO_BARS = {"GGG"}                                            # 공급자에 일봉이 없는 종목 -> history_gap


def constituents_html():
    extra = "".join(f"<tr><td>P{i:03d}</td><td>p</td><td>x</td><td>x</td><td>x</td><td>2010-01-01</td><td>1</td><td>1</td></tr>" for i in range(450))
    return CONSTITUENTS.replace("</table>", extra + "</table>")


def fake_page(title):
    return (111, constituents_html()) if title == eq.universe.CONSTITUENTS_PAGE else (222, CHANGES)


def fake_changes(symbols):
    return [{"id": "n1", "old_symbol": "OLDCO", "new_symbol": "NEWCO", "process_date": "2023-05-01"}] if "OLDCO" in symbols else []


def fake_fetch(raw_override=None, total_scale=None):
    raw_override, total_scale = raw_override or {}, total_scale or {}

    def fetch(symbols, start, end, adjustment):
        out = {}
        for symbol in symbols:
            n = sum(map(ord, symbol)) % 20
            if symbol in NO_BARS or symbol.startswith("P"):
                continue
            bars, day = [], max(start, date(2016, 1, 4))
            last = date.fromisoformat(LAST_BAR.get(symbol, "2099-01-01"))
            while day < end and day <= last:
                if day.weekday() < 5:
                    close = 50 + n + (day.toordinal() % 11)
                    if adjustment == "split":
                        close *= 0.5
                    elif adjustment == "all":
                        close *= total_scale.get(symbol, 0.9)
                    close = raw_override.get((symbol, day.isoformat()), close) if adjustment == "raw" else close
                    bars.append({"t": f"{day.isoformat()}T04:00:00Z", "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 10})
                day += timedelta(days=1)
            out[symbol] = bars
        return out
    return fetch


def verified(symbol, rows, through):
    return {"status": "verified", "overlap_count": 60, "max_deviation_pct": 0.0, "verified": True}


async def membership():
    return await eq.refresh_membership(fetch_page=fake_page, fetch_changes=fake_changes, month_ends=["2016-01-29", "2026-09-30"])


@pytest.mark.asyncio
async def test_membership_is_stored_once_per_wikipedia_revision():
    first = await membership()
    again = await membership()
    assert first["_id"] == again["_id"] == "111:222" and await get_db()[eq.MEMBERSHIP].count_documents({}) == 1
    assert first["aliases"] == {"OLDCO": [["2023-05-01", "NEWCO"]]} and first["anomalies"] == []
    assert {"DDD", "EEE", "FFF", "GGG", "NEWCO"} <= set(first["symbols"]) and "OLDCO" not in first["symbols"]


@pytest.mark.asyncio
async def test_backfill_includes_delisted_names_and_flags_gaps(monkeypatch):
    monkeypatch.setattr(eq.universe, "nyse_month_ends", lambda start, end: ["2019-12-31", "2024-01-31"])
    result = await eq.collect_us_equity_data(now=AFTER_CLOSE, fetch=fake_fetch(), crosscheck=verified, membership_loader=membership)
    assert result["last_session"] == "2026-10-09"
    db = get_db()
    ddd = await db[eq.SYMBOLS].find_one({"_id": "DDD"})
    assert ddd["first"] == "2016-01-04" and ddd["last"] == "2024-02-29"         # 폐지 종목도 마지막 거래일까지
    doc = await db[eq.BARS].find_one({"_id": "AAA:2020"})
    day = "2020-03-02"
    assert doc["split"][day] == 0.5 and abs(doc["total"][day] - 0.9) < 1e-9 and doc["days"][day]["run"] == result["run_id"]
    run = await db[eq.RUNS].find_one({"run_id": result["run_id"]})
    gaps = {(g["symbol"], g["kind"]) for g in run["quality"]["history_gaps"]}
    assert ("GGG", "no_bars") in gaps and not any(symbol == "DDD" for symbol, _ in gaps)
    assert run["quality"]["coverage_review"] == ["2019-12-31", "2024-01-31"]    # P000.. 종목은 일봉이 없어 coverage 미달
    assert run["status"] == "partial" and run["backfill"]["symbols"] == run["membership"]["symbols"]
    assert len(run["policy_sha256"]) == 64


@pytest.mark.asyncio
async def test_raw_is_immutable_and_new_corporate_action_refetches_factors(monkeypatch):
    monkeypatch.setattr(eq.universe, "nyse_month_ends", lambda start, end: [])
    await eq.collect_us_equity_data(now=AFTER_CLOSE, fetch=fake_fetch(), crosscheck=verified, membership_loader=membership)
    db = get_db()
    before = await db[eq.BARS].find_one({"_id": "AAA:2026"})
    revised = fake_fetch(raw_override={("AAA", "2026-10-08"): 2.0}, total_scale={"BBB": 0.8})
    second = await eq.collect_us_equity_data(now=AFTER_CLOSE, fetch=revised, crosscheck=verified, membership_loader=membership)
    after = await db[eq.BARS].find_one({"_id": "AAA:2026"})
    assert after["days"]["2026-10-08"] == before["days"]["2026-10-08"]
    run = await db[eq.RUNS].find_one({"run_id": second["run_id"]})
    assert run["raw_revisions"] == 1 and run["raw_revision_samples"][0]["symbol"] == "AAA"
    assert run["recent"]["refactored"] == ["BBB"] and run["backfill"]["symbols"] == 0
    old = await db[eq.BARS].find_one({"_id": "BBB:2017"})
    assert abs(old["total"]["2017-03-01"] - 0.8) < 1e-9                       # 과거 계수까지 다시 받음


@pytest.mark.asyncio
async def test_provider_failure_and_status_api(client, monkeypatch):
    def broken(*args):
        raise eq.UsMarketDataError("Alpaca 시장데이터 오류 429")
    result = await eq.collect_us_equity_data(now=AFTER_CLOSE, fetch=broken, crosscheck=verified, membership_loader=membership)
    assert result["status"] == "failed"
    status = await eq.us_equity_status()
    assert status["available"] and status["errors"][0].startswith("bars UsMarketDataError")
    assert (await client.get("/api/market/us-data-status")).status_code == 401


def test_crosscheck_sample_is_fixed_by_rule():
    symbols = [f"S{i}" for i in range(100)]
    assert eq.crosscheck_sample(symbols) == eq.crosscheck_sample(list(reversed(symbols))) and len(eq.crosscheck_sample(symbols)) == 20
