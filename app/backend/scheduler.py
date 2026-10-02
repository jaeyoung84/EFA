"""요청-응답 흐름 밖에서 시세 캐시를 미리 데우는 인프로세스 스케줄러.

개인 데스크톱 앱 규모에서는 Celery/Redis 같은 별도 워커 인프라를 추가하는
비용이 이득보다 크다고 판단해, docker-compose.desktop.yml에 새 컨테이너를
더하지 않는 APScheduler 기반 인프로세스 작업으로 "데이터 갱신 작업의 분리"를
구현한다. FastAPI 요청 처리와 같은 프로세스 안에서 돌지만, 주기적 실행은
uvicorn 워커 스레드에서 별도로 처리되어 개별 요청 처리를 막지 않는다.

5분 단위 작업은 `quote_cache.py`의 공유 TTL 캐시만 미리 채운다. 별도 평일
장 마감 작업은 핵심 지수와 보유·전략 종목의 검증 일봉을 저장하고 실행 이력을
남긴다. 두 작업 모두 실패해도 요청 시점의 마지막 검증 캐시를 유지하므로,
스케줄러는 데이터 안전성을 해치지 않는 운영 보조 장치다.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone

try:
    from .db import get_db
    from .dart_market_activity import collect_market_activity
    from .market_data import MarketDataError, capture_market_data_lineage_snapshot, get_daily_ohlcv, market_factor_snapshot, sync_market_data_alerts
    from .quote_cache import fetch_quote_cached
    from .routers.strategy import capture_paper_snapshot, refresh_paper_alerts, capture_monthly_operations_reports, capture_monthly_revalidation_evidence, run_leverage_dca_daily_cycle
    from .routers.kqs import capture_all_portfolio_reconciliation_snapshots, capture_candidate_queue_snapshot, capture_candidate_outcome_snapshots, capture_kqs_decision_outcomes, _candidate_collection
    from .market_calendar import is_within_execution_window
except ImportError:
    from db import get_db
    from dart_market_activity import collect_market_activity
    from market_data import MarketDataError, capture_market_data_lineage_snapshot, get_daily_ohlcv, market_factor_snapshot, sync_market_data_alerts
    from quote_cache import fetch_quote_cached
    from routers.strategy import capture_paper_snapshot, refresh_paper_alerts, capture_monthly_operations_reports, capture_monthly_revalidation_evidence, run_leverage_dca_daily_cycle
    from routers.kqs import capture_all_portfolio_reconciliation_snapshots, capture_candidate_queue_snapshot, capture_candidate_outcome_snapshots, capture_kqs_decision_outcomes, _candidate_collection
    from market_calendar import is_within_execution_window

logger = logging.getLogger("jy_investing.scheduler")

QUOTE_REFRESH_INTERVAL_MINUTES = 5
PAPER_SNAPSHOT_HOUR_KST = 18
PAPER_SNAPSHOT_MINUTE_KST = 10
KQS_RECONCILIATION_SNAPSHOT_HOUR_KST = 9
KQS_RECONCILIATION_SNAPSHOT_MINUTE_KST = 15
MARKET_DATA_REFRESH_HOUR_KST = 18
MARKET_DATA_REFRESH_MINUTE_KST = 25
MARKET_DATA_REFRESH_DAYS = 370
MARKET_DATA_CORE_TICKERS = {"^KS11", "^KQ11", "^GSPC", "^IXIC", "KRW=X"}
DART_MARKET_ACTIVITY_HOUR_KST = 20
DART_MARKET_ACTIVITY_MINUTE_KST = 0
LEVERAGE_DCA_WATCH_INTERVAL_MINUTES = 5

_scheduler = None


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _daily_refresh_enabled() -> bool:
    return os.getenv("MARKET_DATA_DAILY_REFRESH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _daily_refresh_core_tickers() -> set[str]:
    configured = os.getenv("MARKET_DATA_CORE_TICKERS", "").strip()
    if not configured:
        return set(MARKET_DATA_CORE_TICKERS)
    return {ticker.strip().upper() for ticker in configured.split(",") if ticker.strip()}


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

    async for doc in db.paper_fills.find({}, {"ticker": 1, "currency": 1}):
        ticker = doc.get("ticker")
        if ticker:
            tickers.add(ticker)
        if doc.get("currency") == "USD":
            needs_fx = True

    # 후보 사후성과 관측에 필요한 종목만 일봉 대상에 더한다. 이 작업은
    # 홈페이지 DB의 캐시를 갱신할 뿐 외부 엔진·후보 스냅샷을 수정하지 않는다.
    async for snapshot in _candidate_collection(db, "kqs_candidate_queue_snapshots").find({}, {"candidates": 1}):
        for candidate in snapshot.get("candidates", []):
            symbol = str(candidate.get("symbol") or "").strip().upper()
            market = str(candidate.get("market") or "").strip().upper()
            if symbol and "." not in symbol and market in {"KOSPI", "KOSDAQ"}:
                tickers.add(f"{symbol}.KS" if market == "KOSPI" else f"{symbol}.KQ")

    # 개인 판단 원장(kqs_paper_decisions)의 사후 수익률 관측에 필요한 종목도 더한다.
    async for decision in db.kqs_paper_decisions.find({}, {"symbol": 1, "market": 1}):
        symbol = str(decision.get("symbol") or "").strip().upper()
        market = str(decision.get("market") or "").strip().upper()
        if symbol and "." not in symbol and market in {"KOSPI", "KOSDAQ"}:
            tickers.add(f"{symbol}.KS" if market == "KOSPI" else f"{symbol}.KQ")

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
    await refresh_all_paper_alerts()


async def refresh_daily_market_data() -> dict:
    """핵심 지수와 실제 보유/전략 종목의 검증 일봉을 장 마감 후 갱신한다.

    성공·실패를 하나의 불변 실행 레코드로 남겨, 공급자 장애가 발생해도 화면은
    마지막 검증 캐시를 계속 사용하고 운영자는 실패 대상과 시각을 확인할 수 있다.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    days = _env_int("MARKET_DATA_REFRESH_DAYS", MARKET_DATA_REFRESH_DAYS, 30, 3650)
    try:
        active_tickers = await collect_active_tickers()
    except Exception:
        logger.warning("일봉 수집 대상 조회 실패", exc_info=True)
        active_tickers = set()
    tickers = sorted(_daily_refresh_core_tickers() | active_tickers)
    job = {
        "job_id": uuid.uuid4().hex,
        "started_at": now,
        "finished_at": None,
        "status": "running",
        "days": days,
        "tickers": tickers,
        "items": [],
        "success_count": 0,
        "failure_count": 0,
    }
    await db.market_data_refresh_jobs.insert_one({**job})
    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=days)
    items: list[dict] = []
    for ticker in tickers:
        try:
            result = await get_daily_ohlcv(ticker, start, end, adjusted=True)
            factor = await market_factor_snapshot(ticker)
            items.append({
                "ticker": ticker, "status": "ok", "bars": len(result["bars"]),
                "source": result["source"], "dataset_version": result["dataset_version"],
                "quality": result["quality"], "provider_attempts": result.get("provider_attempts", []),
                "provider_routing": result.get("provider_routing", []),
                "factor_snapshot": {"available": factor["available"], "as_of": factor.get("as_of"),
                                    "factor_version": factor["factor_version"]},
            })
        except MarketDataError as exc:
            items.append({"ticker": ticker, "status": "error", "error": str(exc)})
        except Exception as exc:
            logger.warning("일봉 수집 실패: %s", ticker, exc_info=True)
            items.append({"ticker": ticker, "status": "error", "error": f"예상하지 못한 수집 오류: {exc}"})
    finished_at = datetime.now(timezone.utc)
    success_count = sum(item["status"] == "ok" for item in items)
    failure_count = len(items) - success_count
    status = "success" if failure_count == 0 else "partial_failure" if success_count else "failed"
    await db.market_data_refresh_jobs.update_one(
        {"job_id": job["job_id"]},
        {"$set": {"finished_at": finished_at, "status": status, "items": items,
                  "success_count": success_count, "failure_count": failure_count}},
    )
    result = {**job, "finished_at": finished_at, "status": status, "items": items,
              "success_count": success_count, "failure_count": failure_count}
    try:
        await sync_market_data_alerts(result)
    except Exception:
        # 경고 이력 저장 실패가 이미 끝난 일봉 수집 결과를 실패로 바꾸지는 않는다.
        logger.warning("일봉 수집 경고 이력 동기화 실패", exc_info=True)
    try:
        await capture_candidate_outcome_snapshots()
    except Exception:
        # 사후성과 산출 실패가 이미 검증된 일봉 수집 결과를 실패로 바꾸지는 않는다.
        logger.warning("후보 사후성과 스냅샷 저장 실패", exc_info=True)
    try:
        await capture_kqs_decision_outcomes()
    except Exception:
        # 개인 판단 원장 사후성과 산출 실패가 이미 검증된 일봉 수집 결과를 실패로 바꾸지는 않는다.
        logger.warning("개인 판단 원장 사후성과 저장 실패", exc_info=True)
    try:
        await capture_market_data_lineage_snapshot(tickers)
    except Exception:
        # 계보 스냅샷 실패가 검증된 일봉 수집·후보 관측 결과를 바꾸지는 않는다.
        logger.warning("시장 데이터 계보 스냅샷 저장 실패", exc_info=True)
    if failure_count:
        logger.warning("일봉 수집 완료: %d 성공, %d 실패", success_count, failure_count)
    else:
        logger.info("일봉 수집 완료: %d개 대상", success_count)
    return result


