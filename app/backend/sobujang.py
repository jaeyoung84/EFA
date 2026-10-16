"""EINT-049: 금일 반도체 소부장 — 네이버 증권 테마 '반도체 장비'·'반도체 재료/부품'·'반도체 기판' 전 종목(장중 지연 시세).

예전 화면은 대표 20종목 표본을 Yahoo 시세로 계산했는데, Yahoo의 '전일 종가'가 코스닥 종목에서 이틀 전 값으로 오는 일이 있어
등락률이 틀렸다(2026-10-16 원익IPS: Yahoo +6.16% vs 거래소 +3.41%). 이제 네이버가 주는 거래소 등락률을 그대로 쓰고,
세 테마의 합집합 전체(약 170종목, 삼성전자·SK하이닉스 같은 완제품 업체 제외)를 대상으로 한다.
테마 분류는 네이버의 것이며 공식 업종 분류가 아니다. 시장별 30초에 한 번만 가져오고, 실패하면 10분 이내 마지막 값을
'지연'으로 표시하거나 이유와 함께 실패를 돌려준다. 값을 만들어 채우지 않는다. 매수 추천이 아니다.
"""
from __future__ import annotations

import json
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timezone

SOURCE = "네이버 증권 테마(장중 지연될 수 있음)"
THEMES = {12: "반도체 장비", 14: "반도체 재료/부품", 608: "반도체 기판"}
THEME_URL = "https://m.stock.naver.com/api/stocks/theme/{no}?page={page}&pageSize=100"
SUFFIX = {"0": ".KS", "1": ".KQ"}
CACHE_SECONDS = 30
STALE_LIMIT_SECONDS = 600
MAX_PAGES = 5
USER_AGENT = "Mozilla/5.0 (compatible; EFA-sobujang/1.0)"

_cache: tuple[float, dict] | None = None
_lock = threading.Lock()


class SobujangError(RuntimeError):
    """The universe could not be fetched and no recent copy exists."""


def _num(value: object) -> float | None:
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text) if text and text not in {"-", "N/A"} else None
    except ValueError:
        return None


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_theme(no: int, fetch=_fetch_json) -> list[dict]:
    rows, page = [], 1
    while page <= MAX_PAGES:
        payload = fetch(THEME_URL.format(no=no, page=page))
        rows += payload.get("stocks") or []
        if page * 100 >= int(payload.get("totalCount") or 0):
            break
        page += 1
    return rows


def merge_themes(themes: dict[int, list[dict]]) -> list[dict]:
    """테마별 응답 → 종목 하나당 한 행(속한 테마 모두 표시). 주식만, 가격·등락률이 있는 것만."""
    merged: dict[str, dict] = {}
    for no, rows in themes.items():
        for row in rows:
            code = str(row.get("itemCode") or "").strip()
            price, pct = _num(row.get("closePrice")), _num(row.get("fluctuationsRatio"))
            if row.get("stockEndType") != "stock" or not code or price is None or pct is None:
                continue
            if code in merged:
                merged[code]["themes"].append(THEMES[no])
                continue
            direction = (row.get("compareToPreviousPrice") or {}).get("name", "")
            change = _num(row.get("compareToPreviousClosePrice"))
            if change is not None and direction in {"FALLING", "LOWER_LIMIT"}:
                change = -abs(change)
            sosok = str(row.get("sosok") or "")
            value_million = _num(row.get("accumulatedTradingValue"))       # 백만원
            cap_hundred_million = _num(row.get("marketValue"))             # 억원
            merged[code] = {
                "code": code, "ticker": code + SUFFIX.get(sosok, ".KS"), "name": str(row.get("stockName") or code),
                "market": "KOSDAQ" if sosok == "1" else "KOSPI", "themes": [THEMES[no]],
                "price": price, "change": change, "change_pct": pct,
                "limit_up": direction == "UPPER_LIMIT", "limit_down": direction == "LOWER_LIMIT",
                "trading_value_krw": value_million * 1_000_000 if value_million is not None else None,
                "market_cap_krw": cap_hundred_million * 100_000_000 if cap_hundred_million is not None else None,
                "traded_at": row.get("localTradedAt"),
            }
    for item in merged.values():
        item["sector"] = " · ".join(item["themes"])
    return sorted(merged.values(), key=lambda item: (-item["change_pct"], -(item["trading_value_krw"] or 0), item["code"]))


def summarize(items: list[dict]) -> dict:
    pcts = [item["change_pct"] for item in items]
    by_theme = {}
    for name in THEMES.values():
        group = [item["change_pct"] for item in items if name in item["themes"]]
        if group:
            by_theme[name] = {"count": len(group), "median_change_pct": round(statistics.median(group), 2)}
    return {"up": sum(p > 0 for p in pcts), "down": sum(p < 0 for p in pcts), "flat": sum(p == 0 for p in pcts),
            "median_change_pct": round(statistics.median(pcts), 2) if pcts else None,
            "mean_change_pct": round(statistics.fmean(pcts), 2) if pcts else None, "by_theme": by_theme}


def _build(fetch) -> dict:
    themes = {no: fetch_theme(no, fetch) for no in THEMES}
    items = merge_themes(themes)
    if not items:
        raise SobujangError("소부장 테마 응답에 종목이 없습니다.")
    return {"items": items, "summary": summarize(items), "universe": "반도체 소부장(네이버 테마: " + ", ".join(THEMES.values()) + ")",
            "universe_size": len(items), "source": SOURCE, "fetched_at": datetime.now(timezone.utc).isoformat()}


def sobujang_ranking(limit: int = 20, fetch=None, now=None) -> dict:
    """limit=0이면 전체. 30초 캐시, 실패 시 10분 이내 마지막 값을 stale로."""
    global _cache
    now = now if now is not None else time.monotonic()
    with _lock:
        cached = _cache
    stale = False
    if cached and now - cached[0] < CACHE_SECONDS:
        data, from_cache = cached[1], True
    else:
        try:
            data = _build(fetch or _fetch_json)
            with _lock:
                _cache = (now, data)
            from_cache = False
        except Exception as exc:                        # 네트워크·형식 오류: 최근 값이 있으면 지연 표시
            if cached and now - cached[0] < STALE_LIMIT_SECONDS:
                data, from_cache, stale = cached[1], True, True
            else:
                raise SobujangError(f"네이버 소부장 테마를 가져오지 못했습니다: {str(exc)[:120]}") from exc
    items = data["items"] if limit <= 0 else data["items"][:limit]
    ranked = [{**item, "rank": index} for index, item in enumerate(items, start=1)]
    return {**{k: v for k, v in data.items() if k != "items"}, "items": ranked, "available": bool(ranked),
            "cached": from_cache, "stale": stale, "excluded": [], "is_simulated": False}


def _reset_cache_for_tests() -> None:
    global _cache
    with _lock:
        _cache = None
