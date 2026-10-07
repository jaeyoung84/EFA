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
