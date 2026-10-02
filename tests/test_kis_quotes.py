"""한투(KIS) 국내 체결가: 파싱·실시간 등록 계획·REST 캐시/토큰·WebSocket 세션·화면용 시세 API·포트폴리오 우선순위.

실제 한투 서버에는 접속하지 않는다(httpx MockTransport·가짜 WebSocket).
"""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.backend import kis_quotes, live_quotes
from app.backend.kis_quotes import KisQuotes, parse_rest_price, parse_ws_frame, plan_subscriptions


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def price_payload(price="71900", change="-100", pct="-0.14"):
    return {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리",
            "output": {"stck_prpr": price, "prdy_vrss": change, "prdy_ctrt": pct, "prdy_vrss_sign": "5"}}


def kis_http(handler_calls, price_responses=None):
    """토큰·접속키·현재가를 흉내 내는 MockTransport. price_responses는 차례로 돌려줄 응답 목록."""
    responses = list(price_responses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        handler_calls.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": f"tok{len(handler_calls)}", "expires_in": 86400})
        if request.url.path == "/oauth2/Approval":
            return httpx.Response(200, json={"approval_key": "approval-1"})
        body = responses.pop(0) if responses else price_payload()
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _reset_service():
    yield
    kis_quotes._set_service_for_tests(None)


def test_domestic_code_and_session_window():
    assert kis_quotes.domestic_code("005930.KS") == "005930"
    assert kis_quotes.domestic_code("0091P0.kq") == "0091P0"
    assert kis_quotes.domestic_code("AAPL") is None and kis_quotes.domestic_code("^KS11") is None
    kst = lambda h, m, day=1: datetime(2026, 10, day, h - 9 if h >= 9 else h + 15, m, tzinfo=timezone.utc)  # noqa: E731
    assert kis_quotes.stream_session_open(kst(10, 0)) is True          # 목요일 10:00
    assert kis_quotes.stream_session_open(kst(15, 36)) is False
    assert kis_quotes.stream_session_open(datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)) is False   # 토요일


