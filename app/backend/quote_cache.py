"""Yahoo Finance 지연 시세를 짧은 TTL로 캐싱하는 공유 모듈.

`db.py`/`routers/auth.py`처럼 여러 라우터(그리고 백그라운드 스케줄러)가 함께
쓰는 몇 안 되는 공유 모듈이다. `portfolio.py`가 처음 만든 캐시 로직을
`strategy.py`·`scheduler.py`에서도 재사용할 수 있도록 이곳으로 옮겼다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from time import monotonic

QUOTE_CACHE_TTL_SECONDS = 20
_cache: dict[str, tuple[float, dict]] = {}
_lock = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_quote(ticker: str) -> dict:
    """Yahoo Finance 지연 시세를 조회한다. 실패하면 사유를 함께 반환한다."""
    import yfinance as yf

    try:
        fast_info = yf.Ticker(ticker).fast_info
        current = float(fast_info.last_price)
        previous = float(fast_info.previous_close) if fast_info.previous_close else current
        change_pct = ((current / previous) - 1) * 100 if previous else 0.0
        return {
            "status": "ok",
            "price": current,
            "change_pct": round(change_pct, 2),
            "quoted_at": _now_iso(),
        }
    except Exception as exc:
        return {"status": "error", "reason": f"시세 조회 실패: {exc}"}


def fetch_quote_cached(ticker: str) -> dict:
    """짧은 TTL 캐시를 거쳐 동일 종목에 대한 중복 Yahoo 요청을 줄인다."""
    now = monotonic()
    with _lock:
        cached = _cache.get(ticker)
        if cached and now - cached[0] < QUOTE_CACHE_TTL_SECONDS:
            return cached[1]
    quote = fetch_quote(ticker)
    with _lock:
        _cache[ticker] = (now, quote)
    return quote
