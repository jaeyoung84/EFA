"""금일 상승종목: 코스피·코스닥 전 종목 등락률 순위(장중 지연 시세).

출처는 네이버 증권 모바일 화면이 쓰는 순위 데이터(공식 공개 API 아님)다. 사이트 방문자 수와 관계없이
시장별로 30초에 한 번만 가져오도록 서버에서 캐시하고, 가져오지 못하면 마지막 값(10분 이내)을 '지연'으로
표시하거나 이유와 함께 실패를 돌려준다. 값을 만들어 채우지 않는다. 매수 추천이 아니다.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timezone

SOURCE = "네이버 증권(장중 지연될 수 있음)"
UP_URL = "https://m.stock.naver.com/api/stocks/up/{market}?page=1&pageSize={size}"
MARKETS = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
FETCH_SIZE = 100            # 주식만 남기고 필터를 적용해도 20개가 남도록 넉넉히
CACHE_SECONDS = 30
STALE_LIMIT_SECONDS = 600
USER_AGENT = "Mozilla/5.0 (compatible; EFA-market-movers/1.0)"

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


class MarketMoversError(RuntimeError):
    """The ranking could not be fetched and no recent copy exists."""


def _num(value: object) -> float | None:
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text) if text and text not in {"-", "N/A"} else None
    except ValueError:
        return None


def parse_up_ranking(payload: dict, market: str) -> dict:
    """네이버 상승 순위 응답 → 주식만(ETF·ETN 제외), 원 단위로 정리."""
    suffix = MARKETS[market]
    items = []
    for row in payload.get("stocks") or []:
        if row.get("stockEndType") != "stock":
            continue
        code = str(row.get("itemCode") or "").strip()
        price = _num(row.get("closePrice"))
        change_pct = _num(row.get("fluctuationsRatio"))
        if not code or price is None or change_pct is None:
            continue
        direction = (row.get("compareToPreviousPrice") or {}).get("name", "")
        change = _num(row.get("compareToPreviousClosePrice"))
        if change is not None and direction in {"FALLING", "LOWER_LIMIT"}:
            change = -abs(change)
        value_million = _num(row.get("accumulatedTradingValue"))     # 백만원
        cap_hundred_million = _num(row.get("marketValue"))           # 억원
        items.append({
            "code": code, "ticker": code + suffix, "name": str(row.get("stockName") or code), "market": market,
            "price": price, "change": change, "change_pct": change_pct,
            "limit_up": direction == "UPPER_LIMIT",
            "volume": _num(row.get("accumulatedTradingVolume")),
            "trading_value_krw": value_million * 1_000_000 if value_million is not None else None,
            "market_cap_krw": cap_hundred_million * 100_000_000 if cap_hundred_million is not None else None,
            "traded_at": row.get("localTradedAt"),
        })
    items.sort(key=lambda item: -item["change_pct"])
    latest = max((item["traded_at"] for item in items if item["traded_at"]), default=None)
    return {"market": market, "items": items, "market_status": payload.get("marketStatus"),
            "as_of": latest, "total_count": payload.get("totalCount")}


def _fetch(market: str) -> dict:
    request = urllib.request.Request(UP_URL.format(market=market, size=FETCH_SIZE),
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def top_gainers(market: str, limit: int = 20, min_trading_value_krw: float = 0, fetch=None,
                clock=time.monotonic) -> dict:
    market = market.upper()
    if market not in MARKETS:
        raise ValueError("market은 KOSPI 또는 KOSDAQ입니다.")
    now = clock()
    stale = False
    with _lock:
        hit = _cache.get(market)
    if hit and now - hit[0] < CACHE_SECONDS:
        ranking, cached = hit[1], True
    else:
        try:
            ranking = parse_up_ranking((fetch or _fetch)(market), market)
            ranking["fetched_at"] = datetime.now(timezone.utc).isoformat()
            with _lock:
                _cache[market] = (now, ranking)
            cached = False
        except Exception as exc:  # 네트워크·형식 오류
            if hit and now - hit[0] < STALE_LIMIT_SECONDS:
                ranking, cached, stale = hit[1], True, True
            else:
                raise MarketMoversError(f"{market} 상승 순위를 가져오지 못했습니다: {exc}") from exc
    rows = [item for item in ranking["items"]
            if not min_trading_value_krw or (item["trading_value_krw"] or 0) >= min_trading_value_krw]
    limit = max(1, min(int(limit), 50))
    return {
        "market": market, "items": [{**item, "rank": index + 1} for index, item in enumerate(rows[:limit])],
        "market_status": ranking.get("market_status"), "as_of": ranking.get("as_of"),
        "fetched_at": ranking.get("fetched_at"), "total_count": ranking.get("total_count"),
        "min_trading_value_krw": min_trading_value_krw, "cached": cached, "stale": stale,
        "refresh_seconds": CACHE_SECONDS, "source": SOURCE,
    }


def _reset_cache_for_tests() -> None:
    with _lock:
        _cache.clear()
