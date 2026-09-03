"""요청-응답 흐름 밖에서 시세 캐시를 미리 데우는 인프로세스 스케줄러.

개인 데스크톱 앱 규모에서는 Celery/Redis 같은 별도 워커 인프라를 추가하는
비용이 이득보다 크다고 판단해, docker-compose.desktop.yml에 새 컨테이너를
더하지 않는 APScheduler 기반 인프로세스 작업으로 "데이터 갱신 작업의 분리"를
구현한다. FastAPI 요청 처리와 같은 프로세스 안에서 돌지만, 주기적 실행은
uvicorn 워커 스레드에서 별도로 처리되어 개별 요청 처리를 막지 않는다.

이 작업은 `quote_cache.py`의 공유 TTL 캐시만 미리 채운다. 캐시를 직접
비우거나 형식을 바꾸지 않으므로, 스케줄러가 죽거나 비활성화돼도 기존
요청-시점 조회 경로가 그대로 동작한다 — 스케줄러는 최적화일 뿐 필수
의존성이 아니다.
"""
from __future__ import annotations

import logging

try:
    from .db import get_db
    from .quote_cache import fetch_quote_cached
except ImportError:
    from db import get_db
    from quote_cache import fetch_quote_cached

logger = logging.getLogger("jy_investing.scheduler")

QUOTE_REFRESH_INTERVAL_MINUTES = 5

_scheduler = None


async def collect_active_tickers() -> set[str]:
    """모든 사용자의 보유 종목·실제 가격 전략 종목을 모아 중복 없이 반환한다."""
    db = get_db()
    tickers: set[str] = set()
    needs_fx = False

    async for doc in db.portfolios.find({}, {"holdings.ticker": 1, "holdings.currency": 1}):
        for holding in doc.get("holdings", []):
            ticker = holding.get("ticker")
            if ticker:
                tickers.add(ticker)
            if holding.get("currency") == "USD":
                needs_fx = True

    async for doc in db.strategies.find({"type": "real_backtest"}, {"ticker": 1}):
        ticker = doc.get("ticker")
        if ticker:
            tickers.add(ticker)

    if needs_fx:
        tickers.add("KRW=X")
    return tickers


async def refresh_active_quote_cache() -> None:
    """보유·전략에 실제로 쓰이는 종목의 지연 시세 캐시를 백그라운드에서 미리 채운다.

    `fetch_quote_cached`를 그대로 호출해 만료된 항목만 자연스럽게 다시
    채운다 — 별도 캐시를 만들지 않아 요청 경로와 항상 같은 데이터를 본다.
    실패한 종목은 로그만 남기고 건너뛴다(다음 실제 요청이 사유를 화면에 표시함).
    """
    import asyncio

    try:
        tickers = await collect_active_tickers()
    except Exception:
        logger.warning("활성 종목 목록 조회 실패", exc_info=True)
        return

    for ticker in tickers:
        try:
            await asyncio.to_thread(fetch_quote_cached, ticker)
        except Exception:
            logger.warning("백그라운드 시세 갱신 실패: %s", ticker, exc_info=True)


def start_scheduler() -> None:
    """앱 시작 시 주기적 시세 갱신 작업을 등록한다. APScheduler 미설치 시 조용히 건너뛴다."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.info("apscheduler가 설치되지 않아 백그라운드 시세 갱신을 건너뜁니다.")
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        refresh_active_quote_cache,
        "interval",
        minutes=QUOTE_REFRESH_INTERVAL_MINUTES,
        id="refresh_active_quote_cache",
        coalesce=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info("백그라운드 시세 갱신 스케줄러 시작 (%d분 주기)", QUOTE_REFRESH_INTERVAL_MINUTES)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
