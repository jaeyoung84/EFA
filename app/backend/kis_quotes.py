"""한국투자증권(KIS) Open API 국내 주식 체결가(시세 조회 전용).

역할 분리: 실시간 대응이 필요한 국내 개별 종목(포트폴리오 보유 종목, KQS 추천 종목, 관심종목)의
체결가만 한투에서 받는다. 지수·해외지수·환율은 기존 Yahoo 지연 시세를 그대로 쓴다.

- 키(KIS_APP_KEY·KIS_APP_SECRET)가 없으면 꺼져 있고, 호출자는 Yahoo로 돌아간다.
- 장중에는 서버가 한투 WebSocket 하나로 최근 화면에서 요청된 종목(최대 40개)의 체결가를 받는다.
  WebSocket에 없는 종목·장외 시간·연결 끊김에는 REST 현재가(5초 캐시, 초당 호출 제한)를 쓴다.
- 주문·잔고·계좌 API는 쓰지 않는다. 값을 만들어 채우지 않고 실패는 이유와 함께 돌려준다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

KST = timezone(timedelta(hours=9))
DOMAINS = {
    "real": ("https://openapi.koreainvestment.com:9443", "ws://ops.koreainvestment.com:21000"),
    "mock": ("https://openapivts.koreainvestment.com:29443", "ws://ops.koreainvestment.com:31000"),
}
# 문서상 실전 초당 20건·모의 2건이지만 신규 키 등은 더 낮게 제한된다(실측 2026-10-02: 초당 2~5건에서도 EGW00201).
# 기본은 보수적으로 두고 KIS_REST_PER_SECOND로 바꿀 수 있다. '초과' 응답을 받으면 속도를 절반으로 낮추고
# 30초 동안 문제가 없을 때마다 1건씩 다시 올린다.
REST_PER_SECOND = {"real": 3.0, "mock": 1.5}
RATE_LIMIT_CODE = "EGW00201"
RATE_LIMIT_RETRIES = 2
RATE_RECOVER_SECONDS = 30
REST_CACHE_SECONDS = 5
ERROR_CACHE_SECONDS = 2
# 조회가 잠깐 실패해도 이 시간 안의 마지막 한투 시세를 계속 쓴다(전일 종가로 되돌아가지 않게).
LAST_GOOD_SECONDS = 600
REST_TIMEOUT_SECONDS = 4
# 한투 WebSocket은 세션당 실시간 등록이 41건까지다. 여유를 두고 40개만 쓴다.
STREAM_LIMIT = 40
# 화면에서 이 시간 동안 다시 요청되지 않은 종목은 실시간 등록을 해제한다.
INTEREST_SECONDS = 600
# 연결이 끊긴 뒤 받은 지 오래된 체결가는 쓰지 않고 REST로 다시 조회한다.
STALE_TICK_SECONDS = 15
TOKEN_RETRY_SECONDS = 65   # 접근토큰 발급은 1분에 1회로 제한된다.
TICKER_PATTERN = re.compile(r"^([0-9A-Z]{6})\.(KS|KQ)$")
TRADE_TR_ID = "H0STCNT0"   # 국내주식 실시간 체결가(KRX)
TRADE_FIELD_COUNT = 46


class KisError(RuntimeError):
    """한투 API가 시세를 돌려주지 않았다."""


def domestic_code(ticker: str) -> str | None:
    match = TICKER_PATTERN.match(str(ticker or "").strip().upper())
    return match.group(1) if match else None


def stream_session_open(now: datetime | None = None) -> bool:
    """정규장(09:00~15:30 KST) 앞뒤 5분, 평일에만 WebSocket을 연다. 휴장일에는 체결이 없을 뿐이다."""
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 8 * 60 + 55 <= minutes <= 15 * 60 + 35


def _num(value: object) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def parse_rest_price(payload: dict) -> dict:
    """주식현재가 시세(FHKST01010100) 응답 → 체결가. rt_cd가 0이 아니면 KisError."""
    if str(payload.get("rt_cd")) != "0":
        raise KisError(f"{payload.get('msg_cd', '')} {payload.get('msg1', '응답 오류')}".strip())
    output = payload.get("output") or {}
    price = _num(output.get("stck_prpr"))
    if not price:
        raise KisError("현재가가 비어 있습니다.")
    return {"price": price, "change": _num(output.get("prdy_vrss")), "change_pct": _num(output.get("prdy_ctrt"))}


def parse_ws_frame(text: str, today: str) -> list[dict]:
    """실시간 체결 프레임 '0|H0STCNT0|건수|필드^필드…' → 체결 목록. 다른 프레임은 빈 목록."""
    if not text or text[0] not in "01":
        return []
    parts = text.split("|", 3)
    if len(parts) < 4 or parts[0] != "0" or parts[1] != TRADE_TR_ID:   # 1은 암호화 프레임(체결통보), 쓰지 않는다
        return []
    fields = parts[3].split("^")
    count = int(parts[2]) if parts[2].isdigit() else 1
    ticks = []
    for index in range(count):
        row = fields[index * TRADE_FIELD_COUNT:(index + 1) * TRADE_FIELD_COUNT]
        if len(row) < 6:
            break
        price = _num(row[2])
        if not price:
            continue
        hhmmss = row[1] if re.fullmatch(r"\d{6}", row[1] or "") else "000000"
        ticks.append({"code": row[0], "price": price, "change": _num(row[4]), "change_pct": _num(row[5]),
                      "traded_at": f"{today}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}+09:00"})
    return ticks


def plan_subscriptions(current: set[str], interest: dict[str, float], now: float,
                       limit: int = STREAM_LIMIT) -> tuple[list[str], list[str]]:
    """최근 요청 순으로 최대 limit개를 실시간 등록한다. (추가, 해제) 목록을 돌려준다."""
    recent = sorted((code for code, at in interest.items() if now - at <= INTEREST_SECONDS),
                    key=lambda code: -interest[code])
    wanted = set(recent[:limit])
    remove = sorted(current - wanted)
    room = limit - (len(current) - len(remove))
    add = [code for code in recent if code in wanted and code not in current][:max(0, room)]
    return add, remove


class MongoTokenStore:
    """접근토큰(24시간 유효)을 DB에 두어 재시작·배포·다른 프로세스가 다시 발급하지 않고 함께 쓴다.

    한투는 토큰 발급을 1분에 1회로 제한하고 잦은 발급을 막는다. 앱키 자체는 저장하지 않고 해시만 키로 쓴다.
    """

    def __init__(self, app_key: str, env: str):
        self._id = f"{env}:{hashlib.sha256(app_key.encode()).hexdigest()[:16]}"

    @staticmethod
    def _collection():
        try:
            from .db import get_db
        except ImportError:
            from db import get_db  # type: ignore
        return get_db().kis_access_tokens

    async def load(self) -> tuple[str, datetime] | None:
        row = await self._collection().find_one({"_id": self._id})
        if not row or not row.get("token") or not row.get("expires_at"):
            return None
        expires = row["expires_at"]
        return row["token"], expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)

    async def save(self, token: str, expires_at: datetime) -> None:
        await self._collection().update_one({"_id": self._id}, {"$set": {
            "token": token, "expires_at": expires_at, "issued_at": datetime.now(timezone.utc)}}, upsert=True)


class KisQuotes:
    def __init__(self, app_key: str = "", app_secret: str = "", env: str = "real", *,
                 http: httpx.AsyncClient | None = None, connect=None, clock=time.monotonic,
                 rest_per_second: float | None = None, token_store=None):
        self.app_key, self.app_secret = app_key.strip(), app_secret.strip()
        self.env = env if env in DOMAINS else "real"
        self.rest_base, self.ws_url = DOMAINS[self.env]
        self._http = http
        self._connect = connect
        self._clock = clock
        self._token: tuple[str, float] | None = None
        self._token_failed_at: float | None = None
        self._token_store = token_store
        self._token_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._next_slot = 0.0
        self.max_rate = float(rest_per_second or REST_PER_SECOND[self.env])
        self.rate = self.max_rate
        self._rate_changed_at = 0.0
        self._rest_cache: dict[str, tuple[float, dict]] = {}
        self._last_good: dict[str, tuple[float, dict]] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self.interest: dict[str, float] = {}
        self.ticks: dict[str, dict] = {}
        self.subscribed: set[str] = set()
        self.rejected: dict[str, float] = {}   # 등록을 거절당한 종목은 관심 시간 동안 다시 시도하지 않는다
        self.connected = False
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.app_key and self.app_secret)

    # ── REST ──
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=REST_TIMEOUT_SECONDS)
        return self._http

    async def _access_token(self) -> str:
        async with self._token_lock:
            now = self._clock()
            if self._token and now < self._token[1]:
                return self._token[0]
            stored = await self._load_stored_token()
            if stored:
                return stored
            if self._token_failed_at is not None and now - self._token_failed_at < TOKEN_RETRY_SECONDS:
                raise KisError("접근토큰 발급 실패 후 재시도 대기 중입니다.")
            response = await self._client().post(f"{self.rest_base}/oauth2/tokenP", json={
                "grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret})
            data = response.json() if response.content else {}
            token = data.get("access_token")
            if response.status_code != 200 or not token:
                self._token_failed_at = now
                raise KisError(f"접근토큰 발급 실패: {data.get('error_description') or data.get('msg1') or response.status_code}")
            expires_in = float(data.get("expires_in") or 86_400)
            self._token, self._token_failed_at = (token, now + max(60.0, expires_in - 600)), None
            if self._token_store:
                try:
                    await self._token_store.save(token, datetime.now(timezone.utc) + timedelta(seconds=expires_in))
                except Exception:   # 저장 실패는 이번 프로세스의 조회를 막지 않는다
                    pass
            return token

    async def _load_stored_token(self) -> str | None:
        if not self._token_store:
            return None
        try:
            stored = await self._token_store.load()
        except Exception:
            return None
        if not stored:
            return None
        token, expires_at = stored
        remaining = (expires_at - datetime.now(timezone.utc)).total_seconds() - 600
        if remaining <= 60:
            return None
        self._token = (token, self._clock() + remaining)
        return token

    async def _wait_rate_slot(self) -> None:
        async with self._rate_lock:
            interval = 1 / self.rate
            now = self._clock()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + interval
        if wait > 0:
            await asyncio.sleep(wait)

    def _slow_down(self) -> None:
        self.rate = max(1.0, self.rate / 2)
        self._rate_changed_at = self._clock()

    def _maybe_speed_up(self) -> None:
        now = self._clock()
        if self.rate < self.max_rate and now - self._rate_changed_at >= RATE_RECOVER_SECONDS:
            self.rate = min(self.max_rate, self.rate + 1)
            self._rate_changed_at = now

    async def _rest_price(self, code: str, retry: bool = True) -> dict:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            payload = await self._rest_call(code)
            if payload.get("msg_cd") != RATE_LIMIT_CODE or attempt == RATE_LIMIT_RETRIES:
                break
            self._slow_down()   # 초당 건수 초과: 속도를 낮춰 다시 줄을 선다
        if payload.get("msg_cd") == "EGW00123" and retry:   # 토큰 만료: 한 번만 새로 발급
            self._token = None
            return await self._rest_price(code, retry=False)
        price = parse_rest_price(payload)
        self._maybe_speed_up()
        return price

    async def _rest_call(self, code: str) -> dict:
        token = await self._access_token()
        await self._wait_rate_slot()
        response = await self._client().get(
            f"{self.rest_base}/uapi/domestic-stock/v1/quotations/inquire-price",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            headers={"authorization": f"Bearer {token}", "appkey": self.app_key, "appsecret": self.app_secret,
                     "tr_id": "FHKST01010100", "custtype": "P"})
        return response.json() if response.content else {}

    # ── 조회 ──
    def note_interest(self, tickers: list[str]) -> None:
        now = self._clock()
        for ticker in tickers:
            code = domestic_code(ticker)
            if code:
                self.interest[code] = now

    def _live_tick(self, code: str) -> dict | None:
        tick = self.ticks.get(code)
        if not tick:
            return None
        if self.connected and code in self.subscribed:
            return tick   # 등록 중이면 체결이 뜸해도 마지막 체결가가 현재가다
        return tick if self._clock() - tick["received"] <= STALE_TICK_SECONDS else None

    async def quote(self, ticker: str) -> dict:
        """국내 종목 체결가. 실패도 {'status': 'error', 'reason'}로 돌려준다."""
        code = domestic_code(ticker)
        if not self.enabled or not code:
            return {"status": "error", "reason": "한투 시세 대상이 아닙니다."}
        self.note_interest([ticker])
        tick = self._live_tick(code)
        if tick:
            return {"status": "ok", "price": tick["price"], "change": tick["change"], "change_pct": tick["change_pct"],
                    "quoted_at": tick["traded_at"], "source": "kis_realtime"}
        now = self._clock()
        cached = self._rest_cache.get(code)
        if cached and now - cached[0] < (REST_CACHE_SECONDS if cached[1]["status"] == "ok" else ERROR_CACHE_SECONDS):
            return self._with_last_good(code, cached[1])
        if code in self._inflight:   # 같은 종목을 동시에 여러 화면이 물으면 한 번만 조회한다
            return self._with_last_good(code, await asyncio.shield(self._inflight[code]))
        future = asyncio.get_running_loop().create_future()
        self._inflight[code] = future
        try:
            price = await asyncio.wait_for(self._rest_price(code), timeout=REST_TIMEOUT_SECONDS + 4)
            result = {"status": "ok", **price, "quoted_at": datetime.now(timezone.utc).isoformat(), "source": "kis_rest"}
            self._last_good[code] = (self._clock(), result)
        except (KisError, httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
            self.last_error = f"REST {code}: {exc or type(exc).__name__}"
            result = {"status": "error", "reason": f"한투 시세 조회 실패: {exc or type(exc).__name__}"}
        finally:
            self._inflight.pop(code, None)
        self._rest_cache[code] = (now, result)
        future.set_result(result)
        return self._with_last_good(code, result)

    def _with_last_good(self, code: str, result: dict) -> dict:
        """실패했어도 10분 안의 마지막 한투 시세(실시간 체결 포함)가 있으면 그것을 '지연'으로 표시해 쓴다."""
        if result["status"] == "ok":
            return result
        candidates = []
        if code in self._last_good:
            candidates.append(self._last_good[code])
        tick = self.ticks.get(code)
        if tick:
            candidates.append((tick["received"], {"status": "ok", "price": tick["price"], "change": tick["change"],
                                                  "change_pct": tick["change_pct"], "quoted_at": tick["traded_at"],
                                                  "source": "kis_realtime"}))
        if not candidates:
            return result
        at, good = max(candidates, key=lambda item: item[0])
        age = self._clock() - at
        if age > LAST_GOOD_SECONDS:
            return result
        return {**good, "stale_seconds": round(age), "stale_reason": result.get("reason")}

    async def quotes(self, tickers: list[str]) -> dict[str, dict]:
        unique = list(dict.fromkeys(tickers))
        results = await asyncio.gather(*(self.quote(ticker) for ticker in unique))
        return dict(zip(unique, results))

    def status(self) -> dict:
        return {"enabled": self.enabled, "env": self.env, "rest_per_second": self.rate, "stream_connected": self.connected,
                "stream_subscribed": len(self.subscribed), "stream_limit": STREAM_LIMIT, "last_error": self.last_error}

    # ── WebSocket ──
    async def _approval_key(self) -> str:
        response = await self._client().post(f"{self.rest_base}/oauth2/Approval", json={
            "grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret})
        key = (response.json() if response.content else {}).get("approval_key")
        if not key:
            raise KisError(f"실시간 접속키 발급 실패({response.status_code})")
        return key

    @staticmethod
    def _subscribe_message(approval_key: str, code: str, register: bool) -> str:
        return json.dumps({"header": {"approval_key": approval_key, "custtype": "P", "tr_type": "1" if register else "2",
                                      "content-type": "utf-8"},
                           "body": {"input": {"tr_id": TRADE_TR_ID, "tr_key": code}}})

    def handle_message(self, text: str) -> str | None:
        """수신 프레임 처리. 응답으로 다시 보내야 하는 메시지(PINGPONG)가 있으면 돌려준다."""
        if text and text[0] in "01":
            today = datetime.now(KST).date().isoformat()
            received = self._clock()
            for tick in parse_ws_frame(text, today):
                code = tick.pop("code")
                self.ticks[code] = {**tick, "received": received}
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        header, body = data.get("header") or {}, data.get("body") or {}
        if header.get("tr_id") == "PINGPONG":
            return text
        if body.get("rt_cd") not in (None, "0"):
            code = header.get("tr_key", "")
            self.last_error = f"실시간 등록 응답({code}): {body.get('msg1', '')}".strip()
            if code in self.subscribed:
                self.subscribed.discard(code)
                self.rejected[code] = self._clock()
        return None

    async def _session(self) -> None:
        import websockets

        approval_key = await self._approval_key()
        connect = self._connect or (lambda url: websockets.connect(url, ping_interval=None, open_timeout=10))
        async with connect(self.ws_url) as socket:
            self.connected, self.subscribed = True, set()
            try:
                while stream_session_open():
                    now = self._clock()
                    candidates = {code: at for code, at in self.interest.items()
                                  if now - self.rejected.get(code, -INTEREST_SECONDS - 1) > INTEREST_SECONDS}
                    add, remove = plan_subscriptions(self.subscribed, candidates, now)
                    for code in remove:
                        await socket.send(self._subscribe_message(approval_key, code, False))
                        self.subscribed.discard(code)
                    for code in add:
                        await socket.send(self._subscribe_message(approval_key, code, True))
                        self.subscribed.add(code)
                    try:
                        reply = self.handle_message(await asyncio.wait_for(socket.recv(), timeout=2))
                        if reply:
                            await socket.send(reply)
                    except asyncio.TimeoutError:
                        pass
            finally:
                self.connected, self.subscribed = False, set()

    async def run_stream(self) -> None:
        delay = 5
        while True:
            if not self.enabled or not stream_session_open() or not self.interest:
                await asyncio.sleep(20)
                continue
            try:
                await self._session()
                delay = 5
            except asyncio.CancelledError:
                raise
            except Exception as exc:   # 연결 오류는 기록하고 잠시 뒤 다시 연결한다. 그동안은 REST가 대신한다.
                self.last_error = f"실시간 연결 오류: {exc}"
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run_stream())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
            self._task = None


_service: KisQuotes | None = None


def kis_service() -> KisQuotes:
    global _service
    if _service is None:
        try:
            rate = float(os.getenv("KIS_REST_PER_SECOND", "") or 0) or None
        except ValueError:
            rate = None
        app_key, env = os.getenv("KIS_APP_KEY", ""), os.getenv("KIS_ENV", "real").strip().lower()
        _service = KisQuotes(app_key, os.getenv("KIS_APP_SECRET", ""), env, rest_per_second=rate,
                             token_store=MongoTokenStore(app_key.strip(), env if env in DOMAINS else "real"))
    return _service


def _set_service_for_tests(service: KisQuotes | None) -> None:
    global _service
    _service = service
