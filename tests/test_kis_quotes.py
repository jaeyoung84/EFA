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

REAL_SESSION_OPEN = kis_quotes.stream_session_open   # 아래 자동 고정 전에 보관(장 시간 판정 자체를 시험할 때 사용)


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
        if request.url.path.endswith("intstock-multprice") and isinstance(body.get("output"), dict):
            # 단일 현재가 형태의 가짜 응답을 멀티종목 시세 형태로 바꾼다(요청한 종목마다 같은 가격).
            out = body["output"]
            codes = [v for k, v in request.url.params.items() if k.startswith("FID_INPUT_ISCD_")]
            body = {**body, "output": [{"inter_shrn_iscd": code, "inter2_prpr": out["stck_prpr"],
                                        "inter2_prdy_vrss": out["prdy_vrss"], "prdy_ctrt": out["prdy_ctrt"]} for code in codes]}
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _market_open_by_default(monkeypatch):
    """캐시 시간이 장 운영 여부에 따라 달라지므로, 테스트는 실행한 날과 무관하게 '장중'을 기본으로 한다."""
    monkeypatch.setattr(kis_quotes, "stream_session_open", lambda now=None: True)


@pytest.fixture(autouse=True)
def _reset_service():
    yield
    kis_quotes._set_service_for_tests(None)


def test_domestic_code_and_session_window():
    assert kis_quotes.domestic_code("005930.KS") == "005930"
    assert kis_quotes.domestic_code("0091P0.kq") == "0091P0"
    assert kis_quotes.domestic_code("AAPL") is None and kis_quotes.domestic_code("^KS11") is None
    kst = lambda h, m, day=1: datetime(2026, 10, day, h - 9 if h >= 9 else h + 15, m, tzinfo=timezone.utc)  # noqa: E731
    assert REAL_SESSION_OPEN(kst(10, 0)) is True          # 목요일 10:00
    assert REAL_SESSION_OPEN(kst(15, 36)) is False
    assert REAL_SESSION_OPEN(datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)) is False   # 토요일


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
    assert sum(c.url.path.endswith("intstock-multprice") for c in calls) == 1      # 5초 캐시
    price_call = next(c for c in calls if c.url.path.endswith("intstock-multprice"))
    assert price_call.headers["tr_id"] == "FHKST11300006" and price_call.url.params["FID_INPUT_ISCD_1"] == "005930"
    clock.now += kis_quotes.REST_CACHE_SECONDS + 1
    await service.quote("005930.KS")
    assert sum(c.url.path.endswith("intstock-multprice") for c in calls) == 2
    assert [c.url.path for c in calls].count("/oauth2/tokenP") == 1


@pytest.mark.asyncio
async def test_expired_token_is_reissued_once_and_errors_are_reported():
    calls = []
    expired = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
    service = KisQuotes("key", "secret", http=kis_http(calls, [expired, price_payload("1000")]), clock=Clock())
    quote = await service.quote("000660.KS")
    assert quote["price"] == 1000.0 and [c.url.path for c in calls].count("/oauth2/tokenP") == 2

    calls2 = []
    failing = KisQuotes("key", "secret", http=kis_http(calls2, [LIMITED] * 3), clock=Clock())
    error = await failing.quote("000660.KS")
    assert error["status"] == "error" and "EGW00201" in error["reason"]
    assert sum(c.url.path.endswith("intstock-multprice") for c in calls2) == 3      # 처음 + 재시도 2번


LIMITED = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}


@pytest.mark.asyncio
async def test_rate_limit_reply_slows_down_retries_and_recovers():
    calls, clock = [], Clock()
    service = KisQuotes("key", "secret", http=kis_http(calls, [LIMITED, price_payload("1234")]), clock=clock, rest_per_second=4)
    quote = await service.quote("005930.KS")
    assert quote["status"] == "ok" and quote["price"] == 1234.0
    assert service.rate == 2.0 and sum(c.url.path.endswith("intstock-multprice") for c in calls) == 2
    clock.now += kis_quotes.RATE_RECOVER_SECONDS + 1
    clock.now += kis_quotes.REST_CACHE_SECONDS
    await service.quote("005930.KS")
    assert service.rate == 3.0                     # 30초 동안 문제가 없으면 1건씩 회복
    assert KisQuotes("k", "s").rate == kis_quotes.REST_PER_SECOND["real"]


