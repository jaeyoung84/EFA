"""저장된 일봉에서 재현 가능한 관측 팩터를 계산한다.

팩터는 과거와 현재 봉만 이용하며, 예측·매수/매도 신호를 만들지 않는다. 입력 봉의
fingerprint를 함께 기록해 같은 기준일의 결과를 추적할 수 있다.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import stdev


FACTOR_VERSION = "1.0.0"
MINIMUM_HISTORY_DAYS = 21


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rolling_mean(values: list[float], window: int) -> float | None:
    return round(sum(values[-window:]) / window, 8) if len(values) >= window else None


def _period_return(values: list[float], days: int) -> float | None:
    if len(values) <= days or values[-days - 1] <= 0:
        return None
    return round(values[-1] / values[-days - 1] - 1, 8)


def _annualized_volatility(values: list[float], window: int = 20) -> float | None:
    if len(values) < window + 1:
        return None
    returns = [values[index] / values[index - 1] - 1 for index in range(len(values) - window, len(values)) if values[index - 1] > 0]
    return round(stdev(returns) * math.sqrt(252), 8) if len(returns) >= 2 else None


def _max_drawdown(values: list[float], window: int = 60) -> float | None:
    if len(values) < 2:
        return None
    peak, minimum = values[-min(window, len(values))], 0.0
    for value in values[-min(window, len(values)):]:
        peak = max(peak, value)
        if peak > 0:
            minimum = min(minimum, value / peak - 1)
    return round(minimum, 8)


def calculate_daily_factors(bars: list[dict]) -> dict:
    """정렬된 일봉에서 수익률·이동평균·변동성·거래량 팩터를 계산한다."""
    valid: list[dict] = []
    for bar in sorted(bars, key=lambda row: str(row.get("timestamp", ""))):
        close = _number(bar.get("close"))
        if close is None or close <= 0:
            continue
        valid.append({"timestamp": str(bar.get("timestamp", ""))[:10], "close": close,
                      "volume": max(_number(bar.get("volume")) or 0.0, 0.0),
                      "fingerprint": str(bar.get("fingerprint", ""))})
    if len(valid) < MINIMUM_HISTORY_DAYS:
        return {"available": False, "factor_version": FACTOR_VERSION, "bar_count": len(valid),
                "minimum_history_days": MINIMUM_HISTORY_DAYS,
                "reason": f"팩터 계산에는 최소 {MINIMUM_HISTORY_DAYS}개 일봉이 필요합니다."}
    closes = [row["close"] for row in valid]
    volumes = [row["volume"] for row in valid]
    sma20, sma60, sma120 = (_rolling_mean(closes, window) for window in (20, 60, 120))
    avg_volume20 = _rolling_mean(volumes, 20)
    fingerprint_rows = [[row["timestamp"], row["close"], row["volume"], row["fingerprint"]] for row in valid]
    input_fingerprint = hashlib.sha256(json.dumps(fingerprint_rows, separators=(",", ":")).encode()).hexdigest()
    return {
        "available": True, "factor_version": FACTOR_VERSION, "as_of": valid[-1]["timestamp"], "bar_count": len(valid),
        "input_fingerprint": input_fingerprint,
        "observations": {"close": closes[-1], "volume": volumes[-1]},
        "factors": {
            "return_5d": _period_return(closes, 5), "return_20d": _period_return(closes, 20),
            "return_60d": _period_return(closes, 60), "sma_20": sma20, "sma_60": sma60, "sma_120": sma120,
            "price_to_sma_20": round(closes[-1] / sma20 - 1, 8) if sma20 else None,
            "price_to_sma_60": round(closes[-1] / sma60 - 1, 8) if sma60 else None,
            "realized_volatility_20d": _annualized_volatility(closes), "max_drawdown_60d": _max_drawdown(closes),
            "average_volume_20d": avg_volume20,
            "volume_ratio_20d": round(volumes[-1] / avg_volume20, 8) if avg_volume20 else None,
        },
    }


def _percentile_ranks(rows: list[dict], factor_name: str, *, higher_is_better: bool) -> dict[str, float | None]:
    usable = [(row["ticker"], _number(row.get("factors", {}).get(factor_name))) for row in rows]
    usable = [(ticker, value) for ticker, value in usable if value is not None]
    if not usable:
        return {row["ticker"]: None for row in rows}
    ordered = sorted(usable, key=lambda item: item[1])
    positions: dict[str, float] = {}
    for index, (ticker, value) in enumerate(ordered):
        same = [position for position, (_, candidate) in enumerate(ordered) if candidate == value]
        position = sum(same) / len(same)
        score = position / max(len(ordered) - 1, 1)
        positions[ticker] = round(score if higher_is_better else 1 - score, 6)
    return {row["ticker"]: positions.get(row["ticker"]) for row in rows}


def rank_factor_universe(rows: list[dict]) -> list[dict]:
    """같은 기준일의 관측 팩터만 상대 순위로 정리한다.

    복합 점수는 모멘텀·저변동성·낙폭 회복력의 동일 가중 요약일 뿐, 주문 또는
    투자 적합성 판단이 아니다.
    """
    dimensions = {
        "momentum_20d": ("return_20d", True),
        "low_volatility_20d": ("realized_volatility_20d", False),
        "drawdown_resilience_60d": ("max_drawdown_60d", True),
    }
    ranks = {label: _percentile_ranks(rows, source, higher_is_better=higher) for label, (source, higher) in dimensions.items()}
    ranked: list[dict] = []
    for row in rows:
        factor_ranks = {label: ranks[label][row["ticker"]] for label in dimensions}
        values = [value for value in factor_ranks.values() if value is not None]
        ranked.append({**row, "factor_ranks": factor_ranks,
                       "factor_composite": round(sum(values) / len(values), 6) if len(values) == len(dimensions) else None})
    return sorted(ranked, key=lambda row: (row["factor_composite"] is None, -(row["factor_composite"] or 0), row["ticker"]))