def test_parse_rest_price_and_errors():
    assert parse_rest_price(price_payload()) == {"price": 71900.0, "change": -100.0, "change_pct": -0.14}
    with pytest.raises(kis_quotes.KisError, match="EGW00201"):
        parse_rest_price({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."})
    with pytest.raises(kis_quotes.KisError):
        parse_rest_price({"rt_cd": "0", "output": {"stck_prpr": "0"}})


def test_parse_ws_frame_reads_every_record_and_skips_others():
    row = lambda code, time, price, pct: [code, time, price, "2", "500", pct] + ["0"] * 40  # noqa: E731
    frame = "0|H0STCNT0|002|" + "^".join(row("005930", "093001", "72000", "0.70") + row("000660", "093002", "180500", "-1.10"))
    ticks = parse_ws_frame(frame, "2026-10-02")
    assert [t["code"] for t in ticks] == ["005930", "000660"]
    assert ticks[1] == {"code": "000660", "price": 180500.0, "change": 500.0, "change_pct": -1.10, "traded_at": "2026-10-02T09:30:02+09:00"}
    assert parse_ws_frame("1|H0STCNI0|001|암호문", "2026-10-02") == []
    assert parse_ws_frame("0|H0STASP0|001|005930^1", "2026-10-02") == []
    assert parse_ws_frame('{"header":{"tr_id":"PINGPONG"}}', "2026-10-02") == []


def test_plan_subscriptions_keeps_most_recent_within_limit():
    interest = {"A": 100.0, "B": 300.0, "C": 200.0, "OLD": -1000.0}
    add, remove = plan_subscriptions(set(), interest, now=310.0, limit=2)
    assert add == ["B", "C"] and remove == []
    add, remove = plan_subscriptions({"A", "OLD"}, interest, now=310.0, limit=2)
    assert sorted(remove) == ["A", "OLD"] and add == ["B", "C"]
    assert plan_subscriptions({"B", "C"}, interest, now=310.0, limit=2) == ([], [])


@pytest.mark.asyncio
async def test_rest_quote_is_cached_and_reuses_token():
    calls, clock = [], Clock()
    service = KisQuotes("key", "secret", http=kis_http(calls), clock=clock)
    first = await service.quote("005930.KS")
    assert first["status"] == "ok" and first["source"] == "kis_rest" and first["price"] == 71900.0
    assert await service.quote("005930.KS") is not None
    assert [c.url.path for c in calls].count("/oauth2/tokenP") == 1
    assert sum(c.url.path.endswith("inquire-price") for c in calls) == 1      # 5초 캐시
    price_call = next(c for c in calls if c.url.path.endswith("inquire-price"))
    assert price_call.headers["tr_id"] == "FHKST01010100" and price_call.url.params["FID_INPUT_ISCD"] == "005930"
    clock.now += kis_quotes.REST_CACHE_SECONDS + 1
    await service.quote("005930.KS")
    assert sum(c.url.path.endswith("inquire-price") for c in calls) == 2
    assert [c.url.path for c in calls].count("/oauth2/tokenP") == 1


@pytest.mark.asyncio
async def test_expired_token_is_reissued_once_and_errors_are_reported():
    calls = []
    expired = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
    service = KisQuotes("key", "secret", http=kis_http(calls, [expired, price_payload("1000")]), clock=Clock())
    quote = await service.quote("000660.KS")
    assert quote["price"] == 1000.0 and [c.url.path for c in calls].count("/oauth2/tokenP") == 2

    calls2 = []
    limited = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
    failing = KisQuotes("key", "secret", http=kis_http(calls2, [limited]), clock=Clock())
    error = await failing.quote("000660.KS")
    assert error["status"] == "error" and "EGW00201" in error["reason"]


@pytest.mark.asyncio
async def test_disabled_or_foreign_ticker_never_calls_kis():
    calls = []
    assert (await KisQuotes("", "", http=kis_http(calls)).quote("005930.KS"))["status"] == "error"
    assert (await KisQuotes("k", "s", http=kis_http(calls)).quote("AAPL"))["status"] == "error"
    assert calls == []


@pytest.mark.asyncio
async def test_realtime_tick_wins_while_subscribed_and_stale_tick_falls_back_to_rest():
    calls, clock = [], Clock()
    service = KisQuotes("key", "secret", http=kis_http(calls), clock=clock)
    service.handle_message("0|H0STCNT0|001|" + "^".join(["005930", "101500", "73000", "2", "1100", "1.53"] + ["0"] * 40))
    service.connected, service.subscribed = True, {"005930"}
    clock.now += 120          # 체결이 뜸해도 등록 중이면 마지막 체결가
    live = await service.quote("005930.KS")
    assert live["source"] == "kis_realtime" and live["price"] == 73000.0 and live["quoted_at"].endswith("10:15:00+09:00")
    assert calls == []
    service.connected, service.subscribed = False, set()
    rest = await service.quote("005930.KS")
    assert rest["source"] == "kis_rest"


def test_pingpong_is_echoed_and_rejected_subscription_is_remembered():
    clock = Clock()
    service = KisQuotes("key", "secret", clock=clock)
    ping = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20261002101500"}})
    assert service.handle_message(ping) == ping
    service.subscribed = {"005930"}
    reply = service.handle_message(json.dumps({"header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
                                               "body": {"rt_cd": "1", "msg1": "MAX SUBSCRIBE OVER"}}))
    assert reply is None and service.subscribed == set() and service.rejected == {"005930": clock.now}
    assert "MAX SUBSCRIBE OVER" in service.last_error


class FakeSocket:
    def __init__(self, frames):
        self.frames, self.sent = list(frames), []

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        await asyncio.sleep(10)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_stream_session_subscribes_recent_codes_and_records_ticks(monkeypatch):
    rounds = iter([True, True, False])
    monkeypatch.setattr(kis_quotes, "stream_session_open", lambda now=None: next(rounds))
    socket = FakeSocket(["0|H0STCNT0|001|" + "^".join(["000660", "090101", "181000", "2", "1000", "0.56"] + ["0"] * 40)])
    clock = Clock()
    service = KisQuotes("key", "secret", http=kis_http([]), connect=lambda url: socket, clock=clock)
    service.note_interest(["005930.KS", "000660.KQ", "AAPL"])
    await service._session()
    registered = [(m["body"]["input"]["tr_key"], m["header"]["tr_type"]) for m in socket.sent]
    assert sorted(registered) == [("000660", "1"), ("005930", "1")]
    assert all(m["header"]["approval_key"] == "approval-1" and m["body"]["input"]["tr_id"] == "H0STCNT0" for m in socket.sent)
    assert service.ticks["000660"]["price"] == 181000.0 and "code" not in service.ticks["000660"]
    assert service.connected is False and service.subscribed == set()


@pytest.mark.asyncio
async def test_live_quotes_api_requires_login_and_uses_kis_then_yahoo(client, make_user, monkeypatch):
    assert (await client.post("/api/market/live-quotes", json={"tickers": ["005930.KS"]})).status_code == 401
    headers = await make_user("live@test.com")

    class FakeKis(KisQuotes):
        async def quote(self, ticker):
            if ticker == "000660.KS":
                return {"status": "error", "reason": "한투 시세 조회 실패: 테스트"}
            return {"status": "ok", "price": 72000.0, "change": 100.0, "change_pct": 0.14, "quoted_at": "t", "source": "kis_rest"}

    kis_quotes._set_service_for_tests(FakeKis("key", "secret"))
    yahoo_calls = []

    def yahoo(ticker):
        yahoo_calls.append(ticker)
        return {"status": "ok", "price": 1.0, "change_pct": 0.0, "quoted_at": "y"}

    monkeypatch.setattr(live_quotes, "fetch_quote_cached", yahoo)
    response = await client.post("/api/market/live-quotes", headers=headers, json={"tickers": ["005930.KS", "000660.KS", "^IXIC"]})
    assert response.status_code == 200
    items = {item["ticker"]: item for item in response.json()["items"]}
    assert items["005930.KS"]["source"] == "kis_rest" and items["005930.KS"]["source_label"] == "한투 현재가"
    assert items["000660.KS"]["source"] == "yahoo_delayed" and "테스트" in items["000660.KS"]["fallback_reason"]
    assert items["^IXIC"]["source"] == "yahoo_delayed"
    assert sorted(yahoo_calls) == ["000660.KS", "^IXIC"]
    assert response.json()["provider"]["enabled"] is True
    too_many = await client.post("/api/market/live-quotes", headers=headers, json={"tickers": [f"{i:06d}.KS" for i in range(41)]})
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_portfolio_valuation_prefers_kis_for_domestic_holdings(monkeypatch):
    from app.backend.routers import portfolio

    class FakeKis(KisQuotes):
        async def quote(self, ticker):
            return {"status": "ok", "price": 500.0, "change": 0.0, "change_pct": 0.0, "quoted_at": "t", "source": "kis_realtime"}

    kis_quotes._set_service_for_tests(FakeKis("key", "secret"))
    asked_cache = []

    async def cached(tickers):
        asked_cache.extend(tickers)
        return {ticker: {"status": "ok", "price": 1.0, "change_pct": 0.0, "quoted_at": "2026-10-01T00:00:00+00:00",
                         "source": "market_data_cache_daily"} for ticker in tickers}

    monkeypatch.setattr(portfolio, "_cached_daily_quotes", cached)
    quotes = await portfolio._valuation_quotes([{"ticker": "005930.KS", "currency": "KRW"}, {"ticker": "AAPL", "currency": "USD"}])
    assert quotes["005930.KS"]["source"] == "kis_realtime" and quotes["005930.KS"]["price"] == 500.0
    assert sorted(asked_cache) == ["AAPL", "KRW=X"]
    assert portfolio._valuation_source(["kis_realtime", "kis_rest"]) == "kis_live"
    assert portfolio._valuation_source(["kis_realtime", "market_data_cache_daily"]) == "mixed_with_kis"
    assert portfolio._valuation_source(["market_data_cache_daily"]) == "market_data_cache_daily"


@pytest.mark.asyncio
async def test_without_keys_portfolio_keeps_previous_path(monkeypatch):
    from app.backend.routers import portfolio

    kis_quotes._set_service_for_tests(KisQuotes("", ""))

    async def cached(tickers):
        return {t: {"status": "ok", "price": 2.0, "change_pct": 0.0, "quoted_at": "d", "source": "market_data_cache_daily"} for t in tickers}

    monkeypatch.setattr(portfolio, "_cached_daily_quotes", cached)
    quotes = await portfolio._valuation_quotes([{"ticker": "005930.KS", "currency": "KRW"}])
    assert quotes["005930.KS"]["source"] == "market_data_cache_daily"