async def refresh_all_paper_alerts() -> None:
    """체결 계좌와 대기 주문을 대상으로 위험·신호 알림을 주기적으로 동기화한다."""
    try:
        db = get_db()
        user_ids = (
            set(await db.paper_fills.distinct("user_id"))
            | set(await db.order_proposals.distinct("user_id", {"status": "pending"}))
            | set(await db.strategy_promotion_states.distinct("user_id", {"status": "approved"}))
        )
    except Exception:
        logger.warning("모의 계좌 알림 대상 조회 실패", exc_info=True)
        return
    for user_id in user_ids:
        try:
            await refresh_paper_alerts({"_id": user_id})
        except Exception:
            logger.warning("모의 계좌 알림 동기화 실패", exc_info=True)


async def capture_daily_paper_snapshots() -> None:
    """평일 장 마감 뒤 체결이 있는 계정의 모의 계좌 평가를 KST 일자로 저장한다."""
    try:
        db = get_db()
        user_ids = set(await db.paper_fills.distinct("user_id")) | set(await db.strategy_promotion_states.distinct("user_id", {"status": "approved"}))
    except Exception:
        logger.warning("모의 계좌 스냅샷 대상 조회 실패", exc_info=True)
        return
    for user_id in user_ids:
        try:
            await capture_paper_snapshot({"_id": user_id})
            await refresh_paper_alerts({"_id": user_id})
        except Exception:
            logger.warning("모의 계좌 일별 스냅샷 저장 실패", exc_info=True)


