"""전략 저장, 실제 가격 백테스트, 주문 제안, 모의 체결 API.

가상 GBM 백테스트(app/backend/routers/quant.py)와 API 수준에서부터 분리한다.
`/backtest`는 실제 종가 이력으로 계산하며, 주문 제안과 모의 체결은
`type="real_backtest"`로 저장한 전략에서만 만들 수 있다. 모의 체결은
`paper_fills` 컬렉션에만 기록하고 사용자의 실제 보유자산(`portfolios`
컬렉션, app/backend/routers/portfolio.py)은 절대 변경하지 않는다 —
가상 체결이 실제 장부처럼 보이는 것을 막기 위한 안전장치다.
"""
from __future__ import annotations

import base64
import asyncio
import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import re
import uuid
from typing import Literal
from zoneinfo import ZoneInfo
from time import monotonic

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

try:
    from ..backtest_engine import BacktestConfig, benchmark_validation, run_ma_crossover, transaction_cost_sensitivity
    from ..db import get_db
    from ..broker_adapter import BrokerOrderRequest, get_broker_adapter, run_local_sandbox_contract_check
    from ..execution_controls import execution_environment_status, paper_order_promotion_alerts_block, paper_order_promotion_approval_required, paper_submission_allowed, user_execution_permission
    from ..execution_audit import append_execution_audit, serialize_execution_audit, verify_execution_audit
    from ..execution_audit import export_execution_audit_ndjson
    from ..execution_operations import approve_readiness_request, create_readiness_request, is_execution_operator, serialize_readiness_request
    from ..execution_risk import limit_buy_quantity
    from ..market_data import market_data_lineage_report, market_data_operations_report, market_factor_universe_snapshot
    from ..quote_cache import fetch_quote_cached
    from ..research_engine import run_research
    from ..risk_engine import DEFAULT_RISK_POLICY, evaluate_policy
    from ..strategy_allocation import build_rebalance_plan, build_strategy_allocation, compare_strategy_versions
    from ..leverage_dca_strategy import (
        DEFAULT_LEVERAGE_DCA_POLICY, compute_daily_buy_allocation, evaluate_buy_stop_conditions,
        estimate_after_tax_profit, evaluate_reserve_deployment_stage, evaluate_resume_condition, evaluate_sell_trigger,
        resolve_effective_stop_all,
    )
    from ..leverage_dca_signals import fetch_market_signals, fetch_reserve_signals
    from ..market_calendar import get_next_execution_schedule, today_kst_iso
    from ..alpaca_broker import alpaca_paper_configured, execute_paper_order_sync, list_positions as alpaca_list_positions
    from .auth import current_user
except ImportError:
    from backtest_engine import BacktestConfig, benchmark_validation, run_ma_crossover, transaction_cost_sensitivity  # type: ignore
    from db import get_db
    from broker_adapter import BrokerOrderRequest, get_broker_adapter, run_local_sandbox_contract_check  # type: ignore
    from execution_controls import execution_environment_status, paper_order_promotion_alerts_block, paper_order_promotion_approval_required, paper_submission_allowed, user_execution_permission  # type: ignore
    from execution_audit import append_execution_audit, serialize_execution_audit, verify_execution_audit  # type: ignore
    from execution_audit import export_execution_audit_ndjson  # type: ignore
    from execution_operations import approve_readiness_request, create_readiness_request, is_execution_operator, serialize_readiness_request  # type: ignore
    from execution_risk import limit_buy_quantity  # type: ignore
    from market_data import market_data_lineage_report, market_data_operations_report, market_factor_universe_snapshot  # type: ignore
    from quote_cache import fetch_quote_cached
    from research_engine import run_research  # type: ignore
    from risk_engine import DEFAULT_RISK_POLICY, evaluate_policy  # type: ignore
    from strategy_allocation import build_rebalance_plan, build_strategy_allocation, compare_strategy_versions  # type: ignore
    from leverage_dca_strategy import (  # type: ignore
        DEFAULT_LEVERAGE_DCA_POLICY, compute_daily_buy_allocation, evaluate_buy_stop_conditions,
        estimate_after_tax_profit, evaluate_reserve_deployment_stage, evaluate_resume_condition, evaluate_sell_trigger,
        resolve_effective_stop_all,
    )
    from leverage_dca_signals import fetch_market_signals, fetch_reserve_signals  # type: ignore
    from market_calendar import get_next_execution_schedule, today_kst_iso  # type: ignore
    from alpaca_broker import alpaca_paper_configured, execute_paper_order_sync, list_positions as alpaca_list_positions  # type: ignore
    from routers.auth import current_user

router = APIRouter(prefix="/api/strategy", tags=["전략"])

TICKER_PATTERN = re.compile(r"^[A-Z0-9.^=-]{1,20}$")
BACKTEST_PERIOD_PATTERN = re.compile(r"^(6mo|1y|2y|3y|5y)$")
MIN_USABLE_TRADING_DAYS = 20
STRATEGY_TYPE_PATTERN = r"^(real_backtest|virtual_backtest|lean)$"
DEFAULT_PAPER_CAPITAL_KRW = 10_000_000.0
PAPER_SNAPSHOT_TIMEZONE = ZoneInfo("Asia/Seoul")
PAPER_HISTORY_DAYS = {"1m": 31, "3m": 93, "6m": 186, "1y": 366, "all": None}
_promotion_locks: dict[str, asyncio.Lock] = {}


def _promotion_review_days() -> int:
    """승인 버전을 사람이 다시 검토할 기본 주기(환경별 7~365일)."""
    try:
        return max(7, min(365, int(os.getenv("STRATEGY_PROMOTION_REVIEW_DAYS", "30"))))
    except ValueError:
        return 30


def _promotion_performance_gap_pct() -> float:
    """백테스트 기대수익률 대비 단순 모의성과 괴리 점검 한도(1~100%)."""
    try:
        return max(1.0, min(100.0, float(os.getenv("STRATEGY_PROMOTION_PERFORMANCE_GAP_PCT", "10"))))
    except ValueError:
        return 10.0


