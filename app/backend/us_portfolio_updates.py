"""미국 보유종목 뉴스와 SEC 제출자료를 백그라운드 수집·캐시한다.

Codex 수정: CODX-USPORT-001 (2026-10-12), 파일 전체.
화면 요청은 외부 공급자를 직접 호출하지 않고 이 모듈의 MongoDB 캐시만 읽는다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

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
) -> dict:
    """미국 뉴스와 SEC 제출자료를 순차 수집한다. SEC 한도(10 req/s)보다 충분히 낮다."""
    yahoo = yahoo or YahooUsNews()
    sec = sec or SecFilings()
    news_cache = get_db()[US_NEWS_CACHE_COLLECTION]
    sec_cache = get_db()[SEC_CACHE_COLLECTION]
    news_ok = sec_ok = failed = 0
    for ticker, name in tickers.items():
        now = _now()
        try:
            items = await yahoo.fetch(ticker)
            await news_cache.update_one(
                {"_id": ticker},
                {"$set": {"name": name, "items": items, "fetched_at": now, "error": None}},
                upsert=True,
            )
            news_ok += 1
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
            failed += 1
            await news_cache.update_one(
                {"_id": ticker},
                {"$set": {"name": name, "error": str(exc), "error_at": now}, "$setOnInsert": {"items": []}},
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
            "sec": sec.disabled_reason or "ok"}


_collect_lock = asyncio.Lock()


async def collect_us_portfolio_updates() -> dict:
    if _collect_lock.locked():
        return {"news_collected": 0, "sec_collected": 0, "skipped": "이미 수집 중"}
    async with _collect_lock:
        result = await collect_us_tickers(await all_us_holdings())
    logger.info("미국 보유종목 뉴스·SEC 수집: %s", result)
    return result


async def _cached_feed(user_id, collection_name: str, item_key: str) -> dict:
    holdings = await user_us_holdings(user_id)
    if not holdings:
        return {"holdings": [], item_key: [], "fetched_at": None, "pending": []}
    tickers = [holding["ticker"] for holding in holdings]
    docs = {doc["_id"]: doc async for doc in get_db()[collection_name].find({"_id": {"$in": tickers}})}
    pending = [ticker for ticker in tickers if ticker not in docs]
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