async def capture_daily_kqs_reconciliation_snapshots() -> None:
    """외부 엔진 일일 파이프라인 종료 뒤 대조·후보 큐 기준선을 저장한다."""
    try:
        await capture_all_portfolio_reconciliation_snapshots()
    except Exception:
        logger.warning("외부 퀀트 엔진 대조 스냅샷 저장 실패", exc_info=True)
    try:
        await capture_candidate_queue_snapshot()
    except Exception:
        logger.warning("외부 퀀트 엔진 후보 큐 스냅샷 저장 실패", exc_info=True)


async def watch_leverage_dca_market_close() -> None:
    """NYSE 장마감 20분 전~마감 사이인지 5분마다 확인해, 그 창 안에서만 레버리지 DCA를 실행한다.

    실제 실행은 사용자별 1일 1회 가드(``leverage_dca_configs.last_run_date``)가
    최종 통제하므로, 창 안에서 여러 번 폴링돼도 중복 매수·매도가 생기지 않는다.
    서머타임 전환과 NYSE 휴장일은 ``market_calendar``가 자동으로 반영한다.
    """
    try:
        window = is_within_execution_window()
    except Exception:
        logger.warning("레버리지 DCA 장마감 시각 계산 실패", exc_info=True)
        return
    if not window["ready"]:
        return
    try:
        result = await run_leverage_dca_daily_cycle()
        logger.info("레버리지 DCA 자동 모의매매 실행(장마감 %s): %d개 계정", window["market_close_kst"], len(result.get("results", [])))
    except Exception:
        logger.warning("레버리지 DCA 자동 모의매매 실행 실패", exc_info=True)


