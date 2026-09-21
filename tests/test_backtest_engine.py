import pandas as pd
import pytest

from app.backend.backtest_engine import BacktestConfig, benchmark_validation, run_ma_crossover, transaction_cost_sensitivity
from app.backend.routers import strategy


def _ohlc():
    index = pd.bdate_range("2026-01-01", periods=12)
    close = [100, 101, 102, 104, 103, 105, 108, 107, 109, 111, 110, 112]
    return pd.DataFrame({"Open": [value - 0.5 for value in close], "Close": close}, index=index)


def test_backtest_uses_next_day_signal_and_deterministic_run_id():
    config = BacktestConfig(fast_ma=2, slow_ma=3, transaction_cost_bps=10, slippage_bps=5)
    frame_a, meta_a = run_ma_crossover(_ohlc(), "TEST", config)
    frame_b, meta_b = run_ma_crossover(_ohlc(), "TEST", config)
    assert meta_a["run_id"] == meta_b["run_id"]
    assert meta_a["execution_rule"] == "전일 종가 신호 → 다음 거래일 시가 체결"
    # MA 신호가 생긴 날과 보유 상태가 바뀌는 날이 같지 않아 미래 정보를 쓰지 않는다.
    assert (frame_a["Position"].iloc[1:] == frame_a["signal_at_close"].shift(1).iloc[1:]).all()


def test_cost_is_applied_on_each_position_change():
    config = BacktestConfig(fast_ma=2, slow_ma=3, transaction_cost_bps=10, slippage_bps=5)
    frame, meta = run_ma_crossover(_ohlc(), "TEST", config)
    changed = frame["Turnover"] > 0
    assert (frame.loc[changed, "Cost"] == 0.0015).all()
    assert meta["cost_model"]["cost_application"] == "편도 체결마다 적용"


def test_validation_reports_benchmark_exposure_and_cost_scenarios():
    config = BacktestConfig(fast_ma=2, slow_ma=3, transaction_cost_bps=10, slippage_bps=5)
    frame, _ = run_ma_crossover(_ohlc(), "TEST", config)
    validation = benchmark_validation(frame["Strat_Ret"], frame["Ret"])
    scenarios = transaction_cost_sensitivity(_ohlc(), "TEST", config)
    assert validation["observations"] == len(frame)
    assert validation["beta"] is not None
    assert [item["cost_multiplier"] for item in scenarios] == [0.5, 1.0, 2.0]
    assert scenarios[0]["total_cost_bps"] < scenarios[-1]["total_cost_bps"]


@pytest.mark.asyncio
async def test_backtest_api_returns_reproducible_execution_metadata(monkeypatch, client):
    frame = _ohlc()
    # API 최소 이력 조건을 충족하도록 같은 추세의 거래일을 확장한다.
    index = pd.bdate_range("2025-01-01", periods=100)
    values = pd.Series(range(100, 200), index=index, dtype=float)
    monkeypatch.setattr(strategy, "_download_ohlc_frame", lambda ticker, period: pd.DataFrame({"Open": values - 0.5, "Close": values}))
    response = await client.post("/api/strategy/backtest", json={"ticker": "AAPL", "fast_ma": 5, "slow_ma": 20, "period": "1y"})
    assert response.status_code == 200, response.text
    execution = response.json()["execution"]
    assert len(execution["run_id"]) == 24
    assert execution["engine_version"] == "1.0.0"
    assert response.json()["benchmark_validation"]["observations"] > 1
    assert len(response.json()["cost_sensitivity"]) == 3
    assert len(response.json()["strategy_return_series"]) > 20
