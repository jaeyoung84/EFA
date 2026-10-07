"""코스피200 선물 주간·야간 시세(한투 조회 TR FHMIF10000000). 상단 시세 띠의 참고 표시용이며 주문과 무관하다.

- 주간: 시장 구분 F, 야간(KRX 야간 파생시장 18:00~다음 날 05:00 KST): 시장 구분 CM.
- 종목코드 10100000 = 한투의 코스피200 선물 최근월물(2026-10-07 서버에서 확인: 202612물과 같은 값).
- 모든 방문자의 시세 띠가 같은 값을 보도록 30초 동안 한 번만 한투를 부른다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from time import monotonic

KST = timezone(timedelta(hours=9))
FRONT_MONTH = "10100000"
PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
TR_ID = "FHMIF10000000"
CACHE_SECONDS = 30
NIGHT_OPEN, NIGHT_CLOSE = time(18, 0), time(5, 0)

_cache: tuple[float, dict] | None = None
_lock = asyncio.Lock()


def night_session_open(now: datetime) -> bool:
    """평일 18:00~다음 날 05:00 KST(금요일 밤 세션은 토요일 05:00까지). 휴장일은 한투 응답 값으로 판단하지 않는다."""
    local = now.astimezone(KST)
    if local.time() >= NIGHT_OPEN:
        return local.weekday() < 5
    if local.time() < NIGHT_CLOSE:
        return (local - timedelta(days=1)).weekday() < 5
    return False


def _parse(payload: dict) -> dict:
    output = payload.get("output1") or {}
    if str(payload.get("rt_cd")) != "0" or not output.get("futs_prpr"):
        return {"status": "error", "reason": f"{payload.get('msg_cd', '')} {payload.get('msg1', '응답 없음')}".strip()}
    try:
        return {"status": "ok", "contract": output.get("hts_kor_isnm"), "price": float(output["futs_prpr"]),
                "change_pct": float(output.get("futs_prdy_ctrt") or 0)}
    except (TypeError, ValueError):
        return {"status": "error", "reason": "가격 형식 오류"}


async def kospi200_futures(service, now: datetime | None = None) -> dict:
    """주간·야간 최근월물 시세와 야간 세션 진행 여부. 한투 키가 없으면 status=unavailable."""
    global _cache
    if _cache and monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    async with _lock:
        if _cache and monotonic() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
        now = now or datetime.now(timezone.utc)
        if not service.enabled:
            return {"status": "unavailable", "reason": "한투 키가 없어 선물 시세를 조회하지 않았습니다."}
        result = {"status": "ok", "fetched_at": now.isoformat(), "night_session_open": night_session_open(now),
                  "source": "한국투자증권 Open API(KRX)"}
        for key, market in (("day", "F"), ("night", "CM")):
            try:
                result[key] = _parse(await service._rest_get(PATH, TR_ID, {"FID_COND_MRKT_DIV_CODE": market,
                                                                           "FID_INPUT_ISCD": FRONT_MONTH}))
            except Exception as exc:   # 네트워크·토큰 오류: 이 칸만 실패로 표시한다
                result[key] = {"status": "error", "reason": str(exc)[:120]}
        _cache = (monotonic(), result)
        return result