def _live_review_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _live_review_pct(name: str, default: float) -> float:
    try:
        return max(0.0, min(100.0, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _live_review_policy() -> dict:
    """실거래 검토 후보 판정용 모의운용 기준. 이 값은 주문 권한이 아니다."""
    return {
        "min_trading_days": _live_review_int("PAPER_LIVE_REVIEW_MIN_TRADING_DAYS", 20, 5, 252),
        "min_order_decisions": _live_review_int("PAPER_LIVE_REVIEW_MIN_ORDER_DECISIONS", 3, 1, 100),
        "max_mdd_pct": _live_review_pct("PAPER_LIVE_REVIEW_MAX_MDD_PCT", 10),
        "max_performance_gap_pct": _live_review_pct("PAPER_LIVE_REVIEW_MAX_PERFORMANCE_GAP_PCT", 10),
        "max_block_rate_pct": _live_review_pct("PAPER_LIVE_REVIEW_MAX_BLOCK_RATE_PCT", 20),
        "max_reduced_rate_pct": _live_review_pct("PAPER_LIVE_REVIEW_MAX_REDUCED_RATE_PCT", 20),
        "max_sandbox_rejection_rate_pct": _live_review_pct("PAPER_LIVE_REVIEW_MAX_SANDBOX_REJECTION_RATE_PCT", 10),
    }

# lean.py와 동일한 LEAN 사전 실행 결과 참조. 파일이 불변이므로 조회 시마다
# 그대로 다시 읽어도 안전하다(라우터 간 결합을 피하기 위해 자체 사본을 둔다,
# ml.py가 main.py의 폰트 설정 함수를 자체 복사해 두는 것과 같은 관례).
LEAN_ROOT_DIR = Path(__file__).resolve().parents[3]
LEAN_RUNS: dict[str, dict[str, object]] = {
    "samsung": {
        "label": "삼성전자",
        "code": "005930",
        "summary_path": LEAN_ROOT_DIR / "lean-results" / "SamsungBuyAndHold-summary.json",
    },
    "hyundai": {
        "label": "현대자동차",
        "code": "005380",
        "summary_path": LEAN_ROOT_DIR / "hyundai-results" / "HyundaiTrendBacktest-summary.json",
    },
}

_matplotlib_font_configured = False


def configure_matplotlib_korean_font(plt) -> None:
    global _matplotlib_font_configured
    if _matplotlib_font_configured:
        return
    import matplotlib.font_manager as fm

    for font_path in (
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if font_path.exists():
            fm.fontManager.addfont(str(font_path))
            plt.rcParams["font.family"] = fm.FontProperties(fname=str(font_path)).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False
    _matplotlib_font_configured = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_lean_summary(path: Path) -> dict:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"LEAN 결과 파일을 찾을 수 없습니다: {path.name}")
    import json

    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _lean_equity_curve(data: dict) -> list[dict[str, float]]:
    try:
        values = data["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
    except (KeyError, TypeError):
        return []
    return [{"time": point[0], "equity": point[4]} for point in values if isinstance(point, list) and len(point) >= 5]


def _download_close_series(ticker: str, period: str):
    """실제 일별 종가를 조회한다. 실패·데이터 없음은 호출부에서 판단한다."""
    import yfinance as yf

    frame = yf.download(
        ticker, period=period, interval="1d", auto_adjust=True, progress=False, threads=False, timeout=10,
    )
    close = frame["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    return close.dropna()


def _download_ohlc_frame(ticker: str, period: str):
    """체결 시가와 평가 종가를 함께 가져온다."""
    import yfinance as yf

    frame = yf.download(
        ticker, period=period, interval="1d", auto_adjust=True, progress=False, threads=False, timeout=10,
    )
    if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
        frame.columns = frame.columns.get_level_values(0)
    return frame[["Open", "Close"]].dropna()


def _run_ma_crossover(close, fast_ma: int, slow_ma: int, transaction_cost_bps: float, slippage_bps: float):
    """기존 주문 제안 경로용 호환 래퍼. 시가가 없으면 종가를 체결 기준으로 사용한다."""
    import pandas as pd

    ohlc = pd.DataFrame({"Open": close, "Close": close})
    df, _ = run_ma_crossover(ohlc, "LEGACY", BacktestConfig(fast_ma, slow_ma, transaction_cost_bps, slippage_bps))
    return df


def _performance_metrics(returns) -> dict:
    """일간 수익률 시리즈에서 총수익률·CAGR·Sharpe·Sortino·MDD·승률을 계산한다."""
    if returns.empty:
        return {"total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "sortino": 0.0, "mdd": 0.0, "win_rate": 0.0}
    cum = (1 + returns).cumprod()
    n_years = len(returns) / 252
    total_return = float(cum.iloc[-1] - 1)
    cagr = float(cum.iloc[-1] ** (1 / n_years) - 1) if n_years > 0 and cum.iloc[-1] > 0 else 0.0
    excess = returns - 0.03 / 252
    sharpe = float(excess.mean() / excess.std() * (252 ** 0.5)) if excess.std() > 0 else 0.0
    downside = excess[excess < 0]
    sortino = float(excess.mean() / downside.std() * (252 ** 0.5)) if len(downside) > 1 and downside.std() > 0 else 0.0
    running_max = cum.cummax()
    mdd = float(((cum - running_max) / running_max).min())
    nonzero = returns[returns != 0]
    win_rate = float(len(returns[returns > 0]) / max(len(nonzero), 1))
    return {
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "mdd": round(mdd, 4),
        "win_rate": round(win_rate, 4),
    }


class RealBacktestRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    fast_ma: int = Field(default=20, ge=5, le=60)
    slow_ma: int = Field(default=60, ge=20, le=200)
    period: str = Field(default="2y")
    transaction_cost_bps: float = Field(default=10.0, ge=0, le=500)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    initial_capital: float = Field(default=10_000_000, ge=100_000, le=10_000_000_000)
    benchmark_ticker: str = Field(default="", max_length=20)


class StrategyResearchRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    period: str = Field(default="5y")
    fast_candidates: list[int] = Field(default_factory=lambda: [5, 10, 20, 30], min_length=1, max_length=8)
    slow_candidates: list[int] = Field(default_factory=lambda: [40, 60, 90, 120], min_length=1, max_length=8)
    transaction_cost_bps: float = Field(default=10.0, ge=0, le=500)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    walk_forward_folds: int = Field(default=3, ge=2, le=5)
    factor_filter_enabled: bool = False
    min_return_20d: float | None = Field(default=None, ge=-1, le=5)
    max_realized_volatility_20d: float | None = Field(default=None, gt=0, le=5)


class ResearchUniverseScreenRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=25)
    as_of: str | None = Field(default=None, max_length=10)
    minimum_average_volume_20d: float = Field(default=0, ge=0)
    minimum_factor_composite: float | None = Field(default=None, ge=0, le=1)


class StrategySaveRequest(BaseModel):
    type: str = Field(pattern=STRATEGY_TYPE_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    ticker: str = Field(default="", max_length=20)
    symbol: str = Field(default="", max_length=20)
    params: dict = Field(default_factory=dict)
    rebalance_note: str = Field(default="", max_length=200)
    snapshot: dict = Field(default_factory=dict)
    previous_version_id: str = Field(default="", max_length=64)


class ProposalApproveRequest(BaseModel):
    quantity: float = Field(gt=0, le=100_000_000)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = Field(default=None, gt=0, le=10_000_000_000)
    max_fill_quantity: float | None = Field(default=None, gt=0, le=100_000_000)


class PaperAccountUpdate(BaseModel):
    initial_capital_krw: float = Field(ge=1_000, le=10_000_000_000)


class PaperAlertResolution(BaseModel):
    action: str = Field(pattern=r"^(acknowledged|deferred|resolved)$")
    note: str = Field(default="", max_length=200)


class PaperExecutionPermissionUpdate(BaseModel):
    paper_trading_enabled: bool


class ReadinessRequestCreate(BaseModel):
    purpose: str = Field(pattern=r"^(secret_rotation_review|worm_archive_review|broker_sandbox_review)$")


class StrategyPromotionApprovalRequest(BaseModel):
    note: str = Field(default="", max_length=400)


class LeverageDcaConfigUpdate(BaseModel):
    enabled: bool
    daily_buy_krw: float = Field(default=5_000_000.0, ge=100_000, le=1_000_000_000)
    max_position_krw: float = Field(default=100_000_000.0, ge=1_000_000, le=10_000_000_000)
    sell_net_profit_target_krw: float = Field(default=5_000_000.0, ge=100_000, le=1_000_000_000)
    reserve_enabled: bool = False
    reserve_cash_krw: float = Field(default=100_000_000.0, ge=1_000_000, le=10_000_000_000)
    alpaca_enabled: bool = False


class StrategyDecisionRecordCreate(BaseModel):
    target_type: Literal["strategy", "candidate"]
    target_id: str = Field(min_length=1, max_length=80)
    decision: Literal["continue_monitoring", "review_required", "stop_consideration"]
    note: str = Field(default="", max_length=500)


def _serialize_strategy(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "type": doc.get("type", "unknown"),
        "name": doc["name"],
        "ticker": doc.get("ticker", ""),
        "symbol": doc.get("symbol", ""),
        "params": doc.get("params", {}),
        "rebalance_note": doc.get("rebalance_note", ""),
        "snapshot": doc.get("snapshot", {}),
        "version": doc.get("version", 1),
        "parent_id": str(doc["parent_id"]) if doc.get("parent_id") else None,
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
    }


def _serialize_proposal(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "strategy_id": str(doc["strategy_id"]),
        "ticker": doc["ticker"],
        "direction": doc["direction"],
        "basis": doc["basis"],
        "reference_price": doc["reference_price"],
        "status": doc["status"],
        "preflight": doc.get("preflight"),
        "sandbox_result": doc.get("sandbox_result"),
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
        "decided_at": doc["decided_at"].isoformat() if doc.get("decided_at") else None,
    }


def _serialize_fill(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "strategy_id": str(doc["strategy_id"]) if doc.get("strategy_id") else None,
        "proposal_id": str(doc["proposal_id"]) if doc.get("proposal_id") else None,
        "ticker": doc["ticker"],
        "direction": doc["direction"],
        "quantity": doc["quantity"],
        "fill_price": doc["fill_price"],
        "requested_quantity": doc.get("requested_quantity", doc["quantity"]),
        "risk_control": doc.get("risk_control"),
        "execution_simulation": doc.get("execution_simulation"),
        "preflight": doc.get("preflight"),
        "sandbox_result": doc.get("sandbox_result"),
        "filled_at": doc["filled_at"].isoformat() if doc.get("filled_at") else None,
    }


def _serialize_paper_alert(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]), "type": doc["type"], "severity": doc["severity"], "title": doc["title"],
        "message": doc["message"], "status": doc["status"], "context": doc.get("context", {}),
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
        "updated_at": doc["updated_at"].isoformat() if doc.get("updated_at") else None,
        "events": [{**event, "at": event["at"].isoformat() if event.get("at") else None} for event in doc.get("events", [])],
    }


async def _get_risk_policy(user_id) -> dict:
    saved = await get_db().portfolio_risk_policies.find_one({"user_id": user_id}, {"_id": 0, "user_id": 0})
    return {**DEFAULT_RISK_POLICY, **(saved or {})}


def _infer_currency(ticker: str) -> str:
    return "KRW" if ticker.endswith((".KS", ".KQ")) else "USD"


def _simulate_paper_execution(*, side: str, quote_price: float, order_type: str, limit_price: float | None,
                              requested_quantity: float, max_fill_quantity: float | None) -> dict:
    """Deterministic fill simulation; it is not a broker, exchange, or market microstructure model."""
    if order_type == "limit" and limit_price is None:
        return {"status": "rejected", "reason": "지정가 주문에는 limit_price가 필요합니다."}
    if order_type == "limit" and ((side == "buy" and quote_price > float(limit_price)) or (side == "sell" and quote_price < float(limit_price))):
        return {"status": "unfilled", "reason": "현재 지연 시세가 지정가 조건에 도달하지 않았습니다.", "quote_price": quote_price,
                "limit_price": limit_price, "order_type": order_type}
    try:
        slippage_bps = max(0.0, min(100.0, float(os.getenv("PAPER_SIMULATION_SLIPPAGE_BPS", "5"))))
    except ValueError:
        slippage_bps = 5.0
    fill_price = quote_price * (1 + (slippage_bps / 10_000 if side == "buy" else -slippage_bps / 10_000)) if order_type == "market" else quote_price
    fill_quantity = min(requested_quantity, max_fill_quantity) if max_fill_quantity is not None else requested_quantity
    return {"status": "partially_filled" if fill_quantity < requested_quantity else "filled", "order_type": order_type,
            "quote_price": quote_price, "limit_price": limit_price, "slippage_bps": slippage_bps if order_type == "market" else 0.0,
            "fill_price": round(fill_price, 8), "fill_quantity": round(fill_quantity, 8), "unfilled_quantity": round(requested_quantity - fill_quantity, 8),
            "model": "delayed_quote_limit_check_and_fixed_slippage", "external_execution": False}


async def _instrument_metadata(user: dict, ticker: str) -> dict:
    """실제 보유 장부의 분류가 있으면 재사용하되, 모의 체결 장부는 변경하지 않는다."""
    portfolio = await get_db().portfolios.find_one({"user_id": user["_id"]}, {"holdings": 1})
    holding = next((item for item in (portfolio or {}).get("holdings", []) if item.get("ticker") == ticker), None)
    return {
        "currency": (holding or {}).get("currency", _infer_currency(ticker)),
        "sector": (holding or {}).get("sector", "미분류") or "미분류",
    }


async def _paper_account(user_id) -> dict:
    document = await get_db().paper_accounts.find_one({"user_id": user_id}, {"_id": 0, "user_id": 0})
    return {"initial_capital_krw": float((document or {}).get("initial_capital_krw", DEFAULT_PAPER_CAPITAL_KRW))}


async def _paper_ledger(user: dict) -> tuple[dict, dict[str, dict]]:
    """모의 체결 원장을 기반으로 현금과 종목별 수량·분류를 계산한다."""
    account = await _paper_account(user["_id"])
    cash_krw = account["initial_capital_krw"]
    positions: dict[str, dict] = {}
    rows = await get_db().paper_fills.find({"user_id": user["_id"]}).to_list(10_000)
    for row in rows:
        quantity = float(row.get("quantity", 0))
        notional_krw = float(row.get("notional_krw", row.get("notional", 0)))
        direction = row.get("direction")
        cash_krw += notional_krw if direction == "sell" else -notional_krw
        ticker = row["ticker"]
        item = positions.setdefault(ticker, {"ticker": ticker, "quantity": 0.0,
                                              "currency": row.get("currency", _infer_currency(ticker)),
                                              "sector": row.get("sector", "미분류")})
        item["quantity"] += quantity if direction == "buy" else -quantity
    return {"initial_capital_krw": account["initial_capital_krw"], "cash_krw": max(0.0, cash_krw)}, {
        ticker: item for ticker, item in positions.items() if item["quantity"] > 1e-8
    }


async def _paper_positions_at_market(user: dict) -> tuple[dict, list[dict]]:
    ledger, quantities = await _paper_ledger(user)
    positions: list[dict] = []
    for ticker, item in quantities.items():
        quote = fetch_quote_cached(ticker)
        if quote["status"] != "ok":
            raise HTTPException(status_code=502, detail=f"기존 모의 보유종목 {ticker}의 시세를 가져오지 못해 주문 위험을 확인할 수 없습니다.")
        fx = 1.0
        if item["currency"] == "USD":
            fx_quote = fetch_quote_cached("KRW=X")
            if fx_quote["status"] != "ok":
                raise HTTPException(status_code=502, detail="USD/KRW 환율을 가져오지 못해 주문 위험을 확인할 수 없습니다.")
            fx = float(fx_quote["price"])
        positions.append({"ticker": ticker, "currency": item["currency"], "sector": item["sector"],
                          "value_krw": float(quote["price"]) * item["quantity"] * fx})
    return ledger, positions


async def _paper_dashboard(user: dict) -> dict:
    """체결 원장과 현재 지연 시세를 결합한 읽기 전용 모의 계좌 현황이다."""
    account = await _paper_account(user["_id"])
    fills = await get_db().paper_fills.find({"user_id": user["_id"]}).sort("filled_at", 1).to_list(10_000)
    strategy_docs = await get_db().strategies.find({"user_id": user["_id"]}, {"name": 1}).to_list(1_000)
    strategy_names = {str(row["_id"]): row.get("name", "삭제된 전략") for row in strategy_docs}

    cash_krw = account["initial_capital_krw"]
    positions: dict[tuple[str, str], dict] = {}
    realized_total = 0.0
    curve: list[dict] = []
    for fill in fills:
        strategy_id = str(fill.get("strategy_id", ""))
        ticker = fill["ticker"]
        key = (strategy_id, ticker)
        item = positions.setdefault(key, {
            "strategy_id": strategy_id, "strategy_name": strategy_names.get(strategy_id, "삭제된 전략"),
            "ticker": ticker, "quantity": 0.0, "average_cost_krw": 0.0,
            "currency": fill.get("currency", _infer_currency(ticker)), "sector": fill.get("sector", "미분류"),
            "realized_pnl_krw": 0.0,
        })
        quantity = float(fill.get("quantity", 0))
        notional_krw = float(fill.get("notional_krw", fill.get("notional", 0)))
        if fill.get("direction") == "buy":
            old_cost = item["average_cost_krw"] * item["quantity"]
            item["quantity"] += quantity
            item["average_cost_krw"] = (old_cost + notional_krw) / item["quantity"] if item["quantity"] else 0.0
            cash_krw -= notional_krw
        else:
            sold = min(quantity, item["quantity"])
            realized = notional_krw - item["average_cost_krw"] * sold
            item["realized_pnl_krw"] += realized
            realized_total += realized
            item["quantity"] = max(0.0, item["quantity"] - sold)
            if item["quantity"] <= 1e-8:
                item["average_cost_krw"] = 0.0
            cash_krw += notional_krw

        # 체결 직후에는 남은 포지션을 평균 취득원가로 평가한다. 마지막 점만 현재 시세다.
        book_equity = cash_krw + sum(p["quantity"] * p["average_cost_krw"] for p in positions.values())
        curve.append({"at": fill["filled_at"].isoformat(), "equity_krw": round(book_equity, 2), "basis": "fill_cost"})

    active = [item for item in positions.values() if item["quantity"] > 1e-8]
    needs_fx = any(item["currency"] == "USD" for item in active)
    fx_rate = None
    if needs_fx:
        fx = fetch_quote_cached("KRW=X")
        if fx["status"] == "ok":
            fx_rate = float(fx["price"])

    unpriced: list[dict] = []
    unrealized_total = 0.0
    for item in active:
        quote = fetch_quote_cached(item["ticker"])
        if quote["status"] != "ok" or (item["currency"] == "USD" and fx_rate is None):
            unpriced.append({"ticker": item["ticker"], "strategy_id": item["strategy_id"],
                             "reason": quote.get("reason", "USD/KRW 환율 조회 실패") if quote["status"] != "ok" else "USD/KRW 환율 조회 실패"})
            item["status"] = "unpriced"
            continue
        fx = fx_rate if item["currency"] == "USD" else 1.0
        market_value = float(quote["price"]) * item["quantity"] * float(fx)
        cost_value = item["average_cost_krw"] * item["quantity"]
        item.update({"status": "ok", "mark_price": float(quote["price"]), "fx_usd_krw": fx_rate if item["currency"] == "USD" else None,
                     "market_value_krw": round(market_value, 2), "unrealized_pnl_krw": round(market_value - cost_value, 2),
                     "return_pct": round((market_value - cost_value) / cost_value * 100, 2) if cost_value else None})
        unrealized_total += market_value - cost_value

    available = not unpriced
    equity = cash_krw + sum(item.get("market_value_krw", 0.0) for item in active) if available else None
    if available and fills:
        curve.append({"at": _now().isoformat(), "equity_krw": round(equity, 2), "basis": "latest_mark"})

    strategy_summary: dict[str, dict] = {}
    for item in positions.values():
        summary = strategy_summary.setdefault(item["strategy_id"], {"strategy_id": item["strategy_id"], "strategy_name": item["strategy_name"],
            "realized_pnl_krw": 0.0, "unrealized_pnl_krw": 0.0, "market_value_krw": 0.0, "open_position_count": 0})
        summary["realized_pnl_krw"] += item["realized_pnl_krw"]
        if item["quantity"] > 1e-8:
            summary["open_position_count"] += 1
            summary["unrealized_pnl_krw"] += item.get("unrealized_pnl_krw", 0.0)
            summary["market_value_krw"] += item.get("market_value_krw", 0.0)
    for summary in strategy_summary.values():
        summary["realized_pnl_krw"] = round(summary["realized_pnl_krw"], 2)
        summary["unrealized_pnl_krw"] = round(summary["unrealized_pnl_krw"], 2) if available else None
        summary["market_value_krw"] = round(summary["market_value_krw"], 2) if available else None
        summary["net_pnl_krw"] = round(summary["realized_pnl_krw"] + (summary["unrealized_pnl_krw"] or 0), 2) if available else None

    return {
        "available": available, "as_of": _now().isoformat(), "initial_capital_krw": account["initial_capital_krw"],
        "cash_krw": round(cash_krw, 2), "equity_krw": round(equity, 2) if equity is not None else None,
        "realized_pnl_krw": round(realized_total, 2), "unrealized_pnl_krw": round(unrealized_total, 2) if available else None,
        "net_pnl_krw": round(realized_total + unrealized_total, 2) if available else None,
        "return_pct": round((equity - account["initial_capital_krw"]) / account["initial_capital_krw"] * 100, 2) if equity is not None and account["initial_capital_krw"] else None,
        "fill_count": len(fills), "positions": active, "strategy_summary": list(strategy_summary.values()), "equity_curve": curve,
        "equity_curve_basis": "체결 시점에는 평균 취득원가, 마지막 점에는 현재 지연 시세로 평가합니다.", "unpriced": unpriced,
    }


async def capture_paper_snapshot(user: dict) -> dict:
    """현재 평가를 한국 거래일별 스냅샷으로 idempotent 저장한다."""
    dashboard = await _paper_dashboard(user)
    now = _now()
    snapshot_date = now.astimezone(PAPER_SNAPSHOT_TIMEZONE).date().isoformat()
    snapshot = {
        "user_id": user["_id"], "snapshot_date": snapshot_date, "captured_at": now,
        "available": dashboard["available"], "equity_krw": dashboard["equity_krw"], "cash_krw": dashboard["cash_krw"],
        "realized_pnl_krw": dashboard["realized_pnl_krw"], "unrealized_pnl_krw": dashboard["unrealized_pnl_krw"],
        "net_pnl_krw": dashboard["net_pnl_krw"], "return_pct": dashboard["return_pct"], "fill_count": dashboard["fill_count"],
        "unpriced": dashboard["unpriced"], "source": "paper_dashboard_delayed_quote",
    }
    await get_db().paper_daily_snapshots.update_one(
        {"user_id": user["_id"], "snapshot_date": snapshot_date}, {"$set": snapshot}, upsert=True,
    )
    promotion_monitor = await capture_promotion_monitor_snapshot(user, dashboard)
    return {key: value for key, value in snapshot.items() if key not in {"user_id", "unpriced"}} | {
        "unpriced_count": len(snapshot["unpriced"]), "promotion_monitor": promotion_monitor,
    }


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _promotion_monitor_records(user: dict, dashboard: dict | None = None) -> list[dict]:
    """승인된 전략별 모의운용 상태를 계산한다.

    모의 수익률은 실제 계좌 수익률이 아니라 체결 누적원가 대비 손익의 참고값이다.
    자금 입출금·체결 시점 차이를 보정하지 않으므로, 자동 철회 기준으로 사용하지 않는다.
    """
    dashboard = dashboard or await _paper_dashboard(user)
    states = await get_db().strategy_promotion_states.find({"user_id": user["_id"], "status": "approved"}).to_list(200)
    summaries = {row["strategy_id"]: row for row in dashboard.get("strategy_summary", [])}
    now = _now()
    records: list[dict] = []
    for state in states:
        strategy = await get_db().strategies.find_one({"_id": state["strategy_id"], "user_id": user["_id"]})
        approved_at = _utc_datetime(state["approved_at"])
        age_days = max(0, (now - approved_at).days)
        record = {
            "strategy_id": str(state["strategy_id"]), "strategy_version": state.get("strategy_version", 1),
            "strategy_name": (strategy or {}).get("name", "삭제된 전략"), "approved_at": approved_at.isoformat(),
            "approval_age_days": age_days, "review_due": age_days >= _promotion_review_days(),
            "approval_fingerprint_matches": bool(strategy) and state.get("strategy_fingerprint") == _strategy_version_fingerprint(strategy),
            "paper_return_proxy_pct": None, "expected_backtest_return_pct": None, "performance_gap_pct": None,
            "monitoring_days": 0, "unpriced": [item for item in dashboard.get("unpriced", []) if item.get("strategy_id") == str(state["strategy_id"])],
        }
        if not strategy:
            record["status"] = "strategy_missing"
            records.append(record)
            continue
        fills = await get_db().paper_fills.find({"user_id": user["_id"], "strategy_id": state["strategy_id"]}).sort("filled_at", 1).to_list(10_000)
        buys = [row for row in fills if row.get("direction") == "buy"]
        deployed_krw = sum(float(row.get("notional_krw", row.get("notional", 0))) for row in buys)
        if not buys or deployed_krw <= 0:
            record["status"] = "waiting_for_paper_fills"
            records.append(record)
            continue
        first_fill_at = _utc_datetime(buys[0]["filled_at"])
        monitoring_days = max(0, (now - first_fill_at).days)
        net_pnl = float(summaries.get(str(state["strategy_id"]), {}).get("net_pnl_krw") or 0)
        paper_return = net_pnl / deployed_krw * 100
        cagr = (strategy.get("snapshot", {}).get("metrics", {}) or {}).get("cagr")
        expected = None
        if isinstance(cagr, (int, float)) and cagr > -1:
            expected = ((1 + float(cagr)) ** (monitoring_days / 365) - 1) * 100
        gap = paper_return - expected if expected is not None else None
        record.update({
            "status": "monitoring", "monitoring_days": monitoring_days,
            "deployed_krw": round(deployed_krw, 2), "paper_return_proxy_pct": round(paper_return, 4),
            "expected_backtest_return_pct": round(expected, 4) if expected is not None else None,
            "performance_gap_pct": round(gap, 4) if gap is not None else None,
        })
        records.append(record)
    return records


async def capture_promotion_monitor_snapshot(user: dict, dashboard: dict | None = None) -> dict:
    """승인 전략의 일별 모의운용 참고 지표를 idempotent하게 기록한다."""
    now = _now()
    snapshot_date = now.astimezone(PAPER_SNAPSHOT_TIMEZONE).date().isoformat()
    records = await _promotion_monitor_records(user, dashboard)
    for record in records:
        await get_db().strategy_promotion_monitor_snapshots.update_one(
            {"user_id": user["_id"], "strategy_id": ObjectId(record["strategy_id"]), "snapshot_date": snapshot_date},
            {"$set": {"user_id": user["_id"], "strategy_id": ObjectId(record["strategy_id"]), "snapshot_date": snapshot_date,
                      "captured_at": now, "source": "paper_strategy_summary_delayed_quote", **record}}, upsert=True,
        )
    return {"snapshot_date": snapshot_date, "approved_strategy_count": len(records), "records": records}


async def _promotion_monitor_findings(user: dict, dashboard: dict | None = None) -> list[dict]:
    """승인 상태는 유지한 채 재검토가 필요한 조건만 알림 후보로 만든다."""
    findings: list[dict] = []
    for record in await _promotion_monitor_records(user, dashboard):
        strategy_id = record["strategy_id"]
        if record["status"] == "strategy_missing" or not record["approval_fingerprint_matches"]:
            findings.append({"fingerprint": f"promotion:integrity:{strategy_id}", "type": "promotion_integrity", "severity": "critical",
                             "title": "승인 전략 무결성 점검", "message": f"{record['strategy_name']} 승인 당시 지문과 현재 전략을 확인할 수 없습니다.", "context": record})
        if record["review_due"]:
            findings.append({"fingerprint": f"promotion:review_due:{strategy_id}", "type": "promotion_review", "severity": "warning",
                             "title": "승인 전략 재검토 기한", "message": f"{record['strategy_name']} 승인 후 {record['approval_age_days']}일이 지났습니다. 재검토가 필요합니다.", "context": record})
        if record["unpriced"]:
            findings.append({"fingerprint": f"promotion:data_quality:{strategy_id}", "type": "promotion_data_quality", "severity": "warning",
                             "title": "승인 전략 시세 확인 필요", "message": f"{record['strategy_name']}의 모의 포지션 시세를 확인할 수 없습니다.", "context": record})
        gap = record.get("performance_gap_pct")
        if record.get("monitoring_days", 0) >= 20 and gap is not None and gap <= -_promotion_performance_gap_pct():
            findings.append({"fingerprint": f"promotion:performance_gap:{strategy_id}", "type": "promotion_performance", "severity": "warning",
                             "title": "백테스트 대비 모의성과 괴리", "message": f"{record['strategy_name']}의 참고 성과가 기대값보다 {abs(gap):.2f}%p 낮습니다. 재검토가 필요합니다.", "context": record})
    return findings


def _proxy_mdd_pct(values: list[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    drawdowns = []
    for value in values:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1 if peak else 0)
    return round(abs(min(drawdowns)) * 100, 4)


async def build_live_review_readiness(user: dict, dashboard: dict | None = None) -> dict:
    """승인 전략의 장기 모의운용 결과를 실거래 *검토 후보*로만 분류한다.

    반환되는 candidate 표시는 실거래 주문, 브로커 연결, 실행 권한을 생성하지 않는다.
    """
    policy = _live_review_policy()
    records = await _promotion_monitor_records(user, dashboard)
    result: list[dict] = []
    for record in records:
        strategy_id = ObjectId(record["strategy_id"])
        snapshots = await get_db().strategy_promotion_monitor_snapshots.find({
            "user_id": user["_id"], "strategy_id": strategy_id, "status": "monitoring", "paper_return_proxy_pct": {"$ne": None},
        }, {"snapshot_date": 1, "captured_at": 1, "paper_return_proxy_pct": 1}).sort("snapshot_date", 1).to_list(2_000)
        # 같은 날짜는 upsert 한 행만 남기므로 이 수가 장 마감 모의운용 관측일 수다.
        observed_days = len({row["snapshot_date"] for row in snapshots})
        proxy_values = [1 + float(row["paper_return_proxy_pct"]) / 100 for row in snapshots]
        monitoring_started_at = snapshots[0].get("captured_at") if snapshots else _utc_datetime(datetime.fromisoformat(record["approved_at"]))
        proposals = await get_db().order_proposals.find({
            "user_id": user["_id"], "strategy_id": strategy_id, "created_at": {"$gte": monitoring_started_at},
        }).to_list(2_000)
        fills = await get_db().paper_fills.find({
            "user_id": user["_id"], "strategy_id": strategy_id, "filled_at": {"$gte": monitoring_started_at},
        }).to_list(2_000)
        decisions = [row for row in proposals if row.get("status") in {"approved", "blocked", "rejected"}]
        blocked = sum(row.get("status") == "blocked" for row in decisions)
        reduced = sum((row.get("risk_control") or {}).get("action") == "reduced" for row in fills)
        sandbox_attempts = [row for row in proposals if (row.get("sandbox_result") or {}).get("attempted")]
        sandbox_rejected = sum((row.get("sandbox_result") or {}).get("accepted") is False for row in sandbox_attempts)
        decision_count = len(decisions)
        fill_count = len(fills)
        metrics = {
            "observed_trading_days": observed_days, "mdd_pct": _proxy_mdd_pct(proxy_values),
            "performance_gap_pct": record.get("performance_gap_pct"), "decision_count": decision_count,
            "block_rate_pct": round(blocked / decision_count * 100, 4) if decision_count else None,
            "reduced_rate_pct": round(reduced / fill_count * 100, 4) if fill_count else None,
            "sandbox_rejection_rate_pct": round(sandbox_rejected / len(sandbox_attempts) * 100, 4) if sandbox_attempts else None,
        }
        reasons: list[str] = []
        if record["status"] != "monitoring":
            reasons.append("유효한 모의 체결·시세 관측이 아직 충분하지 않습니다.")
        if not record["approval_fingerprint_matches"]:
            reasons.append("승인 당시 전략 지문과 현재 전략이 일치하지 않습니다.")
        if record["unpriced"]:
            reasons.append("현재 모의 포지션의 시세를 확인할 수 없습니다.")
        if observed_days < policy["min_trading_days"]:
            reasons.append(f"모의운용 관측일이 {observed_days}/{policy['min_trading_days']}일입니다.")
        if decision_count < policy["min_order_decisions"]:
            reasons.append(f"주문 결정 표본이 {decision_count}/{policy['min_order_decisions']}건입니다.")
        enough_observation = not reasons
        if enough_observation:
            if metrics["mdd_pct"] is None or metrics["mdd_pct"] > policy["max_mdd_pct"]:
                reasons.append("모의운용 최대낙폭이 기준을 초과했습니다.")
            gap = metrics["performance_gap_pct"]
            if gap is None or gap < -policy["max_performance_gap_pct"]:
                reasons.append("백테스트 대비 모의 성과 괴리가 기준을 초과했습니다.")
            if metrics["block_rate_pct"] is not None and metrics["block_rate_pct"] > policy["max_block_rate_pct"]:
                reasons.append("주문 차단 비율이 기준을 초과했습니다.")
            if metrics["reduced_rate_pct"] is not None and metrics["reduced_rate_pct"] > policy["max_reduced_rate_pct"]:
                reasons.append("위험 한도 감액 비율이 기준을 초과했습니다.")
            if metrics["sandbox_rejection_rate_pct"] is not None and metrics["sandbox_rejection_rate_pct"] > policy["max_sandbox_rejection_rate_pct"]:
                reasons.append("로컬 샌드박스 거절 비율이 기준을 초과했습니다.")
        if not enough_observation:
            status = "monitoring"
        elif reasons:
            status = "review_required"
        else:
            status = "live_review_candidate"
        result.append({
            "strategy_id": record["strategy_id"], "strategy_name": record["strategy_name"], "strategy_version": record["strategy_version"],
            "status": status, "candidate_for_live_review": status == "live_review_candidate", "metrics": metrics,
            "reasons": reasons, "creates_live_authority": False, "automatic_action": "재검토 표기만 하며 승인 철회·실거래 주문은 하지 않습니다.",
        })
    return {"policy": policy, "items": result, "live_trading_authorized": False,
            "disclaimer": "이는 제한적 실거래 검토 후보 분류일 뿐 실거래 권한이나 브로커 주문 전송을 만들지 않습니다."}


async def build_strategy_governance_board(user: dict) -> dict:
    """Combine existing paper-monitoring evidence into a human-review decision board.

    The board deliberately has no mutation endpoint: neither a candidate label nor
    a review-required label can promote, revoke, or submit an order.
    """
    dashboard = await _paper_dashboard(user)
    promotion_records = await _promotion_monitor_records(user, dashboard)
    live_review = await build_live_review_readiness(user, dashboard)
    try:
        # kqs는 전략 라우터를 가져오지 않으므로 지연 import로 순환 의존 없이
        # 동일 사용자의 후보 선정 연속성·검토 범위를 함께 표시할 수 있다.
        from .kqs import build_candidate_selection_quality
        candidate_selection_quality = await build_candidate_selection_quality(user["_id"], 30)
    except Exception:
        candidate_selection_quality = {"available": False, "summary": {},
                                       "disclaimer": "후보 선정 품질 리포트를 불러오지 못했습니다."}
    status_counts: dict[str, int] = {}
    for item in live_review["items"]:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    records_by_id = {record["strategy_id"]: record for record in promotion_records}
    items = []
    for item in live_review["items"]:
        monitor = records_by_id.get(item["strategy_id"], {})
        if item["status"] == "live_review_candidate":
            recommendation = "human_review_candidate"
        elif item["status"] == "review_required":
            recommendation = "human_review_required"
        else:
            recommendation = "continue_paper_monitoring"
        items.append({
            "strategy_id": item["strategy_id"], "strategy_name": item["strategy_name"],
            "strategy_version": item["strategy_version"], "readiness_status": item["status"],
            "recommendation": recommendation, "reasons": item["reasons"], "metrics": item["metrics"],
            "promotion_monitor": {key: monitor.get(key) for key in ("review_due", "approval_age_days", "monitoring_days",
                                                                        "paper_return_proxy_pct", "expected_backtest_return_pct",
                                                                        "performance_gap_pct", "status")},
        })
    return {
        "board_version": "strategy-governance-board-v1", "generated_at": _now().isoformat(),
        "summary": {"approved_strategy_count": len(promotion_records), "live_review_candidate_count": status_counts.get("live_review_candidate", 0),
                    "review_required_count": status_counts.get("review_required", 0), "monitoring_count": status_counts.get("monitoring", 0),
                    "paper_mark_available": dashboard["available"]},
        "paper_strategy_performance": dashboard.get("strategy_summary", []), "candidate_selection_quality": candidate_selection_quality, "items": items,
        "policy": live_review["policy"], "live_trading_authorized": False,
        "disclaimer": "장기 모의운용·승인 전략 재검토를 위한 읽기 전용 의사결정 보드입니다. 전략 모의성과와 후보 연속성은 나란히 보이지만 인과관계·수익률 예측으로 해석하지 않습니다. ‘검토 후보’ 표시는 실거래 권한, 승인 상태, 주문 제출, 전략 중단을 자동으로 바꾸지 않습니다.",
    }


async def build_performance_decomposition(user: dict) -> dict:
    """Place paper-strategy and candidate-group observations side by side without attribution claims."""
    dashboard = await _paper_dashboard(user)
    monitor = await _promotion_monitor_records(user, dashboard)
    try:
        from .kqs import build_candidate_performance_decomposition
        candidates = await build_candidate_performance_decomposition(365)
    except Exception:
        candidates = {"available": False, "items": [], "disclaimer": "후보군 사후성과 자료를 불러오지 못했습니다."}
    strategy_rows = []
    monitor_by_id = {item["strategy_id"]: item for item in monitor}
    for item in dashboard.get("strategy_summary", []):
        item_id = str(item.get("strategy_id", ""))
        monitor_item = monitor_by_id.get(item_id, {})
        strategy_rows.append({
            "strategy_id": item_id, "strategy_name": item.get("strategy_name", "삭제된 전략"),
            "realized_pnl_krw": item.get("realized_pnl_krw"), "unrealized_pnl_krw": item.get("unrealized_pnl_krw"),
            "net_pnl_krw": item.get("net_pnl_krw"), "paper_return_proxy_pct": monitor_item.get("paper_return_proxy_pct"),
            "expected_backtest_return_pct": monitor_item.get("expected_backtest_return_pct"),
            "performance_gap_pct": monitor_item.get("performance_gap_pct"), "monitoring_days": monitor_item.get("monitoring_days", 0),
        })
    return {
        "report_version": "quant-performance-decomposition-v1", "generated_at": _now().isoformat(),
        "paper_mark_available": dashboard["available"], "strategy_items": strategy_rows,
        "candidate_groups": candidates, "disclaimer": "전략 모의원장 손익과 후보군 사후 관측치를 나란히 보여 주는 분석 화면입니다. 표본·측정 방식이 다르므로 인과관계나 우열로 단정할 수 없고, 주문·승격·중단을 자동 실행하지 않습니다.",
    }


async def build_strategy_data_lineage(user: dict) -> dict:
    """Report only the current user's strategy, paper, and portfolio-market inputs."""
    db = get_db()
    tickers = {"^KS11", "^KQ11"}
    async for strategy in db.strategies.find({"user_id": user["_id"], "type": "real_backtest"}, {"ticker": 1}):
        if strategy.get("ticker"):
            tickers.add(str(strategy["ticker"]).upper())
    async for fill in db.paper_fills.find({"user_id": user["_id"]}, {"ticker": 1}):
        if fill.get("ticker"):
            tickers.add(str(fill["ticker"]).upper())
    portfolio = await db.portfolios.find_one({"user_id": user["_id"]}, {"holdings.ticker": 1}) or {}
    for holding in portfolio.get("holdings", []):
        if holding.get("ticker"):
            tickers.add(str(holding["ticker"]).upper())
    report = await market_data_lineage_report(sorted(tickers))
    return {**report, "scope": "로그인 사용자의 실제가격 전략·모의체결·보유종목과 핵심 한국 지수",
            "disclaimer": "사용자 범위의 저장 일봉 계보·재현성 점검 자료입니다. 새 데이터를 수집하거나 전략·포트폴리오·주문을 변경하지 않습니다."}


def _revalidation_threshold_days() -> int:
    return _live_review_int("RESEARCH_REVALIDATION_MAX_AGE_DAYS", 31, 7, 366)


def _revalidation_summary(research: dict, latest: dict | None) -> dict:
    """Compare a manually rerun research result with its frozen held-out baseline."""
    result = research.get("result", {})
    selected = result.get("selected", {})
    baseline = selected.get("final_test", {})
    created = _utc_datetime(research.get("created_at", _now()))
    age_days = max(0, (_now() - created).days)
    item = {
        "research_id": research["research_id"], "ticker": result.get("ticker") or research.get("request", {}).get("ticker"),
        "created_at": created.isoformat(), "baseline": {key: baseline.get(key) for key in ("total_return", "sharpe", "mdd", "days")},
        "baseline_data_fingerprint": selected.get("data_fingerprint"), "age_days": age_days,
        "revalidation_due": age_days >= _revalidation_threshold_days(), "latest_revalidation": None,
        "status": "refresh_required" if age_days >= _revalidation_threshold_days() else "baseline_only", "reasons": [],
    }
    if not latest:
        item["reasons"] = ["최근 데이터로 다시 실행한 검증 기록이 없습니다."]
        return item
    metrics = latest.get("metrics", {})
    deltas = {}
    for key in ("total_return", "sharpe", "mdd"):
        before, after = baseline.get(key), metrics.get(key)
        deltas[key] = round(float(after) - float(before), 6) if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None
    status, reasons = "current", []
    if latest.get("data_fingerprint") == selected.get("data_fingerprint"):
        reasons.append("입력 데이터 지문이 기준 연구와 같습니다.")
    else:
        reasons.append("입력 데이터 지문이 기준 연구와 달라 재검토가 필요합니다.")
        status = "review_required"
    if (deltas.get("total_return") is not None and deltas["total_return"] <= -0.10) or (deltas.get("sharpe") is not None and deltas["sharpe"] <= -0.50) or (deltas.get("mdd") is not None and deltas["mdd"] <= -0.05):
        status = "review_required"
        reasons.append("보존된 최종 테스트 대비 성과 저하 임계치를 넘었습니다.")
    captured = _utc_datetime(latest.get("captured_at", _now()))
    if (_now() - captured).days >= _revalidation_threshold_days():
        status = "refresh_required"
        reasons.append("재검증 기록의 최신성 기한이 지났습니다.")
    item.update({"status": status, "revalidation_due": status != "current", "deltas": deltas,
                 "latest_revalidation": {"captured_at": captured.isoformat(), "metrics": metrics,
                                           "data_fingerprint": latest.get("data_fingerprint"), "run_id": latest.get("run_id")}, "reasons": reasons})
    return item


async def build_revalidation_monitor(user: dict) -> dict:
    """Read revalidation evidence only; it does not rerun market downloads or alter strategy state."""
    rows = await get_db().strategy_research_runs.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(100)
    items = []
    for research in rows:
        latest = await get_db().strategy_revalidations.find_one(
            {"user_id": user["_id"], "research_id": research["research_id"]}, sort=[("captured_at", -1)],
        )
        items.append(_revalidation_summary(research, latest))
    return {"report_version": "research-revalidation-monitor-v1", "generated_at": _now().isoformat(),
            "maximum_age_days": _revalidation_threshold_days(), "items": items,
            "summary": {"research_count": len(items), "current_count": sum(item["status"] == "current" for item in items),
                        "review_required_count": sum(item["status"] == "review_required" for item in items),
                        "refresh_required_count": sum(item["status"] == "refresh_required" for item in items)},
            "disclaimer": "저장된 연구의 기준 최종 테스트와 사람이 실행한 재검증 결과를 비교하는 읽기 전용 모니터입니다. 성능 저하는 재검토 신호일 뿐 전략 승인·배분·주문을 자동 변경하지 않습니다."}


async def capture_monthly_revalidation_evidence() -> dict:
    """Persist a monthly audit view without fetching prices or rerunning research automatically."""
    now = _now()
    month = now.astimezone(PAPER_SNAPSHOT_TIMEZONE).strftime("%Y-%m")
    users = await get_db().users.find({}, {"_id": 1, "email": 1, "username": 1}).to_list(2_000)
    stored = 0
    for user in users:
        try:
            monitor = await build_revalidation_monitor(user)
            await get_db().research_revalidation_monthly_snapshots.update_one(
                {"user_id": user["_id"], "month": month},
                {"$set": {"user_id": user["_id"], "month": month, "captured_at": now, "monitor": monitor}}, upsert=True,
            )
            stored += 1
        except Exception:
            # 한 사용자 연구 기록의 손상이 다른 사용자 월간 감사 저장을 막지 않는다.
            continue
    return {"month": month, "stored_count": stored, "captured_at": now.isoformat()}


async def build_integrated_risk_board(user: dict, dashboard: dict | None = None) -> dict:
    """Combine personal book and paper ledgers as labelled proxies, never as an executable account."""
    dashboard = dashboard or await _paper_dashboard(user)
    portfolio = await get_db().portfolios.find_one({"user_id": user["_id"]}, {"holdings": 1, "cash_krw": 1}) or {}
    positions: dict[str, dict] = {}
    excluded: list[dict] = []
    for holding in portfolio.get("holdings", []):
        ticker = str(holding.get("ticker") or "").upper()
        if not ticker:
            continue
        if holding.get("currency", "KRW") != "KRW":
            excluded.append({"ticker": ticker, "reason": "외화 보유분은 환율 시계열 없이 원화 통합 프록시에서 제외했습니다."})
            continue
        value = float(holding.get("quantity", 0)) * float(holding.get("average_price", 0))
        row = positions.setdefault(ticker, {"ticker": ticker, "sector": holding.get("sector") or "미분류", "portfolio_book_krw": 0.0, "paper_mark_krw": 0.0, "strategy_ids": set()})
        row["portfolio_book_krw"] += value
    for paper in dashboard.get("positions", []):
        if paper.get("status") != "ok":
            continue
        ticker = paper["ticker"]
        row = positions.setdefault(ticker, {"ticker": ticker, "sector": paper.get("sector") or "미분류", "portfolio_book_krw": 0.0, "paper_mark_krw": 0.0, "strategy_ids": set()})
        row["paper_mark_krw"] += float(paper.get("market_value_krw", 0))
        row["strategy_ids"].add(str(paper.get("strategy_id") or ""))
    items = []
    for row in positions.values():
        proxy = row["portfolio_book_krw"] + row["paper_mark_krw"]
        items.append({"ticker": row["ticker"], "sector": row["sector"], "portfolio_book_krw": round(row["portfolio_book_krw"], 2),
                      "paper_mark_krw": round(row["paper_mark_krw"], 2), "combined_proxy_krw": round(proxy, 2),
                      "cross_ledger_duplicate": row["portfolio_book_krw"] > 0 and row["paper_mark_krw"] > 0,
                      "strategy_count": len({value for value in row["strategy_ids"] if value})})
    total = sum(item["combined_proxy_krw"] for item in items) + float(portfolio.get("cash_krw", 0)) + float(dashboard.get("cash_krw", 0))
    for item in items:
        item["combined_proxy_weight_pct"] = round(item["combined_proxy_krw"] / total * 100, 4) if total > 0 else None
    items.sort(key=lambda item: item["combined_proxy_krw"], reverse=True)
    sector_exposure: dict[str, float] = {}
    for item in items:
        sector_exposure[item["sector"]] = sector_exposure.get(item["sector"], 0.0) + item["combined_proxy_krw"]
    policy = await _get_risk_policy(user["_id"])
    findings = []
    for item in items:
        if item["cross_ledger_duplicate"]:
            findings.append({"code": "cross_ledger_duplicate", "severity": "warning", "ticker": item["ticker"], "message": "실제 보유 장부와 모의 원장에 같은 종목이 있어 합산 프록시 해석에 주의가 필요합니다."})
        if item["combined_proxy_weight_pct"] is not None and item["combined_proxy_weight_pct"] > policy["max_position_pct"]:
            findings.append({"code": "integrated_concentration", "severity": "warning", "ticker": item["ticker"], "message": "통합 프록시 종목 비중이 개인 위험정책 한도를 넘었습니다."})
    shock_pct = 10.0
    return {"report_version": "integrated-risk-board-v1", "generated_at": _now().isoformat(), "available": total > 0,
            "total_parallel_ledger_proxy_krw": round(total, 2), "items": items, "sector_exposure_pct": {key: round(value / total * 100, 4) for key, value in sector_exposure.items()} if total else {},
            "stress_scenarios": [{"name": "주식 프록시 -10%", "shock_pct": -shock_pct, "estimated_proxy_loss_krw": round(sum(item["combined_proxy_krw"] for item in items) * shock_pct / 100, 2)}],
            "findings": findings, "excluded": excluded, "paper_mark_available": dashboard.get("available"),
            "disclaimer": "실제 보유 장부와 모의 체결 원장을 나란히 합산한 참고용 노출 프록시입니다. 두 장부는 서로 다른 자산일 수 있어 순자산·VaR·실제 주문 한도로 해석하면 안 되며, 어떠한 주문도 변경하지 않습니다."}


async def build_quant_operations_board(user: dict) -> dict:
    """In-app operational queue with runbook references only; no outbound messaging is sent."""
    revalidation = await build_revalidation_monitor(user)
    integrated_risk = await build_integrated_risk_board(user)
    active_alerts = await get_db().paper_alerts.find({"user_id": user["_id"], "condition_active": True}, {"_id": 0, "title": 1, "severity": 1, "message": 1, "updated_at": 1}).sort("updated_at", -1).to_list(50)
    for alert in active_alerts:
        if alert.get("updated_at"):
            alert["updated_at"] = _utc_datetime(alert["updated_at"]).isoformat()
    latest_contract = await get_db().broker_sandbox_contract_checks.find_one({"user_id": user["_id"]}, {"_id": 0, "user_id": 0}, sort=[("checked_at", -1)])
    if latest_contract and latest_contract.get("checked_at"):
        latest_contract["checked_at"] = _utc_datetime(latest_contract["checked_at"]).isoformat()
    latest_refresh = await get_db().market_data_refresh_jobs.find_one({}, {"_id": 0, "status": 1, "finished_at": 1, "success_count": 1, "failure_count": 1}, sort=[("started_at", -1)])
    latest_lineage = await get_db().market_data_lineage_snapshots.find_one({}, {"_id": 0, "snapshot_date": 1, "captured_at": 1, "report.report_fingerprint": 1}, sort=[("captured_at", -1)])
    latest_monthly = await get_db().monthly_operations_reports.find_one({"user_id": user["_id"]}, {"_id": 0, "month": 1, "captured_at": 1}, sort=[("month", -1)])
    for row in (latest_refresh, latest_lineage, latest_monthly):
        if row:
            for key in ("finished_at", "captured_at"):
                if row.get(key):
                    row[key] = _utc_datetime(row[key]).isoformat()
    return {"report_version": "quant-operations-runbook-v1", "generated_at": _now().isoformat(), "runbook": "docs/QUANT_OPERATIONS_RUNBOOK.md",
            "active_alerts": active_alerts, "revalidation": revalidation["summary"], "integrated_risk": {"finding_count": len(integrated_risk["findings"]), "findings": integrated_risk["findings"]},
            "latest_sandbox_contract": latest_contract, "system_observability": {"latest_market_refresh": latest_refresh,
            "latest_data_lineage": latest_lineage, "latest_monthly_operations_report": latest_monthly}, "outbound_notifications": False,
            "disclaimer": "운영자는 화면의 경고와 런북을 보고 직접 조치합니다. 이 보드는 외부 메시지 발송, 데이터 수정, 전략 승인·중단, 주문·실거래를 자동 실행하지 않습니다."}


async def _quant_governance_findings(user: dict, dashboard: dict) -> list[dict]:
    findings = []
    for item in (await build_revalidation_monitor(user))["items"]:
        if item["status"] in {"refresh_required", "review_required"}:
            findings.append({"fingerprint": f"research:revalidation:{item['research_id']}", "type": "research_revalidation", "severity": "warning",
                             "title": "워크포워드 재검증 필요", "message": f"{item['ticker']} 연구는 {item['status']} 상태입니다.", "context": item})
    for item in (await build_integrated_risk_board(user, dashboard))["findings"]:
        findings.append({"fingerprint": f"integrated-risk:{item['code']}:{item.get('ticker', '')}", "type": "integrated_risk", "severity": item["severity"],
                         "title": "통합 노출 점검", "message": item["message"], "context": item})
    latest = await get_db().broker_sandbox_contract_checks.find_one({"user_id": user["_id"]}, sort=[("checked_at", -1)])
    if latest and not latest.get("passed"):
        findings.append({"fingerprint": "sandbox:contract_failed", "type": "sandbox_contract", "severity": "critical", "title": "브로커 샌드박스 계약 검증 실패",
                         "message": "로컬 샌드박스 계약 검증이 실패했습니다. 런북에 따라 원인을 확인하세요.", "context": {"check_id": latest.get("check_id")}})
    return findings


async def build_recovery_readiness() -> dict:
    """Check backup/drill prerequisites without creating archives or restoring data."""
    root = Path(__file__).resolve().parents[3]
    backup_dir = root / "backups"
    archives = sorted(backup_dir.glob("*.archive.gz"), key=lambda item: item.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    latest = archives[0] if archives else None
    checksum = Path(f"{latest}.sha256") if latest else None
    checks = [
        {"name": "백업 스크립트", "passed": (root / "scripts/mongo_backup.sh").is_file()},
        {"name": "복구 훈련 스크립트", "passed": (root / "scripts/mongo_restore_drill.sh").is_file()},
        {"name": "복구 런북", "passed": (root / "docs/BACKUP_RESTORE_RUNBOOK.md").is_file()},
        {"name": "최근 백업 아카이브", "passed": latest is not None},
        {"name": "최근 백업 SHA-256", "passed": checksum is not None and checksum.is_file()},
    ]
    return {
        "generated_at": _now().isoformat(), "checks": checks,
        "latest_backup": {"filename": latest.name, "bytes": latest.stat().st_size} if latest else None,
        "restore_drill_mode": "temporary_database_only", "automatic_restore": False,
        "runbook": "docs/BACKUP_RESTORE_RUNBOOK.md",
        "disclaimer": "이 화면은 백업·복구 훈련 준비 상태만 읽습니다. 백업 생성, SHA 검증, 복구 훈련 및 운영 DB 복원은 자동 실행하지 않습니다.",
    }


async def build_live_integration_readiness(user: dict) -> dict:
    """Expose the remaining safety gates while keeping live order submission unavailable."""
    environment = execution_environment_status()
    readiness = await build_live_review_readiness(user)
    audit = await verify_execution_audit(user["_id"])
    requests = await get_db().execution_readiness_requests.find(
        {"user_id": user["_id"]}, {"_id": 0, "request_id": 1, "purpose": 1, "status": 1, "created_at": 1, "approved_at": 1},
    ).sort("created_at", -1).to_list(20)
    for request in requests:
        for key in ("created_at", "approved_at"):
            if request.get(key):
                request[key] = request[key].isoformat()
    sandbox_contract = await get_db().broker_sandbox_contract_checks.find_one(
        {"user_id": user["_id"]}, {"passed": 1, "checked_at": 1}, sort=[("checked_at", -1)],
    )
    gates = [
        {"name": "실거래 어댑터", "passed": bool(environment["live_adapter"].get("live_capable")), "detail": environment["live_adapter"].get("message")},
        {"name": "실거래 킬스위치 해제", "passed": not environment["live_trading_kill_switch"], "detail": "기본값은 차단이며, 이 화면은 값을 변경하지 않습니다."},
        {"name": "감사 원장 무결성", "passed": audit["valid"], "detail": f"검증 이벤트 {audit['event_count']}건"},
        {"name": "장기 모의운용 검토 후보", "passed": any(item["candidate_for_live_review"] for item in readiness["items"]),
         "detail": f"후보 {sum(item['candidate_for_live_review'] for item in readiness['items'])}개"},
        {"name": "로컬 샌드박스 계약 검증", "passed": bool((sandbox_contract or {}).get("passed")),
         "detail": "외부 브로커에 연결하지 않는 형식·거절 처리 검증입니다."},
        {"name": "실브로커 보안 설계", "passed": (Path(__file__).resolve().parents[3] / "docs/BROKER_ADAPTER_SECURITY_DESIGN.md").is_file(),
         "detail": "멱등성·부분체결·시크릿·외부 감사 보관 요구사항을 문서화했습니다."},
        {"name": "실거래 주문 전송", "passed": False, "detail": "현재 배포본에는 실브로커 주문 전송 구현이 없습니다."},
    ]
    return {
        "generated_at": _now().isoformat(), "gates": gates, "environment": environment,
        "live_review": {"policy": readiness["policy"], "items": readiness["items"]}, "readiness_requests": requests,
        "live_order_submission_available": False,
        "disclaimer": "실거래 연동 가능성을 검토하기 위한 상태표입니다. 어떤 설정 조합도 이 API를 통해 실거래 권한·브로커 연결·주문 전송을 만들지 않습니다.",
    }


async def _live_review_findings(user: dict, dashboard: dict | None = None) -> list[dict]:
    findings = []
    for item in (await build_live_review_readiness(user, dashboard))["items"]:
        if item["status"] == "review_required":
            findings.append({"fingerprint": f"promotion:live_review:{item['strategy_id']}", "type": "promotion_live_review", "severity": "warning",
                             "title": "장기 모의운용 재검토", "message": f"{item['strategy_name']}은 실거래 검토 기준을 충족하지 못했습니다.", "context": item})
    return findings


async def paper_history(user: dict, period: str) -> dict:
    if period not in PAPER_HISTORY_DAYS:
        raise HTTPException(status_code=422, detail="period는 1m, 3m, 6m, 1y, all 중 하나여야 합니다.")
    query: dict = {"user_id": user["_id"]}
    days = PAPER_HISTORY_DAYS[period]
    if days is not None:
        query["snapshot_date"] = {"$gte": (_now().astimezone(PAPER_SNAPSHOT_TIMEZONE).date() - timedelta(days=days)).isoformat()}
    rows = await get_db().paper_daily_snapshots.find(query, {"_id": 0, "user_id": 0}).sort("snapshot_date", 1).to_list(2_000)
    for row in rows:
        if row.get("captured_at"):
            row["captured_at"] = row["captured_at"].isoformat()
    valid = [row for row in rows if row.get("available") and row.get("equity_krw") is not None]
    equities = [float(row["equity_krw"]) for row in valid]
    returns = [(equities[index] / equities[index - 1] - 1) for index in range(1, len(equities)) if equities[index - 1] > 0]
    mdd = None
    if equities:
        peak = equities[0]
        drawdowns = []
        for equity in equities:
            peak = max(peak, equity)
            drawdowns.append(equity / peak - 1 if peak else 0)
        mdd = round(abs(min(drawdowns)) * 100, 2)
    annual_volatility = sharpe = None
    if len(returns) >= 2:
        import math
        mean = sum(returns) / len(returns)
        std = math.sqrt(sum((value - mean) ** 2 for value in returns) / (len(returns) - 1))
        annual_volatility = round(std * (252 ** 0.5) * 100, 2)
        sharpe = round((mean - 0.03 / 252) / std * (252 ** 0.5), 2) if std > 0 else None
    return {
        "period": period, "snapshots": rows, "valid_snapshot_count": len(valid),
        "metrics": {
            "period_return_pct": round((equities[-1] / equities[0] - 1) * 100, 2) if len(equities) >= 2 and equities[0] else None,
            "mdd_pct": mdd, "annual_volatility_pct": annual_volatility, "sharpe": sharpe,
            "start_equity_krw": round(equities[0], 2) if equities else None, "end_equity_krw": round(equities[-1], 2) if equities else None,
        },
        "method": "한국 영업일별 저장된 모의 계좌 지연시세 평가값으로 계산합니다. 유효 스냅샷이 2개 이상일 때만 기간 수익률을 계산합니다.",
    }


def _paper_alert_composition(dashboard: dict) -> dict:
    """전략별 포지션을 종목·업종·통화 기준으로 합쳐 정책 평가용 구성으로 만든다."""
    cash = float(dashboard.get("cash_krw", 0))
    grouped: dict[str, dict] = {}
    for position in dashboard.get("positions", []):
        if position.get("status") != "ok":
            continue
        ticker = position["ticker"]
        item = grouped.setdefault(ticker, {"ticker": ticker, "currency": position["currency"],
                                            "sector": position.get("sector") or "미분류", "value_krw": 0.0})
        item["value_krw"] += float(position.get("market_value_krw", 0))
    total = cash + sum(item["value_krw"] for item in grouped.values())
    if total <= 0:
        return {"max_position": None, "currency_exposure": {}, "sector_exposure": {}}
    currencies: dict[str, float] = {"KRW": cash} if cash else {}
    sectors: dict[str, float] = {}
    weighted = []
    for item in grouped.values():
        currencies[item["currency"]] = currencies.get(item["currency"], 0) + item["value_krw"]
        sectors[item["sector"]] = sectors.get(item["sector"], 0) + item["value_krw"]
        weighted.append({**item, "weight_pct": round(item["value_krw"] / total * 100, 8)})
    maximum = max(weighted, key=lambda item: item["value_krw"]) if weighted else None
    return {"max_position": maximum,
            "currency_exposure": {key: round(value / total * 100, 8) for key, value in currencies.items()},
            "sector_exposure": {key: round(value / total * 100, 8) for key, value in sectors.items()}}


async def _paper_daily_loss_pct(user: dict, dashboard: dict) -> float | None:
    """오늘 평가값과 직전 유효 일별 스냅샷을 비교한다. 손실일 때만 양수다."""
    equity = dashboard.get("equity_krw")
    if equity is None:
        return None
    rows = await get_db().paper_daily_snapshots.find(
        {"user_id": user["_id"], "available": True, "equity_krw": {"$ne": None}}, {"snapshot_date": 1, "equity_krw": 1},
    ).sort("snapshot_date", -1).to_list(2)
    if not rows:
        return None
    today = _now().astimezone(PAPER_SNAPSHOT_TIMEZONE).date().isoformat()
    baseline = rows[1] if rows[0].get("snapshot_date") == today and len(rows) > 1 else rows[0]
    previous = float(baseline.get("equity_krw", 0))
    return round(max(0.0, (previous - float(equity)) / previous * 100), 4) if previous > 0 else None


async def refresh_paper_alerts(user: dict) -> dict:
    """현재 위험·대기 주문 신호를 idempotent 알림으로 동기화한다. 주문은 실행하지 않는다."""
    now = _now()
    findings: list[dict] = []
    dashboard = await _paper_dashboard(user)
    if dashboard["available"]:
        policy = await _get_risk_policy(user["_id"])
        daily_loss = await _paper_daily_loss_pct(user, dashboard)
        alerts = evaluate_policy(_paper_alert_composition(dashboard), {"daily_loss_pct": daily_loss}, policy)
        for alert in alerts:
            findings.append({"fingerprint": f"risk:{alert['code']}", "type": "risk_limit", "severity": alert["severity"],
                             "title": "위험 한도 경고", "message": alert["message"],
                             "context": {"code": alert["code"], "actual_pct": alert["actual_pct"], "limit_pct": alert["limit_pct"], "daily_loss_pct": daily_loss}})
        history = await paper_history(user, "1m")
        metrics = history["metrics"]
        if history["valid_snapshot_count"] >= 2 and metrics["mdd_pct"] is not None and metrics["mdd_pct"] >= 10:
            findings.append({"fingerprint": "performance:drawdown_10pct", "type": "strategy_performance", "severity": "warning",
                             "title": "모의 전략 낙폭 점검", "message": f"최근 1개월 모의 계좌 최대낙폭이 {metrics['mdd_pct']:.2f}%입니다.",
                             "context": {"period": "1m", "mdd_pct": metrics["mdd_pct"], "threshold_pct": 10}})
        if history["valid_snapshot_count"] >= 2 and metrics["period_return_pct"] is not None and metrics["period_return_pct"] <= -10:
            findings.append({"fingerprint": "performance:return_minus_10pct", "type": "strategy_performance", "severity": "warning",
                             "title": "모의 전략 성과 점검", "message": f"최근 1개월 모의 계좌 수익률이 {metrics['period_return_pct']:.2f}%입니다.",
                             "context": {"period": "1m", "return_pct": metrics["period_return_pct"], "threshold_pct": -10}})
    else:
        findings.append({"fingerprint": "system:paper_mark_unavailable", "type": "data_quality", "severity": "warning",
                         "title": "모의 계좌 시세 확인 필요", "message": "일부 모의 포지션의 현재 시세를 확인할 수 없어 위험 한도를 평가하지 않았습니다.",
                         "context": {"unpriced": dashboard.get("unpriced", [])}})

    findings.extend(await _promotion_monitor_findings(user, dashboard))
    findings.extend(await _live_review_findings(user, dashboard))
    findings.extend(await _quant_governance_findings(user, dashboard))

    pending = await get_db().order_proposals.find({"user_id": user["_id"], "status": "pending"}).to_list(200)
    for proposal in pending:
        findings.append({"fingerprint": f"signal:{proposal['_id']}", "type": "strategy_signal", "severity": "info",
                         "title": "전략 주문 제안 대기", "message": f"{proposal['ticker']} {('매수' if proposal['direction'] == 'buy' else '매도')} 신호의 모의 주문 제안이 대기 중입니다.",
                         "context": {"proposal_id": str(proposal["_id"]), "strategy_id": str(proposal["strategy_id"]), "ticker": proposal["ticker"], "direction": proposal["direction"]}})

    active_fingerprints = {finding["fingerprint"] for finding in findings}
    existing = await get_db().paper_alerts.find({"user_id": user["_id"], "condition_active": True}).to_list(500)
    for alert in existing:
        if alert["fingerprint"] not in active_fingerprints:
            await get_db().paper_alerts.update_one({"_id": alert["_id"]}, {"$set": {"condition_active": False, "status": "resolved", "updated_at": now},
                "$push": {"events": {"at": now, "action": "system_resolved", "note": "경고 조건이 해소되었거나 주문 제안이 처리되었습니다."}}})

    created = updated = 0
    for finding in findings:
        latest = await get_db().paper_alerts.find_one({"user_id": user["_id"], "fingerprint": finding["fingerprint"]}, sort=[("created_at", -1)])
        if latest and latest.get("condition_active"):
            await get_db().paper_alerts.update_one({"_id": latest["_id"]}, {"$set": {**finding, "updated_at": now}})
            updated += 1
        else:
            await get_db().paper_alerts.insert_one({"user_id": user["_id"], **finding, "status": "open", "condition_active": True,
                "created_at": now, "updated_at": now, "events": [{"at": now, "action": "created", "note": "자동 감지"}]})
            created += 1
    return {"created": created, "updated": updated, "active_conditions": len(findings), "dashboard_available": dashboard["available"]}


async def build_paper_report(user: dict, period: str) -> dict:
    """모의 계좌 원장·일별 성과·위험·알림 이력을 묶는 읽기 전용 감사 리포트다."""
    history = await paper_history(user, period)
    dashboard = await _paper_dashboard(user)
    policy = await _get_risk_policy(user["_id"])
    days = PAPER_HISTORY_DAYS[period]
    fill_query: dict = {"user_id": user["_id"]}
    if days is not None:
        cutoff = _now() - timedelta(days=days)
        fill_query["filled_at"] = {"$gte": cutoff}
    fills = await get_db().paper_fills.find(fill_query).sort("filled_at", 1).to_list(2_000)
    alerts = await get_db().paper_alerts.find({"user_id": user["_id"]}).sort("updated_at", -1).to_list(500)
    strategies = await get_db().strategies.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(200)
    severity_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for alert in alerts:
        severity_counts[alert["severity"]] = severity_counts.get(alert["severity"], 0) + 1
        status_counts[alert["status"]] = status_counts.get(alert["status"], 0) + 1
    return {
        "report_version": "paper-research-report-v1", "generated_at": _now().isoformat(), "period": period,
        "disclaimer": "이 보고서는 실제 계좌가 아닌 모의 체결 원장과 지연 시세를 바탕으로 한 리서치·감사 기록입니다. 투자 권유 또는 성과 보장이 아닙니다.",
        "performance": {"history": history["metrics"], "valid_snapshot_count": history["valid_snapshot_count"],
                        "dashboard": {key: dashboard.get(key) for key in ("available", "equity_krw", "cash_krw", "realized_pnl_krw", "unrealized_pnl_krw", "net_pnl_krw", "return_pct")}},
        "risk": {"policy": policy, "active_alert_count": sum(1 for alert in alerts if alert.get("condition_active")),
                 "current_mark_available": dashboard["available"], "unpriced": dashboard.get("unpriced", [])},
        "audit": {"fill_count": len(fills), "alert_count": len(alerts), "alert_status_counts": status_counts,
                  "alert_severity_counts": severity_counts},
        "strategies": [_serialize_strategy(row) for row in strategies],
        "strategy_performance": dashboard.get("strategy_summary", []),
        "fills": [_serialize_fill(row) | {"notional_krw": row.get("notional_krw", row.get("notional"))} for row in fills],
        "alerts": [_serialize_paper_alert(row) for row in alerts],
        "snapshots": history["snapshots"],
    }


async def verify_strategy_promotion_integrity(user: dict) -> dict:
    """승인 상태가 최신 승인 이벤트와 전략 지문에 맞는지 읽기 전용으로 검사한다."""
    states = await get_db().strategy_promotion_states.find({"user_id": user["_id"], "status": "approved"}).to_list(200)
    issues: list[dict] = []
    for state in states:
        strategy = await get_db().strategies.find_one({"_id": state["strategy_id"], "user_id": user["_id"]})
        latest = await get_db().strategy_promotion_events.find_one(
            {"user_id": user["_id"], "lineage_root_id": state["lineage_root_id"]}, sort=[("created_at", -1)],
        )
        if not strategy:
            issues.append({"strategy_id": str(state["strategy_id"]), "code": "strategy_missing", "message": "승인 상태의 전략을 찾을 수 없습니다."})
        elif state.get("strategy_fingerprint") != _strategy_version_fingerprint(strategy):
            issues.append({"strategy_id": str(state["strategy_id"]), "code": "fingerprint_mismatch", "message": "승인 당시 전략 지문과 현재 전략이 다릅니다."})
        if not latest or latest.get("action") != "approved" or latest.get("strategy_id") != state["strategy_id"]:
            issues.append({"strategy_id": str(state["strategy_id"]), "code": "event_state_mismatch", "message": "최신 승인 이력과 현재 승인 상태가 일치하지 않습니다."})
    return {"valid": not issues, "approved_strategy_count": len(states), "issues": issues}


async def build_operations_report(user: dict, days: int = 7) -> dict:
    """개인 퀀트 시스템의 주간 모의운영·통제 상태를 요약한다. 주문을 실행하지 않는다."""
    period_days = max(1, min(31, int(days)))
    generated_at = _now()
    cutoff = generated_at - timedelta(days=period_days)
    proposals = await get_db().order_proposals.find({"user_id": user["_id"], "created_at": {"$gte": cutoff}}).to_list(2_000)
    fills = await get_db().paper_fills.find({"user_id": user["_id"], "filled_at": {"$gte": cutoff}}).to_list(2_000)
    alerts = await get_db().paper_alerts.find({"user_id": user["_id"], "updated_at": {"$gte": cutoff}}).to_list(2_000)
    audit_events = await get_db().execution_audit_events.find({"user_id": user["_id"], "occurred_at": {"$gte": cutoff}}).to_list(2_000)
    proposal_statuses: dict[str, int] = {}
    for proposal in proposals:
        proposal_statuses[proposal.get("status", "unknown")] = proposal_statuses.get(proposal.get("status", "unknown"), 0) + 1
    audit_actions: dict[str, int] = {}
    latencies: list[float] = []
    sandbox_accepted = sandbox_rejected = 0
    for event in audit_events:
        action = event.get("action", "unknown")
        audit_actions[action] = audit_actions.get(action, 0) + 1
        payload = event.get("payload", {})
        latency = payload.get("latency_ms", payload.get("sandbox_latency_ms"))
        if isinstance(latency, (int, float)):
            latencies.append(float(latency))
        sandbox_accepted += action == "paper_sandbox_order_accepted"
        sandbox_rejected += action == "paper_sandbox_order_rejected"
    audit = await verify_execution_audit(user["_id"])
    promotions = await verify_strategy_promotion_integrity(user)
    live_review = await build_live_review_readiness(user)
    live_review_status_counts: dict[str, int] = {}
    for item in live_review["items"]:
        live_review_status_counts[item["status"]] = live_review_status_counts.get(item["status"], 0) + 1
    environment = execution_environment_status()
    try:
        market_data_report = await market_data_operations_report(period_days)
    except Exception:
        # 시세 품질 리포트 일시 장애가 기존 모의운영 통제 리포트를 막지 않게 한다.
        market_data_report = {"available": False, "summary": {"active_alert_count": None, "stale_core_tickers": []},
                              "latest_job": None, "disclaimer": "시세 데이터 운영 리포트를 불러오지 못했습니다."}
    active_alerts = sum(1 for alert in alerts if alert.get("condition_active"))
    decisions = sum(proposal_statuses.get(status, 0) for status in ("approved", "rejected", "blocked"))
    safety_checks = [
        {"name": "실거래 주문 차단", "passed": not environment["live_order_submission_available"], "detail": "현재 어댑터를 포함해 실거래 전송 가능 상태가 아닙니다."},
        {"name": "비밀값 비노출", "passed": environment["secrets_exposed"] is False, "detail": "상태 API는 자격증명 값 대신 설정 여부만 반환합니다."},
        {"name": "감사 원장 해시 체인", "passed": audit["valid"], "detail": f"검증 이벤트 {audit['event_count']}건"},
        {"name": "전략 승인 상태 정합성", "passed": promotions["valid"], "detail": f"승인 전략 {promotions['approved_strategy_count']}개"},
        {"name": "승인 전략 주문 제한", "passed": environment["paper_order_requires_promotion_approval"], "detail": "새 모의 주문은 승인 전략·재검토 경고 상태를 점검합니다."},
    ]
    return {
        "report_version": "quant-operations-report-v1", "generated_at": generated_at.isoformat(), "period_days": period_days,
        "period_start": cutoff.isoformat(), "execution_mode": environment["execution_mode"],
        "safety_checks": safety_checks,
        "order_quality": {
            "proposal_status_counts": proposal_statuses, "fill_count": len(fills),
            "decision_count": decisions, "approval_rate_pct": round(proposal_statuses.get("approved", 0) / decisions * 100, 2) if decisions else None,
            "sandbox_accepted": sandbox_accepted, "sandbox_rejected": sandbox_rejected,
            "average_processing_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        },
        "risk_and_review": {"active_alert_count": active_alerts, "recent_alert_count": len(alerts), "promotion_integrity": promotions,
                            "live_review_status_counts": live_review_status_counts,
                            "live_review_candidate_count": live_review_status_counts.get("live_review_candidate", 0)},
        "market_data": market_data_report,
        "audit": {"verification": audit, "recent_action_counts": audit_actions},
        "backup_restore": {
            "runbook": "docs/BACKUP_RESTORE_RUNBOOK.md", "backup_script": "scripts/mongo_backup.sh",
            "restore_drill_script": "scripts/mongo_restore_drill.sh", "automatic_restore": False,
        },
        "disclaimer": "이 리포트는 모의운영·감사 점검용이며 실거래 권한을 만들거나 주문을 실행하지 않습니다.",
    }


async def build_monthly_operations_report(user: dict) -> dict:
    """Monthly evidence package; it is a report, never an execution schedule."""
    now = _now()
    operations = await build_operations_report(user, 31)
    recovery = await build_recovery_readiness()
    live = await build_live_integration_readiness(user)
    try:
        from .kqs import build_candidate_outcome_report
        candidate_outcomes = await build_candidate_outcome_report(365)
    except Exception:
        candidate_outcomes = {"available": False, "summary": {}, "quality_gate": {},
                              "disclaimer": "후보 사후성과 리포트를 불러오지 못했습니다."}
    return {
        "report_version": "quant-monthly-operations-report-v1", "month": now.astimezone(PAPER_SNAPSHOT_TIMEZONE).strftime("%Y-%m"),
        "generated_at": now.isoformat(), "operations": operations, "recovery": recovery,
        "live_integration": {"live_order_submission_available": live["live_order_submission_available"], "gates": live["gates"]},
        "candidate_outcomes": {"summary": candidate_outcomes.get("summary", {}), "quality_gate": candidate_outcomes.get("quality_gate", {})},
        "disclaimer": "월간 운영 리포트는 모의운용·후보 관측·복구 준비 상태를 묶은 감사 자료입니다. 백업 생성·복구·주문·실거래 권한을 자동 실행하지 않습니다.",
    }


async def capture_monthly_operations_reports() -> dict:
    """Save at most one monthly audit snapshot per user; no external action is taken."""
    now = _now()
    month = now.astimezone(PAPER_SNAPSHOT_TIMEZONE).strftime("%Y-%m")
    users = await get_db().users.find({}, {"_id": 1, "email": 1, "username": 1}).to_list(2_000)
    stored = 0
    for user in users:
        try:
            report = await build_monthly_operations_report(user)
            await get_db().monthly_operations_reports.update_one(
                {"user_id": user["_id"], "month": month}, {"$set": {"user_id": user["_id"], "month": month, "captured_at": now, "report": report}}, upsert=True,
            )
            stored += 1
        except Exception:
            continue
    return {"month": month, "stored_count": stored, "captured_at": now.isoformat()}


def _paper_report_csv(report: dict) -> str:
    """스프레드시트로 열 수 있는 단일 CSV 감사 내보내기 형식이다."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["section", "recorded_at", "field", "value"])
    writer.writerow(["report", report["generated_at"], "period", report["period"]])
    for section in ("performance", "risk", "audit"):
        for key, value in report[section].items():
            writer.writerow([section, report["generated_at"], key, json.dumps(value, ensure_ascii=False, default=str)])
    for snapshot in report["snapshots"]:
        for key, value in snapshot.items():
            writer.writerow(["snapshot", snapshot.get("snapshot_date", ""), key, json.dumps(value, ensure_ascii=False, default=str)])
    for fill in report["fills"]:
        for key, value in fill.items():
            writer.writerow(["fill", fill.get("filled_at", ""), key, json.dumps(value, ensure_ascii=False, default=str)])
    for alert in report["alerts"]:
        for key, value in alert.items():
            writer.writerow(["alert", alert.get("updated_at", ""), key, json.dumps(value, ensure_ascii=False, default=str)])
    return "\ufeff" + output.getvalue()


async def _get_owned_strategy(strategy_id: str, user: dict) -> dict:
    try:
        oid = ObjectId(strategy_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="유효하지 않은 전략 ID입니다.") from exc
    doc = await get_db().strategies.find_one({"_id": oid, "user_id": user["_id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="전략을 찾을 수 없습니다.")
    return doc


async def _strategy_lineage_root(strategy: dict, user_id) -> dict:
    """부모 링크를 따라 전략 계보의 최초 버전을 찾는다. 순환·타인 참조는 허용하지 않는다."""
    current = strategy
    visited = {str(current["_id"])}
    while current.get("parent_id"):
        parent_id = current["parent_id"]
        if str(parent_id) in visited:
            raise HTTPException(status_code=409, detail="전략 버전 계보에 순환 참조가 있습니다.")
        parent = await get_db().strategies.find_one({"_id": parent_id, "user_id": user_id})
        if not parent:
            raise HTTPException(status_code=409, detail="전략 버전 계보의 이전 버전을 찾을 수 없습니다.")
        visited.add(str(parent_id))
        current = parent
    return current


def _strategy_version_fingerprint(strategy: dict) -> str:
    """승인 시점의 전략 정의·백테스트 스냅샷을 식별하는 읽기 전용 지문이다."""
    fixed = {
        "strategy_id": str(strategy["_id"]), "type": strategy.get("type"), "name": strategy.get("name"),
        "ticker": strategy.get("ticker"), "symbol": strategy.get("symbol"), "version": strategy.get("version"),
        "parent_id": str(strategy["parent_id"]) if strategy.get("parent_id") else None,
        "params": strategy.get("params", {}), "snapshot": strategy.get("snapshot", {}),
    }
    canonical = json.dumps(fixed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_strategy_promotion_event(event: dict) -> dict:
    return {
        "event_id": event["event_id"], "action": event["action"],
        "strategy_id": str(event["strategy_id"]), "strategy_version": event["strategy_version"],
        "note": event.get("note", ""), "operator_email": event.get("operator_email", ""),
        "strategy_fingerprint": event["strategy_fingerprint"],
        "created_at": event["created_at"].isoformat(),
    }


async def _strategy_promotion_status(strategy: dict, user: dict) -> dict:
    """현재 선택된 승인 버전과, 이 버전이 사람이 승인 가능한 후보인지 반환한다.

    이 상태는 배분안·주문·모의 체결을 변경하지 않는다. 승인 버전 선택은 별도 상태
    문서로 고정하고, 각 승인/철회 행위는 append-only 이벤트로 남긴다.
    """
    root = await _strategy_lineage_root(strategy, user["_id"])
    state = await get_db().strategy_promotion_states.find_one({"user_id": user["_id"], "lineage_root_id": root["_id"]})
    events = await get_db().strategy_promotion_events.find(
        {"user_id": user["_id"], "lineage_root_id": root["_id"]},
    ).sort("created_at", -1).to_list(100)
    comparison = None
    eligible = False
    if strategy.get("type") == "real_backtest" and strategy.get("parent_id"):
        previous = await get_db().strategies.find_one({"_id": strategy["parent_id"], "user_id": user["_id"]})
        if previous:
            rows = await get_db().strategies.find({"user_id": user["_id"]}).to_list(200)
            comparison = compare_strategy_versions(strategy, previous, rows)
            eligible = bool(comparison.get("available") and comparison.get("promotion_assessment", {}).get("status") == "candidate")
    approved_id = str(state["strategy_id"]) if state and state.get("status") == "approved" else None
    fingerprint = _strategy_version_fingerprint(strategy)
    return {
        "strategy_id": str(strategy["_id"]), "lineage_root_id": str(root["_id"]),
        "eligible_for_approval": eligible, "can_manage": is_execution_operator(user),
        "is_current_approved": approved_id == str(strategy["_id"]),
        "approved_strategy_id": approved_id,
        "approved_strategy_version": state.get("strategy_version") if approved_id else None,
        "approved_at": state["approved_at"].isoformat() if approved_id and state.get("approved_at") else None,
        "approval_fingerprint_matches": state.get("strategy_fingerprint") == fingerprint if approved_id == str(strategy["_id"]) else None,
        "comparison_status": comparison.get("promotion_assessment", {}).get("status") if comparison else None,
        "history": [_serialize_strategy_promotion_event(event) for event in events],
        "creates_order_authority": False, "changes_strategy_allocation": False,
    }


async def _paper_order_preflight(strategy: dict, user: dict) -> dict:
    """모의 주문 전 승인·재검토 경고·권한 상태를 확인한다.

    수량은 승인 시에만 입력되므로 현금·집중도 위험 한도는 체결 직전에 별도로
    강제한다. 이 함수는 실거래 권한을 만들거나 주문을 보내지 않는다.
    """
    submit_allowed, submit_reason = await paper_submission_allowed(user["_id"])
    required = paper_order_promotion_approval_required()
    state = await get_db().strategy_promotion_states.find_one({
        "user_id": user["_id"], "strategy_id": strategy["_id"], "status": "approved",
    })
    fingerprint_matches = bool(state) and state.get("strategy_fingerprint") == _strategy_version_fingerprint(strategy)
    alerts = await get_db().paper_alerts.find({
        "user_id": user["_id"], "condition_active": True,
        "type": {"$in": ["promotion_integrity", "promotion_review", "promotion_data_quality", "promotion_performance"]},
        "context.strategy_id": str(strategy["_id"]),
    }, {"type": 1, "title": 1}).to_list(50)
    reasons: list[dict] = []
    if not submit_allowed:
        reasons.append({"code": "paper_submission_disabled", "message": submit_reason})
    if required and not state:
        reasons.append({"code": "promotion_not_approved", "message": "운영자가 승인 고정한 전략 버전만 모의 주문 제안을 만들 수 있습니다."})
    if required and state and not fingerprint_matches:
        reasons.append({"code": "promotion_fingerprint_mismatch", "message": "승인 당시 전략 지문과 현재 전략이 달라 모의 주문을 차단했습니다."})
    if required and paper_order_promotion_alerts_block() and alerts:
        reasons.append({"code": "promotion_review_alert", "message": "승인 전략에 활성 재검토 경고가 있어 모의 주문을 차단했습니다."})
    return {
        "allowed": not reasons, "promotion_approval_required": required,
        "promotion_approved": bool(state), "approval_fingerprint_matches": fingerprint_matches if state else None,
        "active_promotion_alerts": [{"type": row["type"], "title": row["title"]} for row in alerts],
        "risk_quantity_check": "모의 체결 승인 시 현금·집중도·업종·외화 한도를 강제합니다.",
        "reasons": reasons, "live_trading_authorized": False,
    }


async def _get_owned_proposal(proposal_id: str, user: dict) -> dict:
    try:
        oid = ObjectId(proposal_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="유효하지 않은 주문 제안 ID입니다.") from exc
    doc = await get_db().order_proposals.find_one({"_id": oid, "user_id": user["_id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="주문 제안을 찾을 수 없습니다.")
    return doc


async def _paper_position_quantity(user_id, strategy_id, ticker: str) -> float:
    """체결 원장에서 전략·종목별 현재 모의 보유수량을 계산한다."""
    rows = await get_db().paper_fills.find(
        {"user_id": user_id, "strategy_id": strategy_id, "ticker": ticker},
        {"direction": 1, "quantity": 1},
    ).to_list(10_000)
    quantity = 0.0
    for row in rows:
        signed = float(row.get("quantity", 0))
        quantity += signed if row.get("direction") == "buy" else -signed
    return max(0.0, quantity)


PAPER_FILL_PROPOSAL_INDEX = "proposal_id_unique_when_set"


async def _ensure_paper_fill_proposal_index(db) -> None:
    """주문 제안 1건당 체결 1건만 허용하되, 제안 없이 생기는 체결(레버리지 DCA 등)은 제외한다.

    예전 인덱스(``proposal_id_1``)는 필드가 없거나 null인 문서도 한 값으로 묶어, 제안 없는 체결이
    두 번째부터 중복 키 오류로 저장되지 않았다. 부분 인덱스로 교체한다.
    """
    existing = await db.paper_fills.index_information()
    legacy = existing.get("proposal_id_1")
    if legacy and not legacy.get("partialFilterExpression"):
        await db.paper_fills.drop_index("proposal_id_1")
    await db.paper_fills.create_index(
        "proposal_id", unique=True, name=PAPER_FILL_PROPOSAL_INDEX,
        partialFilterExpression={"proposal_id": {"$type": "objectId"}},
    )


@router.on_event("startup")
async def _ensure_strategy_indexes() -> None:
    try:
        db = get_db()
        await db.strategies.create_index([("user_id", 1), ("created_at", -1)])
        await db.order_proposals.create_index([("user_id", 1), ("created_at", -1)])
        await db.order_proposals.create_index([("strategy_id", 1), ("status", 1)])
        await db.strategy_research_runs.create_index([("user_id", 1), ("created_at", -1)])
        await db.strategy_revalidations.create_index([("user_id", 1), ("research_id", 1), ("captured_at", -1)])
        await db.research_revalidation_monthly_snapshots.create_index([("user_id", 1), ("month", 1)], unique=True)
        await db.broker_sandbox_contract_checks.create_index([("user_id", 1), ("checked_at", -1)])
        await db.paper_fills.create_index([("user_id", 1), ("filled_at", -1)])
        await _ensure_paper_fill_proposal_index(db)
        await db.paper_accounts.create_index("user_id", unique=True)
        await db.paper_daily_snapshots.create_index([("user_id", 1), ("snapshot_date", 1)], unique=True)
        await db.paper_daily_snapshots.create_index([("user_id", 1), ("snapshot_date", -1)])
        await db.paper_alerts.create_index([("user_id", 1), ("status", 1), ("updated_at", -1)])
        await db.paper_alerts.create_index([("user_id", 1), ("fingerprint", 1), ("created_at", -1)])
        await db.execution_permissions.create_index("user_id", unique=True)
        await db.execution_audit_counters.create_index("user_id", unique=True)
        await db.execution_audit_events.create_index([("user_id", 1), ("sequence", 1)], unique=True)
        await db.execution_readiness_requests.create_index("request_id", unique=True)
        await db.strategy_promotion_states.create_index([("user_id", 1), ("lineage_root_id", 1)], unique=True)
        await db.strategy_promotion_events.create_index([("user_id", 1), ("lineage_root_id", 1), ("created_at", -1)])
        await db.strategy_promotion_monitor_snapshots.create_index([("user_id", 1), ("strategy_id", 1), ("snapshot_date", 1)], unique=True)
        await db.strategy_promotion_monitor_snapshots.create_index([("user_id", 1), ("snapshot_date", -1)])
        await db.strategy_decision_records.create_index([("user_id", 1), ("created_at", -1)])
        await db.monthly_operations_reports.create_index([("user_id", 1), ("month", 1)], unique=True)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("MongoDB not ready during strategy startup indexes creation: %s", exc)


@router.post("")
async def save_strategy(payload: StrategySaveRequest, user: dict = Depends(current_user)) -> dict:
    """가상/실제/LEAN 백테스트 결과를 "전략" 단위로 저장한다."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="전략 이름을 입력하세요.")

    version = 1
    parent_id = None
    if payload.previous_version_id:
        try:
            parent_oid = ObjectId(payload.previous_version_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="유효하지 않은 이전 버전 ID입니다.") from exc
        parent = await get_db().strategies.find_one({"_id": parent_oid, "user_id": user["_id"]})
        if not parent:
            raise HTTPException(status_code=404, detail="이전 버전 전략을 찾을 수 없습니다.")
        version = parent.get("version", 1) + 1
        parent_id = parent_oid

    ticker = ""
    symbol = ""
    if payload.type == "real_backtest":
        ticker = payload.ticker.strip().upper()
        if not TICKER_PATTERN.fullmatch(ticker):
            raise HTTPException(status_code=422, detail="티커 형식이 올바르지 않습니다.")
        required = {"fast_ma", "slow_ma", "period", "transaction_cost_bps", "slippage_bps", "benchmark_ticker"}
        if not required.issubset(payload.params.keys()):
            raise HTTPException(status_code=422, detail="실제 가격 백테스트 저장에는 fast_ma/slow_ma/period/transaction_cost_bps/slippage_bps/benchmark_ticker가 필요합니다.")
        if "metrics" not in payload.snapshot:
            raise HTTPException(status_code=422, detail="백테스트 결과(snapshot.metrics)가 필요합니다.")
        if payload.params["fast_ma"] >= payload.params["slow_ma"]:
            raise HTTPException(status_code=422, detail="단기 이동평균은 장기 이동평균보다 작아야 합니다.")
    elif payload.type == "virtual_backtest":
        required = {"fast_ma", "slow_ma", "n_days", "volatility"}
        if not required.issubset(payload.params.keys()):
            raise HTTPException(status_code=422, detail="가상 백테스트 저장에는 fast_ma/slow_ma/n_days/volatility가 필요합니다.")
        if "metrics" not in payload.snapshot:
            raise HTTPException(status_code=422, detail="백테스트 결과(snapshot.metrics)가 필요합니다.")
        if payload.params["fast_ma"] >= payload.params["slow_ma"]:
            raise HTTPException(status_code=422, detail="단기 이동평균은 장기 이동평균보다 작아야 합니다.")
    else:  # lean
        symbol = payload.symbol.strip().lower()
        if symbol not in LEAN_RUNS:
            raise HTTPException(status_code=422, detail="지원하지 않는 LEAN 종목입니다. hyundai 또는 samsung을 사용하세요.")

    now = _now()
    document = {
        "user_id": user["_id"],
        "type": payload.type,
        "name": name,
        "ticker": ticker,
        "symbol": symbol,
        "params": payload.params,
        "rebalance_note": payload.rebalance_note.strip() or "MA 교차 시 신호 변경 (일별 확인)",
        "snapshot": payload.snapshot if payload.type != "lean" else {},
        "version": version,
        "parent_id": parent_id,
        "created_at": now,
    }
    result = await get_db().strategies.insert_one(document)
    document["_id"] = result.inserted_id
    return _serialize_strategy(document)


@router.get("")
async def list_strategies(user: dict = Depends(current_user)) -> dict:
    rows = await get_db().strategies.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(200)
    return {"items": [_serialize_strategy(row) for row in rows]}


@router.post("/backtest")
def run_real_backtest(req: RealBacktestRequest) -> dict:
    """실제 종가 이력으로 MA 크로스오버 전략을 백테스트한다(가상 GBM이 아님). 저장하지 않는 미리보기."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    configure_matplotlib_korean_font(plt)

    ticker = req.ticker.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise HTTPException(status_code=422, detail="티커는 영문·숫자와 . ^ = - 기호만 사용할 수 있습니다.")
    if not BACKTEST_PERIOD_PATTERN.fullmatch(req.period):
        raise HTTPException(status_code=422, detail="period는 6mo, 1y, 2y, 3y, 5y 중 하나여야 합니다.")
    benchmark_ticker = (req.benchmark_ticker or ticker).strip().upper()
    if not TICKER_PATTERN.fullmatch(benchmark_ticker):
        raise HTTPException(status_code=422, detail="벤치마크 티커 형식이 올바르지 않습니다.")
    if req.fast_ma >= req.slow_ma:
        raise HTTPException(status_code=422, detail=f"단기 이동평균(fast_ma={req.fast_ma})은 장기 이동평균(slow_ma={req.slow_ma})보다 작아야 합니다.")

    try:
        ohlc = _download_ohlc_frame(ticker, req.period)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{ticker} 가격 이력을 가져오지 못했습니다: {exc}") from exc
    if len(ohlc) < req.slow_ma + 20:
        raise HTTPException(status_code=422, detail=f"{ticker}의 가격 이력이 부족합니다. 장기 이동평균({req.slow_ma}일)을 계산하려면 최소 {req.slow_ma + 20}거래일이 필요합니다.")

    config = BacktestConfig(req.fast_ma, req.slow_ma, req.transaction_cost_bps, req.slippage_bps, req.initial_capital)
    df, run_metadata = run_ma_crossover(ohlc, ticker, config)
    if len(df) < MIN_USABLE_TRADING_DAYS:
        raise HTTPException(status_code=422, detail="백테스트에 사용할 수 있는 거래일이 너무 적습니다. 기간을 늘려보세요.")

    metrics = _performance_metrics(df["Strat_Ret"])
    n_trades = int(df["Turnover"].astype(bool).sum())

    if benchmark_ticker == ticker:
        benchmark_returns = df["Ret"]
        benchmark_label = f"{ticker} Buy & Hold"
    else:
        try:
            bench_close = _download_close_series(benchmark_ticker, req.period)
            benchmark_returns = bench_close.pct_change().reindex(df.index).fillna(0)
            benchmark_label = f"{benchmark_ticker} Buy & Hold"
        except Exception:
            benchmark_returns = df["Ret"]
            benchmark_label = f"{ticker} Buy & Hold (벤치마크 조회 실패로 대체)"
    benchmark_metrics = _performance_metrics(benchmark_returns)
    validation = benchmark_validation(df["Strat_Ret"], benchmark_returns)
    cost_sensitivity = transaction_cost_sensitivity(ohlc, ticker, config)

    last_position = float(df["Position"].iloc[-1])
    cross_mask = df["Position"].diff().abs() > 0
    last_cross_at = df.index[cross_mask][-1].date().isoformat() if cross_mask.any() else None

    strat_cum = (1 + df["Strat_Ret"]).cumprod()
    bench_cum = (1 + benchmark_returns).cumprod()
    running_max = strat_cum.cummax()
    drawdown = (strat_cum - running_max) / running_max

    fig = plt.figure(figsize=(14, 9), facecolor="#0f172a")
    gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.3)
    text_c = "#e2e8f0"
    grid_c = "#1e293b"

    ax1 = fig.add_subplot(gs[0, :])
    ax1.set_facecolor("#1e293b")
    ax1.plot(df.index, df["Close"], color="#64748b", lw=0.8, label=f"{ticker} 종가")
    ax1.plot(df.index, df["MA_fast"], color="#3b82f6", lw=1.5, label=f"MA{req.fast_ma}")
    ax1.plot(df.index, df["MA_slow"], color="#f97316", lw=1.5, label=f"MA{req.slow_ma}")
    buy_m = (df["Position"] == 1) & (df["Position"].shift(1) == 0)
    sell_m = (df["Position"] == 0) & (df["Position"].shift(1) == 1)
    ax1.scatter(df.index[buy_m], df["Close"][buy_m], marker="^", color="#22c55e", s=50, zorder=5, label="매수")
    ax1.scatter(df.index[sell_m], df["Close"][sell_m], marker="v", color="#ef4444", s=50, zorder=5, label="매도")
    ax1.set_title(f"{ticker} MA 크로스오버 (실제 종가, MA{req.fast_ma}/MA{req.slow_ma})", color=text_c, fontsize=11, fontweight="bold")
    ax1.legend(fontsize=8, ncol=5, labelcolor=text_c, facecolor="#0f172a")
    ax1.tick_params(colors=text_c); ax1.spines[:].set_color(grid_c)
    ax1.grid(True, alpha=0.2, color=grid_c)

    ax2 = fig.add_subplot(gs[1, :])
    ax2.set_facecolor("#1e293b")
    ax2.plot(df.index, strat_cum, color="#3b82f6", lw=2, label=f"전략 ({metrics['total_return']:+.1%})")
    ax2.plot(df.index, bench_cum, color="#94a3b8", lw=2, ls="--", label=f"{benchmark_label} ({benchmark_metrics['total_return']:+.1%})")
    ax2.axhline(1.0, color="#475569", lw=0.6)
    ax2.set_title("누적 수익률 비교 (거래비용·슬리피지 반영)", color=text_c, fontsize=11)
    ax2.legend(fontsize=9, labelcolor=text_c, facecolor="#0f172a")
    ax2.tick_params(colors=text_c); ax2.spines[:].set_color(grid_c)
    ax2.grid(True, alpha=0.2, color=grid_c)

    ax3 = fig.add_subplot(gs[2, 0])
    ax3.set_facecolor("#1e293b")
    ax3.fill_between(df.index, drawdown * 100, 0, color="#ef4444", alpha=0.5)
    ax3.set_title("낙폭 Drawdown (%)", color=text_c, fontsize=11)
    ax3.tick_params(colors=text_c); ax3.spines[:].set_color(grid_c)
    ax3.grid(True, alpha=0.2, color=grid_c)

    ax4 = fig.add_subplot(gs[2, 1])
    ax4.set_facecolor("#1e293b")
    ax4.axis("off")
    rows = [
        ["총수익률", f"{metrics['total_return']:+.1%}"],
        ["CAGR", f"{metrics['cagr']:+.2%}"],
        ["Sharpe", f"{metrics['sharpe']:.2f}"],
        ["Sortino", f"{metrics['sortino']:.2f}"],
        ["MDD", f"{metrics['mdd']:.1%}"],
        ["승률", f"{metrics['win_rate']:.1%}"],
        ["거래횟수", f"{n_trades}회"],
        ["비용+슬리피지", f"{req.transaction_cost_bps + req.slippage_bps:.0f}bp/회전"],
        ["체결 규칙", "다음 거래일 시가"],
    ]
    tbl = ax4.table(cellText=rows, colLabels=["지표", "값"], loc="center", bbox=[0, 0, 1, 1])
    tbl.auto_set_font_size(False); tbl.set_fontsize(9)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_facecolor("#0f172a" if r == 0 else "#1e293b")
        cell.set_text_props(color=text_c)
        cell.set_edgecolor(grid_c)
    ax4.set_title("성과 요약 (실제 가격)", color=text_c, fontsize=11)

    fig.patch.set_facecolor("#0f172a")
    plt.suptitle(f"실제 가격 백테스트 — {ticker}", color=text_c, fontsize=13, fontweight="bold", y=1.01)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="#0f172a")
    plt.close(fig)

    return {
        "data_source": "real_price",
        "quote_source": "yahoo_finance_daily_close",
        "ticker": ticker,
        "as_of": df.index[-1].date().isoformat(),
        "period": req.period,
        "fast_ma": req.fast_ma,
        "slow_ma": req.slow_ma,
        "transaction_cost_bps": req.transaction_cost_bps,
        "slippage_bps": req.slippage_bps,
        "initial_capital": req.initial_capital,
        "execution": run_metadata,
        "benchmark_ticker": benchmark_ticker,
        "benchmark_label": benchmark_label,
        "n_trades": n_trades,
        "metrics": metrics,
        "benchmark_metrics": benchmark_metrics,
        "benchmark_validation": validation,
        "cost_sensitivity": cost_sensitivity,
        "strategy_return_series": [{"date": index.date().isoformat(), "return": round(float(value), 10)} for index, value in df["Strat_Ret"].tail(2_000).items()],
        "current_position": "매수 보유" if last_position > 0 else "미보유(현금)",
        "last_signal_change_at": last_cross_at,
        "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
    }


@router.post("/research")
async def run_strategy_research(req: StrategyResearchRequest, user: dict = Depends(current_user)) -> dict:
    """시간 순서가 보존된 파라미터 연구를 실행하고 사용자별 결과를 저장한다."""
    ticker = req.ticker.strip().upper()
    if not TICKER_PATTERN.fullmatch(ticker):
        raise HTTPException(status_code=422, detail="티커는 영문·숫자와 . ^ = - 기호만 사용할 수 있습니다.")
    if not BACKTEST_PERIOD_PATTERN.fullmatch(req.period):
        raise HTTPException(status_code=422, detail="period는 6mo, 1y, 2y, 3y, 5y 중 하나여야 합니다.")
    try:
        ohlc = _download_ohlc_frame(ticker, req.period)
        result = run_research(
            ohlc, ticker, [(fast, slow) for fast in req.fast_candidates for slow in req.slow_candidates],
            req.transaction_cost_bps, req.slippage_bps, walk_forward_folds=req.walk_forward_folds,
            factor_filter={"enabled": req.factor_filter_enabled, "min_return_20d": req.min_return_20d,
                           "max_realized_volatility_20d": req.max_realized_volatility_20d},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"전략 연구용 가격 이력을 가져오지 못했습니다: {exc}") from exc
    document = {"research_id": uuid.uuid4().hex, "user_id": user["_id"], "created_at": _now(),
                "request": req.model_dump(), "result": result}
    await get_db().strategy_research_runs.insert_one(document)
    return {"research_id": document["research_id"], "created_at": document["created_at"].isoformat(), **result}


@router.get("/research")
async def list_strategy_research(user: dict = Depends(current_user)) -> dict:
    rows = await get_db().strategy_research_runs.find({"user_id": user["_id"]}, {"user_id": 0}).sort("created_at", -1).to_list(50)
    for row in rows:
        row.pop("_id", None)
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()
    return {"items": rows}


def _research_registry_item(document: dict) -> dict:
    """Keep the experiment registry small and compare only held-out evidence."""
    result = document.get("result", {})
    selected = result.get("selected", {})
    final_test = selected.get("final_test", {})
    validation = selected.get("validation", {})
    assessment = result.get("paper_candidate_assessment", {})
    statistical_validation = result.get("statistical_validation", {})
    return {
        "research_id": document["research_id"], "created_at": document["created_at"].isoformat() if document.get("created_at") else None,
        "ticker": result.get("ticker") or document.get("request", {}).get("ticker"),
        "period": document.get("request", {}).get("period"),
        "parameters": {"fast_ma": selected.get("fast_ma"), "slow_ma": selected.get("slow_ma"),
                       "transaction_cost_bps": document.get("request", {}).get("transaction_cost_bps"),
                       "slippage_bps": document.get("request", {}).get("slippage_bps"),
                       "factor_filter": result.get("factor_filter", {})},
        "validation": {key: validation.get(key) for key in ("total_return", "sharpe", "mdd", "days")},
        "final_test": {key: final_test.get(key) for key in ("total_return", "sharpe", "mdd", "days")},
        "point_in_time_audit_passed": bool(result.get("point_in_time_audit", {}).get("passed")),
        "statistical_validation": {key: statistical_validation.get(key) for key in ("available", "status", "observations", "bootstrap_positive_total_return_probability", "multiple_testing_sharpe_hurdle")},
        "paper_candidate_status": assessment.get("status"),
        "paper_candidate_reasons": assessment.get("reasons", []),
    }


@router.get("/research/registry")
async def get_strategy_research_registry(user: dict = Depends(current_user)) -> dict:
    """List immutable per-user research runs as an experiment registry."""
    rows = await get_db().strategy_research_runs.find(
        {"user_id": user["_id"]}, {"user_id": 0},
    ).sort("created_at", -1).to_list(100)
    return {
        "items": [_research_registry_item(row) for row in rows],
        "disclaimer": "실험 레지스트리는 저장된 훈련·검증·최종 테스트 결과를 비교합니다. 최종 테스트는 선택 규칙에 쓰지 않으며, 비교 결과는 주문·승인·전략 상태를 변경하지 않습니다.",
    }


@router.post("/research/universe-screen")
async def screen_research_universe(payload: ResearchUniverseScreenRequest, user: dict = Depends(current_user)) -> dict:
    """Screen a caller-supplied universe from stored same-date factor snapshots only."""
    tickers = [ticker.strip().upper() for ticker in payload.tickers]
    if any(not TICKER_PATTERN.fullmatch(ticker) for ticker in tickers):
        raise HTTPException(status_code=422, detail="유니버스에는 유효한 티커만 넣을 수 있습니다.")
    try:
        snapshot = await market_factor_universe_snapshot(tickers, payload.as_of)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    items, excluded = [], list(snapshot.get("excluded", []))
    for item in snapshot.get("items", []):
        factors = item.get("factors", {})
        average_volume = factors.get("average_volume_20d")
        composite = item.get("factor_composite")
        reasons = []
        if average_volume is None or float(average_volume) < payload.minimum_average_volume_20d:
            reasons.append("20일 평균 거래량 기준 미달")
        if payload.minimum_factor_composite is not None and (composite is None or float(composite) < payload.minimum_factor_composite):
            reasons.append("팩터 복합점수 기준 미달")
        row = {"ticker": item["ticker"], "factor_composite": composite, "factors": factors,
               "factor_ranks": item.get("factor_ranks", {}), "input_fingerprint": item.get("input_fingerprint"),
               "eligible_for_research": not reasons, "exclusion_reasons": reasons}
        (items if not reasons else excluded).append(row)
    return {"report_version": "research-universe-screen-v1", "as_of": snapshot.get("as_of"), "factor_version": snapshot.get("factor_version"),
            "universe_id": snapshot.get("universe_id"), "items": items, "excluded": excluded,
            "criteria": {"minimum_average_volume_20d": payload.minimum_average_volume_20d, "minimum_factor_composite": payload.minimum_factor_composite},
            "disclaimer": "동일 기준일에 저장된 관측 팩터로 연구 대상을 거르는 도구입니다. 시장 전체를 자동 수집하거나 매수·매도 신호·주문·전략 상태를 만들지 않습니다."}


@router.get("/research/revalidation-monitor")
async def get_research_revalidation_monitor(user: dict = Depends(current_user)) -> dict:
    """저장된 연구의 재검증 최신성·성과 드리프트를 읽기 전용으로 확인한다."""
    return await build_revalidation_monitor(user)


@router.post("/research/{research_id}/revalidate")
async def revalidate_strategy_research(research_id: str, user: dict = Depends(current_user)) -> dict:
    """Run a user-requested fresh walk-forward validation; it creates no strategy or order."""
    research = await get_db().strategy_research_runs.find_one({"user_id": user["_id"], "research_id": research_id})
    if not research:
        raise HTTPException(status_code=404, detail="재검증할 연구를 찾지 못했습니다.")
    try:
        req = StrategyResearchRequest(**research["request"])
        ticker = req.ticker.strip().upper()
        ohlc = _download_ohlc_frame(ticker, req.period)
        result = run_research(
            ohlc, ticker, [(fast, slow) for fast in req.fast_candidates for slow in req.slow_candidates],
            req.transaction_cost_bps, req.slippage_bps, walk_forward_folds=req.walk_forward_folds,
            factor_filter={"enabled": req.factor_filter_enabled, "min_return_20d": req.min_return_20d,
                           "max_realized_volatility_20d": req.max_realized_volatility_20d},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"재검증용 가격 이력을 가져오지 못했습니다: {exc}") from exc
    now = _now()
    selected = result["selected"]
    snapshot = {"revalidation_id": uuid.uuid4().hex, "user_id": user["_id"], "research_id": research_id,
                "captured_at": now, "metrics": selected["final_test"], "run_id": selected["run_id"],
                "data_fingerprint": selected["data_fingerprint"], "walk_forward_fold_count": len(result.get("walk_forward", [])),
                "point_in_time_audit_passed": bool(result.get("point_in_time_audit", {}).get("passed")), "automatic_action": False}
    await get_db().strategy_revalidations.insert_one(snapshot)
    await append_execution_audit(user["_id"], "research_revalidation_recorded", "strategy_research", {
        "research_id": research_id, "revalidation_id": snapshot["revalidation_id"], "changes_order_authority": False,
    })
    return {**{key: value for key, value in snapshot.items() if key not in {"_id", "user_id", "captured_at"}}, "captured_at": now.isoformat(),
            "disclaimer": "재검증 결과를 감사용으로 기록했습니다. 전략 승인·중단·배분·주문·실거래는 자동으로 변경하지 않습니다."}


@router.get("/research/compare")
async def compare_strategy_research(control_id: str = Query(min_length=8, max_length=64), challenger_id: str = Query(min_length=8, max_length=64), user: dict = Depends(current_user)) -> dict:
    """A/B comparison of two saved research runs from the same user and ticker."""
    rows = await get_db().strategy_research_runs.find(
        {"user_id": user["_id"], "research_id": {"$in": [control_id, challenger_id]}}, {"user_id": 0},
    ).to_list(2)
    by_id = {row["research_id"]: row for row in rows}
    if control_id not in by_id or challenger_id not in by_id:
        raise HTTPException(status_code=404, detail="비교할 두 실험 기록을 모두 찾지 못했습니다.")
    control, challenger = _research_registry_item(by_id[control_id]), _research_registry_item(by_id[challenger_id])
    if control["ticker"] != challenger["ticker"]:
        raise HTTPException(status_code=422, detail="동일 티커의 실험만 A/B 비교할 수 있습니다.")
    deltas = {}
    for key in ("total_return", "sharpe", "mdd"):
        base, candidate = control["final_test"].get(key), challenger["final_test"].get(key)
        deltas[key] = round(float(candidate) - float(base), 6) if isinstance(base, (int, float)) and isinstance(candidate, (int, float)) else None
    return {
        "control": control, "challenger": challenger, "final_test_delta": deltas,
        "comparison_rule": "동일 티커의 저장된 실험만 비교하며, 선택에 사용하지 않은 최종 테스트 지표 차이를 표시합니다.",
        "disclaimer": "A/B 비교는 과거 연구 결과의 재현·검토용입니다. 유의성 검정이나 미래 성과를 보장하지 않으며 주문·승인·배분을 변경하지 않습니다.",
    }


@router.get("/fills")
async def list_fills(user: dict = Depends(current_user)) -> dict:
    rows = await get_db().paper_fills.find({"user_id": user["_id"]}).sort("filled_at", -1).to_list(200)
    return {"items": [_serialize_fill(row) for row in rows]}


@router.get("/strategy-portfolio")
async def get_strategy_portfolio(max_strategy_pct: float = Query(default=40, ge=1, le=100), user: dict = Depends(current_user)) -> dict:
    """저장 전략으로 만든 참고용 위험 예산. 주문·모의 체결을 생성하지 않는다."""
    rows = await get_db().strategies.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(200)
    return build_strategy_allocation(rows, max_strategy_pct)


@router.get("/strategy-rebalance-plan")
async def get_strategy_rebalance_plan(max_strategy_pct: float = Query(default=40, ge=1, le=100), tolerance_pct: float = Query(default=2, ge=0, le=100), user: dict = Depends(current_user)) -> dict:
    """목표 위험 예산과 현재 모의 전략 평가액의 차이를 읽기 전용 계획으로 반환한다."""
    rows = await get_db().strategies.find({"user_id": user["_id"]}).sort("created_at", -1).to_list(200)
    return build_rebalance_plan(build_strategy_allocation(rows, max_strategy_pct), await _paper_dashboard(user), tolerance_pct)


@router.get("/strategy-version-comparison/{strategy_id}")
async def get_strategy_version_comparison(strategy_id: str, max_strategy_pct: float = Query(default=40, ge=1, le=100), user: dict = Depends(current_user)) -> dict:
    current = await _get_owned_strategy(strategy_id, user)
    if not current.get("parent_id"):
        raise HTTPException(status_code=422, detail="비교할 이전 버전이 없는 전략입니다.")
    previous = await get_db().strategies.find_one({"_id": current["parent_id"], "user_id": user["_id"]})
    if not previous:
        raise HTTPException(status_code=404, detail="이전 전략 버전을 찾을 수 없습니다.")
    rows = await get_db().strategies.find({"user_id": user["_id"]}).to_list(200)
    return compare_strategy_versions(current, previous, rows, max_strategy_pct)


@router.get("/strategy-version-promotion/{strategy_id}")
async def get_strategy_version_promotion(strategy_id: str, user: dict = Depends(current_user)) -> dict:
    """전략 계보의 승인 고정 상태와 변경 불가능한 이력을 조회한다."""
    return await _strategy_promotion_status(await _get_owned_strategy(strategy_id, user), user)


@router.post("/strategy-version-promotion/{strategy_id}/approve")
async def approve_strategy_version_promotion(strategy_id: str, payload: StrategyPromotionApprovalRequest, user: dict = Depends(current_user)) -> dict:
    """검증된 최신 후보 버전을 운영자가 승인 고정한다. 주문/배분은 변경하지 않는다."""
    if not is_execution_operator(user):
        raise HTTPException(status_code=403, detail="서버에 등록된 운영자만 전략 버전을 승인할 수 있습니다.")
    strategy = await _get_owned_strategy(strategy_id, user)
    if strategy.get("type") != "real_backtest" or not strategy.get("parent_id"):
        raise HTTPException(status_code=422, detail="이전 버전이 있는 실제 가격 백테스트만 승인 후보가 될 수 있습니다.")
    if await get_db().strategies.find_one({"user_id": user["_id"], "parent_id": strategy["_id"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="더 최신의 전략 버전이 있어 이 버전은 승인 고정할 수 없습니다.")

    root = await _strategy_lineage_root(strategy, user["_id"])
    lock = _promotion_locks.setdefault(f"{user['_id']}:{root['_id']}", asyncio.Lock())
    async with lock:
        status = await _strategy_promotion_status(strategy, user)
        if not status["eligible_for_approval"]:
            raise HTTPException(status_code=422, detail="성과 비교를 통과한 승격 후보만 승인할 수 있습니다.")
        if status["is_current_approved"]:
            raise HTTPException(status_code=409, detail="이미 승인 고정된 전략 버전입니다.")
        now = _now()
        fingerprint = _strategy_version_fingerprint(strategy)
        state = {
            "user_id": user["_id"], "lineage_root_id": root["_id"], "strategy_id": strategy["_id"],
            "strategy_version": strategy.get("version", 1), "strategy_fingerprint": fingerprint,
            "status": "approved", "approved_at": now, "approved_by": user["_id"], "approved_by_email": user["email"],
            "approval_note": payload.note.strip(), "updated_at": now,
        }
        await get_db().strategy_promotion_states.update_one(
            {"user_id": user["_id"], "lineage_root_id": root["_id"]}, {"$set": state}, upsert=True,
        )
        event = {
            "event_id": uuid.uuid4().hex, "user_id": user["_id"], "lineage_root_id": root["_id"],
            "strategy_id": strategy["_id"], "strategy_version": strategy.get("version", 1),
            "strategy_fingerprint": fingerprint, "action": "approved", "note": payload.note.strip(),
            "operator_id": user["_id"], "operator_email": user["email"], "created_at": now,
        }
        await get_db().strategy_promotion_events.insert_one(event)
        await append_execution_audit(user["_id"], "strategy_version_approved", "strategy_promotion", {
            "strategy_id": str(strategy["_id"]), "lineage_root_id": str(root["_id"]), "strategy_version": strategy.get("version", 1),
            "strategy_fingerprint": fingerprint, "creates_order_authority": False,
        })
    return await _strategy_promotion_status(strategy, user)


@router.post("/strategy-version-promotion/{strategy_id}/revoke")
async def revoke_strategy_version_promotion(strategy_id: str, payload: StrategyPromotionApprovalRequest, user: dict = Depends(current_user)) -> dict:
    """운영자가 현재 고정된 승인 버전을 철회한다. 주문·배분·모의 체결은 그대로 둔다."""
    if not is_execution_operator(user):
        raise HTTPException(status_code=403, detail="서버에 등록된 운영자만 전략 버전 승인을 철회할 수 있습니다.")
    strategy = await _get_owned_strategy(strategy_id, user)
    root = await _strategy_lineage_root(strategy, user["_id"])
    lock = _promotion_locks.setdefault(f"{user['_id']}:{root['_id']}", asyncio.Lock())
    async with lock:
        now = _now()
        state = await get_db().strategy_promotion_states.find_one_and_update(
            {"user_id": user["_id"], "lineage_root_id": root["_id"], "strategy_id": strategy["_id"], "status": "approved"},
            {"$set": {"status": "revoked", "revoked_at": now, "revoked_by": user["_id"], "revocation_note": payload.note.strip(), "updated_at": now}},
            return_document=ReturnDocument.BEFORE,
        )
        if not state:
            raise HTTPException(status_code=409, detail="현재 승인 고정된 전략 버전만 철회할 수 있습니다.")
        event = {
            "event_id": uuid.uuid4().hex, "user_id": user["_id"], "lineage_root_id": root["_id"],
            "strategy_id": strategy["_id"], "strategy_version": strategy.get("version", 1),
            "strategy_fingerprint": state["strategy_fingerprint"], "action": "revoked", "note": payload.note.strip(),
            "operator_id": user["_id"], "operator_email": user["email"], "created_at": now,
        }
        await get_db().strategy_promotion_events.insert_one(event)
        await append_execution_audit(user["_id"], "strategy_version_revoked", "strategy_promotion", {
            "strategy_id": str(strategy["_id"]), "lineage_root_id": str(root["_id"]), "strategy_version": strategy.get("version", 1),
            "strategy_fingerprint": state["strategy_fingerprint"], "creates_order_authority": False,
        })
    return await _strategy_promotion_status(strategy, user)


@router.get("/promotion-monitor")
async def get_promotion_monitor(user: dict = Depends(current_user)) -> dict:
    """승인 전략의 현재 모의운용 점검 상태다. 주문·승인 상태는 바꾸지 않는다."""
    dashboard = await _paper_dashboard(user)
    records = await _promotion_monitor_records(user, dashboard)
    return {
        "review_period_days": _promotion_review_days(), "performance_gap_threshold_pct": _promotion_performance_gap_pct(),
        "records": records, "findings": await _promotion_monitor_findings(user, dashboard),
        "automatic_action": "재검토 알림만 생성하며 승인 철회·주문·배분 변경은 하지 않습니다.",
    }


@router.post("/promotion-monitor/refresh")
async def refresh_promotion_monitor(user: dict = Depends(current_user)) -> dict:
    """현재 지연 시세로 승인 전략 점검 스냅샷과 재검토 알림을 즉시 갱신한다."""
    dashboard = await _paper_dashboard(user)
    snapshot = await capture_promotion_monitor_snapshot(user, dashboard)
    alerts = await refresh_paper_alerts(user)
    return {"snapshot": snapshot, "alerts": alerts, "automatic_action": "재검토 알림만 생성하며 승인 철회·주문·배분 변경은 하지 않습니다."}


@router.get("/live-review-readiness")
async def get_live_review_readiness(user: dict = Depends(current_user)) -> dict:
    """장기 모의운용 기준에 따른 실거래 검토 후보 상태. 권한·주문은 변경하지 않는다."""
    return await build_live_review_readiness(user)


@router.get("/governance-board")
async def get_strategy_governance_board(user: dict = Depends(current_user)) -> dict:
    """승인 전략의 장기 모의운용 증거를 한데 보되 어떠한 실행 권한도 만들지 않는다."""
    return await build_strategy_governance_board(user)


@router.get("/performance-decomposition")
async def get_performance_decomposition(user: dict = Depends(current_user)) -> dict:
    """전략군·후보군의 측정 범위를 명시한 읽기 전용 성과 분해 자료다."""
    return await build_performance_decomposition(user)


@router.get("/data-lineage")
async def get_strategy_data_lineage(user: dict = Depends(current_user)) -> dict:
    """현재 사용자의 연구 입력 일봉 계보를 읽기 전용으로 반환한다."""
    return await build_strategy_data_lineage(user)


@router.get("/integrated-risk")
async def get_integrated_risk_board(user: dict = Depends(current_user)) -> dict:
    """실제 보유 장부와 모의 원장의 중복·집중도 프록시를 조회한다."""
    return await build_integrated_risk_board(user)


@router.get("/operations-runbook")
async def get_quant_operations_runbook(user: dict = Depends(current_user)) -> dict:
    """운영 경고를 런북 경로와 함께 읽기 전용으로 제공한다."""
    return await build_quant_operations_board(user)


@router.get("/broker-sandbox/contract-check")
async def get_latest_sandbox_contract_check(user: dict = Depends(current_user)) -> dict:
    row = await get_db().broker_sandbox_contract_checks.find_one(
        {"user_id": user["_id"]}, {"_id": 0, "user_id": 0}, sort=[("checked_at", -1)],
    )
    if not row:
        return {"available": False, "live_order_submission_available": False,
                "disclaimer": "아직 로컬 샌드박스 계약 검증 기록이 없습니다. 검증은 외부 브로커에 연결하지 않습니다."}
    row["checked_at"] = _utc_datetime(row["checked_at"]).isoformat()
    return {"available": True, **row}


@router.post("/broker-sandbox/contract-check")
async def create_sandbox_contract_check(user: dict = Depends(current_user)) -> dict:
    """Run the in-process fake-adapter contract suite and retain an audit record only."""
    result = run_local_sandbox_contract_check()
    now = _now()
    document = {"check_id": uuid.uuid4().hex, "user_id": user["_id"], "checked_at": now, **result}
    await get_db().broker_sandbox_contract_checks.insert_one(document)
    await append_execution_audit(user["_id"], "broker_sandbox_contract_checked", "broker_sandbox", {
        "check_id": document["check_id"], "passed": result["passed"], "external_network_used": False,
        "live_order_submission_available": False,
    })
    return {**{key: value for key, value in document.items() if key not in {"_id", "user_id", "checked_at"}}, "checked_at": now.isoformat()}


@router.get("/decision-records")
async def list_strategy_decision_records(days: int = Query(default=180, ge=1, le=365), user: dict = Depends(current_user)) -> dict:
    cutoff = _now() - timedelta(days=days)
    rows = await get_db().strategy_decision_records.find(
        {"user_id": user["_id"], "created_at": {"$gte": cutoff}}, {"_id": 0, "user_id": 0},
    ).sort("created_at", -1).to_list(500)
    for row in rows:
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()
    return {"days": days, "items": rows,
            "disclaimer": "전략·후보에 대한 사람의 재검토·중단 고려·모니터링 결정 기록입니다. 기록은 전략 승인 상태, 후보, 주문, 포트폴리오를 변경하지 않습니다."}


@router.post("/decision-records")
async def create_strategy_decision_record(payload: StrategyDecisionRecordCreate, user: dict = Depends(current_user)) -> dict:
    """Append a human decision note; it intentionally has no state-changing side effect."""
    target_id = payload.target_id.strip()
    if not target_id:
        raise HTTPException(status_code=422, detail="결정 대상 식별자는 비워둘 수 없습니다.")
    if payload.target_type == "strategy":
        try:
            await _get_owned_strategy(target_id, user)
        except HTTPException:
            raise HTTPException(status_code=404, detail="내 전략에서 결정 대상을 찾지 못했습니다.")
    now = _now()
    document = {"decision_id": uuid.uuid4().hex, "user_id": user["_id"], "target_type": payload.target_type,
                "target_id": target_id, "decision": payload.decision, "note": payload.note.strip(), "created_at": now}
    await get_db().strategy_decision_records.insert_one(document)
    await append_execution_audit(user["_id"], "strategy_decision_recorded", "strategy_governance", {
        "decision_id": document["decision_id"], "target_type": document["target_type"], "target_id": document["target_id"],
        "decision": document["decision"], "changes_order_authority": False,
    })
    return {**{key: value for key, value in document.items() if key not in {"_id", "user_id", "created_at"}}, "created_at": now.isoformat(),
            "disclaimer": "결정 기록만 추가했습니다. 전략 중단·승인 철회·후보 제거·주문 실행은 자동으로 수행하지 않습니다."}


@router.get("/recovery-readiness")
async def get_recovery_readiness(user: dict = Depends(current_user)) -> dict:
    """백업·복구 훈련 준비 상태만 조회한다. 복구 작업은 호출하지 않는다."""
    return await build_recovery_readiness()


@router.get("/live-integration-readiness")
async def get_live_integration_readiness(user: dict = Depends(current_user)) -> dict:
    """실거래 연동의 남은 안전 관문을 조회한다. 전송 권한은 만들지 않는다."""
    return await build_live_integration_readiness(user)


@router.get("/paper-order-preflight/{strategy_id}")
async def get_paper_order_preflight(strategy_id: str, user: dict = Depends(current_user)) -> dict:
    """모의 주문 제안 전 통제 상태를 미리 보여 준다. 이 요청은 상태를 변경하지 않는다."""
    strategy = await _get_owned_strategy(strategy_id, user)
    if strategy.get("type") != "real_backtest":
        raise HTTPException(status_code=422, detail="실제 가격 백테스트 전략만 모의 주문 점검을 할 수 있습니다.")
    return await _paper_order_preflight(strategy, user)


@router.get("/paper-account")
async def get_paper_account(user: dict = Depends(current_user)) -> dict:
    """모의 주문 위험 통제에 쓰는 별도 원화 가상자본을 반환한다."""
    account = await _paper_account(user["_id"])
    fills = await get_db().paper_fills.count_documents({"user_id": user["_id"]})
    return {**account, "fill_count": fills, "currency": "KRW"}


@router.get("/execution-status")
async def get_execution_status(user: dict = Depends(current_user)) -> dict:
    """비밀값을 반환하지 않는 주문 가능 상태 점검 API. 현재 live는 항상 미지원이다."""
    return {**execution_environment_status(), "user_permission": await user_execution_permission(user["_id"])}


@router.put("/execution-permission")
async def update_execution_permission(payload: PaperExecutionPermissionUpdate, user: dict = Depends(current_user)) -> dict:
    """사용자가 자신의 모의 주문 제출 권한만 켜고 끈다. live 권한은 이 API로 만들 수 없다."""
    now = _now()
    await get_db().execution_permissions.update_one(
        {"user_id": user["_id"]}, {"$set": {"user_id": user["_id"], "paper_trading_enabled": payload.paper_trading_enabled, "updated_at": now}}, upsert=True,
    )
    await append_execution_audit(user["_id"], "paper_permission_changed", "execution_permission", {"paper_trading_enabled": payload.paper_trading_enabled})
    return {"paper_trading_enabled": payload.paper_trading_enabled, "live_trading_authorized": False, "updated_at": now.isoformat()}


@router.get("/execution-audit")
async def get_execution_audit(user: dict = Depends(current_user)) -> dict:
    """본인 주문 관련 감사 원장과 해시 체인 검증 결과를 읽기 전용으로 반환한다."""
    rows = await get_db().execution_audit_events.find({"user_id": user["_id"]}).sort("sequence", -1).to_list(100)
    return {"verification": await verify_execution_audit(user["_id"]), "items": [serialize_execution_audit(row) for row in rows]}


@router.get("/execution-audit/export")
async def export_execution_audit(user: dict = Depends(current_user)) -> Response:
    """외부 WORM 보관소에 업로드할 본인 감사 원장 파일을 내려받는다."""
    content, metadata = await export_execution_audit_ndjson(user["_id"])
    return Response(content=content, media_type="application/x-ndjson", headers={
        "Content-Disposition": "attachment; filename=execution-audit.ndjson",
        "X-Audit-SHA256": metadata["sha256"], "X-Audit-Chain-Valid": str(metadata["valid"]).lower(),
    })


@router.post("/execution-operations/requests")
async def create_execution_readiness_request(payload: ReadinessRequestCreate, user: dict = Depends(current_user)) -> dict:
    if not is_execution_operator(user):
        raise HTTPException(status_code=403, detail="서버에 등록된 운영자만 준비 승인 요청을 만들 수 있습니다.")
    request = await create_readiness_request(user, payload.purpose)
    await append_execution_audit(user["_id"], "readiness_request_created", "execution_operations", {"request_id": request["request_id"], "purpose": payload.purpose})
    return serialize_readiness_request(request)


@router.post("/execution-operations/requests/{request_id}/approve")
async def approve_execution_readiness_request(request_id: str, user: dict = Depends(current_user)) -> dict:
    if not is_execution_operator(user):
        raise HTTPException(status_code=403, detail="서버에 등록된 운영자만 준비 요청을 승인할 수 있습니다.")
    request = await approve_readiness_request(request_id, user)
    if not request:
        raise HTTPException(status_code=409, detail="요청이 없거나, 작성자/중복 승인/완료된 요청입니다.")
    await append_execution_audit(user["_id"], "readiness_request_approved", "execution_operations", {"request_id": request_id, "status": request["status"]})
    return serialize_readiness_request(request)


@router.get("/paper-dashboard")
async def get_paper_dashboard(user: dict = Depends(current_user)) -> dict:
    """모의 체결 원장과 현재 지연 시세 기준의 계좌·전략별 성과를 반환한다."""
    return await _paper_dashboard(user)


@router.post("/paper-dashboard/snapshot")
async def create_paper_snapshot(user: dict = Depends(current_user)) -> dict:
    """현재 모의 계좌 평가를 오늘(KST) 스냅샷으로 저장 또는 갱신한다."""
    return await capture_paper_snapshot(user)


@router.get("/paper-dashboard/history")
async def get_paper_history(period: str = Query(default="3m"), user: dict = Depends(current_user)) -> dict:
    return await paper_history(user, period)


@router.post("/alerts/refresh")
async def refresh_alerts(user: dict = Depends(current_user)) -> dict:
    return await refresh_paper_alerts(user)


@router.get("/alerts")
async def list_alerts(status: str = Query(default="active"), user: dict = Depends(current_user)) -> dict:
    query: dict = {"user_id": user["_id"]}
    if status == "active":
        query["condition_active"] = True
    elif status == "history":
        pass
    else:
        raise HTTPException(status_code=422, detail="status는 active 또는 history 중 하나여야 합니다.")
    rows = await get_db().paper_alerts.find(query).sort("updated_at", -1).to_list(200)
    return {"items": [_serialize_paper_alert(row) for row in rows]}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str, payload: PaperAlertResolution, user: dict = Depends(current_user)) -> dict:
    try:
        oid = ObjectId(alert_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="유효하지 않은 알림 ID입니다.") from exc
    alert = await get_db().paper_alerts.find_one({"_id": oid, "user_id": user["_id"]})
    if not alert:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    now = _now()
    status = "resolved" if payload.action == "resolved" else payload.action
    updated = await get_db().paper_alerts.find_one_and_update(
        {"_id": oid, "user_id": user["_id"]}, {"$set": {"status": status, "updated_at": now},
        "$push": {"events": {"at": now, "action": payload.action, "note": payload.note.strip()}}}, return_document=ReturnDocument.AFTER,
    )
    return _serialize_paper_alert(updated)


@router.get("/paper-report")
async def get_paper_report(period: str = Query(default="3m"), user: dict = Depends(current_user)) -> dict:
    return await build_paper_report(user, period)


@router.get("/paper-report/export")
async def export_paper_report(period: str = Query(default="3m"), format: str = Query(default="csv"), user: dict = Depends(current_user)) -> Response:
    report = await build_paper_report(user, period)
    stamp = _now().astimezone(PAPER_SNAPSHOT_TIMEZONE).date().isoformat()
    if format == "json":
        return Response(content=json.dumps(report, ensure_ascii=False, default=str, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="paper-research-report-{period}-{stamp}.json"'})
    if format == "csv":
        return Response(content=_paper_report_csv(report), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="paper-research-report-{period}-{stamp}.csv"'})
    raise HTTPException(status_code=422, detail="format은 csv 또는 json 중 하나여야 합니다.")


@router.get("/operations-report")
async def get_operations_report(days: int = Query(default=7, ge=1, le=31), user: dict = Depends(current_user)) -> dict:
    """주간 운영·통제 리포트. 조회만 하며 주문·승인·복구를 실행하지 않는다."""
    return await build_operations_report(user, days)


@router.get("/operations-report/export")
async def export_operations_report(days: int = Query(default=7, ge=1, le=31), user: dict = Depends(current_user)) -> Response:
    report = await build_operations_report(user, days)
    stamp = _now().astimezone(PAPER_SNAPSHOT_TIMEZONE).date().isoformat()
    return Response(content=json.dumps(report, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="quant-operations-report-{stamp}.json"'})


@router.get("/monthly-operations-report")
async def get_monthly_operations_report(user: dict = Depends(current_user)) -> dict:
    """Current monthly report view; scheduled snapshots are retained separately for audit."""
    report = await build_monthly_operations_report(user)
    stored = await get_db().monthly_operations_reports.find_one(
        {"user_id": user["_id"], "month": report["month"]}, {"_id": 0, "captured_at": 1},
    )
    return {**report, "scheduled_snapshot_captured_at": stored.get("captured_at").isoformat() if stored and stored.get("captured_at") else None}


@router.get("/monthly-operations-report/history")
async def get_monthly_operations_report_history(user: dict = Depends(current_user)) -> dict:
    rows = await get_db().monthly_operations_reports.find(
        {"user_id": user["_id"]}, {"_id": 0, "user_id": 0},
    ).sort("month", -1).to_list(24)
    for row in rows:
        if row.get("captured_at"):
            row["captured_at"] = row["captured_at"].isoformat()
    return {"items": rows, "disclaimer": "월 1회 저장되는 운영 감사 스냅샷입니다. 자동 백업·복구·주문을 실행하지 않습니다."}


@router.put("/paper-account")
async def update_paper_account(payload: PaperAccountUpdate, user: dict = Depends(current_user)) -> dict:
    """체결 전까지만 가상자본을 조정한다. 원장 재해석을 막기 위한 불변 규칙이다."""
    if await get_db().paper_fills.count_documents({"user_id": user["_id"]}):
        raise HTTPException(status_code=409, detail="모의 체결 이력이 있어 가상자본을 변경할 수 없습니다. 새 모의 계좌를 사용하려면 체결 이력을 초기화하는 기능을 별도로 사용하세요.")
    await get_db().paper_accounts.update_one(
        {"user_id": user["_id"]}, {"$set": {"user_id": user["_id"], "initial_capital_krw": payload.initial_capital_krw, "updated_at": _now()}}, upsert=True,
    )
    return {"initial_capital_krw": payload.initial_capital_krw, "fill_count": 0, "currency": "KRW"}


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str, user: dict = Depends(current_user)) -> dict:
    doc = await _get_owned_strategy(strategy_id, user)
    result = _serialize_strategy(doc)
    if doc["type"] == "lean":
        meta = LEAN_RUNS[doc["symbol"]]
        data = _load_lean_summary(meta["summary_path"])
        config = data.get("algorithmConfiguration", {})
        state = data.get("state", {})
        result["snapshot"] = {
            "label": meta["label"],
            "code": meta["code"],
            "status": state.get("Status"),
            "start_date": config.get("startDate"),
            "end_date": config.get("endDate"),
            "statistics": data.get("statistics", {}),
            "equity_curve": _lean_equity_curve(data),
        }
    return result


@router.delete("/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: str, user: dict = Depends(current_user)) -> None:
    doc = await _get_owned_strategy(strategy_id, user)
    await get_db().strategies.delete_one({"_id": doc["_id"]})


@router.post("/{strategy_id}/proposals")
async def create_proposal(strategy_id: str, user: dict = Depends(current_user)) -> dict:
    """저장된 실제 가격 전략의 현재 신호를 다시 계산해 대기 중인 주문 제안을 만든다."""
    started_at = monotonic()
    doc = await _get_owned_strategy(strategy_id, user)
    if doc["type"] != "real_backtest":
        raise HTTPException(status_code=422, detail="주문 제안은 실제 가격 백테스트로 저장한 전략에서만 만들 수 있습니다.")
    preflight = await _paper_order_preflight(doc, user)
    if not preflight["allowed"]:
        await append_execution_audit(user["_id"], "paper_order_preflight_blocked", "strategy", {
            "strategy_id": str(doc["_id"]), "reason_codes": [reason["code"] for reason in preflight["reasons"]],
            "latency_ms": round((monotonic() - started_at) * 1000, 2), "live_trading_authorized": False,
        })
        raise HTTPException(status_code=422, detail="모의 주문 사전 점검에서 차단되었습니다: " + " ".join(reason["message"] for reason in preflight["reasons"]))

    params = doc.get("params", {})
    ticker = doc.get("ticker", "")
    fast_ma = int(params.get("fast_ma", 20))
    slow_ma = int(params.get("slow_ma", 60))

    try:
        close = _download_close_series(ticker, "1y")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{ticker} 최신 시세를 가져오지 못했습니다: {exc}") from exc
    if len(close) < slow_ma + 20:
        raise HTTPException(status_code=422, detail="현재 신호를 판단하기에 최근 가격 이력이 부족합니다.")

    df = _run_ma_crossover(close, fast_ma, slow_ma, 0, 0)
    if df.empty:
        raise HTTPException(status_code=422, detail="현재 신호를 계산할 수 없습니다.")

    current_signal = float(df["Signal"].iloc[-1])
    direction = "buy" if current_signal > 0 else "sell"
    as_of = df.index[-1].date().isoformat()
    basis = f"MA{fast_ma}{'가 MA' + str(slow_ma) + ' 위' if current_signal > 0 else '가 MA' + str(slow_ma) + ' 아래'} ({as_of} 종가 기준)"
    reference_price = float(close.iloc[-1])

    existing = await get_db().order_proposals.find_one({
        "user_id": user["_id"], "strategy_id": doc["_id"], "status": "pending", "direction": direction,
    })
    if existing:
        return _serialize_proposal(existing)

    paper_quantity = await _paper_position_quantity(user["_id"], doc["_id"], ticker)
    if direction == "buy" and paper_quantity > 0:
        raise HTTPException(status_code=409, detail="이미 이 전략의 모의 포지션을 보유 중입니다. 신호가 매도로 바뀔 때까지 추가 매수 제안을 만들지 않습니다.")
    if direction == "sell" and paper_quantity <= 0:
        raise HTTPException(status_code=409, detail="매도할 모의 보유수량이 없어 주문 제안을 만들지 않았습니다.")

    now = _now()
    proposal = {
        "user_id": user["_id"],
        "strategy_id": doc["_id"],
        "ticker": ticker,
        "direction": direction,
        "basis": basis,
        "reference_price": reference_price,
        "status": "pending",
        "preflight": preflight,
        "preflight_checked_at": now,
        "created_at": now,
        "decided_at": None,
    }
    result = await get_db().order_proposals.insert_one(proposal)
    proposal["_id"] = result.inserted_id
    await append_execution_audit(user["_id"], "paper_order_preflight_passed", "order_proposal", {
        "proposal_id": str(proposal["_id"]), "strategy_id": str(doc["_id"]), "ticker": ticker,
        "latency_ms": round((monotonic() - started_at) * 1000, 2), "promotion_approval_required": preflight["promotion_approval_required"],
        "live_trading_authorized": False,
    })
    return _serialize_proposal(proposal)


@router.get("/{strategy_id}/proposals")
async def list_proposals(strategy_id: str, user: dict = Depends(current_user)) -> dict:
    doc = await _get_owned_strategy(strategy_id, user)
    rows = await get_db().order_proposals.find({"strategy_id": doc["_id"], "user_id": user["_id"]}).sort("created_at", -1).to_list(100)
    return {"items": [_serialize_proposal(row) for row in rows]}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, payload: ProposalApproveRequest, user: dict = Depends(current_user)) -> dict:
    """제안을 승인하고 모의 체결을 기록한다. 실제 보유자산(portfolios)은 변경하지 않는다."""
    started_at = monotonic()
    allowed, reason = await paper_submission_allowed(user["_id"])
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    proposal = await _get_owned_proposal(proposal_id, user)
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail="이미 처리된 제안입니다.")
    strategy = await get_db().strategies.find_one({"_id": proposal["strategy_id"], "user_id": user["_id"]})
    if not strategy:
        raise HTTPException(status_code=409, detail="주문 제안의 전략을 찾을 수 없습니다.")
    preflight = await _paper_order_preflight(strategy, user)
    if not preflight["allowed"]:
        now = _now()
        await get_db().order_proposals.update_one(
            {"_id": proposal["_id"], "user_id": user["_id"], "status": "pending"},
            {"$set": {"status": "blocked", "decided_at": now, "preflight": preflight, "block_reason": preflight["reasons"]}},
        )
        await append_execution_audit(user["_id"], "paper_order_execution_blocked", "order_proposal", {
            "proposal_id": str(proposal["_id"]), "strategy_id": str(strategy["_id"]),
            "reason_codes": [item["code"] for item in preflight["reasons"]],
            "latency_ms": round((monotonic() - started_at) * 1000, 2), "live_trading_authorized": False,
        })
        raise HTTPException(status_code=422, detail="모의 체결 직전 점검에서 차단되었습니다: " + " ".join(item["message"] for item in preflight["reasons"]))

    quote = fetch_quote_cached(proposal["ticker"])
    if quote["status"] != "ok":
        raise HTTPException(status_code=502, detail=f"승인 시점 시세를 가져오지 못해 모의 체결을 만들 수 없습니다: {quote['reason']}")
    execution_simulation = _simulate_paper_execution(
        side=proposal["direction"], quote_price=float(quote["price"]), order_type=payload.order_type,
        limit_price=payload.limit_price, requested_quantity=payload.quantity, max_fill_quantity=payload.max_fill_quantity,
    )
    if execution_simulation["status"] == "rejected":
        raise HTTPException(status_code=422, detail=execution_simulation["reason"])
    if execution_simulation["status"] == "unfilled":
        now = _now()
        await get_db().order_proposals.update_one(
            {"_id": proposal["_id"], "user_id": user["_id"], "status": "pending"},
            {"$set": {"status": "unfilled", "decided_at": now, "execution_simulation": execution_simulation}},
        )
        await append_execution_audit(user["_id"], "paper_order_unfilled", "order_proposal", {
            "proposal_id": str(proposal["_id"]), "order_type": payload.order_type, "live_trading_authorized": False,
        })
        return {"status": "unfilled", "proposal_id": str(proposal["_id"]), "execution_simulation": execution_simulation,
                "disclaimer": "지연 시세 기준 지정가 조건 미충족으로 모의 체결을 만들지 않았습니다. 실제 주문은 전송하지 않습니다."}
    simulated_quantity = float(execution_simulation["fill_quantity"])

    paper_quantity = await _paper_position_quantity(
        user["_id"], proposal["strategy_id"], proposal["ticker"],
    )
    if proposal["direction"] == "sell" and simulated_quantity > paper_quantity:
        raise HTTPException(
            status_code=422,
            detail=f"모의 보유수량({paper_quantity:g})보다 많이 매도할 수 없습니다.",
        )

    metadata = await _instrument_metadata(user, proposal["ticker"])
    fx_rate = 1.0
    if metadata["currency"] == "USD":
        fx_quote = fetch_quote_cached("KRW=X")
        if fx_quote["status"] != "ok":
            raise HTTPException(status_code=502, detail="USD/KRW 환율을 가져오지 못해 모의 주문 위험을 확인할 수 없습니다.")
        fx_rate = float(fx_quote["price"])

    approved_quantity = simulated_quantity
    risk_control = {"action": "approved", "requested_quantity": payload.quantity, "approved_quantity": simulated_quantity,
                    "simulated_quantity": simulated_quantity, "reasons": [], "policy_enforced": []}
    if proposal["direction"] == "buy":
        ledger, positions = await _paper_positions_at_market(user)
        policy = await _get_risk_policy(user["_id"])
        risk_control = limit_buy_quantity(
            requested_quantity=simulated_quantity,
            unit_notional_krw=float(execution_simulation["fill_price"]) * fx_rate,
            cash_krw=ledger["cash_krw"], positions=positions, ticker=proposal["ticker"],
            currency=metadata["currency"], sector=metadata["sector"], policy=policy,
        )
        risk_control.update({"requested_quantity": payload.quantity, "simulated_quantity": simulated_quantity,
                             "policy_enforced": ["cash", "max_position", "max_sector", "max_foreign_currency"]})
        if risk_control["action"] == "blocked":
            await append_execution_audit(user["_id"], "paper_order_risk_blocked", "order_proposal", {
                "proposal_id": str(proposal["_id"]), "ticker": proposal["ticker"], "direction": proposal["direction"],
                "requested_quantity": payload.quantity, "reasons": risk_control["reasons"],
                "latency_ms": round((monotonic() - started_at) * 1000, 2), "live_trading_authorized": False,
            })
            raise HTTPException(status_code=422, detail="위험 한도로 모의 매수를 실행하지 않았습니다: " + " ".join(risk_control["reasons"]))
        approved_quantity = float(risk_control["approved_quantity"])

    now = _now()
    claimed = await get_db().order_proposals.find_one_and_update(
        {"_id": proposal["_id"], "user_id": user["_id"], "status": "pending"},
        {"$set": {"status": "processing", "processing_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        raise HTTPException(status_code=409, detail="이미 처리 중이거나 완료된 제안입니다.")

    environment = execution_environment_status()
    sandbox_result = {"attempted": False, "mode": environment["execution_mode"], "accepted": None,
                      "message": "paper 모드에서는 로컬 브로커 샌드박스를 호출하지 않습니다.", "latency_ms": 0.0}
    if environment["execution_mode"] == "sandbox":
        sandbox_started_at = monotonic()
        try:
            result = get_broker_adapter().submit_order(BrokerOrderRequest(
                ticker=proposal["ticker"], side=proposal["direction"], quantity=approved_quantity,
            ))
            sandbox_result = {"attempted": True, "mode": "sandbox", "accepted": result.accepted,
                              "broker_order_id": result.broker_order_id, "message": result.message,
                              "latency_ms": round((monotonic() - sandbox_started_at) * 1000, 2)}
        except Exception as exc:
            sandbox_result = {"attempted": True, "mode": "sandbox", "accepted": False,
                              "message": f"로컬 샌드박스 호출 실패: {exc}", "latency_ms": round((monotonic() - sandbox_started_at) * 1000, 2)}
        if not sandbox_result["accepted"]:
            await get_db().order_proposals.update_one(
                {"_id": proposal["_id"], "status": "processing"},
                {"$set": {"status": "blocked", "decided_at": now, "sandbox_result": sandbox_result}, "$unset": {"processing_at": ""}},
            )
            await append_execution_audit(user["_id"], "paper_sandbox_order_rejected", "order_proposal", {
                "proposal_id": str(proposal["_id"]), "ticker": proposal["ticker"], "direction": proposal["direction"],
                "sandbox_latency_ms": sandbox_result["latency_ms"], "message": sandbox_result["message"], "live_trading_authorized": False,
            })
            raise HTTPException(status_code=422, detail="로컬 샌드박스가 모의 주문을 거절했습니다: " + sandbox_result["message"])
        await append_execution_audit(user["_id"], "paper_sandbox_order_accepted", "sandbox_order", {
            "proposal_id": str(proposal["_id"]), "ticker": proposal["ticker"], "direction": proposal["direction"],
            "sandbox_latency_ms": sandbox_result["latency_ms"], "live_trading_authorized": False,
        })

    position_after = paper_quantity + approved_quantity if proposal["direction"] == "buy" else paper_quantity - approved_quantity
    fill = {
        "user_id": user["_id"],
        "strategy_id": proposal["strategy_id"],
        "proposal_id": proposal["_id"],
        "ticker": proposal["ticker"],
        "direction": proposal["direction"],
        "quantity": approved_quantity,
        "requested_quantity": payload.quantity,
        "fill_price": execution_simulation["fill_price"],
        "notional": round(float(execution_simulation["fill_price"]) * approved_quantity, 8),
        "notional_krw": round(float(execution_simulation["fill_price"]) * fx_rate * approved_quantity, 8),
        "currency": metadata["currency"],
        "sector": metadata["sector"],
        "risk_control": risk_control,
        "execution_simulation": execution_simulation,
        "preflight": preflight,
        "sandbox_result": sandbox_result,
        "position_before": paper_quantity,
        "position_after": max(0.0, position_after),
        "filled_at": now,
    }
    try:
        fill_result = await get_db().paper_fills.insert_one(fill)
    except DuplicateKeyError as exc:
        await get_db().order_proposals.update_one(
            {"_id": proposal["_id"], "status": "processing"},
            {"$set": {"status": "approved", "decided_at": now}, "$unset": {"processing_at": ""}},
        )
        raise HTTPException(status_code=409, detail="이미 체결된 주문 제안입니다.") from exc
    except Exception:
        await get_db().order_proposals.update_one(
            {"_id": proposal["_id"], "status": "processing"},
            {"$set": {"status": "pending"}, "$unset": {"processing_at": ""}},
        )
        raise
    await get_db().order_proposals.update_one(
        {"_id": proposal["_id"], "status": "processing"},
        {"$set": {"status": "partially_filled" if execution_simulation["status"] == "partially_filled" else "approved", "decided_at": now,
                  "preflight": preflight, "sandbox_result": sandbox_result, "execution_simulation": execution_simulation}, "$unset": {"processing_at": ""}},
    )
    fill["_id"] = fill_result.inserted_id
    await append_execution_audit(user["_id"], "paper_proposal_approved", "paper_fill", {
        "proposal_id": str(proposal["_id"]), "ticker": proposal["ticker"], "direction": proposal["direction"],
        "requested_quantity": payload.quantity, "approved_quantity": approved_quantity,
        "risk_action": risk_control["action"], "sandbox_attempted": sandbox_result["attempted"],
        "latency_ms": round((monotonic() - started_at) * 1000, 2), "live_trading_authorized": False,
    })
    return _serialize_fill(fill)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, user: dict = Depends(current_user)) -> dict:
    started_at = monotonic()
    proposal = await _get_owned_proposal(proposal_id, user)
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail="이미 처리된 제안입니다.")
    now = _now()
    rejected = await get_db().order_proposals.find_one_and_update(
        {"_id": proposal["_id"], "user_id": user["_id"], "status": "pending"},
        {"$set": {"status": "rejected", "decided_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not rejected:
        raise HTTPException(status_code=409, detail="이미 처리 중이거나 완료된 제안입니다.")
    await append_execution_audit(user["_id"], "paper_proposal_rejected", "order_proposal", {
        "proposal_id": str(proposal["_id"]), "ticker": proposal["ticker"],
        "latency_ms": round((monotonic() - started_at) * 1000, 2), "live_trading_authorized": False,
    })
    return _serialize_proposal(rejected)


# ── 레버리지 ETF(UPRO/SOXL) DCA 자동 모의매매 ──────────────────────────────
#
# 아래 흐름은 기존 제안(propose)/승인(approve) 2단계 대신 스케줄러가 사람 개입 없이
# 신호를 계산하고 바로 모의 체결(paper_fills)을 만든다. 다만 생성하는 체결은 다른
# 전략과 동일하게 paper_fills 컬렉션에만 기록되고, 실제 보유자산(portfolios)이나
# 브로커 주문은 절대 만들지 않는다. 승격(promotion) 전용 재검토 경고는 이 전략
# 유형에 적용되지 않으므로 paper_submission_allowed(킬스위치·사용자 권한)만 강제한다.
LEVERAGE_DCA_TICKERS = ("UPRO", "SOXL")
LEVERAGE_DCA_STRATEGY_NAME = "UPRO/SOXL 레버리지 DCA (자동 모의매매)"
LEVERAGE_DCA_RESERVE_STRATEGY_NAME = "UPRO/SOXL 레버리지 DCA 예비비 (자동 모의매매)"
LEVERAGE_DCA_SECTOR = "레버리지ETF"


def _leverage_dca_allowed_emails() -> set[str]:
    return {email.strip().lower() for email in os.getenv("LEVERAGE_DCA_ALLOWED_EMAILS", "").split(",") if email.strip()}


async def require_leverage_dca_access(user: dict = Depends(current_user)) -> dict:
    """LEVERAGE_DCA_ALLOWED_EMAILS가 설정되어 있으면 그 계정만 이 기능을 쓸 수 있게 막는다.

    비어 있으면(기본값) 기존처럼 로그인한 모든 계정을 허용한다 — 다른 배포본과의
    호환을 위한 안전한 기본값이며, 이 잠금은 모의 체결 API 접근 범위만 제한할 뿐
    실거래 권한과는 무관하다.
    """
    allowed = _leverage_dca_allowed_emails()
    if allowed and user.get("email", "").lower() not in allowed:
        raise HTTPException(status_code=403, detail="레버리지 DCA 기능은 허용된 계정만 사용할 수 있습니다.")
    return user


def _leverage_dca_owner_email() -> str:
    return os.getenv("LEVERAGE_DCA_OWNER_EMAIL", "").strip().lower()


async def require_leverage_dca_editor(user: dict = Depends(current_user)) -> dict:
    """설정 변경·즉시 실행·기록 초기화 권한.

    LEVERAGE_DCA_OWNER_EMAIL이 설정되어 있으면 그 운영자 계정만 바꿀 수 있고, 다른 계정은 403이다.
    비어 있으면 기존 규칙(LEVERAGE_DCA_ALLOWED_EMAILS)을 그대로 따른다.
    """
    owner_email = _leverage_dca_owner_email()
    if not owner_email:
        return await require_leverage_dca_access(user)
    if user.get("email", "").lower() != owner_email:
        raise HTTPException(status_code=403, detail="모니터링 전용 계정입니다. 레버리지 DCA 설정 변경·실행은 운영자 계정에서만 할 수 있습니다.")
    return user


async def leverage_dca_viewer(user: dict = Depends(current_user)) -> dict:
    """조회 대상과 읽기 전용 여부. 운영자가 지정되면 모든 로그인 계정이 운영자의 현황을 읽기 전용으로 본다."""
    owner_email = _leverage_dca_owner_email()
    if not owner_email:
        return {"subject": await require_leverage_dca_access(user), "read_only": False}
    if user.get("email", "").lower() == owner_email:
        return {"subject": user, "read_only": False}
    owner = await get_db().users.find_one({"email": owner_email}, {"_id": 1})
    if not owner:
        raise HTTPException(status_code=503, detail="레버리지 DCA 운영자 계정을 찾을 수 없습니다.")
    return {"subject": owner, "read_only": True}


def _leverage_dca_policy(config: dict | None) -> dict:
    policy = dict(DEFAULT_LEVERAGE_DCA_POLICY)
    if config:
        for key in ("daily_buy_krw", "max_position_krw", "sell_net_profit_target_krw", "reserve_cash_krw"):
            if config.get(key) is not None:
                policy[key] = float(config[key])
    return policy


async def _leverage_dca_config(user_id) -> dict | None:
    return await get_db().leverage_dca_configs.find_one({"user_id": user_id})


async def _get_or_create_leverage_dca_strategy(user: dict) -> dict:
    existing = await get_db().strategies.find_one({"user_id": user["_id"], "type": "leverage_dca"})
    if existing:
        return existing
    now = _now()
    doc = {
        "user_id": user["_id"], "type": "leverage_dca", "name": LEVERAGE_DCA_STRATEGY_NAME,
        "ticker": "", "symbol": "", "params": {"tickers": list(LEVERAGE_DCA_TICKERS)},
        "rebalance_note": "", "snapshot": {}, "version": 1, "parent_id": None, "created_at": now,
    }
    result = await get_db().strategies.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def _get_or_create_leverage_dca_reserve_strategy(user: dict) -> dict:
    """예비비 단계 투입 전용 포지션. 주전략과 원가·수량을 분리해 매도 트리거에 영향을 주지 않는다."""
    existing = await get_db().strategies.find_one({"user_id": user["_id"], "type": "leverage_dca_reserve"})
    if existing:
        return existing
    now = _now()
    doc = {
        "user_id": user["_id"], "type": "leverage_dca_reserve", "name": LEVERAGE_DCA_RESERVE_STRATEGY_NAME,
        "ticker": "", "symbol": "", "params": {"tickers": ["UPRO"]},
        "rebalance_note": "", "snapshot": {}, "version": 1, "parent_id": None, "created_at": now,
    }
    result = await get_db().strategies.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def _leverage_dca_position_state(user_id, strategy_id) -> dict[str, dict]:
    """전략별 종목 수량·평균 취득원가를 체결 원장에서 계산한다."""
    fills = await get_db().paper_fills.find({"user_id": user_id, "strategy_id": strategy_id}).sort("filled_at", 1).to_list(10_000)
    positions: dict[str, dict] = {}
    for fill in fills:
        ticker = fill["ticker"]
        item = positions.setdefault(ticker, {"ticker": ticker, "quantity": 0.0, "average_cost_krw": 0.0,
                                              "average_cost_usd": 0.0, "realized_gain_ytd_krw": 0.0,
                                              "currency": fill.get("currency", _infer_currency(ticker))})
        quantity = float(fill.get("quantity", 0))
        notional_krw = float(fill.get("notional_krw", fill.get("notional", 0)))
        notional_usd = float(fill.get("notional", 0))
        if fill.get("direction") == "buy":
            old_cost = item["average_cost_krw"] * item["quantity"]
            old_cost_usd = item["average_cost_usd"] * item["quantity"]
            item["quantity"] += quantity
            item["average_cost_krw"] = (old_cost + notional_krw) / item["quantity"] if item["quantity"] else 0.0
            item["average_cost_usd"] = (old_cost_usd + notional_usd) / item["quantity"] if item["quantity"] else 0.0
        else:
            # 평균단가 방식 실현손익. 올해(KST) 매도분만 연간 양도차익 공제 계산에 쓴다.
            sold = min(quantity, item["quantity"])
            filled_at = fill.get("filled_at")
            if filled_at and filled_at.replace(tzinfo=filled_at.tzinfo or timezone.utc).astimezone(PAPER_SNAPSHOT_TIMEZONE).year == _now().astimezone(PAPER_SNAPSHOT_TIMEZONE).year:
                item["realized_gain_ytd_krw"] += notional_krw * (sold / quantity if quantity else 0) - item["average_cost_krw"] * sold
            item["quantity"] = max(0.0, item["quantity"] - quantity)
            if item["quantity"] <= 1e-8:
                item["average_cost_krw"] = 0.0
                item["average_cost_usd"] = 0.0
    return positions


async def _leverage_dca_valuation(positions: dict[str, dict]) -> tuple[float, float, list[str]]:
    """(취득원가 합계, 현재 평가액 합계, 시세 조회 오류 목록)를 계산한다."""
    cost_basis_krw = 0.0
    market_value_krw = 0.0
    errors: list[str] = []
    fx_rate: float | None = None
    for ticker, item in positions.items():
        if item["quantity"] <= 1e-8:
            continue
        cost_basis_krw += item["average_cost_krw"] * item["quantity"]
        quote = fetch_quote_cached(ticker)
        if quote["status"] != "ok":
            errors.append(f"{ticker} 시세 조회 실패")
            continue
        fx = 1.0
        if item["currency"] == "USD":
            if fx_rate is None:
                fx_quote = fetch_quote_cached("KRW=X")
                fx_rate = float(fx_quote["price"]) if fx_quote["status"] == "ok" else None
            if fx_rate is None:
                errors.append("USD/KRW 환율 조회 실패")
                continue
            fx = fx_rate
        market_value_krw += float(quote["price"]) * item["quantity"] * fx
    return round(cost_basis_krw, 2), round(market_value_krw, 2), errors


def _holding_summary(rows: list[dict], *, realized_gain_ytd_krw: float, policy: dict, fx_rate: float | None,
                     errors: list[str]) -> dict:
    """종목별 평단·평가금액·수익률과 합계·세후 실순수익(지금 전량 매도 가정)을 만든다."""
    cost_basis_krw = sum(row["cost_basis_krw"] for row in rows)
    market_value_krw = sum(row["market_value_krw"] or 0.0 for row in rows)
    priced = not errors and all(row["market_value_krw"] is not None for row in rows)
    totals = {"quantity_by_ticker": {row["ticker"]: row["quantity"] for row in rows},
              "cost_basis_krw": round(cost_basis_krw, 2), "market_value_krw": round(market_value_krw, 2) if priced else None,
              "unrealized_pnl_krw": round(market_value_krw - cost_basis_krw, 2) if priced else None,
              "return_pct": round((market_value_krw - cost_basis_krw) / cost_basis_krw * 100, 4) if priced and cost_basis_krw > 0 else None}
    after_tax = (estimate_after_tax_profit(market_value_krw=market_value_krw, cost_basis_krw=cost_basis_krw,
                                           realized_gain_ytd_krw=realized_gain_ytd_krw, policy=policy)
                 if priced and rows else None)
    return {"tickers": rows, "totals": totals, "after_tax": after_tax, "fx_rate": fx_rate, "errors": errors,
            "realized_gain_ytd_krw": round(realized_gain_ytd_krw, 2)}


def _quote_row(ticker: str, quantity: float, average_cost_usd: float, cost_basis_krw: float, fx_rate: float | None,
               errors: list[str]) -> dict:
    quote = fetch_quote_cached(ticker)
    price = float(quote["price"]) if quote["status"] == "ok" else None
    if price is None:
        errors.append(f"{ticker} 시세 조회 실패")
    market_value_krw = price * quantity * fx_rate if price is not None and fx_rate else None
    return {"ticker": ticker, "quantity": round(quantity, 6), "average_cost_usd": round(average_cost_usd, 4),
            "average_cost_krw": round(cost_basis_krw / quantity, 2) if quantity else 0.0,
            "current_price_usd": price, "cost_basis_krw": round(cost_basis_krw, 2),
            "market_value_krw": round(market_value_krw, 2) if market_value_krw is not None else None,
            "unrealized_pnl_krw": round(market_value_krw - cost_basis_krw, 2) if market_value_krw is not None else None,
            "return_pct": (round((price - average_cost_usd) / average_cost_usd * 100, 4)
                           if price is not None and average_cost_usd > 0 else None)}


def _leverage_dca_fx_rate(errors: list[str]) -> float | None:
    fx_quote = fetch_quote_cached("KRW=X")
    if fx_quote["status"] != "ok":
        errors.append("USD/KRW 환율 조회 실패")
        return None
    return float(fx_quote["price"])


async def _leverage_dca_website_holding_summary(user_id, strategy_ids: list, policy: dict) -> dict:
    """홈페이지 자체 모의 체결(주전략+예비비 합산) 기준 보유 요약."""
    merged: dict[str, dict] = {}
    realized = 0.0
    for strategy_id in strategy_ids:
        for ticker, item in (await _leverage_dca_position_state(user_id, strategy_id)).items():
            realized += item.get("realized_gain_ytd_krw", 0.0)
            if item["quantity"] <= 1e-8:
                continue
            row = merged.setdefault(ticker, {"quantity": 0.0, "cost_usd": 0.0, "cost_krw": 0.0})
            row["quantity"] += item["quantity"]
            row["cost_usd"] += item.get("average_cost_usd", 0.0) * item["quantity"]
            row["cost_krw"] += item["average_cost_krw"] * item["quantity"]
    errors: list[str] = []
    fx_rate = _leverage_dca_fx_rate(errors) if merged else None
    rows = [_quote_row(ticker, row["quantity"], row["cost_usd"] / row["quantity"], row["cost_krw"], fx_rate, errors)
            for ticker, row in sorted(merged.items())]
    return {"source": "website_paper_fills", **_holding_summary(rows, realized_gain_ytd_krw=realized, policy=policy,
                                                                fx_rate=fx_rate, errors=errors)}


async def _leverage_dca_alpaca_holding_summary(user_id, policy: dict) -> dict | None:
    """Alpaca 페이퍼 계좌 실제 보유(UPRO/SOXL) 기준 요약. 원화 원가는 체결 기록의 환율로 계산한다."""
    if not alpaca_paper_configured():
        return None
    errors: list[str] = []
    try:
        positions = await asyncio.to_thread(alpaca_list_positions)
    except Exception as exc:
        return {"source": "alpaca_paper_account", "tickers": [], "totals": None, "after_tax": None,
                "errors": [f"Alpaca 포지션 조회 실패: {exc}"]}
    fills = await get_db().leverage_dca_alpaca_fills.find(
        {"user_id": user_id, "fill_quantity": {"$gt": 0}}).sort("filled_at", 1).to_list(10_000)
    # 체결 기록으로 원화 평균원가(체결 시점 환율)와 올해 실현손익을 평균단가 방식으로 계산한다.
    book: dict[str, dict] = {}
    realized = 0.0
    this_year = _now().astimezone(PAPER_SNAPSHOT_TIMEZONE).year
    for fill in fills:
        item = book.setdefault(fill["ticker"], {"quantity": 0.0, "cost_krw": 0.0})
        quantity, notional_krw = float(fill["fill_quantity"]), float(fill.get("notional_krw") or 0.0)
        if fill["direction"] == "buy":
            item["quantity"] += quantity
            item["cost_krw"] += notional_krw
        elif item["quantity"] > 1e-8:
            sold = min(quantity, item["quantity"])
            average = item["cost_krw"] / item["quantity"]
            filled_at = fill["filled_at"].replace(tzinfo=fill["filled_at"].tzinfo or timezone.utc)
            if filled_at.astimezone(PAPER_SNAPSHOT_TIMEZONE).year == this_year:
                realized += notional_krw * (sold / quantity) - average * sold
            item["quantity"] -= sold
            item["cost_krw"] -= average * sold
    fx_rate = _leverage_dca_fx_rate(errors)
    rows, notes = [], []
    for position in positions:
        ticker = position.get("symbol")
        if ticker not in LEVERAGE_DCA_TICKERS:
            continue
        quantity = float(position.get("qty") or 0)
        average_usd = float(position.get("avg_entry_price") or 0)
        recorded = book.get(ticker)
        if recorded and abs(recorded["quantity"] - quantity) < 1e-6 and recorded["quantity"] > 0:
            cost_krw = recorded["cost_krw"]
        else:
            # 체결 기록과 계좌 수량이 다르면(체결 대기 주문 등) 현재 환율로 원가를 환산하고 표시한다.
            cost_krw = average_usd * quantity * (fx_rate or 0.0)
            notes.append(f"{ticker}: 체결 기록과 계좌 수량이 달라 원화 원가를 현재 환율로 환산했습니다.")
        rows.append(_quote_row(ticker, quantity, average_usd, cost_krw, fx_rate, errors))
    return {"source": "alpaca_paper_account", "notes": notes,
            **_holding_summary(rows, realized_gain_ytd_krw=realized, policy=policy, fx_rate=fx_rate, errors=errors)}


def _leverage_dca_build_fill(*, user: dict, strategy_id, ticker: str, direction: str, quantity: float,
                             execution_simulation: dict, fx_rate: float, risk_control: dict) -> dict:
    fill_price = float(execution_simulation["fill_price"])
    return {
        "user_id": user["_id"], "strategy_id": strategy_id, "ticker": ticker, "direction": direction,
        "quantity": round(quantity, 8), "requested_quantity": round(quantity, 8), "fill_price": fill_price,
        "notional": round(fill_price * quantity, 8), "notional_krw": round(fill_price * fx_rate * quantity, 8),
        "currency": "USD", "sector": LEVERAGE_DCA_SECTOR, "risk_control": risk_control,
        "execution_simulation": execution_simulation,
        "source": "leverage_dca_auto",
        "filled_at": _now(),
    }


def _leverage_dca_limit_offset_rate() -> float:
    return 0.0005


def _leverage_dca_submit_order_internal(*, side: str, quote_price: float, requested_quantity: float) -> dict:
    """자체 모의 체결 엔진: 지정가(현재가 ±0.05%) 우선 시도 후 미체결이면 시장가로 전환한다.

    즉시 체결형이라 지정가가 대개 곧바로 채워지지만, 시장가 슬리피지 없이 체결되는
    효과가 있어 지정가 우선 실행의 취지를 그대로 살린다.
    """
    offset = _leverage_dca_limit_offset_rate()
    limit_price = round(quote_price * (1 + offset if side == "buy" else 1 - offset), 8)
    simulation = _simulate_paper_execution(
        side=side, quote_price=quote_price, order_type="limit", limit_price=limit_price,
        requested_quantity=requested_quantity, max_fill_quantity=None,
    )
    if simulation["status"] in {"filled", "partially_filled"}:
        return simulation
    return _simulate_paper_execution(
        side=side, quote_price=quote_price, order_type="market", limit_price=None,
        requested_quantity=requested_quantity, max_fill_quantity=None,
    )


def _alpaca_mirror_summary(alpaca_result: dict | None) -> dict | None:
    if alpaca_result is None:
        return None
    return {"status": alpaca_result["status"], "quantity": alpaca_result["fill_quantity"],
            "notional_krw": alpaca_result["notional_krw"], "reason": alpaca_result.get("reason")}


async def _leverage_dca_submit_alpaca_mirror(*, user: dict, strategy_id, ticker: str, side: str,
                                              quote_price: float, requested_quantity: float, fx_rate: float) -> dict | None:
    """설정이 켜져 있으면 Alpaca 페이퍼 브로커에도 같은 주문을 독립적으로 낸다.

    홈페이지 자체 모의 체결(paper_fills)과 완전히 분리된 기록(leverage_dca_alpaca_fills)이며,
    홈페이지 쪽 포지션·현금 계산에는 전혀 영향을 주지 않는다. Alpaca 쪽이 실패해도 홈페이지
    자체 체결은 항상 별도로 진행되고, 반대로 Alpaca가 성공해도 홈페이지 수량·가격은 바뀌지 않는다.
    Alpaca 미설정 시 아무 기록도 남기지 않고 None을 반환한다.
    """
    config = await _leverage_dca_config(user["_id"])
    if not (config and config.get("alpaca_enabled") and alpaca_paper_configured()):
        return None
    offset = _leverage_dca_limit_offset_rate()
    limit_price = round(quote_price * (1 + offset if side == "buy" else 1 - offset), 2)
    try:
        result = await asyncio.to_thread(
            execute_paper_order_sync, ticker=ticker, side=side, requested_quantity=requested_quantity, limit_price=limit_price,
        )
    except Exception as exc:
        result = {"status": "error", "reason": str(exc)}
    fill_quantity = float(result.get("fill_quantity") or 0)
    fill_price = float(result.get("fill_price") or 0)
    doc = {
        "user_id": user["_id"], "strategy_id": strategy_id, "ticker": ticker, "direction": side,
        "requested_quantity": round(requested_quantity, 8), "status": result.get("status", "error"),
        "fill_quantity": round(fill_quantity, 8), "fill_price": round(fill_price, 8),
        "notional_usd": round(fill_price * fill_quantity, 8), "notional_krw": round(fill_price * fill_quantity * fx_rate, 8),
        "alpaca_order_id": result.get("alpaca_order_id"), "reason": result.get("reason"), "filled_at": _now(),
    }
    await get_db().leverage_dca_alpaca_fills.insert_one(doc)
    await append_execution_audit(user["_id"], "leverage_dca_alpaca_mirror_order", ticker, {
        "strategy_id": str(strategy_id), "side": side, "status": doc["status"],
        "fill_quantity": doc["fill_quantity"], "live_trading_authorized": False,
    })
    return doc


async def _leverage_dca_auto_buy(*, user: dict, strategy_id, ticker: str, amount_krw: float) -> dict:
    if amount_krw <= 0:
        return {"ticker": ticker, "direction": "buy", "status": "skipped", "reason": "매수 금액이 0입니다."}
    quote = fetch_quote_cached(ticker)
    if quote["status"] != "ok":
        return {"ticker": ticker, "direction": "buy", "status": "skipped", "reason": f"{ticker} 시세 조회 실패"}
    fx_quote = fetch_quote_cached("KRW=X")
    if fx_quote["status"] != "ok":
        return {"ticker": ticker, "direction": "buy", "status": "skipped", "reason": "USD/KRW 환율 조회 실패"}
    fx_rate = float(fx_quote["price"])
    # 실제 미국 주식 시장가·지정가 주문은 정수 주 단위만 가능하다(Alpaca 등). 배정 금액을
    # 정확히 맞추지 않고, 그 금액으로 살 수 있는 정수 주 수만큼만 내림해 주문한다.
    requested_quantity = math.floor(amount_krw / (float(quote["price"]) * fx_rate))
    if requested_quantity < 1:
        return {"ticker": ticker, "direction": "buy", "status": "skipped", "reason": "배정 금액으로 1주도 살 수 없습니다."}
    # Alpaca 페이퍼 브로커 체결과 홈페이지 자체 모의 체결은 서로 독립적으로 각각 진행된다.
    alpaca_result = await _leverage_dca_submit_alpaca_mirror(
        user=user, strategy_id=strategy_id, ticker=ticker, side="buy",
        quote_price=float(quote["price"]), requested_quantity=requested_quantity, fx_rate=fx_rate,
    )
    execution_simulation = _leverage_dca_submit_order_internal(side="buy", quote_price=float(quote["price"]), requested_quantity=requested_quantity)
    alpaca_summary = _alpaca_mirror_summary(alpaca_result)
    if execution_simulation["status"] not in {"filled", "partially_filled"}:
        return {"ticker": ticker, "direction": "buy", "status": "skipped",
                "reason": execution_simulation.get("reason", "체결되지 않았습니다."), "alpaca": alpaca_summary}
    # 레버리지 DCA는 포트폴리오 전반의 종목·업종·외화 집중도 한도(35%/50%/60%)와는 별개의
    # 독립 전략으로 운영한다. 예산은 이 전략 자체의 daily_buy_krw/max_position_krw로만
    # 통제하고, 여기서는 모의 계좌 현금이 실제로 부족해 음수가 되지 않는지만 확인한다.
    ledger, _positions = await _paper_positions_at_market(user)
    unit_notional_krw = float(execution_simulation["fill_price"]) * fx_rate
    affordable_quantity = math.floor(ledger["cash_krw"] / unit_notional_krw) if unit_notional_krw > 0 else 0
    fill_quantity = min(math.floor(float(execution_simulation["fill_quantity"])), affordable_quantity)
    if fill_quantity < 1:
        return {"ticker": ticker, "direction": "buy", "status": "blocked",
                "reason": "모의 계좌 사용 가능 현금이 부족합니다.", "alpaca": alpaca_summary}
    risk_control = {"action": "approved" if fill_quantity == requested_quantity else "reduced",
                     "reasons": [] if fill_quantity == requested_quantity else ["모의 계좌 사용 가능 현금 범위로 수량을 줄였습니다."],
                     "scope": "leverage_dca_independent_strategy"}
    fill = _leverage_dca_build_fill(user=user, strategy_id=strategy_id, ticker=ticker, direction="buy",
                                    quantity=fill_quantity, execution_simulation=execution_simulation,
                                    fx_rate=fx_rate, risk_control=risk_control)
    try:
        await get_db().paper_fills.insert_one(fill)
    except PyMongoError as exc:
        return {"ticker": ticker, "direction": "buy", "status": "error",
                "reason": f"자체 모의 체결 저장 실패: {exc}", "alpaca": alpaca_summary}
    await append_execution_audit(user["_id"], "leverage_dca_auto_buy", ticker, {
        "strategy_id": str(strategy_id), "quantity": fill["quantity"], "notional_krw": fill["notional_krw"],
        "live_trading_authorized": False,
    })
    return {"ticker": ticker, "direction": "buy", "status": "filled", "quantity": fill["quantity"],
            "notional_krw": fill["notional_krw"], "alpaca": alpaca_summary}


async def _leverage_dca_auto_sell(*, user: dict, strategy_id, ticker: str, quantity: float) -> dict:
    if quantity <= 1e-8:
        return {"ticker": ticker, "direction": "sell", "status": "skipped", "reason": "매도할 모의 보유수량이 없습니다."}
    quote = fetch_quote_cached(ticker)
    if quote["status"] != "ok":
        return {"ticker": ticker, "direction": "sell", "status": "skipped", "reason": f"{ticker} 시세 조회 실패"}
    fx_quote = fetch_quote_cached("KRW=X")
    if fx_quote["status"] != "ok":
        return {"ticker": ticker, "direction": "sell", "status": "skipped", "reason": "USD/KRW 환율 조회 실패"}
    fx_rate = float(fx_quote["price"])
    # Alpaca 페이퍼 브로커 체결과 홈페이지 자체 모의 체결은 서로 독립적으로 각각 진행된다.
    alpaca_result = await _leverage_dca_submit_alpaca_mirror(
        user=user, strategy_id=strategy_id, ticker=ticker, side="sell",
        quote_price=float(quote["price"]), requested_quantity=quantity, fx_rate=fx_rate,
    )
    alpaca_summary = _alpaca_mirror_summary(alpaca_result)
    execution_simulation = _leverage_dca_submit_order_internal(side="sell", quote_price=float(quote["price"]), requested_quantity=quantity)
    if execution_simulation["status"] not in {"filled", "partially_filled"}:
        return {"ticker": ticker, "direction": "sell", "status": "skipped",
                "reason": execution_simulation.get("reason", "체결되지 않았습니다."), "alpaca": alpaca_summary}
    fill_quantity = float(execution_simulation["fill_quantity"])
    fill = _leverage_dca_build_fill(user=user, strategy_id=strategy_id, ticker=ticker, direction="sell",
                                    quantity=fill_quantity, execution_simulation=execution_simulation,
                                    fx_rate=fx_rate, risk_control={"action": "approved", "reasons": []})
    try:
        await get_db().paper_fills.insert_one(fill)
    except PyMongoError as exc:
        return {"ticker": ticker, "direction": "sell", "status": "error",
                "reason": f"자체 모의 체결 저장 실패: {exc}", "alpaca": alpaca_summary}
    await append_execution_audit(user["_id"], "leverage_dca_auto_sell", ticker, {
        "strategy_id": str(strategy_id), "quantity": fill["quantity"], "notional_krw": fill["notional_krw"],
        "live_trading_authorized": False,
    })
    return {"ticker": ticker, "direction": "sell", "status": "filled", "quantity": fill["quantity"],
            "notional_krw": fill["notional_krw"], "alpaca": alpaca_summary}


async def _run_leverage_dca_reserve_cycle(*, user: dict, config: dict, policy: dict, signals: dict,
                                          reserve_signals: dict | None) -> dict:
    """예비비 단계 투입 판정과 모의 체결.

    한 번 도달한 단계는 되돌리지 않는다(단계가 낮아져도 이미 투입한 예비비를 회수하지
    않음). UPRO만 매수하며(SOXL 특유 붕괴 위험 회피), 자동 매도는 만들지 않는다 —
    예비비는 장기 회복을 기다리는 포지션이라는 원 전략의 전제를 그대로 따른다.
    """
    if not config.get("reserve_enabled"):
        return {"status": "disabled"}
    if not reserve_signals or not reserve_signals.get("available"):
        return {"status": "signals_unavailable", "errors": (reserve_signals or {}).get("errors", [])}

    reserve_result = evaluate_reserve_deployment_stage(
        spx_drawdown_pct=signals["spx_drawdown_pct"], fed_funds_trend=reserve_signals["fed_funds_trend"],
        vix=signals["vix"], ten_year_yield_trend=reserve_signals["ten_year_yield_trend"],
        soxl_drawdown_pct=signals["soxl_drawdown_pct"], upro_drawdown_pct=signals["upro_drawdown_pct"],
        nfp_3m_avg=reserve_signals["nfp_3m_avg"], unemployment_rate=reserve_signals["unemployment_rate"],
        drawdown_days=signals.get("spx_drawdown_days"), policy=policy,
    )
    deployed_stage = int(config.get("reserve_deployed_stage", 0))
    target_stage = reserve_result["stage"] if reserve_result["eligible"] else deployed_stage
    if target_stage <= deployed_stage:
        return {"status": "no_action", "reserve_result": reserve_result, "deployed_stage": deployed_stage, "actions": []}

    reserve_cash_krw = float(policy["reserve_cash_krw"])
    deployed_krw = float(config.get("reserve_deployed_krw", 0.0))
    tranche_krw = min(reserve_cash_krw / 3 * (target_stage - deployed_stage), max(0.0, reserve_cash_krw - deployed_krw))
    if tranche_krw <= 0:
        return {"status": "no_action", "reserve_result": reserve_result, "deployed_stage": deployed_stage, "actions": []}

    reserve_strategy = await _get_or_create_leverage_dca_reserve_strategy(user)
    action = await _leverage_dca_auto_buy(user=user, strategy_id=reserve_strategy["_id"], ticker="UPRO", amount_krw=tranche_krw)
    if action["status"] != "filled":
        return {"status": "blocked", "reserve_result": reserve_result, "deployed_stage": deployed_stage, "actions": [action]}

    new_deployed_krw = deployed_krw + float(action.get("notional_krw", 0.0))
    await get_db().leverage_dca_configs.update_one(
        {"user_id": user["_id"]}, {"$set": {"reserve_deployed_stage": target_stage, "reserve_deployed_krw": new_deployed_krw}},
    )
    await append_execution_audit(user["_id"], "leverage_dca_reserve_deploy", "UPRO", {
        "stage": target_stage, "tranche_krw": round(tranche_krw, 2), "live_trading_authorized": False,
    })
    return {"status": "deployed", "reserve_result": reserve_result, "deployed_stage": target_stage, "actions": [action]}


async def run_leverage_dca_cycle_for_user(user: dict, signals: dict, reserve_signals: dict | None = None) -> dict:
    """한 사용자의 오늘 레버리지 DCA 매수/매도 결정과 예비비 단계 투입을 실행한다. 모의 체결만 만든다."""
    config = await _leverage_dca_config(user["_id"])
    if not config or not config.get("enabled"):
        return {"status": "disabled"}
    allowed, reason = await paper_submission_allowed(user["_id"])
    if not allowed:
        return {"status": "blocked", "reason": reason}
    if not signals.get("available"):
        return {"status": "signals_unavailable", "errors": signals.get("errors", [])}
    if signals.get("stale"):
        # 오래된 시세로는 매수·매도·예비비 판정을 하지 않는다. 오늘 실행을 선점하지 않으므로 다음 확인 때 다시 시도한다.
        return {"status": "signals_stale", "stale_tickers": signals.get("stale_tickers", []),
                "data_dates": signals.get("data_dates", {}), "errors": signals.get("errors", [])}
    today = today_kst_iso()
    if config.get("last_run_date") == today:
        return {"status": "already_run_today", "last_run_date": today}

    policy = _leverage_dca_policy(config)
    strategy = await _get_or_create_leverage_dca_strategy(user)
    positions = await _leverage_dca_position_state(user["_id"], strategy["_id"])
    cost_basis_krw, market_value_krw, pricing_errors = await _leverage_dca_valuation(positions)
    if pricing_errors:
        return {"status": "pricing_unavailable", "errors": pricing_errors}

    # 주문(Alpaca·자체 모의 체결)을 내기 전에 오늘 실행을 원자적으로 선점한다. 이후 어떤 단계가
    # 실패해도 같은 날 재시도가 주문을 다시 내지 않는다(2026-09-30: 자체 체결 저장 실패 뒤 5분마다
    # 재시도하며 Alpaca에서만 4번 매수된 사고의 재발 방지). 주문 전 일시 오류는 위에서 이미 반환했다.
    claimed = await get_db().leverage_dca_configs.update_one(
        {"user_id": user["_id"], "last_run_date": {"$ne": today}}, {"$set": {"last_run_date": today}},
    )
    if claimed.modified_count != 1:
        return {"status": "already_run_today", "last_run_date": today}

    sell_result = evaluate_sell_trigger(position_value_krw=market_value_krw, cost_basis_krw=cost_basis_krw, policy=policy)
    actions: list[dict] = []
    if sell_result["trigger"]:
        for ticker in LEVERAGE_DCA_TICKERS:
            quantity = positions.get(ticker, {}).get("quantity", 0.0)
            if quantity > 1e-8:
                actions.append(await _leverage_dca_auto_sell(user=user, strategy_id=strategy["_id"], ticker=ticker, quantity=quantity))
        result = {"status": "sold", "sell_trigger": sell_result, "actions": actions}
    else:
        has_soxl_position = positions.get("SOXL", {}).get("quantity", 0.0) > 1e-8
        soxl_position_return_pct = None
        if has_soxl_position:
            # 2026-10-08 사용자 결정: 보유 중 SOXL 추가 매수 중단은 내 SOXL 평가손익률(-20%) 기준이다.
            soxl_cost, soxl_value, soxl_errors = await _leverage_dca_valuation({"SOXL": positions["SOXL"]})
            if not soxl_errors and soxl_cost > 0:
                soxl_position_return_pct = (soxl_value / soxl_cost - 1) * 100
        raw_stop_state = evaluate_buy_stop_conditions(
            spx_drawdown_pct=signals["spx_drawdown_pct"], vix=signals["vix"],
            soxl_drawdown_pct=signals["soxl_drawdown_pct"], upro_drawdown_pct=signals["upro_drawdown_pct"], policy=policy,
            has_soxl_position=has_soxl_position, soxl_position_return_pct=soxl_position_return_pct,
        )
        # S&P/VIX발 매수 전면중지는 재개 조건(50일선 위 3일 연속 + VIX 재개 기준 미만)을
        # 충족하기 전까지 유지한다 — 오늘 지표만 보고 바로 재개하지 않는 히스테리시스.
        previously_stopped = bool(config.get("buy_stopped", False))
        resumed = evaluate_resume_condition(
            spx_above_ma_consecutive_days=signals.get("spx_above_ma_consecutive_days", 0), vix=signals["vix"], policy=policy,
        )
        effective_stop_all = resolve_effective_stop_all(
            previously_stopped=previously_stopped, raw_stop_all=raw_stop_state["stop_all"], resumed=resumed,
        )
        stop_state = {**raw_stop_state, "stop_all": effective_stop_all,
                     "hysteresis": {"previously_stopped": previously_stopped, "resumed": resumed}}
        if effective_stop_all and not raw_stop_state["stop_all"]:
            stop_state["reasons"] = [*raw_stop_state["reasons"], "재개 조건(50일선 위 3일 연속 + VIX 재개 기준 미만) 미충족으로 매수 중지를 유지합니다."]
        if effective_stop_all != previously_stopped:
            await get_db().leverage_dca_configs.update_one({"user_id": user["_id"]}, {"$set": {"buy_stopped": effective_stop_all}})

        allocation = compute_daily_buy_allocation(stop_state=stop_state, current_position_krw=cost_basis_krw, policy=policy)
        for ticker in LEVERAGE_DCA_TICKERS:
            amount_krw = allocation.get(ticker, 0.0)
            if amount_krw > 0:
                actions.append(await _leverage_dca_auto_buy(user=user, strategy_id=strategy["_id"], ticker=ticker, amount_krw=amount_krw))
        result = {"status": "bought" if actions else "no_action", "stop_state": stop_state, "allocation": allocation,
                  "sell_trigger": sell_result, "actions": actions}

    result["reserve"] = await _run_leverage_dca_reserve_cycle(user=user, config=config, policy=policy, signals=signals,
                                                              reserve_signals=reserve_signals)
    result["last_run_date"] = today
    return result


async def capture_leverage_dca_daily_log(user_id, result: dict, signals: dict, reserve_signals: dict | None = None) -> dict:
    now = _now()
    snapshot_date = now.astimezone(PAPER_SNAPSHOT_TIMEZONE).date().isoformat()
    log = {"user_id": user_id, "snapshot_date": snapshot_date, "captured_at": now, "signals": signals,
           "reserve_signals": reserve_signals, "result": result}
    if result.get("status") == "already_run_today":
        # 장마감 전 구간의 5분 간격 재확인이 그날 실제 판정 기록(매수·중지 이유)을 덮어쓰지 않게 한다.
        await get_db().leverage_dca_daily_logs.update_one(
            {"user_id": user_id, "snapshot_date": snapshot_date},
            {"$setOnInsert": log, "$set": {"last_checked_at": now}}, upsert=True,
        )
        return log
    await get_db().leverage_dca_daily_logs.update_one(
        {"user_id": user_id, "snapshot_date": snapshot_date}, {"$set": log}, upsert=True,
    )
    return log


def _strip_signal_series(raw_signals: dict) -> dict:
    """API·로그 응답이 무거워지지 않도록 판정용 원시 가격 배열을 제거한다."""
    return {key: value for key, value in raw_signals.items() if key not in {"spx_dates", "spx_closes"}}


async def _fetch_leverage_dca_reserve_signals(raw_signals: dict, *, needed: bool) -> dict:
    """예비비를 실제로 쓰는 사용자가 있을 때만 FRED 조회를 수행한다."""
    if not needed or not raw_signals.get("available"):
        return {"available": False, "errors": []}
    return await fetch_reserve_signals(raw_signals.get("spx_dates", []), raw_signals.get("spx_closes", []))


async def run_leverage_dca_daily_cycle() -> dict:
    """활성화된 모든 사용자의 레버리지 DCA 모의매매를 실행한다.

    실거래 주문이나 브로커 연동은 만들지 않으며, 결과는 사용자별 paper_fills와
    leverage_dca_daily_logs에만 남는다.
    """
    configs = await get_db().leverage_dca_configs.find({"enabled": True}).to_list(1_000)
    raw_signals = await fetch_market_signals()
    reserve_signals = await _fetch_leverage_dca_reserve_signals(
        raw_signals, needed=any(config.get("reserve_enabled") for config in configs),
    )
    signals = _strip_signal_series(raw_signals)
    results = []
    for config in configs:
        user = {"_id": config["user_id"]}
        try:
            result = await run_leverage_dca_cycle_for_user(user, signals, reserve_signals)
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}
        await capture_leverage_dca_daily_log(config["user_id"], result, signals, reserve_signals)
        results.append({"user_id": str(config["user_id"]), **result})
    return {"signals": signals, "reserve_signals": reserve_signals, "results": results}


@router.put("/leverage-dca/config")
async def update_leverage_dca_config(payload: LeverageDcaConfigUpdate, user: dict = Depends(require_leverage_dca_editor)) -> dict:
    """레버리지 DCA 자동 모의매매를 켜거나 끈다. 실거래 권한은 만들지 않는다."""
    now = _now()
    fields = {
        "user_id": user["_id"], "enabled": payload.enabled, "daily_buy_krw": payload.daily_buy_krw,
        "max_position_krw": payload.max_position_krw, "sell_net_profit_target_krw": payload.sell_net_profit_target_krw,
        "reserve_enabled": payload.reserve_enabled, "reserve_cash_krw": payload.reserve_cash_krw,
        "alpaca_enabled": payload.alpaca_enabled, "updated_at": now,
    }
    await get_db().leverage_dca_configs.update_one(
        {"user_id": user["_id"]}, {"$set": fields, "$setOnInsert": {"created_at": now, "buy_stopped": False,
                                                                     "reserve_deployed_stage": 0, "reserve_deployed_krw": 0.0}},
        upsert=True,
    )
    await append_execution_audit(user["_id"], "leverage_dca_config_updated", "leverage_dca_config", {
        "enabled": payload.enabled, "daily_buy_krw": payload.daily_buy_krw, "reserve_enabled": payload.reserve_enabled,
        "alpaca_enabled": payload.alpaca_enabled, "live_trading_authorized": False,
    })
    return {"enabled": payload.enabled, "daily_buy_krw": payload.daily_buy_krw, "max_position_krw": payload.max_position_krw,
            "sell_net_profit_target_krw": payload.sell_net_profit_target_krw, "reserve_enabled": payload.reserve_enabled,
            "reserve_cash_krw": payload.reserve_cash_krw, "alpaca_enabled": payload.alpaca_enabled,
            "alpaca_paper_configured": alpaca_paper_configured(), "updated_at": now.isoformat(),
            "policy": _leverage_dca_policy(fields),
            "disclaimer": "모의 체결(paper_fills)에만 적용됩니다. 실거래 주문이나 브로커 연동을 만들지 않습니다."}


@router.get("/leverage-dca/config")
async def get_leverage_dca_config(view: dict = Depends(leverage_dca_viewer)) -> dict:
    user = view["subject"]
    config = await _leverage_dca_config(user["_id"])
    return {"read_only": view["read_only"], "enabled": bool((config or {}).get("enabled", False)), "reserve_enabled": bool((config or {}).get("reserve_enabled", False)),
            "buy_stopped": bool((config or {}).get("buy_stopped", False)),
            "reserve_deployed_stage": int((config or {}).get("reserve_deployed_stage", 0)),
            "reserve_deployed_krw": float((config or {}).get("reserve_deployed_krw", 0.0)),
            "alpaca_enabled": bool((config or {}).get("alpaca_enabled", False)), "alpaca_paper_configured": alpaca_paper_configured(),
            "policy": _leverage_dca_policy(config),
            "updated_at": config["updated_at"].isoformat() if config and config.get("updated_at") else None,
            "disclaimer": "모의 체결에만 적용됩니다. 실거래 주문이나 브로커 연동을 만들지 않습니다."}


@router.get("/leverage-dca/status")
async def get_leverage_dca_status(view: dict = Depends(leverage_dca_viewer)) -> dict:
    user = view["subject"]
    config = await _leverage_dca_config(user["_id"])
    strategy = await get_db().strategies.find_one({"user_id": user["_id"], "type": "leverage_dca"})
    positions = await _leverage_dca_position_state(user["_id"], strategy["_id"]) if strategy else {}
    cost_basis_krw, market_value_krw, pricing_errors = await _leverage_dca_valuation(positions) if positions else (0.0, 0.0, [])
    reserve_strategy = await get_db().strategies.find_one({"user_id": user["_id"], "type": "leverage_dca_reserve"})
    reserve_positions = await _leverage_dca_position_state(user["_id"], reserve_strategy["_id"]) if reserve_strategy else {}
    reserve_cost_basis_krw, reserve_market_value_krw, reserve_pricing_errors = (
        await _leverage_dca_valuation(reserve_positions) if reserve_positions else (0.0, 0.0, [])
    )
    latest_log = await get_db().leverage_dca_daily_logs.find_one(
        {"user_id": user["_id"]}, {"_id": 0, "user_id": 0}, sort=[("snapshot_date", -1)],
    )
    if latest_log and latest_log.get("captured_at"):
        latest_log["captured_at"] = latest_log["captured_at"].isoformat()
    execution_schedule = get_next_execution_schedule()
    policy = _leverage_dca_policy(config)
    strategy_ids = [item["_id"] for item in (strategy, reserve_strategy) if item]
    holding_summary = await _leverage_dca_website_holding_summary(user["_id"], strategy_ids, policy) if strategy_ids else None
    alpaca_holding_summary = await _leverage_dca_alpaca_holding_summary(user["_id"], policy)
    return {
        "read_only": view["read_only"],
        "holding_summary": holding_summary, "alpaca_holding_summary": alpaca_holding_summary,
        "enabled": bool((config or {}).get("enabled", False)), "buy_stopped": bool((config or {}).get("buy_stopped", False)),
        "last_run_date": (config or {}).get("last_run_date"),
        "execution_schedule": execution_schedule,
        "strategy_id": str(strategy["_id"]) if strategy else None,
        "positions": [{"ticker": ticker, "quantity": item["quantity"], "average_cost_krw": round(item["average_cost_krw"], 4)}
                      for ticker, item in positions.items() if item["quantity"] > 1e-8],
        "cost_basis_krw": cost_basis_krw, "market_value_krw": market_value_krw, "pricing_errors": pricing_errors,
        "reserve": {
            "enabled": bool((config or {}).get("reserve_enabled", False)),
            "deployed_stage": int((config or {}).get("reserve_deployed_stage", 0)),
            "deployed_krw": float((config or {}).get("reserve_deployed_krw", 0.0)),
            "reserve_cash_krw": _leverage_dca_policy(config)["reserve_cash_krw"],
            "positions": [{"ticker": ticker, "quantity": item["quantity"], "average_cost_krw": round(item["average_cost_krw"], 4)}
                          for ticker, item in reserve_positions.items() if item["quantity"] > 1e-8],
            "cost_basis_krw": reserve_cost_basis_krw, "market_value_krw": reserve_market_value_krw,
            "pricing_errors": reserve_pricing_errors,
        },
        "latest_log": latest_log,
        "disclaimer": "모의 체결(paper_fills) 기준 현황입니다. 실거래 주문이나 브로커 연동을 만들지 않습니다.",
    }


@router.post("/leverage-dca/run-now")
async def run_leverage_dca_now(user: dict = Depends(require_leverage_dca_editor)) -> dict:
    """스케줄러를 기다리지 않고 지금 바로 한 사이클을 모의 실행한다(테스트용)."""
    config = await _leverage_dca_config(user["_id"])
    raw_signals = await fetch_market_signals()
    reserve_signals = await _fetch_leverage_dca_reserve_signals(raw_signals, needed=bool(config and config.get("reserve_enabled")))
    signals = _strip_signal_series(raw_signals)
    result = await run_leverage_dca_cycle_for_user(user, signals, reserve_signals)
    await capture_leverage_dca_daily_log(user["_id"], result, signals, reserve_signals)
    return {"signals": signals, "reserve_signals": reserve_signals, "result": result}


@router.delete("/leverage-dca/trades")
async def reset_leverage_dca_trades(user: dict = Depends(require_leverage_dca_editor)) -> dict:
    """테스트로 실행한 모의 체결 내역·포지션·일일 로그를 모두 지우고 처음 상태로 되돌린다.

    설정값(daily_buy_krw 등)은 유지하고, 체결 기록·포지션·정지/예비비 진행 상태·오늘 실행 여부만 초기화한다.
    실거래 주문이나 브로커 연동에는 영향을 주지 않는다.
    """
    db = get_db()
    strategies = await db.strategies.find(
        {"user_id": user["_id"], "type": {"$in": ["leverage_dca", "leverage_dca_reserve"]}}, {"_id": 1},
    ).to_list(10)
    strategy_ids = [doc["_id"] for doc in strategies]
    deleted_fills = 0
    deleted_alpaca_fills = 0
    if strategy_ids:
        fills_result = await db.paper_fills.delete_many({"user_id": user["_id"], "strategy_id": {"$in": strategy_ids}})
        deleted_fills = fills_result.deleted_count
        alpaca_fills_result = await db.leverage_dca_alpaca_fills.delete_many({"user_id": user["_id"], "strategy_id": {"$in": strategy_ids}})
        deleted_alpaca_fills = alpaca_fills_result.deleted_count
        await db.strategies.delete_many({"_id": {"$in": strategy_ids}})
    logs_result = await db.leverage_dca_daily_logs.delete_many({"user_id": user["_id"]})
    await db.leverage_dca_configs.update_one(
        {"user_id": user["_id"]},
        {"$set": {"buy_stopped": False, "reserve_deployed_stage": 0, "reserve_deployed_krw": 0.0},
         "$unset": {"last_run_date": ""}},
    )
    await append_execution_audit(user["_id"], "leverage_dca_reset", "leverage_dca_config", {
        "deleted_fills": deleted_fills, "deleted_alpaca_fills": deleted_alpaca_fills,
        "deleted_logs": logs_result.deleted_count, "live_trading_authorized": False,
    })
    return {"status": "reset", "deleted_fills": deleted_fills, "deleted_alpaca_fills": deleted_alpaca_fills,
            "deleted_logs": logs_result.deleted_count}


@router.get("/leverage-dca/trades")
async def list_leverage_dca_trades(view: dict = Depends(leverage_dca_viewer)) -> dict:
    """레버리지 DCA(주전략+예비비) 모의 체결 내역을 날짜·가격·수량 그대로 보여준다.

    ``items``는 홈페이지 자체 모의 체결(paper_fills) 기준이고, ``alpaca_items``는 Alpaca
    페이퍼 브로커에 독립적으로 낸 주문의 실제 체결 결과다. 두 목록은 서로 다른 계좌·엔진의
    결과라 수량·가격이 정확히 같지 않을 수 있다.
    """
    user = view["subject"]
    strategies = await get_db().strategies.find(
        {"user_id": user["_id"], "type": {"$in": ["leverage_dca", "leverage_dca_reserve"]}}, {"type": 1},
    ).to_list(10)
    strategy_kind = {doc["_id"]: doc["type"] for doc in strategies}
    if not strategy_kind:
        return {"read_only": view["read_only"], "items": [], "alpaca_items": [], "alpaca_paper_configured": alpaca_paper_configured()}
    strategy_ids = list(strategy_kind.keys())
    rows = await get_db().paper_fills.find(
        {"user_id": user["_id"], "strategy_id": {"$in": strategy_ids}},
    ).sort("filled_at", -1).to_list(1000)
    items = [{
        "date": row["filled_at"].astimezone(PAPER_SNAPSHOT_TIMEZONE).strftime("%Y-%m-%d %H:%M"),
        "ticker": row["ticker"], "direction": row["direction"],
        "quantity": round(float(row["quantity"]), 6), "fill_price": round(float(row["fill_price"]), 4),
        "notional_krw": round(float(row.get("notional_krw", row.get("notional", 0))), 2),
        "is_reserve": strategy_kind.get(row["strategy_id"]) == "leverage_dca_reserve",
        "source": row.get("source", "leverage_dca_auto"),
    } for row in rows if row.get("filled_at")]
    alpaca_rows = await get_db().leverage_dca_alpaca_fills.find(
        {"user_id": user["_id"], "strategy_id": {"$in": strategy_ids}},
    ).sort("filled_at", -1).to_list(1000)
    alpaca_items = [{
        "date": row["filled_at"].astimezone(PAPER_SNAPSHOT_TIMEZONE).strftime("%Y-%m-%d %H:%M"),
        "ticker": row["ticker"], "direction": row["direction"], "status": row["status"],
        "requested_quantity": round(float(row.get("requested_quantity", 0)), 6),
        "quantity": round(float(row.get("fill_quantity", 0)), 6), "fill_price": round(float(row.get("fill_price", 0)), 4),
        "notional_krw": round(float(row.get("notional_krw", 0)), 2),
        "is_reserve": strategy_kind.get(row["strategy_id"]) == "leverage_dca_reserve",
        "alpaca_order_id": row.get("alpaca_order_id"), "reason": row.get("reason"),
    } for row in alpaca_rows if row.get("filled_at")]
    return {"read_only": view["read_only"], "items": items, "alpaca_items": alpaca_items, "alpaca_paper_configured": alpaca_paper_configured()}
