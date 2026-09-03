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
import io
from datetime import datetime, timezone
from pathlib import Path
import re

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ..db import get_db
    from ..quote_cache import fetch_quote_cached
    from .auth import current_user
except ImportError:
    from db import get_db
    from quote_cache import fetch_quote_cached
    from routers.auth import current_user

router = APIRouter(prefix="/api/strategy", tags=["전략"])

TICKER_PATTERN = re.compile(r"^[A-Z0-9.^=-]{1,20}$")
BACKTEST_PERIOD_PATTERN = re.compile(r"^(6mo|1y|2y|3y|5y)$")
MIN_USABLE_TRADING_DAYS = 20
STRATEGY_TYPE_PATTERN = r"^(real_backtest|virtual_backtest|lean)$"

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

    frame = yf.download(ticker, period=period, interval="1d", auto_adjust=True, progress=False, threads=False)
    close = frame["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    return close.dropna()


def _run_ma_crossover(close, fast_ma: int, slow_ma: int, transaction_cost_bps: float, slippage_bps: float):
    """실제 종가 시리즈에 MA 크로스오버 전략을 적용해 일간 전략 수익률과 신호를 계산한다."""
    import pandas as pd

    df = pd.DataFrame({"Close": close})
    df["MA_fast"] = df["Close"].rolling(fast_ma).mean()
    df["MA_slow"] = df["Close"].rolling(slow_ma).mean()
    df["Signal"] = (df["MA_fast"] > df["MA_slow"]).astype(float)
    df["Position"] = df["Signal"].shift(1).fillna(0)
    df["Ret"] = df["Close"].pct_change()
    df["Turnover"] = df["Position"].diff().abs().fillna(0)
    cost_rate = (transaction_cost_bps + slippage_bps) / 10000
    df["Strat_Ret"] = df["Position"] * df["Ret"] - df["Turnover"] * cost_rate
    return df.dropna(subset=["MA_fast", "MA_slow", "Ret"])


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
    benchmark_ticker: str = Field(default="", max_length=20)


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


def _serialize_strategy(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "type": doc["type"],
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
        "filled_at": doc["filled_at"].isoformat() if doc.get("filled_at") else None,
    }


async def _get_owned_strategy(strategy_id: str, user: dict) -> dict:
    try:
        oid = ObjectId(strategy_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="유효하지 않은 전략 ID입니다.") from exc
    doc = await get_db().strategies.find_one({"_id": oid, "user_id": user["_id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="전략을 찾을 수 없습니다.")
    return doc


async def _get_owned_proposal(proposal_id: str, user: dict) -> dict:
    try:
        oid = ObjectId(proposal_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="유효하지 않은 주문 제안 ID입니다.") from exc
    doc = await get_db().order_proposals.find_one({"_id": oid, "user_id": user["_id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="주문 제안을 찾을 수 없습니다.")
    return doc


@router.on_event("startup")
async def _ensure_strategy_indexes() -> None:
    db = get_db()
    await db.strategies.create_index([("user_id", 1), ("created_at", -1)])
    await db.order_proposals.create_index([("user_id", 1), ("created_at", -1)])
    await db.order_proposals.create_index([("strategy_id", 1), ("status", 1)])
    await db.paper_fills.create_index([("user_id", 1), ("filled_at", -1)])


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
    elif payload.type == "virtual_backtest":
        required = {"fast_ma", "slow_ma", "n_days", "volatility"}
        if not required.issubset(payload.params.keys()):
            raise HTTPException(status_code=422, detail="가상 백테스트 저장에는 fast_ma/slow_ma/n_days/volatility가 필요합니다.")
        if "metrics" not in payload.snapshot:
            raise HTTPException(status_code=422, detail="백테스트 결과(snapshot.metrics)가 필요합니다.")
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

    try:
        close = _download_close_series(ticker, req.period)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{ticker} 가격 이력을 가져오지 못했습니다: {exc}") from exc
    if len(close) < req.slow_ma + 20:
        raise HTTPException(status_code=422, detail=f"{ticker}의 가격 이력이 부족합니다. 장기 이동평균({req.slow_ma}일)을 계산하려면 최소 {req.slow_ma + 20}거래일이 필요합니다.")

    df = _run_ma_crossover(close, req.fast_ma, req.slow_ma, req.transaction_cost_bps, req.slippage_bps)
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
        "benchmark_ticker": benchmark_ticker,
        "benchmark_label": benchmark_label,
        "n_trades": n_trades,
        "metrics": metrics,
        "benchmark_metrics": benchmark_metrics,
        "current_position": "매수 보유" if last_position > 0 else "미보유(현금)",
        "last_signal_change_at": last_cross_at,
        "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
    }


@router.get("/fills")
async def list_fills(user: dict = Depends(current_user)) -> dict:
    rows = await get_db().paper_fills.find({"user_id": user["_id"]}).sort("filled_at", -1).to_list(200)
    return {"items": [_serialize_fill(row) for row in rows]}


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
    doc = await _get_owned_strategy(strategy_id, user)
    if doc["type"] != "real_backtest":
        raise HTTPException(status_code=422, detail="주문 제안은 실제 가격 백테스트로 저장한 전략에서만 만들 수 있습니다.")

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

    now = _now()
    proposal = {
        "user_id": user["_id"],
        "strategy_id": doc["_id"],
        "ticker": ticker,
        "direction": direction,
        "basis": basis,
        "reference_price": reference_price,
        "status": "pending",
        "created_at": now,
        "decided_at": None,
    }
    result = await get_db().order_proposals.insert_one(proposal)
    proposal["_id"] = result.inserted_id
    return _serialize_proposal(proposal)


@router.get("/{strategy_id}/proposals")
async def list_proposals(strategy_id: str, user: dict = Depends(current_user)) -> dict:
    doc = await _get_owned_strategy(strategy_id, user)
    rows = await get_db().order_proposals.find({"strategy_id": doc["_id"], "user_id": user["_id"]}).sort("created_at", -1).to_list(100)
    return {"items": [_serialize_proposal(row) for row in rows]}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: str, payload: ProposalApproveRequest, user: dict = Depends(current_user)) -> dict:
    """제안을 승인하고 모의 체결을 기록한다. 실제 보유자산(portfolios)은 변경하지 않는다."""
    proposal = await _get_owned_proposal(proposal_id, user)
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail="이미 처리된 제안입니다.")

    quote = fetch_quote_cached(proposal["ticker"])
    if quote["status"] != "ok":
        raise HTTPException(status_code=502, detail=f"승인 시점 시세를 가져오지 못해 모의 체결을 만들 수 없습니다: {quote['reason']}")

    now = _now()
    fill = {
        "user_id": user["_id"],
        "strategy_id": proposal["strategy_id"],
        "proposal_id": proposal["_id"],
        "ticker": proposal["ticker"],
        "direction": proposal["direction"],
        "quantity": payload.quantity,
        "fill_price": quote["price"],
        "filled_at": now,
    }
    fill_result = await get_db().paper_fills.insert_one(fill)
    await get_db().order_proposals.update_one({"_id": proposal["_id"]}, {"$set": {"status": "approved", "decided_at": now}})
    fill["_id"] = fill_result.inserted_id
    return _serialize_fill(fill)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: str, user: dict = Depends(current_user)) -> dict:
    proposal = await _get_owned_proposal(proposal_id, user)
    if proposal["status"] != "pending":
        raise HTTPException(status_code=409, detail="이미 처리된 제안입니다.")
    now = _now()
    await get_db().order_proposals.update_one({"_id": proposal["_id"]}, {"$set": {"status": "rejected", "decided_at": now}})
    proposal["status"] = "rejected"
    proposal["decided_at"] = now
    return _serialize_proposal(proposal)