@pytest.mark.asyncio
async def test_failure_keeps_last_kis_price_instead_of_previous_close():
    calls, clock = [], Clock()
    service = KisQuotes("key", "secret", http=kis_http(calls, [price_payload("5000"), *([LIMITED] * 6)]), clock=clock)
    assert (await service.quote("005930.KS"))["price"] == 5000.0
    clock.now += 60
    held = await service.quote("005930.KS")
    assert held["status"] == "ok" and held["price"] == 5000.0 and held["source"] == "kis_rest"
    assert held["stale_seconds"] == 60 and "EGW00201" in held["stale_reason"]
    clock.now += kis_quotes.LAST_GOOD_SECONDS          # 10분이 넘은 값은 더 쓰지 않는다
    expired = await service.quote("005930.KS")
    assert expired["status"] == "error" and "EGW00201" in expired["reason"]


@pytest.mark.asyncio
async def test_concurrent_requests_for_one_stock_share_a_single_call():
    calls = []
    service = KisQuotes("key", "secret", http=kis_http(calls), clock=Clock())
    first, second = await asyncio.gather(service.quote("005930.KS"), service.quote("005930.KS"))
    assert first["price"] == second["price"] == 71900.0
    assert sum(c.url.path.endswith("intstock-multprice") for c in calls) == 1


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
        async def quotes(self, tickers):
            return {ticker: await self.quote(ticker) for ticker in dict.fromkeys(tickers)}

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
        async def quotes(self, tickers):
            return {ticker: await self.quote(ticker) for ticker in dict.fromkeys(tickers)}

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


@pytest.mark.asyncio
async def test_kis_failure_tries_yahoo_intraday_before_previous_close(monkeypatch):
    from app.backend.routers import portfolio

    class FakeKis(KisQuotes):
        async def quotes(self, tickers):
            return {ticker: await self.quote(ticker) for ticker in dict.fromkeys(tickers)}

        async def quote(self, ticker):
            if ticker == "005930.KS":
                return {"status": "ok", "price": 500.0, "change": 0.0, "change_pct": 0.0, "quoted_at": "t", "source": "kis_realtime"}
            return {"status": "error", "reason": "한투 시세 조회 실패: EGW00201"}

    kis_quotes._set_service_for_tests(FakeKis("key", "secret"))

    async def cached(tickers):
        return {t: {"status": "ok", "price": 1.0, "change_pct": 0.0, "quoted_at": "2026-10-01T00:00:00+00:00",
                    "source": "market_data_cache_daily"} for t in tickers}

    async def yahoo(ticker):
        if ticker == "000660.KS":
            return {"status": "ok", "price": 300.0, "change_pct": 1.0, "quoted_at": "y", "source": "yahoo_delayed"}
        return {"status": "error", "reason": "yahoo down"}

    monkeypatch.setattr(portfolio, "_cached_daily_quotes", cached)
    monkeypatch.setattr(portfolio, "_bounded_quote", yahoo)
    quotes = await portfolio._valuation_quotes([{"ticker": t, "currency": "KRW"} for t in ("005930.KS", "000660.KS", "247540.KQ")])
    assert quotes["005930.KS"]["source"] == "kis_realtime"
    assert quotes["000660.KS"]["price"] == 300.0                                   # 한투 실패 → Yahoo 장중
    assert quotes["247540.KQ"]["source"] == "market_data_cache_daily"              # 둘 다 실패해야 전일 종가


class MemoryTokenStore:
    def __init__(self, row=None):
        self.row, self.saved = row, []

    async def load(self):
        return self.row

    async def save(self, token, expires_at):
        self.saved.append((token, expires_at))
        self.row = (token, expires_at)


