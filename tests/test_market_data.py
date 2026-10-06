from datetime import date, datetime, timedelta, timezone

import pytest

from app.backend import main, market_data, scheduler
from app.backend.db import get_db


def test_daily_quality_validation_rejects_bad_bars():
    bars, rejected = market_data.validate_daily_bars([
        {"timestamp": "2026-01-02", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1000},
        {"timestamp": "2026-01-02", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1000},
        {"timestamp": "2026-01-03", "open": 100, "high": 99, "low": 98, "close": 101, "volume": 1000},
        {"timestamp": "bad-date", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1000},
    ])
    assert [bar["timestamp"] for bar in bars] == ["2026-01-02"]
    assert {item["reason"] for item in rejected} == {"duplicate_timestamp", "invalid_ohlc_range", "invalid_timestamp"}


def test_ingestion_quality_distinguishes_minor_rejections_from_cross_check_requirement(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_REVIEW_REJECTION_PCT", "1")
    minor = market_data.assess_ingestion_quality(245, [{"reason": "invalid_ohlc_range"}])
    material = market_data.assess_ingestion_quality(262, [{"reason": "invalid_ohlc_range"}] * 41)
    assert minor == {"status": "passed_with_minor_rejections", "rejected": 1, "rejection_rate_pct": 0.4082,
                     "secondary_verification_required": False}
    assert material["status"] == "review_required"
    assert material["secondary_verification_required"] is True
    assert material["rejection_rate_pct"] > 15


def test_fred_mapping_supports_yahoo_style_fx_ticker():
    assert market_data.FredKrwDailyProvider()._series("KRW=X") == "DEXKOUS"


@pytest.mark.asyncio
async def test_high_rejection_rate_uses_cleaner_secondary_provider(monkeypatch):
    class NoisyProvider:
        name = "noisy"

        def fetch_daily(self, ticker, start, end, adjusted):
            return market_data.ProviderResult("noisy", "test", [
                {"timestamp": "2026-01-02", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1},
                {"timestamp": "2026-01-03", "open": 100, "high": 99, "low": 98, "close": 101, "volume": 1},
            ])

    class CleanProvider:
        name = "clean"

        def fetch_daily(self, ticker, start, end, adjusted):
            return market_data.ProviderResult("clean", "test", [
                {"timestamp": "2026-01-02", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1},
                {"timestamp": "2026-01-03", "open": 102, "high": 106, "low": 101, "close": 104, "volume": 1},
            ])

    monkeypatch.setenv("MARKET_DATA_REVIEW_REJECTION_PCT", "1")
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [NoisyProvider(), CleanProvider()])
    result = await market_data.get_daily_ohlcv("TEST", date(2026, 1, 1), date(2026, 1, 4))
    run = await get_db().market_data_runs.find_one({"ticker": "TEST"})
    assert result["source"] == "clean"
    assert result["quality"]["status"] == "passed"
    assert [item["status"] for item in result["provider_attempts"]] == ["review_candidate", "accepted"]
    assert [item["provider"] for item in run["provider_attempts"]] == ["noisy", "clean"]


@pytest.mark.asyncio
async def test_fx_fixing_cross_check_preserves_primary_ohlcv_when_verified(monkeypatch):
    dates = [(date(2026, 1, 2) + timedelta(days=offset)).isoformat() for offset in range(21)]
    clean_bars = [{"timestamp": stamp, "open": 1400, "high": 1402, "low": 1398, "close": 1400, "volume": 1} for stamp in dates]

    class YahooLikeProvider:
        name = "yahoo"

        def fetch_daily(self, ticker, start, end, adjusted):
            return market_data.ProviderResult("yahoo", "test", clean_bars + [
                # 1% 넘게 범위를 벗어난 봉은 환율 범위 보정 대상이 아니라 기각되고 교차검증으로 간다.
                {"timestamp": "2026-01-30", "open": 1400, "high": 1399, "low": 1398, "close": 1450, "volume": 1},
            ])

    class FixingProvider:
        name = "fred"
        verification_only = True

        def fetch_daily(self, ticker, start, end, adjusted):
            return market_data.ProviderResult("fred", "fred-DEXKOUS", [
                {"timestamp": stamp, "open": 1400, "high": 1400, "low": 1400, "close": 1400, "volume": 0}
                for stamp in dates
            ])

    monkeypatch.setenv("MARKET_DATA_REVIEW_REJECTION_PCT", "1")
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [YahooLikeProvider(), FixingProvider()])
    result = await market_data.get_daily_ohlcv("KRW=X", date(2026, 1, 1), date(2026, 2, 1))
    assert result["source"] == "yahoo"
    assert result["quality"]["status"] == "passed_with_cross_verification"
    assert result["quality"]["secondary_verification_required"] is False
    assert result["quality"]["secondary_verification"]["reference_provider"] == "fred"
    assert result["provider_attempts"][-1]["status"] == "verified"


