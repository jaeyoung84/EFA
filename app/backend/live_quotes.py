"""화면용 현재가: 국내 개별 종목은 한투(설정된 경우), 그 밖의 종목·지수·환율과 한투 실패 시에는 Yahoo 지연 시세."""
from __future__ import annotations

import asyncio

try:
    from .kis_quotes import domestic_code, kis_service, stream_session_open
    from .quote_cache import fetch_quote_cached
except ImportError:
    from kis_quotes import domestic_code, kis_service, stream_session_open  # type: ignore
    from quote_cache import fetch_quote_cached  # type: ignore

YAHOO_TIMEOUT_SECONDS = 4
SOURCE_LABELS = {
    "kis_realtime": "한투 실시간 체결",
    "kis_rest": "한투 현재가",
    "yahoo_delayed": "Yahoo 지연 시세",
}


async def _yahoo(ticker: str) -> dict:
    try:
        quote = await asyncio.wait_for(asyncio.to_thread(fetch_quote_cached, ticker), timeout=YAHOO_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return {"status": "error", "reason": f"외부 시세 조회가 {YAHOO_TIMEOUT_SECONDS}초 안에 완료되지 않았습니다."}
    except Exception as exc:
        return {"status": "error", "reason": f"시세 조회 실패: {exc}"}
    return {**quote, "source": "yahoo_delayed"} if quote.get("status") == "ok" else quote


async def domestic_kis_quotes(tickers: list[str]) -> dict[str, dict]:
    """한투가 켜져 있으면 국내 종목만 한투로 조회한다(실패 포함). 꺼져 있으면 빈 dict."""
    service = kis_service()
    domestic = [ticker for ticker in dict.fromkeys(tickers) if domestic_code(ticker)]
    if not service.enabled or not domestic:
        return {}
    return await service.quotes(domestic)


async def resolve_quotes(tickers: list[str]) -> dict[str, dict]:
    unique = list(dict.fromkeys(tickers))
    kis = await domestic_kis_quotes(unique)
    fallback = [ticker for ticker in unique if kis.get(ticker, {}).get("status") != "ok"]
    yahoo = dict(zip(fallback, await asyncio.gather(*(_yahoo(ticker) for ticker in fallback))))
    result: dict[str, dict] = {}
    for ticker in unique:
        quote = kis[ticker] if kis.get(ticker, {}).get("status") == "ok" else yahoo[ticker]
        if ticker in kis and kis[ticker].get("status") != "ok":
            quote = {**quote, "fallback_reason": kis[ticker].get("reason")}
        result[ticker] = {"ticker": ticker, **quote, "source_label": SOURCE_LABELS.get(quote.get("source", ""))}
    return result


def provider_status() -> dict:
    return {**kis_service().status(), "stream_session_open": stream_session_open()}
