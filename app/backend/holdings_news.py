"""내 보유종목 주요 뉴스: 한투 뉴스 제목을 스케줄러가 모아 DB 캐시에 두고, 화면은 캐시만 읽는다.

흐름
1) 스케줄러(장 시간대 10분마다): 모든 회원 포트폴리오의 국내 보유 종목 코드(합집합)를 모아 종목마다 한투
   '종합 시황/공시(제목)' TR을 한 번씩 부른다(현재가와 같은 초당 호출 한도를 나눠 씀) → 관련성 필터 →
   새 기사만 네이버 뉴스 검색 API로 원문 URL을 찾아 붙인다(기사당 한 번, 결과도 캐시) → `holding_news_cache`.
2) 화면(`GET /api/portfolio/news`): 로그인 사용자의 보유 종목을 평가 비중 순으로 정렬하고 캐시에서 그 종목들의
   뉴스를 읽는다. 새로고침마다 한투·네이버를 부르지 않는다. 캐시에 아직 없는 새 보유 종목만 한 번 수집을 예약한다.

한투 뉴스 TR은 원문 링크를 주지 않는다(2026-10-02 실응답 확인). 원문을 못 찾으면 네이버 증권의 종목 뉴스 목록으로
연결한다. 투자 권유가 아니며 주문과 무관하다.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime

import httpx

try:
    from .db import get_db
    from .kis_quotes import KisError, domestic_code, kis_service
except ImportError:
    from db import get_db  # type: ignore
    from kis_quotes import KisError, domestic_code, kis_service  # type: ignore

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
CACHE_COLLECTION = "holding_news_cache"
LINK_COLLECTION = "holding_news_links"
ITEMS_PER_STOCK_CACHED = 15
ITEMS_PER_STOCK_SHOWN = 3
MAX_ITEMS_SHOWN = 20
MAX_STOCKS = 20
# 관련 종목이 이보다 많이 붙은 기사는 순위표·시황 정리 같은 자동 생성 목록으로 보고 뺀다(실응답 확인).
MAX_RELATED_FOR_LIST = 5
LIST_TITLE_PATTERN = re.compile(r"상위종목|하위종목|특징종목|마감시황|장마감|시황\)|체결강도|대차거래|공매도 잔고")
# 기사로 들어오는 광고(스탁론·리딩방 등). 실응답에서 확인한 '-씽크풀스탁론-' 형태 포함.
AD_TITLE_PATTERN = re.compile(r"스탁론|주식담보대출|기존대출|무료\s*(체험|상담|추천)|리딩방|급등주\s*추천|수익률\s*보장")
# 제목에서 종목을 다르게 부르는 흔한 이름. 제목에 종목명(또는 별칭)이 있는 기사를 먼저 보여 준다.
NAME_ALIASES = {
    "NAVER": ("네이버",), "SK하이닉스": ("하이닉스",), "삼성전자": ("삼전",), "LG디스플레이": ("LGD", "LG디플"),
    "현대자동차": ("현대차",), "LG에너지솔루션": ("LG엔솔", "엔솔"), "삼성바이오로직스": ("삼바",),
    "두산에너빌리티": ("두산에빌",), "한화에어로스페이스": ("한화에어로",), "POSCO홀딩스": ("포스코홀딩스",),
}
# 네이버 검색 API는 2026-07-31부터 developers.naver.com 신규 발급이 끝나고 NAVER API HUB(네이버 클라우드)로 옮겨졌다.
# API HUB 키(NAVER_SEARCH_*)는 아래 주소·헤더를 쓴다. 응답 필드(title·originallink·link·pubDate)는 예전과 같다.
NAVER_API_HUB_NEWS_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
NAVER_LEGACY_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
NAVER_STOCK_NEWS_URL = "https://m.stock.naver.com/domestic/stock/{code}/news"
TITLE_MATCH_MIN = 0.72
COLLECT_START_HOUR_KST, COLLECT_END_HOUR_KST = 6, 23


# ── 1. 보유 종목(비중 순) ──────────────────────────────────────────

def _last_known_price(code: str) -> float | None:
    """한투 서비스가 이미 들고 있는 가격(실시간 체결·최근 REST)만 쓴다. 네트워크 호출 없음."""
    service = kis_service()
    tick = service.ticks.get(code)
    if tick:
        return float(tick["price"])
    good = service._last_good.get(code)
    return float(good[1]["price"]) if good else None


async def _daily_closes(tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    rows = await get_db().market_ohlcv.aggregate([
        {"$match": {"ticker": {"$in": tickers}, "interval": "1d"}},
        {"$sort": {"ticker": 1, "timestamp": -1}},
        {"$group": {"_id": "$ticker", "close": {"$first": "$close"}}},
    ]).to_list(len(tickers))
    return {row["_id"]: float(row["close"]) for row in rows if row.get("close")}


def weighted_holdings(holdings: list[dict], prices: dict[str, float]) -> list[dict]:
    """국내 보유 종목을 평가금액 비중 순으로. 가격을 모르면 취득금액으로 대신하고 그 사실을 표시한다."""
    rows = []
    for holding in holdings:
        code = domestic_code(holding.get("ticker", ""))
        if not code:
            continue
        quantity = float(holding.get("quantity") or 0)
        price = prices.get(holding["ticker"])
        value = quantity * (price if price else float(holding.get("average_price") or 0))
        rows.append({"code": code, "ticker": holding["ticker"], "name": holding.get("name") or code,
                     "value_krw": value, "basis": "market" if price else "cost"})
    total = sum(row["value_krw"] for row in rows) or 1.0
    for row in rows:
        row["weight_pct"] = round(row["value_krw"] / total * 100, 2)
    return sorted(rows, key=lambda row: (-row["value_krw"], row["code"]))[:MAX_STOCKS]


async def user_holding_codes(user_id) -> list[dict]:
    """로그인 사용자의 '내 자산' 국내 보유 종목 → [{code, ticker, name, weight_pct, basis}] (비중 높은 순)."""
    portfolio = await get_db().portfolios.find_one({"user_id": user_id}) or {}
    holdings = [h for h in portfolio.get("holdings", []) if domestic_code(h.get("ticker", ""))]
    prices = {h["ticker"]: p for h in holdings if (p := _last_known_price(domestic_code(h["ticker"])))}
    prices.update({t: c for t, c in (await _daily_closes([h["ticker"] for h in holdings if h["ticker"] not in prices])).items()})
    return weighted_holdings(holdings, prices)


async def all_held_codes() -> dict[str, str]:
    """모든 회원의 국내 보유 종목 {code: name} (수집 대상)."""
    codes: dict[str, str] = {}
    async for portfolio in get_db().portfolios.find({}, {"holdings.ticker": 1, "holdings.name": 1}):
        for holding in portfolio.get("holdings", []):
            code = domestic_code(holding.get("ticker", ""))
            if code:
                codes.setdefault(code, holding.get("name") or code)
    return codes


# ── 2. 한투 뉴스 → 관련성 필터 ────────────────────────────────────

def _published_at(row: dict) -> str | None:
    day, clock = str(row.get("data_dt") or ""), str(row.get("data_tm") or "").zfill(6)
    if not re.fullmatch(r"\d{8}", day):
        return None
    return f"{day[:4]}-{day[4:6]}-{day[6:]}T{clock[:2]}:{clock[2:4]}:{clock[4:6]}+09:00"


def relevant_news(rows: list[dict], code: str, name: str) -> list[dict]:
    """이 종목에 관한 기사만 남긴다: 제목에 종목명(별칭 포함)이 있거나, 첫 번째 관련 종목이 이 종목이면서
    순위표류가 아닌 기사. 광고는 뺀다. 종목명이 제목에 있는 기사가 앞에 온다."""
    items, seen_titles = [], set()
    names = [re.sub(r"\s+", "", n).lower() for n in (name, *NAME_ALIASES.get(name, ())) if n]
    for row in rows:
        title = html.unescape(str(row.get("hts_pbnt_titl_cntt") or "")).strip()
        related = [str(row.get(f"iscd{i}") or "").strip() for i in range(1, 11)]
        related = [c for c in related if c]
        if not title or code not in related or AD_TITLE_PATTERN.search(title):
            continue
        plain_title = re.sub(r"\s+", "", title).lower()
        in_title = any(n in plain_title for n in names)
        is_list = len(related) > MAX_RELATED_FOR_LIST or bool(LIST_TITLE_PATTERN.search(title))
        if not in_title and (related[0] != code or is_list):
            continue
        key = re.sub(r"\W", "", title)
        if key in seen_titles:   # 같은 기사가 여러 제공사로 들어오는 경우
            continue
        seen_titles.add(key)
        items.append({"id": str(row.get("cntt_usiq_srno") or key), "code": code, "title": title,
                      "source": str(row.get("dorg") or "").strip() or None, "published_at": _published_at(row),
                      "mentions_name": bool(in_title)})
    # 제목에 종목명이 있는 기사를 먼저(대형주는 관련성이 약한 기사도 많이 붙는다), 그 안에서는 최신순.
    items.sort(key=lambda item: (item["mentions_name"], item["published_at"] or ""), reverse=True)
    return items[:ITEMS_PER_STOCK_CACHED]


# ── 3. 원문 링크(네이버 뉴스 검색) ─────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).lower()


def best_link(title: str, published_at: str | None, candidates: list[dict]) -> str | None:
    """네이버 검색 결과 중 제목이 충분히 같고 발행 시각이 이틀 안인 기사의 원문 URL."""
    target = _normalize(title)
    when = datetime.fromisoformat(published_at) if published_at else None
    best, best_score = None, 0.0
    for item in candidates:
        score = SequenceMatcher(None, target, _normalize(item.get("title", ""))).ratio()
        if when and item.get("pubDate"):
            try:
                if abs((parsedate_to_datetime(item["pubDate"]) - when).total_seconds()) > 2 * 86400:
                    continue
            except (TypeError, ValueError):
                pass
        if score > best_score:
            best, best_score = item, score
    if best is None or best_score < TITLE_MATCH_MIN:
        return None
    for url in (best.get("originallink"), best.get("link")):   # http(s) 주소만(javascript: 등 차단)
        if isinstance(url, str) and re.match(r"https?://", url):
            return url
    return None


class NaverNewsSearch:
    def __init__(self, client_id: str = "", client_secret: str = "", http: httpx.AsyncClient | None = None,
                 api_hub: bool = True):
        # NAVER API HUB에서 발급한 검색 키(NAVER_SEARCH_*)를 쓴다. 없으면 예전 개발자센터 방식으로 로그인 키를 시도한다.
        hub_id, hub_secret = os.getenv("NAVER_SEARCH_CLIENT_ID", "").strip(), os.getenv("NAVER_SEARCH_CLIENT_SECRET", "").strip()
        # 키를 직접 넘기면 api_hub 인자를 따르고, 환경변수에서 읽을 때는 API HUB 키가 있을 때만 API HUB 방식이다.
        self.api_hub = api_hub if client_id else bool(hub_id)
        self.client_id = client_id or hub_id or os.getenv("NAVER_CLIENT_ID", "").strip()
        self.client_secret = client_secret or hub_secret or os.getenv("NAVER_CLIENT_SECRET", "").strip()
        self._http = http
        self.disabled_reason: str | None = None if self.client_id and self.client_secret else "네이버 키 없음"

    async def search(self, title: str) -> list[dict]:
        if self.disabled_reason:
            return []
        query = re.sub(r"[\[\]【】<>“”\"'‘’…·]", " ", title)[:80]
        client = self._http or httpx.AsyncClient(timeout=6)
        try:
            if self.api_hub:
                url, headers = NAVER_API_HUB_NEWS_URL, {"X-NCP-APIGW-API-KEY-ID": self.client_id,
                                                        "X-NCP-APIGW-API-KEY": self.client_secret}
            else:
                url, headers = NAVER_LEGACY_NEWS_URL, {"X-Naver-Client-Id": self.client_id,
                                                       "X-Naver-Client-Secret": self.client_secret}
            response = await client.get(url, params={"query": query, "display": 5, "sort": "sim"}, headers=headers)
        finally:
            if self._http is None:
                await client.aclose()
        if response.status_code in (401, 403):
            # 애플리케이션에 '검색' API가 추가되지 않았거나 키가 다르다. 이번 수집에서는 더 부르지 않는다.
            self.disabled_reason = f"네이버 검색 API 권한 없음({response.status_code})"
            return []
        response.raise_for_status()
        return response.json().get("items") or []


async def attach_links(items: list[dict], naver: NaverNewsSearch) -> None:
    """기사마다 원문 URL을 붙인다. 한 번 찾은(또는 못 찾은) 결과는 DB에 두고 다시 검색하지 않는다."""
    links = get_db()[LINK_COLLECTION]
    known = {row["_id"]: row async for row in links.find({"_id": {"$in": [item["id"] for item in items]}})}
    for item in items:
        row = known.get(item["id"])
        if row is None:
            url, kind = None, "stock_news"
            try:
                url = best_link(item["title"], item["published_at"], await naver.search(item["title"]))
            except (httpx.HTTPError, ValueError) as exc:
                logger.info("네이버 뉴스 검색 실패: %s", exc)
            if url:
                kind = "original"
            if not naver.disabled_reason or url:   # 권한 문제로 못 찾은 것은 저장하지 않고 다음에 다시 시도
                await links.update_one({"_id": item["id"]}, {"$set": {"url": url, "kind": kind,
                                                                      "checked_at": datetime.now(timezone.utc)}}, upsert=True)
            row = {"url": url, "kind": kind}
        item["url"] = row.get("url") or NAVER_STOCK_NEWS_URL.format(code=item["code"])
        item["link_kind"] = "original" if row.get("url") else "stock_news"


# ── 4. 수집(스케줄러) ──────────────────────────────────────────────

_collect_lock = asyncio.Lock()


async def collect_codes(codes: dict[str, str]) -> dict:
    service = kis_service()
    if not service.enabled:
        return {"collected": 0, "skipped": "한투 키 없음"}
    naver = NaverNewsSearch()
    cache = get_db()[CACHE_COLLECTION]
    collected, failed = 0, 0
    for code, name in codes.items():
        now = datetime.now(timezone.utc)
        try:
            items = relevant_news(await service.news_titles(code), code, name)
            await attach_links(items, naver)
            await cache.update_one({"_id": code}, {"$set": {"name": name, "items": items, "fetched_at": now, "error": None}},
                                   upsert=True)
            collected += 1
        except (KisError, httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
            failed += 1
            await cache.update_one({"_id": code}, {"$set": {"name": name, "error": f"{exc}", "error_at": now},
                                                   "$setOnInsert": {"items": []}}, upsert=True)
    return {"collected": collected, "failed": failed, "naver": naver.disabled_reason or "ok"}


async def collect_holdings_news(force: bool = False) -> dict:
    """스케줄러 작업: 모든 회원의 국내 보유 종목 뉴스를 한 번 모은다. 겹쳐 실행되지 않는다."""
    hour = datetime.now(KST).hour
    if not force and not COLLECT_START_HOUR_KST <= hour < COLLECT_END_HOUR_KST:
        return {"collected": 0, "skipped": "수집 시간 외"}
    if _collect_lock.locked():
        return {"collected": 0, "skipped": "이미 수집 중"}
    async with _collect_lock:
        result = await collect_codes(await all_held_codes())
    logger.info("보유종목 뉴스 수집: %s", result)
    return result


_pending: set[str] = set()


def schedule_missing(codes: dict[str, str]) -> None:
    """캐시에 아직 없는 새 보유 종목만 한 번 수집한다(화면 새로고침마다 부르지 않도록 중복 예약 방지)."""
    new = {code: name for code, name in codes.items() if code not in _pending}
    if not new or not kis_service().enabled:
        return
    _pending.update(new)

    async def run() -> None:
        try:
            async with _collect_lock:
                await collect_codes(new)
        finally:
            _pending.difference_update(new)

    asyncio.get_running_loop().create_task(run())


# ── 5. 화면용 읽기(캐시만) ─────────────────────────────────────────

async def holdings_news_feed(user_id) -> dict:
    holdings = await user_holding_codes(user_id)
    if not holdings:
        return {"holdings": [], "items": [], "fetched_at": None, "note": "내 자산에 국내 보유 종목이 없습니다."}
    docs = {doc["_id"]: doc async for doc in get_db()[CACHE_COLLECTION].find({"_id": {"$in": [h["code"] for h in holdings]}})}
    missing = {h["code"]: h["name"] for h in holdings if h["code"] not in docs}
    if missing:
        schedule_missing(missing)
    items = []
    for holding in holdings:   # 비중 높은 종목부터, 종목 안에서는 최신순
        for item in (docs.get(holding["code"], {}).get("items") or [])[:ITEMS_PER_STOCK_SHOWN]:
            items.append({**item, "name": holding["name"], "weight_pct": holding["weight_pct"]})
    fetched = [doc["fetched_at"] for doc in docs.values() if doc.get("fetched_at")]
    return {
        "holdings": [{k: h[k] for k in ("code", "name", "weight_pct", "basis")} for h in holdings],
        "items": items[:MAX_ITEMS_SHOWN],
        "fetched_at": max(fetched).replace(tzinfo=timezone.utc).isoformat() if fetched else None,
        "pending": sorted(missing),
        "errors": {code: doc["error"] for code, doc in docs.items() if doc.get("error")},
        "note": "한국투자증권 뉴스 제목(10분 주기 수집). 원문은 네이버 뉴스 검색으로 찾고, 못 찾으면 네이버 증권 종목 뉴스로 연결합니다.",
    }