def test_quality_monitor_marks_stale_gaps_and_extreme_moves_for_review():
    report = market_data.inspect_daily_quality([
        {"timestamp": "2026-01-02", "close": 100},
        {"timestamp": "2026-01-06", "close": 150},
    ], as_of=date(2026, 1, 15))
    assert report["status"] == "warning"
    assert {item["code"] for item in report["findings"]} == {"stale_data", "weekday_gaps", "extreme_return"}


@pytest.mark.asyncio
async def test_data_lineage_report_and_daily_snapshot_are_reproducible():
    await get_db().market_ohlcv.insert_many([
        {"ticker": "005930.KS", "interval": "1d", "timestamp": "2026-01-02", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 10,
         "fingerprint": "bar-a", "source": "fixture", "source_version": "v1", "dataset_version": "run-1", "fetched_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
        {"ticker": "005930.KS", "interval": "1d", "timestamp": "2026-01-05", "open": 102, "high": 106, "low": 101, "close": 104, "volume": 11,
         "fingerprint": "bar-b", "source": "fixture", "source_version": "v1", "dataset_version": "run-1", "fetched_at": datetime(2026, 1, 5, tzinfo=timezone.utc)},
    ])
    first = await market_data.market_data_lineage_report(["005930.KS"])
    second = await market_data.market_data_lineage_report(["005930.KS"])
    snapshot = await market_data.capture_market_data_lineage_snapshot(["005930.KS"])

    assert first["items"][0]["status"] == "reproducible"
    assert first["items"][0]["bar_count"] == 2
    assert first["report_fingerprint"] == second["report_fingerprint"]
    assert len(first["items"][0]["dataset_fingerprint"]) == 64
    assert snapshot["report_fingerprint"] == first["report_fingerprint"]
    assert await get_db().market_data_lineage_snapshots.count_documents({"universe_id": snapshot["universe_id"]}) == 1


@pytest.mark.asyncio
async def test_data_status_includes_non_mutating_quality_monitor(client):
    await get_db().market_ohlcv.insert_many([
        {"ticker": "^KS11", "interval": "1d", "timestamp": "2026-01-02", "close": 100},
        {"ticker": "^KS11", "interval": "1d", "timestamp": "2026-01-05", "close": 101},
    ])
    response = await client.get("/api/market/data-status?ticker=%5EKS11")
    assert response.status_code == 200
    assert response.json()["quality_monitor"]["bar_count"] == 2


@pytest.mark.asyncio
async def test_market_data_refresh_records_provenance(monkeypatch, client):
    async def fake_get_daily(ticker, start, end, *, adjusted=True, prefer_cached=False):
        assert ticker == "^KS11"
        assert start < end
        return {
            "bars": [{"timestamp": "2026-01-02", "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 123}],
            "source": "yahoo", "source_version": "yfinance", "dataset_version": "test-version",
            "cached": False, "quality": {"status": "passed", "rejected": 0},
        }

    monkeypatch.setattr(main, "get_daily_ohlcv", fake_get_daily)
    response = await client.post("/api/market/data/refresh", json={"tickers": ["^KS11"], "days": 30})
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item == {"ticker": "^KS11", "status": "ok", "bars": 1, "source": "yahoo", "dataset_version": "test-version",
                    "quality": {"status": "passed", "rejected": 0}, "provider_attempts": [], "provider_routing": []}


@pytest.mark.asyncio
async def test_scheduled_daily_refresh_tracks_targets_and_preserves_cached_success(monkeypatch):
    async def fake_collect_active_tickers():
        return {"005930.KS"}

    async def fake_get_daily(ticker, start, end, *, adjusted=True, prefer_cached=False):
        assert start < end
        assert adjusted is True
        if ticker == "^KQ11":
            raise market_data.MarketDataError("공급자와 검증 캐시 모두 사용할 수 없습니다")
        return {
            "bars": [{"timestamp": "2026-09-16"}],
            "source": "cached" if ticker == "^KS11" else "yahoo",
            "dataset_version": "test-version", "quality": {"status": "last_verified_cache", "rejected": 0},
        }

    monkeypatch.setenv("MARKET_DATA_CORE_TICKERS", "^KS11,^KQ11")
    monkeypatch.setenv("MARKET_DATA_REFRESH_DAYS", "31")
    monkeypatch.setattr(scheduler, "collect_active_tickers", fake_collect_active_tickers)
    monkeypatch.setattr(scheduler, "get_daily_ohlcv", fake_get_daily)

    result = await scheduler.refresh_daily_market_data()
    saved = await get_db().market_data_refresh_jobs.find_one({"job_id": result["job_id"]})
    assert result["status"] == "partial_failure"
    assert result["success_count"] == 2
    assert result["failure_count"] == 1
    assert result["tickers"] == ["005930.KS", "^KQ11", "^KS11"]
    assert next(item for item in result["items"] if item["ticker"] == "^KS11")["source"] == "cached"
    assert saved["status"] == "partial_failure"
    alerts = await get_db().market_data_alerts.find({"condition_active": True}).to_list(20)
    assert {alert["type"] for alert in alerts} >= {"refresh_failure", "cached_fallback"}


@pytest.mark.asyncio
async def test_refresh_status_reports_latest_job_and_core_cache(client):
    today = datetime.now(timezone.utc).date().isoformat()
    await get_db().market_data_refresh_jobs.insert_one({
        "job_id": "refresh-job", "started_at": datetime.now(timezone.utc),
        "finished_at": datetime.now(timezone.utc), "status": "success",
        "success_count": 3, "failure_count": 0,
    })
    await get_db().market_ohlcv.insert_many([
        {"ticker": ticker, "interval": "1d", "timestamp": today, "source": "yahoo"}
        for ticker in ("^KS11", "^KQ11", "KRW=X")
    ])

    response = await client.get("/api/market/refresh-status")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["last_job"]["job_id"] == "refresh-job"
    assert payload["stale_tickers"] == []
    assert payload["core_cache"]["^KS11"] == {"latest": today, "source": "yahoo"}


@pytest.mark.asyncio
async def test_market_operations_report_keeps_alert_history_and_daily_summary(client):
    now = datetime.now(timezone.utc)
    await get_db().market_data_refresh_jobs.insert_one({
        "job_id": "failed-refresh", "started_at": now, "finished_at": now,
        "status": "partial_failure", "tickers": ["^KS11", "^KQ11"], "success_count": 1, "failure_count": 1,
    })
    await get_db().market_data_alerts.insert_one({
        "fingerprint": "refresh_failure:^KQ11", "type": "refresh_failure", "severity": "warning",
        "title": "일봉 자동 수집 실패", "message": "테스트", "status": "open", "condition_active": True,
        "created_at": now, "updated_at": now, "events": [{"at": now, "action": "created", "note": "테스트"}],
    })
    response = await client.get("/api/market/operations-report?days=7")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["report_version"] == "market-data-operations-report-v1"
    assert report["summary"]["active_alert_count"] == 1
    assert report["daily_runs"][0]["failure_count"] == 1
    assert report["alerts"][0]["type"] == "refresh_failure"


@pytest.mark.asyncio
async def test_provider_health_creates_persistent_degradation_alert_and_snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("MARKET_DATA_PROVIDER_MIN_ATTEMPTS", "3")
    monkeypatch.setenv("MARKET_DATA_PROVIDER_MAX_ERROR_RATE_PCT", "20")
    for index, status in enumerate(("error", "error", "accepted")):
        await get_db().market_data_runs.insert_one({
            "run_id": f"provider-run-{index}", "ticker": "TEST", "finished_at": now,
            "provider_attempts": [{"provider": "fixture", "status": status, "bars_received": 100,
                                   "bars_valid": 100 if status == "accepted" else 0, "bars_rejected": 0}],
        })
    health = await market_data.market_provider_health(7)
    fixture = next(row for row in health["providers"] if row["provider"] == "fixture")
    assert fixture["status"] == "degraded"
    assert fixture["error_rate_pct"] == 66.6667

    await market_data.sync_market_data_alerts({"job_id": "health-job", "items": []})
    alert = await get_db().market_data_alerts.find_one({"fingerprint": "provider_degraded:fixture"})
    snapshot = await get_db().market_provider_health_snapshots.find_one({"period_days": 7})
    assert alert["condition_active"] is True
    assert snapshot["degraded_provider_count"] == 1


@pytest.mark.asyncio
async def test_provider_health_excludes_unsupported_symbol_mapping_from_error_rate():
    now = datetime.now(timezone.utc)
    await get_db().market_data_runs.insert_one({
        "run_id": "stooq-not-applicable", "ticker": "KRW=X", "finished_at": now,
        "provider_attempts": [{"provider": "stooq", "status": "error", "error": "Stooq 심볼 매핑이 없습니다: KRW=X"}],
    })
    health = await market_data.market_provider_health(7)
    stooq = next(row for row in health["providers"] if row["provider"] == "stooq")
    assert stooq["attempt_count"] == 0
    assert stooq["not_applicable_count"] == 1
    assert stooq["status"] == "healthy"


@pytest.mark.asyncio
async def test_provider_health_calculates_latency_and_marks_slow_provider(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("MARKET_DATA_PROVIDER_MIN_ATTEMPTS", "3")
    monkeypatch.setenv("MARKET_DATA_PROVIDER_SLOW_LATENCY_MS", "5000")
    await get_db().market_data_runs.insert_many([
        {"run_id": f"latency-run-{index}", "ticker": "TEST", "finished_at": now,
         "provider_attempts": [
             {"provider": "fast", "status": "accepted", "bars_received": 1, "bars_valid": 1, "latency_ms": latency},
             {"provider": "slow", "status": "accepted", "bars_received": 1, "bars_valid": 1, "latency_ms": latency * 100},
         ]}
        for index, latency in enumerate((100, 200, 300))
    ])
    health = await market_data.market_provider_health(7)
    fast = next(row for row in health["providers"] if row["provider"] == "fast")
    slow = next(row for row in health["providers"] if row["provider"] == "slow")
    assert fast["latency_sample_count"] == 3
    assert fast["average_latency_ms"] == 200.0
    assert fast["p95_latency_ms"] == 300.0
    assert fast["performance_status"] == "normal"
    assert slow["average_latency_ms"] == 20000.0
    assert slow["performance_status"] == "slow"
    assert health["thresholds"]["slow_latency_ms"] == 5000


@pytest.mark.asyncio
async def test_provider_latency_trend_detects_regression_and_creates_finding(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("MARKET_DATA_PROVIDER_MIN_ATTEMPTS", "3")
    monkeypatch.setenv("MARKET_DATA_PROVIDER_LATENCY_BASELINE_DAYS", "7")
    monkeypatch.setenv("MARKET_DATA_PROVIDER_LATENCY_MIN_BASELINE_SNAPSHOTS", "3")
    monkeypatch.setenv("MARKET_DATA_PROVIDER_LATENCY_REGRESSION_PCT", "75")
    await get_db().market_data_runs.insert_many([
        {"run_id": f"current-latency-{index}", "ticker": "TEST", "finished_at": now,
         "provider_attempts": [{"provider": "fixture", "status": "accepted", "bars_received": 1,
                                "bars_valid": 1, "latency_ms": 800}]}
        for index in range(3)
    ])
    for days_ago in (1, 2, 3):
        await get_db().market_provider_health_snapshots.insert_one({
            "snapshot_date": (now - timedelta(days=days_ago)).date().isoformat(), "period_days": 7,
            "captured_at": now - timedelta(days=days_ago),
            "providers": [{"provider": "fixture", "average_latency_ms": 400, "latency_sample_count": 3}],
        })
    health = await market_data.market_provider_health(7)
    trends = await market_data.provider_latency_trends(health)
    fixture = next(row for row in trends["providers"] if row["provider"] == "fixture")
    assert fixture["status"] == "regressed"
    assert fixture["baseline_average_latency_ms"] == 400.0
    assert fixture["change_pct"] == 100.0
    findings = market_data._provider_health_findings(health, trends)
    assert findings[-1]["fingerprint"] == "provider_latency_regression:fixture"


@pytest.mark.asyncio
async def test_provider_backoff_grows_on_error_and_resets_after_success(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_PROVIDER_BACKOFF_BASE_MINUTES", "1")
    monkeypatch.setenv("MARKET_DATA_PROVIDER_BACKOFF_MAX_MINUTES", "4")
    await market_data.record_provider_attempt_outcome("fixture", "error", "temporary outage")
    first = await get_db().market_provider_backoff.find_one({"provider": "fixture"})
    await market_data.record_provider_attempt_outcome("fixture", "error", "temporary outage")
    second = await get_db().market_provider_backoff.find_one({"provider": "fixture"})
    assert first["failure_streak"] == 1
    assert second["failure_streak"] == 2
    assert second["retry_after"] > first["retry_after"]

    await market_data.record_provider_attempt_outcome("fixture", "accepted")
    recovered = await get_db().market_provider_backoff.find_one({"provider": "fixture"})
    assert recovered["failure_streak"] == 0
    assert recovered["last_status"] == "accepted"


@pytest.mark.asyncio
async def test_provider_routing_skips_provider_during_active_cooldown(monkeypatch):
    class FirstProvider:
        name = "first"

        def fetch_daily(self, ticker, start, end, adjusted):
            raise AssertionError("cooldown 공급자는 호출하면 안 됩니다")

    class HealthyProvider:
        name = "healthy"

        def fetch_daily(self, ticker, start, end, adjusted):
            raise AssertionError("이 테스트에서는 라우팅만 확인합니다")

    now = datetime.now(timezone.utc)
    await get_db().market_provider_backoff.insert_one({
        "provider": "first", "failure_streak": 2, "last_status": "error", "retry_after": now + timedelta(minutes=10),
    })
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [FirstProvider(), HealthyProvider()])
    providers, routing = await market_data.provider_routing_plan("TEST")
    assert [provider.name for provider in providers] == ["healthy"]
    assert routing[0]["provider"] == "first"
    assert routing[0]["action"] == "cooldown_skip"
    assert routing[0]["failure_streak"] == 2
    assert routing[0]["retry_after"] > now


@pytest.mark.asyncio
async def test_provider_routing_prefers_faster_healthy_provider(monkeypatch):
    class SlowProvider:
        name = "slow"

        def fetch_daily(self, ticker, start, end, adjusted):
            raise AssertionError("이 테스트에서는 라우팅만 확인합니다")

    class FastProvider:
        name = "fast"

        def fetch_daily(self, ticker, start, end, adjusted):
            raise AssertionError("이 테스트에서는 라우팅만 확인합니다")

    now = datetime.now(timezone.utc)
    await get_db().market_provider_health_snapshots.insert_one({
        "snapshot_date": "2026-01-02", "period_days": 7, "captured_at": now,
        "providers": [
            {"provider": "slow", "status": "healthy", "performance_status": "slow", "average_latency_ms": 6000},
            {"provider": "fast", "status": "healthy", "performance_status": "normal", "average_latency_ms": 100},
        ],
    })
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [SlowProvider(), FastProvider()])
    providers, routing = await market_data.provider_routing_plan("TEST")
    assert [provider.name for provider in providers] == ["fast", "slow"]
    assert [(item["provider"], item["priority"]) for item in routing] == [("fast", 1), ("slow", 2)]


@pytest.mark.asyncio
async def test_home_daily_candle_exposes_dataset_provenance(monkeypatch, client):
    async def fake_get_daily(ticker, start, end, *, adjusted=True, prefer_cached=False):
        return {
            "bars": [
                {"timestamp": "2026-01-02", "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 123},
                {"timestamp": "2026-01-05", "open": 102.0, "high": 106.0, "low": 101.0, "close": 104.0, "volume": 456},
            ],
            "source": "cached", "dataset_version": "test-version", "quality": {"status": "last_verified_cache", "rejected": 0},
        }

    monkeypatch.setattr(main, "get_daily_ohlcv", fake_get_daily)
    response = await client.get("/api/home/market-candle?market=kospi&period=3mo")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["is_simulated"] is False
    assert payload["data_source"] == "cached"
    assert payload["dataset_version"] == "test-version"
    assert payload["interval"] == "1d"
    assert [bar["c"] for bar in payload["ohlcv"]] == [102.0, 104.0]


@pytest.fixture(autouse=True)
def _reset_display_cache_state():
    market_data._cache_refreshed_at.clear()
    market_data._background_refreshes.clear()
    yield
    market_data._cache_refreshed_at.clear()


@pytest.mark.asyncio
async def test_preferred_cache_returns_before_provider_call(monkeypatch):
    market_data._cache_refreshed_at["^KS11"] = market_data.monotonic()   # 15분 안에 새로 받은 저장본
    await get_db().market_ohlcv.insert_one({
        "ticker": "^KS11", "interval": "1d", "timestamp": "2026-01-02",
        "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1,
        "dataset_version": "cached-version",
    })

    class UnexpectedProvider:
        name = "unexpected"

        def fetch_daily(self, ticker, start, end, adjusted):
            raise AssertionError("prefer_cached 요청에서는 공급자를 호출하면 안 됩니다")

    monkeypatch.setattr(market_data, "_provider_chain", lambda: [UnexpectedProvider()])
    result = await market_data.get_daily_ohlcv("^KS11", date(2026, 1, 1), date(2026, 1, 3), prefer_cached=True)
    assert result["source"] == "cached"
    assert result["cached"] is True


@pytest.mark.asyncio
async def test_provider_failure_returns_last_verified_cache(monkeypatch):
    class FailingProvider:
        name = "failing"

        def fetch_daily(self, ticker, start, end, adjusted):
            raise market_data.MarketDataError("source unavailable")

    await get_db().market_ohlcv.insert_one({
        "ticker": "^KS11", "interval": "1d", "timestamp": "2026-01-02",
        "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1,
        "source": "yahoo", "dataset_version": "previous-version",
    })
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [FailingProvider()])
    result = await market_data.get_daily_ohlcv("^KS11", date(2026, 1, 1), date(2026, 1, 3))
    assert result["cached"] is True
    assert result["source"] == "cached"
    assert result["dataset_version"] == "previous-version"


@pytest.mark.asyncio
async def test_corporate_actions_are_versioned_with_daily_data(monkeypatch):
    class Provider:
        name = "fixture"

        def fetch_daily(self, ticker, start, end, adjusted):
            return market_data.ProviderResult("fixture", "test", [
                {"timestamp": "2026-01-02", "open": 100, "high": 105, "low": 99, "close": 102, "volume": 1},
            ], [{"effective_date": "2026-01-02", "action_type": "dividend", "value": 10.0}])

    monkeypatch.setattr(market_data, "_provider_chain", lambda: [Provider()])
    result = await market_data.get_daily_ohlcv("005930.KS", date(2026, 1, 1), date(2026, 1, 3))
    action = await get_db().market_corporate_actions.find_one({"ticker": "005930.KS"})
    assert action["action_type"] == "dividend"
    assert action["dataset_version"] == result["dataset_version"]


# 화면용 캐시(2026-10-06): 오래된 저장본은 새로 받고, 최근 저장본은 먼저 보여 주며 뒤에서 갱신한다.
def _bar(day: date, close: float = 100.0) -> dict:
    return {"timestamp": day.isoformat(), "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1}


class _CountingProvider:
    name = "counting"

    def __init__(self, bars):
        self.bars, self.calls = bars, 0

    def fetch_daily(self, ticker, start, end, adjusted):
        self.calls += 1
        return market_data.ProviderResult("counting", "test", self.bars)


@pytest.mark.asyncio
async def test_stale_display_cache_is_refreshed_before_showing(monkeypatch):
    today = date.today()
    await get_db().market_ohlcv.insert_one({"ticker": "GC=F", "interval": "1d", **_bar(today - timedelta(days=26), 90.0)})
    provider = _CountingProvider([_bar(today - timedelta(days=1), 101.0), _bar(today, 102.0)])
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [provider])
    result = await market_data.get_daily_ohlcv("GC=F", today - timedelta(days=60), today + timedelta(days=1), prefer_cached=True)
    assert result["cached"] is False and result["bars"][-1]["timestamp"] == today.isoformat()
    again = await market_data.get_daily_ohlcv("GC=F", today - timedelta(days=60), today + timedelta(days=1), prefer_cached=True)
    assert again["cached"] is True and again["bars"][-1]["close"] == 102.0   # 15분 안에는 저장본
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_recent_display_cache_is_shown_then_refreshed_in_background(monkeypatch):
    today = date.today()
    await get_db().market_ohlcv.insert_one({"ticker": "^DJI", "interval": "1d", **_bar(today - timedelta(days=1), 90.0)})
    provider = _CountingProvider([_bar(today - timedelta(days=1), 90.0), _bar(today, 95.0)])
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [provider])
    result = await market_data.get_daily_ohlcv("^DJI", today - timedelta(days=60), today + timedelta(days=1), prefer_cached=True)
    assert result["cached"] is True and result["bars"][-1]["close"] == 90.0     # 화면은 기다리지 않음
    await market_data._background_refreshes["^DJI"]
    assert provider.calls == 1
    stored = await get_db().market_ohlcv.find_one({"ticker": "^DJI", "timestamp": today.isoformat()})
    assert stored["close"] == 95.0                                               # 다음 요청부터 새 봉


@pytest.mark.asyncio
async def test_stale_display_cache_survives_provider_failure(monkeypatch):
    class Failing:
        name = "failing"

        def fetch_daily(self, ticker, start, end, adjusted):
            raise market_data.MarketDataError("down")

    old_day = date.today() - timedelta(days=26)
    await get_db().market_ohlcv.insert_one({"ticker": "BTC-USD", "interval": "1d", **_bar(old_day)})
    monkeypatch.setattr(market_data, "_provider_chain", lambda: [Failing()])
    result = await market_data.get_daily_ohlcv("BTC-USD", old_day - timedelta(days=5), date.today() + timedelta(days=1), prefer_cached=True)
    assert result["cached"] is True and result["bars"][-1]["timestamp"] == old_day.isoformat()


def test_fx_bars_with_small_range_errors_are_repaired_not_rejected():
    slightly_off = {"timestamp": "2025-11-17", "open": 1454.15, "high": 1462.45, "low": 1453.5, "close": 1446.53, "volume": 0}
    far_off = {"timestamp": "2025-11-18", "open": 1450.0, "high": 1451.0, "low": 1449.0, "close": 1400.0, "volume": 0}
    accepted, rejected = market_data.validate_daily_bars([slightly_off, far_off], repair_fx_range=True)
    assert [bar["timestamp"] for bar in accepted] == ["2025-11-17"]
    assert accepted[0]["low"] == 1446.53 and accepted[0]["high"] == 1462.45
    assert [item["bar"]["timestamp"] for item in rejected] == ["2025-11-18"]
    _accepted, strict = market_data.validate_daily_bars([slightly_off])      # 환율이 아니면 그대로 기각
    assert strict[0]["reason"] == "invalid_ohlc_range"


@pytest.mark.asyncio
async def test_home_candle_reports_last_bar_date_and_stale_flag(monkeypatch, client):
    old_day = (date.today() - timedelta(days=26)).isoformat()

    async def fake_get_daily(ticker, start, end, *, adjusted=True, prefer_cached=False):
        return {"bars": [{"timestamp": old_day, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}],
                "source": "cached", "dataset_version": "v", "quality": {"status": "last_verified_cache"}}

    monkeypatch.setattr(main, "get_daily_ohlcv", fake_get_daily)
    payload = (await client.get("/api/home/market-candle?market=gold&period=3mo")).json()
    assert payload["last_bar_date"] == old_day and payload["stale"] is True
