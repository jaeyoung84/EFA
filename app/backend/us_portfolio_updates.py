"""미국 보유종목 뉴스와 SEC 제출자료를 백그라운드 수집·캐시한다.

Codex 수정: CODX-USPORT-001 (2026-10-12), 파일 전체.
화면 요청은 외부 공급자를 직접 호출하지 않고 이 모듈의 MongoDB 캐시만 읽는다.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import httpx

try:
    from .db import get_db
except ImportError:
    from db import get_db  # type: ignore

logger = logging.getLogger(__name__)
US_NEWS_CACHE_COLLECTION = "us_holding_news_cache"
SEC_CACHE_COLLECTION = "us_holding_sec_filings_cache"
YAHOO_NEWS_URL = "https://query1.finance.yahoo.com/v1/finance/search"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
ITEMS_PER_STOCK_CACHED = 15
ITEMS_PER_STOCK_SHOWN = 3
MAX_ITEMS_SHOWN = 20
MAX_US_STOCKS = 20
SEC_FORMS = {"10-K", "10-Q", "8-K", "6-K", "20-F", "DEF 14A", "S-1", "SC 13D", "SC 13G", "4"}
US_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,19}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_us_equity(holding: dict) -> bool:
    """USD 보유 중 지수·환율 심볼을 제외한 미국 주식형 티커만 수집 대상으로 본다."""
    ticker = str(holding.get("ticker") or "").strip().upper()
    return (
        holding.get("currency") == "USD"
        and bool(US_TICKER_PATTERN.fullmatch(ticker))
        and not ticker.startswith("^")
        and not ticker.endswith("=X")
    )


def weighted_us_holdings(holdings: list[dict]) -> list[dict]:
    """미국 보유분 안에서 취득금액(USD) 비중을 계산한다. 뉴스 정렬용 참고값이다."""
    rows = []
    for holding in holdings:
        if not is_us_equity(holding):
            continue
        quantity = float(holding.get("quantity") or 0)
        cost_usd = quantity * float(holding.get("average_price") or 0)
        ticker = str(holding["ticker"]).strip().upper()
        rows.append({
            "ticker": ticker,
            "name": holding.get("name") or ticker,
            "value_usd": cost_usd,
            "basis": "cost_usd",
            "market": "US",
        })
    total = sum(row["value_usd"] for row in rows) or 1.0
    for row in rows:
        row["weight_pct"] = round(row["value_usd"] / total * 100, 2)
    return sorted(rows, key=lambda row: (-row["value_usd"], row["ticker"]))[:MAX_US_STOCKS]


async def user_us_holdings(user_id) -> list[dict]:
    portfolio = await get_db().portfolios.find_one({"user_id": user_id}) or {}
    return weighted_us_holdings(portfolio.get("holdings", []))


async def all_us_holdings() -> dict[str, str]:
    """모든 회원의 미국 보유종목 합집합. 종목당 한 번만 외부 자료를 수집한다."""
    tickers: dict[str, str] = {}
    async for portfolio in get_db().portfolios.find({}, {"holdings": 1}):
        for holding in portfolio.get("holdings", []):
            if is_us_equity(holding):
                ticker = str(holding["ticker"]).strip().upper()
                tickers.setdefault(ticker, holding.get("name") or ticker)
    return tickers


def _safe_http_url(value: object) -> str | None:
    url = str(value or "").strip()
    return url if re.match(r"^https?://", url, re.I) else None


def parse_yahoo_news(payload: dict, ticker: str) -> list[dict]:
    items, seen = [], set()
    for row in payload.get("news") or []:
        related = {str(value).upper() for value in (row.get("relatedTickers") or [])}
        if related and ticker.upper() not in related:
            continue
        title = str(row.get("title") or "").strip()
        url = _safe_http_url(row.get("link"))
        if not title or not url or title.lower() in seen:
            continue
        seen.add(title.lower())
        published = row.get("providerPublishTime")
        try:
            published_at = datetime.fromtimestamp(float(published), timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            published_at = None
        items.append({
            "id": str(row.get("uuid") or f"{ticker}:{len(items)}:{title}"),
            "ticker": ticker,
            "title": title,
            "source": str(row.get("publisher") or "Yahoo Finance"),
            "published_at": published_at,
            "url": url,
            "link_kind": "original",
            "market": "US",
        })
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return items[:ITEMS_PER_STOCK_CACHED]


class YahooUsNews:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self.http = http

    async def fetch(self, ticker: str) -> list[dict]:
        client = self.http or httpx.AsyncClient(timeout=10, follow_redirects=True)
        close = self.http is None
        try:
            response = await client.get(
                YAHOO_NEWS_URL,
                params={"q": ticker, "quotesCount": 0, "newsCount": ITEMS_PER_STOCK_CACHED},
                headers={"User-Agent": "Mozilla/5.0 EFA-Quant/1.0"},
            )
            response.raise_for_status()
            return parse_yahoo_news(response.json(), ticker)
        finally:
            if close:
                await client.aclose()


# Claude Code 수정 (2026-10-12, 사용자 승인): 미국 보유종목에 네이버 뉴스(한글)를 더한다.
# 한글 별칭이 있으면 그 이름(TSLA -> 테슬라), ETF는 티커, 그 밖에는 법인 접미어를 뗀 영문 회사명으로 검색하고
# 제목에 그 이름이나 티커가 있는 기사만 남긴다. Yahoo(영문)와 번갈아 저장해 두 출처가 함께 보이게 한다.
NAVER_ITEMS = 10
_ETF_NAME = re.compile(r"\b(ETF|Trust|ProShares|Direxion|SPDR|iShares|Vanguard|Invesco|Schwab)\b", re.I)


def naver_query(ticker: str, name: str) -> str:
    try:
        from .us_search_aliases import US_KOREAN_ALIASES
    except ImportError:
        from us_search_aliases import US_KOREAN_ALIASES  # type: ignore
    alias_name = ""
    for alias, (alias_ticker, display) in US_KOREAN_ALIASES.items():
        if alias_ticker == ticker:
            alias_name = display
            if re.search(r"[가-힣]", alias) and not _ETF_NAME.search(display):
                return alias
            break
    if _ETF_NAME.search(alias_name or name or ""):
        return ticker
    words = re.sub(r"[^0-9A-Za-z&]+", " ", name or ticker).split()
    words = [w for w in words if w.lower() not in {"inc", "corp", "corporation", "co", "company", "ltd", "plc", "holdings", "the", "com"}]
    return " ".join(words[:3]) or ticker


def _plain(text: object) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def parse_naver_news(rows: list[dict], ticker: str, query: str) -> list[dict]:
    """제목에 검색 이름(공백 무시)이나 티커가 들어간 기사만, 같은 제목은 한 번만."""
    needle, items, seen = re.sub(r"\s+", "", query).lower(), [], set()
    for row in rows or []:
        title = _plain(row.get("title"))
        compact = re.sub(r"\s+", "", title).lower()
        url = _safe_http_url(row.get("originallink")) or _safe_http_url(row.get("link"))
        if not title or not url or compact in seen:
            continue
        if needle not in compact and not re.search(rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])", title):
            continue
        seen.add(compact)
        try:
            published_at = parsedate_to_datetime(str(row.get("pubDate"))).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            published_at = None
        host = urlparse(url).hostname or "네이버 뉴스"
        items.append({"id": "naver:" + hashlib.sha1(url.encode()).hexdigest()[:16], "ticker": ticker, "title": title,
                      "source": host.removeprefix("www."), "published_at": published_at, "url": url,
                      "link_kind": "original", "market": "US", "provider": "naver"})
    items.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return items[:NAVER_ITEMS]


async def fetch_naver_news(naver, ticker: str, name: str) -> list[dict]:
    query = naver_query(ticker, name)
    return parse_naver_news(await naver.search(query, display=20, sort="date"), ticker, query)


def interleave(first: list[dict], second: list[dict], limit: int) -> list[dict]:
    """두 출처를 한 건씩 번갈아(각자 최신순) 합친다. 종목당 3건만 보여도 두 출처가 섞인다."""
    out = []
    for index in range(max(len(first), len(second))):
        out.extend(item for item in (first[index] if index < len(first) else None,
                                     second[index] if index < len(second) else None) if item)
    return out[:limit]


def _naver_search():
    try:
        from .holdings_news import NaverNewsSearch
    except ImportError:
        from holdings_news import NaverNewsSearch  # type: ignore
    return NaverNewsSearch()


def parse_sec_filings(payload: dict, ticker: str) -> list[dict]:
    recent = ((payload.get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    items = []
    cik = int(payload.get("cik") or 0)
    company = str(payload.get("name") or ticker)
    for index, form in enumerate(forms):
        base_form = str(form).removesuffix("/A")
        if base_form not in SEC_FORMS:
            continue
        try:
            accession = str(recent["accessionNumber"][index])
            filing_date = str(recent["filingDate"][index])
            primary = str(recent.get("primaryDocument", [])[index] or "")
            description = str(recent.get("primaryDocDescription", [])[index] or "")
        except (IndexError, KeyError, TypeError):
            continue
        compact = accession.replace("-", "")
        document = quote(primary, safe="._-") if primary else f"{accession}-index.html"
        url = SEC_ARCHIVE_URL.format(cik=cik, accession=compact, document=document)
        items.append({
            "id": accession,
            "ticker": ticker,
            "company": company,
            "form": form,
            "title": description or f"SEC {form}",
            "filed_at": filing_date,
            "url": url,
            "source": "SEC EDGAR",
            "market": "US",
        })
        if len(items) >= ITEMS_PER_STOCK_CACHED:
            break
    return items


class SecFilings:
    """SEC 공정접근 정책에 맞춰 식별 가능한 User-Agent가 있을 때만 호출한다."""

    def __init__(self, user_agent: str | None = None, http: httpx.AsyncClient | None = None):
        self.user_agent = (user_agent if user_agent is not None else os.getenv("SEC_USER_AGENT", "")).strip()
        self.http = http
        self.disabled_reason = None if "@" in self.user_agent else "SEC_USER_AGENT에 운영 연락 이메일이 필요합니다."
        self._ticker_map: dict[str, dict] | None = None

    async def _get_json(self, url: str) -> dict:
        if self.disabled_reason:
            raise ValueError(self.disabled_reason)
        client = self.http or httpx.AsyncClient(timeout=15, follow_redirects=True)
        close = self.http is None
        try:
            response = await client.get(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
            response.raise_for_status()
            return response.json()
        finally:
            if close:
                await client.aclose()

    async def ticker_map(self) -> dict[str, dict]:
        if self._ticker_map is None:
            payload = await self._get_json(SEC_TICKERS_URL)
            self._ticker_map = {
                str(row.get("ticker") or "").upper(): row
                for row in payload.values()
                if row.get("ticker") and row.get("cik_str")
            }
        return self._ticker_map

    async def fetch(self, ticker: str) -> list[dict]:
        row = (await self.ticker_map()).get(ticker.upper())
        if not row:
            raise ValueError(f"SEC CIK를 찾지 못했습니다: {ticker}")
        cik = str(int(row["cik_str"])).zfill(10)
        payload = await self._get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
        return parse_sec_filings(payload, ticker.upper())


async def collect_us_tickers(
    tickers: dict[str, str],
    *,
    yahoo: YahooUsNews | None = None,
    sec: SecFilings | None = None,
    naver=None,
) -> dict:
    """미국 뉴스(Yahoo 영문 + 네이버 한글)와 SEC 제출자료를 순차 수집한다. SEC 한도(10 req/s)보다 충분히 낮다."""
    yahoo = yahoo or YahooUsNews()
    sec = sec or SecFilings()
    naver = naver or _naver_search()
    news_cache = get_db()[US_NEWS_CACHE_COLLECTION]
    sec_cache = get_db()[SEC_CACHE_COLLECTION]
    news_ok = sec_ok = failed = 0
    for ticker, name in tickers.items():
        now = _now()
        # Claude Code 수정 (2026-10-12, 사용자 승인): 두 출처 중 하나만 성공해도 저장하고, 실패한 쪽은 partial_error로 남긴다.
        sources, errors = {}, []
        for label, fetch in (("yahoo", lambda: yahoo.fetch(ticker)), ("naver", lambda: fetch_naver_news(naver, ticker, name))):
            try:
                sources[label] = await fetch()
            except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
                errors.append(f"{label}: {exc}")
        if sources:
            items = interleave(sources.get("naver", []), sources.get("yahoo", []), ITEMS_PER_STOCK_CACHED * 2)
            await news_cache.update_one(
                {"_id": ticker},
                {"$set": {"name": name, "items": items, "fetched_at": now, "error": None,
                          "partial_error": "; ".join(errors) or None,
                          "source_counts": {label: len(rows) for label, rows in sources.items()}}},
                upsert=True,
            )
            news_ok += 1
        else:
            failed += 1
            await news_cache.update_one(
                {"_id": ticker},
                {"$set": {"name": name, "error": "; ".join(errors), "error_at": now}, "$setOnInsert": {"items": []}},
                upsert=True,
            )
        if sec.disabled_reason:
            await sec_cache.update_one(
                {"_id": ticker},
                {"$set": {"name": name, "error": sec.disabled_reason, "error_at": now},
                 "$setOnInsert": {"items": []}},
                upsert=True,
            )
            continue
        try:
            filings = await sec.fetch(ticker)
            await sec_cache.update_one(
                {"_id": ticker},
                {"$set": {"name": name, "items": filings, "fetched_at": now, "error": None}},
                upsert=True,
            )
            sec_ok += 1
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
            failed += 1
            await sec_cache.update_one(
                {"_id": ticker},
                {"$set": {"name": name, "error": str(exc), "error_at": now}, "$setOnInsert": {"items": []}},
                upsert=True,
            )
        await asyncio.sleep(0.12)
    return {"news_collected": news_ok, "sec_collected": sec_ok, "failed": failed,
            "sec": sec.disabled_reason or "ok", "naver": getattr(naver, "disabled_reason", None) or "ok"}


_collect_lock = asyncio.Lock()


async def collect_us_portfolio_updates() -> dict:
    if _collect_lock.locked():
        return {"news_collected": 0, "sec_collected": 0, "skipped": "이미 수집 중"}
    async with _collect_lock:
        result = await collect_us_tickers(await all_us_holdings())
    logger.info("미국 보유종목 뉴스·SEC 수집: %s", result)
    return result


# Claude Code 수정 (2026-10-12, 사용자 승인): 캐시가 아직 없는 새 미국 종목은 정기 수집(1시간)을 기다리지 않고
# 그 종목만 바로 한 번 수집한다. 화면을 새로고침할 때마다 다시 예약하지 않도록 진행 중인 종목은 건너뛴다.
_pending_us: set[str] = set()


def schedule_missing_us(tickers: dict[str, str]) -> None:
    new = {ticker: name for ticker, name in tickers.items() if ticker not in _pending_us}
    if not new:
        return
    _pending_us.update(new)

    async def run() -> None:
        try:
            async with _collect_lock:
                await collect_us_tickers(new)
        except Exception:   # 수집 실패는 캐시의 error로 남고 다음 정기 수집이 다시 시도한다
            logger.warning("새 미국 보유종목 즉시 수집 실패: %s", sorted(new), exc_info=True)
        finally:
            _pending_us.difference_update(new)

    asyncio.get_running_loop().create_task(run())


async def _cached_feed(user_id, collection_name: str, item_key: str) -> dict:
    holdings = await user_us_holdings(user_id)
    if not holdings:
        return {"holdings": [], item_key: [], "fetched_at": None, "pending": []}
    tickers = [holding["ticker"] for holding in holdings]
    docs = {doc["_id"]: doc async for doc in get_db()[collection_name].find({"_id": {"$in": tickers}})}
    pending = [ticker for ticker in tickers if ticker not in docs]
    if pending:
        schedule_missing_us({holding["ticker"]: holding["name"] for holding in holdings if holding["ticker"] in pending})
    items = []
    for holding in holdings:
        for item in (docs.get(holding["ticker"], {}).get("items") or [])[:ITEMS_PER_STOCK_SHOWN]:
            items.append({**item, "name": holding["name"], "weight_pct": holding["weight_pct"]})
    fetched = [doc["fetched_at"] for doc in docs.values() if doc.get("fetched_at")]
    return {
        "holdings": holdings,
        item_key: items[:MAX_ITEMS_SHOWN],
        "fetched_at": max(fetched).replace(tzinfo=timezone.utc).isoformat() if fetched else None,
        "pending": pending,
        "errors": {ticker: doc["error"] for ticker, doc in docs.items() if doc.get("error")},
    }


async def us_news_feed(user_id) -> dict:
    return await _cached_feed(user_id, US_NEWS_CACHE_COLLECTION, "items")


async def us_disclosures_feed(user_id) -> dict:
    result = await _cached_feed(user_id, SEC_CACHE_COLLECTION, "items")
    result["note"] = "미국 보유종목의 SEC EDGAR 최근 제출자료입니다. 투자판단 참고자료이며 매매 신호가 아닙니다."
    result["source"] = "sec_edgar_submissions"
    if not result["items"] and result.get("errors"):
        result["error"] = next(iter(result["errors"].values()))
    return result
