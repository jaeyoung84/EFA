"""코스피200 선물 주간·야간 시세(한투): 야간 세션 시각, 응답 해석, 30초 캐시, 키 없음."""
from datetime import datetime, timedelta, timezone

import pytest

from app.backend import krx_futures

KST = timezone(timedelta(hours=9))


def test_night_session_hours():
    at = lambda *args: datetime(*args, tzinfo=KST)  # noqa: E731
    assert krx_futures.night_session_open(at(2026, 10, 7, 18, 0))          # 수 18:00
    assert krx_futures.night_session_open(at(2026, 10, 8, 4, 59))          # 목 04:59(수요일 밤 세션)
    assert not krx_futures.night_session_open(at(2026, 10, 8, 5, 0))
    assert not krx_futures.night_session_open(at(2026, 10, 8, 10, 0))      # 주간
    assert krx_futures.night_session_open(at(2026, 10, 10, 4, 0))          # 토 04:00(금요일 밤 세션)
    assert not krx_futures.night_session_open(at(2026, 10, 10, 18, 30))    # 토 저녁
    assert not krx_futures.night_session_open(at(2026, 10, 12, 3, 0))      # 월 새벽(일요일 밤 세션 없음)


class FakeService:
    enabled = True

    def __init__(self):
        self.calls = []

    async def _rest_get(self, path, tr_id, params):
        self.calls.append(params["FID_COND_MRKT_DIV_CODE"])
        price = "1085.65" if params["FID_COND_MRKT_DIV_CODE"] == "F" else "1091.25"
        return {"rt_cd": "0", "output1": {"hts_kor_isnm": "F 202612", "futs_prpr": price, "futs_prdy_ctrt": "-1.07"}}


@pytest.mark.asyncio
async def test_quotes_are_parsed_and_cached(monkeypatch):
    monkeypatch.setattr(krx_futures, "_cache", None)
    service = FakeService()
    first = await krx_futures.kospi200_futures(service, now=datetime(2026, 10, 7, 20, tzinfo=KST))
    assert first["night"] == {"status": "ok", "contract": "F 202612", "price": 1091.25, "change_pct": -1.07}
    assert first["day"]["price"] == 1085.65 and first["night_session_open"] is True
    await krx_futures.kospi200_futures(service)
    assert service.calls == ["F", "CM"]                                     # 30초 안에는 다시 부르지 않음


@pytest.mark.asyncio
async def test_errors_and_missing_keys(monkeypatch):
    monkeypatch.setattr(krx_futures, "_cache", None)
    assert krx_futures._parse({"rt_cd": "1", "msg_cd": "X", "msg1": "오류"})["status"] == "error"

    class NoKey:
        enabled = False

    assert (await krx_futures.kospi200_futures(NoKey()))["status"] == "unavailable"


@pytest.mark.asyncio
async def test_endpoint_is_public(monkeypatch, client):
    async def fake(service, now=None):
        return {"status": "ok", "night": {"status": "ok", "price": 1.0, "change_pct": 0.0}}
    monkeypatch.setattr(krx_futures, "kospi200_futures", fake)
    response = await client.get("/api/market/krx-futures")
    assert response.status_code == 200 and response.json()["night"]["price"] == 1.0


def test_us_futures_change_uses_the_previous_cme_session_close():
    import pandas as pd
    from app.backend.main import futures_session_quote
    et = lambda s: pd.Timestamp(s, tz="America/New_York")  # noqa: E731
    index = [et("2026-10-05 16:55"), et("2026-10-06 16:55"), et("2026-10-06 18:00"), et("2026-10-06 23:05")]
    quote = futures_session_quote(pd.Series([31339.5, 31500.5, 31501.5, 31467.75], index=index))
    assert quote["previous_close"] == 31500.5 and quote["change_pct"] == -0.1        # 화요일 세션 마감 대비
    afternoon = futures_session_quote(pd.Series([31339.5, 31400.0, 31500.5], index=index[:1] + [et("2026-10-06 09:30"), et("2026-10-06 16:55")]))
    assert afternoon["previous_close"] == 31339.5                                     # 같은 세션 안이면 전날 마감
    weekend = futures_session_quote(pd.Series([7800.0, 7810.0], index=[et("2026-10-09 16:55"), et("2026-10-11 18:05")]))
    assert weekend["previous_close"] == 7800.0                                        # 일요일 밤 개장 → 금요일 마감


def test_us_futures_prefers_a_fresher_last_price():
    import pandas as pd
    from app.backend.main import futures_session_quote
    et = lambda s: pd.Timestamp(s, tz="America/New_York")  # noqa: E731
    closes = pd.Series([31500.5, 31460.0], index=[et("2026-10-06 16:55"), et("2026-10-06 23:00")])
    assert futures_session_quote(closes, last_price=31467.5)["value"] == 31467.5
    assert futures_session_quote(closes, last_price=None)["value"] == 31460.0