async def capture_monthly_quant_operations_reports() -> None:
    """월 1회 감사 스냅샷만 저장한다. 백업·복구·주문은 실행하지 않는다."""
    try:
        await capture_monthly_operations_reports()
    except Exception:
        logger.warning("월간 퀀트 운영 리포트 저장 실패", exc_info=True)
    try:
        await capture_monthly_revalidation_evidence()
    except Exception:
        logger.warning("월간 연구 재검증 근거 저장 실패", exc_info=True)


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
    _scheduler.add_job(
        capture_daily_paper_snapshots,
        "cron",
        day_of_week="mon-fri",
        hour=PAPER_SNAPSHOT_HOUR_KST,
        minute=PAPER_SNAPSHOT_MINUTE_KST,
        timezone="Asia/Seoul",
        id="capture_daily_paper_snapshots",
        coalesce=True,
        max_instances=1,
    )
    _scheduler.add_job(
        capture_daily_kqs_reconciliation_snapshots,
        "cron",
        day_of_week="mon-fri",
        hour=KQS_RECONCILIATION_SNAPSHOT_HOUR_KST,
        minute=KQS_RECONCILIATION_SNAPSHOT_MINUTE_KST,
        timezone="Asia/Seoul",
        id="capture_daily_kqs_reconciliation_snapshots",
        coalesce=True,
        max_instances=1,
    )
    _scheduler.add_job(
        watch_leverage_dca_market_close,
        "interval",
        minutes=LEVERAGE_DCA_WATCH_INTERVAL_MINUTES,
        id="watch_leverage_dca_market_close",
        coalesce=True,
        max_instances=1,
    )
    _scheduler.add_job(
        capture_monthly_quant_operations_reports,
        "cron",
        day=1,
        hour=9,
        minute=30,
        timezone="Asia/Seoul",
        id="capture_monthly_quant_operations_reports",
        coalesce=True,
        max_instances=1,
    )
    if _daily_refresh_enabled():
        refresh_hour = _env_int("MARKET_DATA_DAILY_REFRESH_HOUR_KST", MARKET_DATA_REFRESH_HOUR_KST, 0, 23)
        refresh_minute = _env_int("MARKET_DATA_DAILY_REFRESH_MINUTE_KST", MARKET_DATA_REFRESH_MINUTE_KST, 0, 59)
        _scheduler.add_job(
            refresh_daily_market_data,
            "cron",
            day_of_week="mon-fri",
            hour=refresh_hour,
            minute=refresh_minute,
            timezone="Asia/Seoul",
            id="refresh_daily_market_data",
            coalesce=True,
            max_instances=1,
        )
    _scheduler.add_job(
        collect_market_activity,
        "cron",
        day_of_week="mon-fri",
        hour=DART_MARKET_ACTIVITY_HOUR_KST,
        minute=DART_MARKET_ACTIVITY_MINUTE_KST,
        timezone="Asia/Seoul",
        id="collect_dart_market_activity",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=21600,
    )
    _scheduler.start()
    logger.info("백그라운드 시세(%d분)·일봉 수집(평일 장 마감 후)·모의계좌 스냅샷 스케줄러 시작", QUOTE_REFRESH_INTERVAL_MINUTES)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
