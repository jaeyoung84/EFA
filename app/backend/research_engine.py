"""시간 순서를 보존하는 MA 전략 연구 도구.

검증 구간으로 파라미터를 고르고 최종 테스트 구간은 선택에 전혀 사용하지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict
import math
import random

from .backtest_engine import BacktestConfig, run_ma_crossover


def _metrics(returns) -> dict:
    import math

    if returns.empty:
        return {"total_return": 0.0, "sharpe": 0.0, "mdd": 0.0, "days": 0}
    equity = (1 + returns).cumprod()
    std = returns.std()
    sharpe = float(returns.mean() / std * math.sqrt(252)) if std and std > 0 else 0.0
    drawdown = (equity / equity.cummax() - 1).min()
    return {"total_return": round(float(equity.iloc[-1] - 1), 6), "sharpe": round(sharpe, 4),
            "mdd": round(float(drawdown), 6), "days": int(len(returns))}


def _factor_filter(frame, factor_filter: dict | None):
    """t일 종가로 확정되는 관측 팩터 조건. 미래 봉을 참조하지 않는다."""
    import pandas as pd

    if not factor_filter or not factor_filter.get("enabled"):
        return None
    close = frame["Close"].astype(float)
    momentum = close.pct_change(20)
    volatility = close.pct_change().rolling(20).std() * (252 ** 0.5)
    condition = pd.Series(True, index=frame.index)
    minimum_return = factor_filter.get("min_return_20d")
    maximum_volatility = factor_filter.get("max_realized_volatility_20d")
    if minimum_return is not None:
        condition &= momentum >= float(minimum_return)
    if maximum_volatility is not None:
        condition &= volatility <= float(maximum_volatility)
    return condition.fillna(False)


def _evaluate(frame, ticker: str, config: BacktestConfig, start_index: int, end_index: int, factor_filter: dict | None = None) -> dict:
    subset = frame.iloc[:end_index]
    full, metadata = run_ma_crossover(subset, ticker, config, _factor_filter(subset, factor_filter))
    start_date = frame.index[start_index]
    sample = full.loc[full.index >= start_date]
    return {"metrics": _metrics(sample["Strat_Ret"]), "run_id": metadata["run_id"], "data_fingerprint": metadata["data_fingerprint"]}


def _cost_stress(frame, ticker: str, config: BacktestConfig, start_index: int, factor_filter: dict | None) -> list[dict]:
    rows = []
    for multiplier in (0.5, 1.0, 2.0):
        scenario = BacktestConfig(config.fast_ma, config.slow_ma, config.transaction_cost_bps * multiplier,
                                  config.slippage_bps * multiplier, config.initial_capital, config.execution_rule)
        full, _ = run_ma_crossover(frame, ticker, scenario, _factor_filter(frame, factor_filter))
        sample = full.loc[full.index >= frame.index[start_index]]
        metrics = _metrics(sample["Strat_Ret"])
        rows.append({"cost_multiplier": multiplier, "metrics": metrics})
    return rows


def _candidate_assessment(final_test: dict, cost_stress: list[dict], audit: dict) -> dict:
    reasons = []
    if not audit["passed"]:
        reasons.append("시점 일관성 검증 실패")
    if final_test["days"] < 20:
        reasons.append("최종 테스트 관측치 부족")
    if final_test["sharpe"] <= 0:
        reasons.append("최종 테스트 Sharpe가 양수가 아님")
    if final_test["total_return"] <= 0:
        reasons.append("최종 테스트 총수익률이 양수가 아님")
    stressed = next((row["metrics"] for row in cost_stress if row["cost_multiplier"] == 2.0), {})
    if stressed.get("total_return", 0) <= 0:
        reasons.append("2배 비용 스트레스에서 총수익률이 양수가 아님")
    return {"status": "paper_candidate" if not reasons else "review_required", "eligible_for_paper_candidate": not reasons,
            "reasons": reasons, "note": "연구 후보 표시는 모의 주문 또는 실거래 권한을 생성하지 않습니다."}


def _statistical_validation(returns, candidate_count: int) -> dict:
    """Deterministic, compact robustness checks for a held-out return sample.

    This is deliberately a screening aid, not a claim of statistical significance.
    Resampling uses a fixed seed derived from the observed sample so identical inputs
    produce identical evidence records.
    """
    values = [float(value) for value in returns.dropna()]
    observations = len(values)
    if observations < 20:
        return {"available": False, "observations": observations, "candidate_count": candidate_count,
                "reason": "통계적 강건성 점검에는 최종 테스트 관측치가 최소 20개 필요합니다."}
    seed = int(sum((index + 1) * round(value * 1_000_000) for index, value in enumerate(values))) & 0xFFFFFFFF
    rng = random.Random(seed)
    bootstrap_returns = []
    for _ in range(400):
        equity = 1.0
        for _ in values:
            equity *= 1 + values[rng.randrange(observations)]
        bootstrap_returns.append(equity - 1)
    mean = sum(values) / observations
    variance = sum((value - mean) ** 2 for value in values) / max(observations - 1, 1)
    sharpe = mean / math.sqrt(variance) * math.sqrt(252) if variance > 0 else 0.0
    multiple_testing_hurdle = math.sqrt(2 * math.log(max(candidate_count, 1)) / observations) * math.sqrt(252)
    probability = sum(value > 0 for value in bootstrap_returns) / len(bootstrap_returns)
    ordered = sorted(bootstrap_returns)
    median = ordered[len(ordered) // 2]
    status = "review_required" if probability < 0.55 or sharpe <= multiple_testing_hurdle else "screen_passed"
    return {
        "available": True, "method": "결정론적 400회 부트스트랩 + 다중 후보 탐색 보수적 Sharpe 기준",
        "observations": observations, "candidate_count": candidate_count, "bootstrap_iterations": len(bootstrap_returns),
        "bootstrap_positive_total_return_probability": round(probability, 4), "bootstrap_median_total_return": round(median, 6),
        "held_out_sharpe": round(sharpe, 4), "multiple_testing_sharpe_hurdle": round(multiple_testing_hurdle, 4),
        "status": status,
        "disclaimer": "부트스트랩과 다중탐색 보정은 과최적화 위험을 줄이기 위한 참고 점검이며 유의성·미래 성과·전략 유효성을 보장하지 않습니다.",
    }


def run_research(frame, ticker: str, candidates: list[tuple[int, int]], transaction_cost_bps: float, slippage_bps: float,
                 train_ratio: float = 0.5, validation_ratio: float = 0.25, walk_forward_folds: int = 3,
                 factor_filter: dict | None = None) -> dict:
    """훈련/검증/최종 테스트 및 확장형 워크포워드 결과를 반환한다."""
    frame = frame[["Open", "Close"]].dropna().sort_index()
    n = len(frame)
    train_end = int(n * train_ratio)
    validation_end = int(n * (train_ratio + validation_ratio))
    if train_end < 80 or validation_end - train_end < 20 or n - validation_end < 20:
        raise ValueError("훈련·검증·테스트를 위해 최소 120개 거래일이 필요합니다.")
    valid_candidates = [(fast, slow) for fast, slow in candidates if fast < slow and slow < train_end]
    if not valid_candidates:
        raise ValueError("훈련 구간보다 작은 fast/slow 파라미터가 필요합니다.")

    sensitivity = []
    for fast, slow in valid_candidates:
        config = BacktestConfig(fast, slow, transaction_cost_bps, slippage_bps)
        train = _evaluate(frame, ticker, config, slow, train_end, factor_filter)
        validation = _evaluate(frame, ticker, config, train_end, validation_end, factor_filter)
        sensitivity.append({"fast_ma": fast, "slow_ma": slow, "train": train["metrics"], "validation": validation["metrics"]})
    sensitivity.sort(key=lambda row: (row["validation"]["sharpe"], row["validation"]["total_return"]), reverse=True)
    selected = sensitivity[0]
    config = BacktestConfig(selected["fast_ma"], selected["slow_ma"], transaction_cost_bps, slippage_bps)
    final_test = _evaluate(frame, ticker, config, validation_end, n, factor_filter)
    final_frame, _ = run_ma_crossover(frame.iloc[:n], ticker, config, _factor_filter(frame.iloc[:n], factor_filter))
    final_returns = final_frame.loc[final_frame.index >= frame.index[validation_end], "Strat_Ret"]

    # 확장형 walk-forward: 과거 train+validation에서 선택하고 바로 다음 test 블록만 평가한다.
    remaining = n - train_end
    test_size = max(20, remaining // (walk_forward_folds + 1))
    folds = []
    for fold in range(walk_forward_folds):
        validation_start = train_end + fold * test_size
        validation_stop = validation_start + test_size
        test_stop = min(validation_stop + test_size, n)
        if test_stop - validation_stop < 20:
            break
        ranked = []
        for fast, slow in valid_candidates:
            cfg = BacktestConfig(fast, slow, transaction_cost_bps, slippage_bps)
            ranked.append(( _evaluate(frame, ticker, cfg, validation_start, validation_stop, factor_filter)["metrics"], fast, slow))
        ranked.sort(key=lambda row: (row[0]["sharpe"], row[0]["total_return"]), reverse=True)
        _, fast, slow = ranked[0]
        test = _evaluate(frame, ticker, BacktestConfig(fast, slow, transaction_cost_bps, slippage_bps), validation_stop, test_stop, factor_filter)
        folds.append({"fold": fold + 1, "fast_ma": fast, "slow_ma": slow,
                      "validation_start": str(frame.index[validation_start])[:10], "validation_end": str(frame.index[validation_stop - 1])[:10],
                      "test_start": str(frame.index[validation_stop])[:10], "test_end": str(frame.index[test_stop - 1])[:10],
                      "test": test["metrics"]})
    audit = {"passed": True, "factor_observation_cutoff": "t일 종가", "execution_rule": "t일 종가 신호 → t+1일 시가 체결",
             "final_test_excluded_from_selection": True, "data_start": str(frame.index[0])[:10], "data_end": str(frame.index[-1])[:10],
             "data_fingerprint": final_test["data_fingerprint"]}
    cost_stress = _cost_stress(frame, ticker, config, validation_end, factor_filter)
    statistical_validation = _statistical_validation(final_returns, len(valid_candidates))
    return {
        "ticker": ticker, "split": {"train_end": str(frame.index[train_end - 1])[:10], "validation_end": str(frame.index[validation_end - 1])[:10],
                                       "test_start": str(frame.index[validation_end])[:10], "test_end": str(frame.index[-1])[:10]},
        "selection_rule": "validation Sharpe, then validation total return; final test excluded from selection",
        "selected": {"fast_ma": selected["fast_ma"], "slow_ma": selected["slow_ma"], "validation": selected["validation"],
                     "final_test": final_test["metrics"], "run_id": final_test["run_id"], "data_fingerprint": final_test["data_fingerprint"]},
        "factor_filter": factor_filter or {"enabled": False}, "point_in_time_audit": audit,
        "cost_stress": cost_stress, "statistical_validation": statistical_validation,
        "paper_candidate_assessment": _candidate_assessment(final_test["metrics"], cost_stress, audit),
        "sensitivity": sensitivity, "walk_forward": folds,
    }
