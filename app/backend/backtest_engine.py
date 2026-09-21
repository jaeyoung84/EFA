"""결정론적 일봉 롱/현금 백테스트 엔진.

신호는 t일 종가에 확정하고 t+1일 시가에 체결한다. 따라서 같은 OHLCV와 설정이면
언제 실행해도 같은 ``run_id``와 결과가 나오며, 미래 데이터를 신호에 사용하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


ENGINE_VERSION = "1.0.0"
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class BacktestConfig:
    fast_ma: int
    slow_ma: int
    transaction_cost_bps: float
    slippage_bps: float
    initial_capital: float = 10_000_000.0
    execution_rule: str = "next_open"


def _hash_frame(frame) -> str:
    rows = [
        [str(index)[:10], round(float(row["Open"]), 8), round(float(row["Close"]), 8)]
        for index, row in frame[["Open", "Close"]].iterrows()
    ]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def _run_id(ticker: str, data_fingerprint: str, config: BacktestConfig) -> str:
    payload = {"engine": ENGINE_VERSION, "ticker": ticker, "data": data_fingerprint, "config": config.__dict__}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def run_ma_crossover(ohlc, ticker: str, config: BacktestConfig, factor_filter=None):
    """MA 크로스오버를 다음 시가 체결과 왕복 비용이 아닌 편도 비용으로 계산한다."""
    import pandas as pd

    frame = ohlc[["Open", "Close"]].copy().dropna()
    if len(frame) <= config.slow_ma + 1:
        raise ValueError("이동평균 계산에 필요한 거래일이 부족합니다.")
    frame["MA_fast"] = frame["Close"].rolling(config.fast_ma).mean()
    frame["MA_slow"] = frame["Close"].rolling(config.slow_ma).mean()
    frame["signal_at_close"] = (frame["MA_fast"] > frame["MA_slow"]).astype(float)
    if factor_filter is not None:
        # 필터도 t일 종가까지의 값만 사용한다. 체결 포지션은 아래에서 한 칸 이동한다.
        frame["signal_at_close"] *= factor_filter.reindex(frame.index).fillna(False).astype(float)
    # 기존 주문 제안 경로와 저장된 전략의 호환 이름. 실제 의미는 signal_at_close다.
    frame["Signal"] = frame["signal_at_close"]
    # 당일 장 마감 후 계산한 신호는 다음 거래일 시가부터 보유 상태가 된다.
    frame["Position"] = frame["signal_at_close"].shift(1).fillna(0.0)
    frame["previous_position"] = frame["Position"].shift(1).fillna(0.0)
    frame["Turnover"] = (frame["Position"] - frame["previous_position"]).abs()
    # 체결일에는 전일 보유분의 야간 수익률과 새 보유분의 장중 수익률을 분리한다.
    # 이 분리가 없으면 다음 시가 체결인데도 전일 종가부터의 전체 수익률을 새 포지션에
    # 잘못 귀속하는 문제가 생긴다.
    frame["overnight_return"] = frame["Open"] / frame["Close"].shift(1) - 1
    frame["intraday_return"] = frame["Close"] / frame["Open"] - 1
    frame["Ret"] = frame["Close"].pct_change().fillna(0.0)
    # 편도 비용: 매수 또는 매도 각각에 거래비용과 슬리피지를 적용한다.
    frame["Cost"] = frame["Turnover"] * (config.transaction_cost_bps + config.slippage_bps) / 10_000
    frame["Strat_Ret"] = (
        frame["previous_position"] * frame["overnight_return"].fillna(0.0)
        + frame["Position"] * frame["intraday_return"].fillna(0.0)
        - frame["Cost"]
    )
    frame = frame.dropna(subset=["MA_fast", "MA_slow"]).copy()
    frame["Equity"] = config.initial_capital * (1 + frame["Strat_Ret"]).cumprod()
    frame["Benchmark_Equity"] = config.initial_capital * (1 + frame["Ret"]).cumprod()
    data_fingerprint = _hash_frame(frame)
    return frame, {
        "run_id": _run_id(ticker, data_fingerprint, config),
        "engine_version": ENGINE_VERSION,
        "execution_rule": "전일 종가 신호 → 다음 거래일 시가 체결",
        "data_fingerprint": data_fingerprint,
        "initial_capital": config.initial_capital,
        "cost_model": {"transaction_cost_bps": config.transaction_cost_bps, "slippage_bps": config.slippage_bps, "cost_application": "편도 체결마다 적용"},
    }


def benchmark_validation(strategy_returns, benchmark_returns) -> dict:
    """같은 날짜의 일간 수익률로 벤치마크 대비 검증 지표를 계산한다."""
    import pandas as pd
    paired = pd.concat([strategy_returns.rename("strategy"), benchmark_returns.rename("benchmark")], axis=1).dropna()
    if len(paired) < 2:
        return {"observations": len(paired), "alpha_annualized": None, "beta": None, "correlation": None, "excess_return": None}
    variance = float(paired["benchmark"].var(ddof=1))
    beta = float(paired["strategy"].cov(paired["benchmark"]) / variance) if variance > 0 else None
    alpha_daily = float((paired["strategy"] - beta * paired["benchmark"]).mean()) if beta is not None else None
    excess = float((1 + paired["strategy"]).prod() / (1 + paired["benchmark"]).prod() - 1)
    return {"observations": len(paired), "alpha_annualized": round(alpha_daily * 252, 4) if alpha_daily is not None else None,
            "beta": round(beta, 4) if beta is not None else None, "correlation": round(float(paired["strategy"].corr(paired["benchmark"])), 4), "excess_return": round(excess, 4)}


def transaction_cost_sensitivity(ohlc, ticker: str, config: BacktestConfig) -> list[dict]:
    """신호는 고정하고 비용만 0.5x/1x/2x로 바꾼 민감도 표다."""
    rows = []
    for multiplier in (0.5, 1.0, 2.0):
        scenario = BacktestConfig(config.fast_ma, config.slow_ma, config.transaction_cost_bps * multiplier, config.slippage_bps * multiplier, config.initial_capital, config.execution_rule)
        frame, _ = run_ma_crossover(ohlc, ticker, scenario)
        rows.append({"cost_multiplier": multiplier, "total_cost_bps": round(scenario.transaction_cost_bps + scenario.slippage_bps, 4), "total_return": round(float((1 + frame["Strat_Ret"]).prod() - 1), 4), "trade_count": int(frame["Turnover"].astype(bool).sum())})
    return rows
