"""EINT-027: 권장 매수 수량 — ATR이 엔진 계산과 같은지, 위험 단위·수량·비중 제한·현금 부족, API(로그인·자산 없음·실계산).

한투에는 접속하지 않는다(가짜 서비스).
"""
import math
import random

import pandas as pd
import pytest

from app.backend import kis_quotes
from app.backend import position_sizing as sizing
from app.backend.kis_quotes import KisQuotes
from app.backend.routers import kqs as kqs_router


def synthetic_bars(n=60, seed=7):
    rng = random.Random(seed)
    close, bars = 10000.0, []
    for i in range(n):
        open_ = close * (1 + rng.uniform(-0.02, 0.02))
        close = max(100.0, open_ * (1 + rng.uniform(-0.04, 0.04)))
        high, low = max(open_, close) * (1 + rng.uniform(0, 0.02)), min(open_, close) * (1 - rng.uniform(0, 0.02))
        bars.append({"date": f"2026-{7 + i // 28:02d}-{i % 28 + 1:02d}", "open": open_, "high": high, "low": low, "close": close})
    return bars


def test_wilder_atr_matches_the_engine_indicator():
    from kqs_runtime.pullback_v4 import compute_indicators
    bars = synthetic_bars()
    frame = pd.DataFrame({"Open": [b["open"] for b in bars], "High": [b["high"] for b in bars],
                          "Low": [b["low"] for b in bars], "Close": [b["close"] for b in bars], "Volume": 1.0},
                         index=pd.date_range("2026-07-01", periods=len(bars), freq="D"))
    engine_atr = float(compute_indicators(frame)["ATR"].iloc[-1])
    assert math.isclose(sizing.wilder_atr(bars), engine_atr, rel_tol=1e-12)
    assert sizing.wilder_atr(bars[:13]) is None


def test_risk_unit_uses_v4_stop_or_assumed_two_atr_for_reversion():
    v4 = sizing.risk_unit("pullback_v4", v4_atr=800, atr14=None)
    assert v4["per_share"] == 1200 and v4["basis"] == "v4_stop"
    swing = sizing.risk_unit("swing", v4_atr=None, atr14=500)
    assert swing["per_share"] == 1000 and swing["basis"] == "reversion_atr" and "청산 규칙 아님" in swing["label"]
    assert sizing.risk_unit("long_term", v4_atr=None, atr14=None) is None
    assert sizing.risk_unit("pullback_v4", v4_atr=0, atr14=None) is None


def test_size_position_risk_budget_weight_cap_and_cash():
    plain = sizing.size_position(equity=10_000_000, cash=5_000_000, price=50_000, unit=2_000, risk_pct=1, max_position_pct=35)
    assert (plain["quantity"], plain["amount_krw"], plain["weight_pct"], plain["risk_krw"]) == (50, 2_500_000, 25.0, 100_000)
    assert plain["capped_by"] is None and plain["cash_short"] is False and plain["cash_quantity"] == 100
    capped = sizing.size_position(equity=10_000_000, cash=1_000_000, price=100_000, unit=500, risk_pct=1, max_position_pct=35)
    assert capped["quantity"] == 35 and capped["capped_by"] == "max_position"       # 위험 기준 200주 → 비중 35%로 35주
    assert capped["cash_short"] is True and capped["cash_quantity"] == 10
    assert sizing.clamp_risk_pct(9) == 3.0 and sizing.clamp_risk_pct(0) == 0.25 and sizing.clamp_risk_pct("x") == 1.0


PAYLOAD = {
    "schema_version": "kqs-recommendations/v2", "engine": {"data_as_of": "2026-10-01"},
    "signals": [
        {"signal_id": "v4-1", "action": "entry", "selection_outcome": "selected", "signal_type": "pullback_v4", "selection_rank": 1,
         "symbol": "034020", "name": "두산에너빌리티", "market": "KOSPI", "reasons": [],
         "features": {"close": 81400, "atr": 2000}},
        {"signal_id": "sw-1", "action": "entry", "selection_outcome": "selected", "signal_type": "swing", "selection_rank": 1,
         "symbol": "012330", "name": "현대모비스", "market": "KOSPI", "reasons": [],
         "features": {"close": 271000, "bb_middle20": 283500, "rsi14": 27.4}},
    ],
}


class FakeKis(KisQuotes):
    def __init__(self):
        super().__init__("key", "secret")
        self.daily_calls = []

    async def quotes(self, tickers):
        prices = {"034020.KS": 80000.0, "012330.KS": 270000.0}
        return {t: ({"status": "ok", "price": prices[t], "source": "kis_rest"} if t in prices else {"status": "error", "reason": "x"})
                for t in tickers}

    async def daily_bars(self, code, start, end):
        self.daily_calls.append((code, start, end))
        bars = [{"date": f"2026-09-{d:02d}", "open": 100.0, "high": 110.0, "low": 90.0, "close": 100.0} for d in range(1, 31)]
        return bars + [{"date": "2026-10-01", "open": 100, "high": 110, "low": 90, "close": 100},
                       {"date": "2026-10-02", "open": 100, "high": 900, "low": 10, "close": 100}]   # 기준일 이후는 쓰지 않음


@pytest.fixture
def fake_kis(monkeypatch):
    service = FakeKis()
    kis_quotes._set_service_for_tests(service)
    kqs_router._SIZING_ATR_CACHE.clear()
    monkeypatch.setattr(kqs_router, "read_local_recommendations", lambda: PAYLOAD)
    yield service
    kis_quotes._set_service_for_tests(None)
    kqs_router._SIZING_ATR_CACHE.clear()


@pytest.mark.asyncio
async def test_position_sizing_api(client, make_user, fake_kis):
    assert (await client.get("/api/kqs/position-sizing")).status_code == 401
    headers = await make_user("sizing@test.com")
    empty = (await client.get("/api/kqs/position-sizing", headers=headers)).json()
    assert empty["available"] is False and "내 자산" in empty["reason"]
    put = await client.put("/api/portfolio", headers=headers, json={"cash_krw": 10_000_000, "holdings": []})
    assert put.status_code == 200, put.text
    data = (await client.get("/api/kqs/position-sizing?risk_pct=1", headers=headers)).json()
    assert data["available"] and data["equity_krw"] == 10_000_000 and data["max_position_pct"] == 35.0
    v4 = data["items"]["v4-1"]          # 위험 단위 1.5×2000 = 3000원 → 100,000 / 3000 = 33주
    assert (v4["basis"], v4["risk_unit_krw"], v4["quantity"], v4["amount_krw"]) == ("v4_stop", 3000.0, 33, 2_640_000)
    assert v4["price_basis"] == "현재가" and v4["risk_krw"] == 99_000
    swing = data["items"]["sw-1"]       # ATR(14)=20 (고가-저가 20, 기준일 이후 일봉 제외) → 위험 단위 40원
    assert swing["basis"] == "reversion_atr" and swing["atr"] == 20.0 and swing["risk_unit_krw"] == 40.0
    assert swing["quantity"] == 12 and swing["capped_by"] == "max_position"   # 위험 기준 2500주 → 비중 35%로 12주
    assert fake_kis.daily_calls == [("012330", "20260524", "20261001")]
    await client.get("/api/kqs/position-sizing?risk_pct=2", headers=headers)
    assert len(fake_kis.daily_calls) == 1                                       # ATR은 기준일마다 한 번
    clamped = (await client.get("/api/kqs/position-sizing?risk_pct=50", headers=headers)).json()
    assert clamped["risk_pct"] == 3.0