@pytest.mark.asyncio
async def test_access_token_is_shared_through_the_store_across_restarts():
    from datetime import timedelta
    store = MemoryTokenStore()
    calls = []
    first = KisQuotes("key", "secret", http=kis_http(calls), clock=Clock(), token_store=store)
    await first.quote("005930.KS")
    assert [c.url.path for c in calls].count("/oauth2/tokenP") == 1 and len(store.saved) == 1
    calls2 = []
    restarted = KisQuotes("key", "secret", http=kis_http(calls2), clock=Clock(), token_store=store)
    await restarted.quote("005930.KS")
    assert [c.url.path for c in calls2].count("/oauth2/tokenP") == 0           # 저장된 토큰 재사용
    price_call = next(c for c in calls2 if c.url.path.endswith("intstock-multprice"))
    assert price_call.headers["authorization"] == f"Bearer {store.saved[0][0]}"
    nearly_expired = MemoryTokenStore(("old", datetime.now(timezone.utc) + timedelta(minutes=5)))
    calls3 = []
    await KisQuotes("key", "secret", http=kis_http(calls3), clock=Clock(), token_store=nearly_expired).quote("005930.KS")
    assert [c.url.path for c in calls3].count("/oauth2/tokenP") == 1             # 곧 만료될 토큰은 새로 발급


@pytest.mark.asyncio
async def test_mongo_token_store_round_trip():
    from datetime import timedelta
    from app.backend.db import get_db
    store = kis_quotes.MongoTokenStore("app-key-not-stored", "real")
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=20)
    try:
        await store.save("tok-1", expires)
        token, loaded = await store.load()
        assert token == "tok-1" and loaded == expires
        row = await get_db().kis_access_tokens.find_one({})
        assert "app-key-not-stored" not in json.dumps(row, default=str)
    finally:
        await get_db().kis_access_tokens.delete_many({})


@pytest.mark.asyncio
async def test_public_provider_status_hides_errors(client):
    service = KisQuotes("key", "secret")
    service.last_error = "secret-ish detail"
    kis_quotes._set_service_for_tests(service)
    data = (await client.get("/api/market/quote-provider-status")).json()
    assert data["enabled"] is True and "last_error" not in data and "secret-ish" not in json.dumps(data)


@pytest.mark.asyncio
async def test_many_holdings_are_priced_in_one_call_and_cached_longer_when_closed(monkeypatch):
    """보유 11종목을 종목마다 부르면 초당 한도 때문에 7~8초 걸렸다(2026-10-03 측정). 이제 한 번에 묶는다."""
    calls, clock = [], Clock()
    service = KisQuotes("key", "secret", http=kis_http(calls), clock=clock)
    tickers = [f"{i:06d}.KS" for i in range(1, 36)]                       # 35종목 → 30 + 5, 두 번
    quotes = await service.quotes(tickers)
    multi = [c for c in calls if c.url.path.endswith("intstock-multprice")]
    assert len(multi) == 2 and len(multi[0].url.params) == 60 and len(multi[1].url.params) == 10
    assert all(q["status"] == "ok" and q["source"] == "kis_rest" for q in quotes.values())
    monkeypatch.setattr(kis_quotes, "stream_session_open", lambda now=None: False)
    clock.now += 120                                                         # 장 마감 뒤에는 2분이 지나도 캐시
    await service.quotes(tickers[:11])
    assert len([c for c in calls if c.url.path.endswith("intstock-multprice")]) == 2
    clock.now += kis_quotes.OFF_SESSION_CACHE_SECONDS
    await service.quotes(tickers[:11])
    assert len([c for c in calls if c.url.path.endswith("intstock-multprice")]) == 3


@pytest.mark.asyncio
async def test_missing_stock_in_multi_response_is_an_error_not_a_guess():
    def handler(request):
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
        return httpx.Response(200, json={"rt_cd": "0", "output": [
            {"inter_shrn_iscd": "005930", "inter2_prpr": "70000", "inter2_prdy_vrss": "100", "prdy_ctrt": "0.14"}]})

    service = KisQuotes("k", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=Clock())
    quotes = await service.quotes(["005930.KS", "000660.KS"])
    assert quotes["005930.KS"]["price"] == 70000.0 and quotes["005930.KS"]["change_pct"] == 0.14
    assert quotes["000660.KS"]["status"] == "error" and "응답에 이 종목이 없습니다" in quotes["000660.KS"]["reason"]
