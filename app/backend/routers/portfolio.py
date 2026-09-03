"""로그인 사용자별 개인 포트폴리오 저장 API.

주문·증권사 연결 전 단계에서 보유 수량과 취득 단가를 안전하게 관리한다.
평가 가격은 외부 시세 API의 책임으로 남기고, 이 라우터는 사용자가 입력한 장부
기준 정보만 사용자별로 분리해 보관한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from ..db import get_db
    from ..quote_cache import fetch_quote_cached
    from .auth import current_user
except ImportError:
    from db import get_db
    from quote_cache import fetch_quote_cached
    from routers.auth import current_user

router = APIRouter(prefix="/api/portfolio", tags=["개인 포트폴리오"])
TICKER_PATTERN = re.compile(r"^[A-Z0-9.^=-]{1,20}$")
RISK_PERIOD_PATTERN = re.compile(r"^(3mo|6mo|1y|2y|3y|5y)$")

# 개별 종목의 일별 가격 이력이 이보다 짧으면 통계적으로 신뢰하기 어려워 리스크 계산에서 제외한다.
MIN_TICKER_TRADING_DAYS = 60
# 보유 종목들의 공통 거래일이 이보다 적으면 포트폴리오 수익률 시계열을 만들지 않는다.
MIN_COMMON_TRADING_DAYS = 20
# 가격 이력이 확인된 자산의 비중이 이보다 낮으면 전체 리스크 수치 대신 제외 사유만 표시한다.
MIN_RISK_COVERAGE = 0.6


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HoldingInput(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    name: str = Field(default="", max_length=80)
    quantity: float = Field(gt=0, le=100_000_000)
    average_price: float = Field(ge=0, le=10_000_000_000)
    currency: str = Field(default="KRW", pattern=r"^[A-Z]{3}$")

class PortfolioUpdate(BaseModel):
    cash_krw: float = Field(default=0, ge=0, le=10_000_000_000_000)
    holdings: list[HoldingInput] = Field(default_factory=list, max_length=100)

def _serialize(document: dict | None) -> dict:
    if not document:
        return {"cash_krw": 0, "holdings": [], "updated_at": None}
    holdings = document.get("holdings", [])
    purchase_value_krw = sum(
        item["quantity"] * item["average_price"]
        for item in holdings
        if item.get("currency", "KRW") == "KRW"
    )
    return {
        "cash_krw": document.get("cash_krw", 0),
        "holdings": holdings,
        "holding_count": len(holdings),
        "purchase_value_krw": round(purchase_value_krw),
        "book_value_krw": round(document.get("cash_krw", 0) + purchase_value_krw),
        "updated_at": document.get("updated_at").isoformat() if document.get("updated_at") else None,
    }


def _compute_valuation(portfolio: dict) -> dict:
    """저장된 장부 정보를 서버 측 지연 시세로 평가한다.

    가격이 확인된 종목만 평가금액에 반영하고, 조회에 실패한 종목은 이유와
    함께 별도로 표시한다. /valuation과 /risk가 이 결과를 함께 사용한다.
    """
    cash_krw = float(portfolio.get("cash_krw", 0))
    holdings = portfolio.get("holdings", [])
    needs_fx = any(holding["currency"] == "USD" for holding in holdings)
    fx_quote = fetch_quote_cached("KRW=X") if needs_fx else None
    fx_rate = fx_quote["price"] if fx_quote and fx_quote["status"] == "ok" else None

    positions: list[dict] = []
    excluded: list[dict] = []
    value_krw = cash_krw
    cost_krw = cash_krw
    priced_count = 0
    for holding in holdings:
        quote = fetch_quote_cached(holding["ticker"])
        if quote["status"] != "ok":
            excluded.append({"ticker": holding["ticker"], "name": holding["name"], "reason": quote["reason"], "stage": "valuation"})
            positions.append({**holding, "status": "error", "reason": quote["reason"]})
            continue
        fx = fx_rate if holding["currency"] == "USD" else 1.0
        if holding["currency"] == "USD" and fx is None:
            reason = "USD/KRW 환율 조회 실패"
            excluded.append({"ticker": holding["ticker"], "name": holding["name"], "reason": reason, "stage": "valuation"})
            positions.append({**holding, "status": "error", "reason": reason})
            continue
        position_value_krw = quote["price"] * holding["quantity"] * fx
        position_cost_krw = holding["average_price"] * holding["quantity"] * fx
        value_krw += position_value_krw
        cost_krw += position_cost_krw
        priced_count += 1
        positions.append({
            **holding,
            "status": "ok",
            "price": quote["price"],
            "change_pct": quote["change_pct"],
            "quoted_at": quote["quoted_at"],
            "value_krw": round(position_value_krw, 2),
            "cost_krw": round(position_cost_krw, 2),
            "profit_krw": round(position_value_krw - position_cost_krw, 2),
        })

    all_priced = priced_count == len(holdings)
    profit_krw = value_krw - cost_krw
    return {
        "source": "yahoo_finance_delayed",
        "fetched_at": _now().isoformat(),
        "cash_krw": cash_krw,
        "fx_usd_krw": fx_rate,
        "available": all_priced,
        "priced_count": priced_count,
        "holding_count": len(holdings),
        "value_krw": round(value_krw, 2) if all_priced else None,
        "profit_krw": round(profit_krw, 2) if all_priced else None,
        "return_pct": round(profit_krw / cost_krw * 100, 2) if all_priced and cost_krw else None,
        "positions": positions,
        "excluded": excluded,
    }


def _composition(cash_krw: float, positions: list[dict]) -> dict:
    """현금을 포함한 평가금액 기준 비중, 최대 보유 비중, 통화 노출을 계산한다."""
    priced = [p for p in positions if p.get("status") == "ok"]
    total = cash_krw + sum(p["value_krw"] for p in priced)
    if total <= 0:
        return {"total_value_krw": 0.0, "max_position": None, "currency_exposure": {}, "weights": {}}

    items = []
    if cash_krw > 0:
        items.append({"ticker": "CASH", "name": "현금", "currency": "KRW", "value_krw": cash_krw})
    items += [{"ticker": p["ticker"], "name": p["name"] or p["ticker"], "currency": p["currency"], "value_krw": p["value_krw"]} for p in priced]

    weighted = sorted(
        ({**item, "weight_pct": round(item["value_krw"] / total * 100, 2)} for item in items),
        key=lambda item: item["value_krw"],
        reverse=True,
    )
    currency_totals: dict[str, float] = {}
    for item in items:
        currency_totals[item["currency"]] = currency_totals.get(item["currency"], 0.0) + item["value_krw"]

    return {
        "total_value_krw": round(total, 2),
        "max_position": weighted[0] if weighted else None,
        "currency_exposure": {ccy: round(v / total * 100, 2) for ccy, v in currency_totals.items()},
        "weights": {p["ticker"]: p["value_krw"] / total for p in priced},
    }


@router.get("")
async def get_portfolio(user: dict = Depends(current_user)) -> dict:
    document = await get_db().portfolios.find_one({"user_id": user["_id"]})
    return _serialize(document)


@router.put("")
async def update_portfolio(payload: PortfolioUpdate, user: dict = Depends(current_user)) -> dict:
    holdings = []
    seen_tickers: set[str] = set()
    for holding in payload.holdings:
        item = holding.dict()  # Pydantic v1/v2 공통 API로 배포 이미지 호환성을 유지한다.
        item["ticker"] = item["ticker"].strip().upper()
        item["name"] = item["name"].strip()
        if not TICKER_PATTERN.fullmatch(item["ticker"]):
            raise HTTPException(status_code=422, detail="티커는 영문·숫자와 . ^ = - 기호만 사용할 수 있습니다.")
        if item["ticker"] in seen_tickers:
            raise HTTPException(status_code=422, detail="같은 티커는 한 번만 입력할 수 있습니다.")
        seen_tickers.add(item["ticker"])
        holdings.append(item)
    now = _now()
    document = {
        "user_id": user["_id"],
        "cash_krw": payload.cash_krw,
        "holdings": holdings,
        "updated_at": now,
    }
    await get_db().portfolios.update_one(
        {"user_id": user["_id"]},
        {"$set": document, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return _serialize(document)


@router.get("/valuation")
async def get_portfolio_valuation(user: dict = Depends(current_user)) -> dict:
    """저장된 보유 자산을 서버에서 지연 시세로 평가한다.

    프런트엔드가 종목별로 개별 조회하던 로직을 서버 서비스로 옮기고,
    짧은 TTL 캐시로 동일 종목의 중복 Yahoo 요청을 줄인다.
    """
    document = await get_db().portfolios.find_one({"user_id": user["_id"]})
    if not document:
        raise HTTPException(status_code=404, detail="저장된 포트폴리오가 없습니다. 먼저 보유 자산을 등록하세요.")
    return _compute_valuation(document)


@router.get("/risk")
async def get_portfolio_risk(period: str = Query(default="1y"), user: dict = Depends(current_user)) -> dict:
    """실제 보유 종목의 일별 가격 이력으로 95% VaR·CVaR, 연환산 변동성, MDD를 계산한다.

    각 종목은 현지통화 기준 일간 수익률을 사용하고, 결합 비중은 현재
    지연 시세로 평가한 원화 환산 비중을 쓴다. 과거 환율 변동은 반영하지
    않으므로 USD 자산의 위험은 "현지통화 기준"이라는 점을 화면에 표시한다.
    데이터가 부족하거나 일부 종목 조회에 실패하면 전체 리스크 수치 대신
    제외 종목과 이유만 반환한다.
    """
    if not RISK_PERIOD_PATTERN.fullmatch(period):
        raise HTTPException(status_code=422, detail="period는 3mo, 6mo, 1y, 2y, 3y, 5y 중 하나여야 합니다.")

    document = await get_db().portfolios.find_one({"user_id": user["_id"]})
    if not document:
        raise HTTPException(status_code=404, detail="저장된 포트폴리오가 없습니다. 먼저 보유 자산을 등록하세요.")

    holdings = document.get("holdings", [])
    cash_krw = float(document.get("cash_krw", 0))
    if not holdings and cash_krw <= 0:
        raise HTTPException(status_code=422, detail="포트폴리오가 비어 있습니다. 보유 자산을 먼저 등록하세요.")

    valuation = _compute_valuation(document)
    composition = _composition(cash_krw, valuation["positions"])
    total_value_krw = composition["total_value_krw"]
    excluded: list[dict] = list(valuation["excluded"])

    if total_value_krw <= 0:
        return {
            "available": False,
            "reason": "지연 시세로 평가 가능한 자산이 없어 리스크를 계산할 수 없습니다.",
            "period": period,
            "excluded": excluded,
            "composition": composition,
        }

    import numpy as np
    import pandas as pd
    import yfinance as yf

    name_by_ticker = {holding["ticker"]: holding["name"] or holding["ticker"] for holding in holdings}
    priced_tickers = [p["ticker"] for p in valuation["positions"] if p.get("status") == "ok"]

    return_series: dict[str, "pd.Series"] = {}
    for ticker in priced_tickers:
        try:
            frame = yf.download(ticker, period=period, interval="1d", auto_adjust=True, progress=False, threads=False)
            close = frame["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            close = close.dropna()
        except Exception as exc:
            excluded.append({"ticker": ticker, "name": name_by_ticker.get(ticker, ticker), "reason": f"가격 이력 조회 실패: {exc}", "stage": "history"})
            continue
        if len(close) < MIN_TICKER_TRADING_DAYS:
            excluded.append({
                "ticker": ticker, "name": name_by_ticker.get(ticker, ticker),
                "reason": f"가격 이력이 {MIN_TICKER_TRADING_DAYS}거래일보다 짧습니다 ({len(close)}거래일).",
                "stage": "history",
            })
            continue
        daily_return = close.pct_change(fill_method=None).dropna()
        if daily_return.empty:
            excluded.append({"ticker": ticker, "name": name_by_ticker.get(ticker, ticker), "reason": "일간 수익률을 계산할 수 없습니다.", "stage": "history"})
            continue
        return_series[ticker] = daily_return

    weights = composition["weights"]
    included_tickers = [ticker for ticker in return_series if weights.get(ticker, 0) > 0]
    cash_weight = cash_krw / total_value_krw if total_value_krw else 0.0
    risk_coverage = sum(weights.get(ticker, 0.0) for ticker in included_tickers) + cash_weight

    if not included_tickers or risk_coverage < MIN_RISK_COVERAGE:
        return {
            "available": False,
            "reason": f"가격 이력을 확인할 수 있는 자산 비중이 {risk_coverage * 100:.0f}%로 부족해 전체 리스크 수치를 만들지 않았습니다.",
            "period": period,
            "risk_coverage_pct": round(risk_coverage * 100, 1),
            "excluded": excluded,
            "composition": composition,
        }

    aligned = pd.concat({ticker: return_series[ticker] for ticker in included_tickers}, axis=1, join="inner").dropna()
    if len(aligned) < MIN_COMMON_TRADING_DAYS:
        return {
            "available": False,
            "reason": f"보유 종목들의 공통 거래일이 {MIN_COMMON_TRADING_DAYS}일보다 적어 리스크를 계산할 수 없습니다.",
            "period": period,
            "excluded": excluded,
            "composition": composition,
        }

    weight_vector = pd.Series({ticker: weights[ticker] for ticker in included_tickers})
    portfolio_returns = aligned.mul(weight_vector, axis=1).sum(axis=1)

    confidence = 0.95
    var_return = float(np.percentile(portfolio_returns, (1 - confidence) * 100))
    tail = portfolio_returns[portfolio_returns <= var_return]
    cvar_return = float(tail.mean()) if not tail.empty else var_return
    annual_volatility = float(portfolio_returns.std(ddof=1) * (252 ** 0.5))
    wealth_index = (1 + portfolio_returns).cumprod()
    drawdown = wealth_index / wealth_index.cummax() - 1
    mdd = float(drawdown.min())

    return {
        "available": True,
        "source": "yahoo_finance_daily_close",
        "currency_basis": "local",
        "period": period,
        "as_of": pd.Timestamp(aligned.index[-1]).date().isoformat(),
        "common_trading_days": int(len(aligned)),
        "risk_coverage_pct": round(risk_coverage * 100, 1),
        "confidence": confidence,
        "var_95_pct": round(abs(var_return) * 100, 2),
        "cvar_95_pct": round(abs(cvar_return) * 100, 2),
        "var_95_amount_krw": round(abs(var_return) * total_value_krw, 0),
        "cvar_95_amount_krw": round(abs(cvar_return) * total_value_krw, 0),
        "annual_volatility_pct": round(annual_volatility * 100, 2),
        "mdd_pct": round(abs(mdd) * 100, 2),
        "total_value_krw": total_value_krw,
        "max_position": composition["max_position"],
        "currency_exposure": composition["currency_exposure"],
        "excluded": excluded,
    }
