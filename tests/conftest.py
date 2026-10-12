"""공통 테스트 픽스처.

MONGODB_DB는 실제 개발용 DB(jy_investing/investment_db)와 절대 겹치지 않도록
테스트 전용 이름·로컬 URI를 앱 import 전과 삭제 직전에 확인한다. db.py가 모듈을 불러오는 시점에
os.getenv로 한 번만 값을 읽으므로, 반드시 app을 import하기 전에 설정해야 한다.
"""
import os

from scripts.test_database_safety import assert_cleanup_target, target_from_environment

# Reject unsafe inherited settings BEFORE importing the application/DB client.
_TEST_DATABASE_TARGET = target_from_environment(os.environ)
os.environ["MONGODB_URL"] = _TEST_DATABASE_TARGET.url
os.environ["MONGODB_DB"] = _TEST_DATABASE_TARGET.database
os.environ["LEGACY_EMAIL_SIGNUP_ENABLED"] = "true"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.backend.db import get_db
from app.backend.main import app

TEST_COLLECTIONS = [
    "users", "auth_sessions", "auth_oauth_states", "user_watchlist", "research_notes", "user_activity", "strategy_decision_records", "monthly_operations_reports", "strategy_revalidations", "research_revalidation_monthly_snapshots", "broker_sandbox_contract_checks",
    "portfolios", "strategies", "order_proposals", "paper_fills", "paper_accounts", "paper_daily_snapshots", "paper_alerts", "execution_permissions", "execution_audit_events", "execution_audit_counters", "execution_readiness_requests", "strategy_promotion_states", "strategy_promotion_events", "strategy_promotion_monitor_snapshots",
    "market_ohlcv", "market_data_runs", "market_data_refresh_jobs", "market_data_alerts", "market_provider_health_snapshots", "market_provider_backoff", "market_factor_snapshots", "market_factor_universe_snapshots", "market_data_lineage_snapshots", "market_corporate_actions", "dart_market_activity_snapshots", "strategy_research_runs", "portfolio_risk_policies", "kqs_reconciliation_snapshots", "kqs_candidate_reviews", "kqs_candidate_review_snapshots", "kqs_candidate_queue_snapshots", "kqs_candidate_outcomes", "kqs_operations_alert_acknowledgements",
    "leverage_dca_configs", "leverage_dca_daily_logs", "leverage_dca_alpaca_fills", "kqs_paper_decisions", "kqs_decision_outcomes",
    "kqs_native_candidate_reviews", "kqs_native_candidate_review_snapshots",
    "kqs_native_candidate_queue_snapshots", "kqs_native_candidate_outcomes",
    "kqs_manual_write_locks", "holding_news_cache", "holding_news_links", "kqs_learning_consents", "kqs_training_experiments", "kqs_dataset_manifests", "kqs_dataset_holdout_openings", "kqs_dataset_access_log",
    "us_daily_bars", "us_market_runs", "us_sector_rotation_snapshots",
    "us_index_membership", "us_equity_bars", "us_equity_symbols", "us_equity_runs",
]


@pytest_asyncio.fixture(autouse=True)
async def _clean_database():
    """각 테스트 전후로 테스트 전용 DB의 관련 컬렉션을 비운다."""
    assert_cleanup_target(_TEST_DATABASE_TARGET, os.environ)
    db = get_db()
    assert_cleanup_target(_TEST_DATABASE_TARGET, os.environ, db.name)
    for name in TEST_COLLECTIONS:
        await db[name].delete_many({})
    yield
    # Recheck both the environment and the cached handle immediately before cleanup.
    assert_cleanup_target(_TEST_DATABASE_TARGET, os.environ)
    db = get_db()
    assert_cleanup_target(_TEST_DATABASE_TARGET, os.environ, db.name)
    for name in TEST_COLLECTIONS:
        await db[name].delete_many({})


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def make_user(client):
    """이메일을 주면 회원가입 후 Authorization 헤더 dict를 돌려주는 헬퍼."""
    async def _make(email: str) -> dict[str, str]:
        username = email.split("@")[0]
        resp = await client.post(
            "/api/auth/signup",
            json={"email": email, "username": username, "password": "testpassword123"},
        )
        assert resp.status_code == 201, resp.text
        token = resp.json()["token"]
        return {"Authorization": f"Bearer {token}"}
    return _make
