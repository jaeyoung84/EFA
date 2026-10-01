"""투자 홈페이지 범위 회귀 테스트.

교육·퀴즈·챗봇·ML 데모를 지운 뒤에도 메뉴·라우트·화면 파일이 서로 맞고,
지운 API가 다시 살아나지 않으며, 시세 실패 시 가짜 데이터를 만들지 않는지 확인한다.
"""
import re
import sys
import types
from pathlib import Path

import pytest

from app.backend import main

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app" / "frontend"
APP_JS = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")


def _route_ids() -> set[str]:
    block = APP_JS[APP_JS.index("const routes = {"):APP_JS.index("};", APP_JS.index("const routes = {"))]
    return set(re.findall(r"^\s*'([a-z0-9-]+)':\s*\{", block, re.M))


def test_every_imported_view_file_exists():
    imports = re.findall(r"from '\./([^'?]+)", APP_JS)
    assert imports
    for module in imports:
        assert (FRONTEND / "js" / module).is_file(), module


@pytest.mark.parametrize("menu", ["index.html", "pages/partials/sidebar-nav.html"])
def test_menu_links_only_existing_routes(menu):
    views = set(re.findall(r'data-view="([a-z0-9-]+)"', (FRONTEND / menu).read_text(encoding="utf-8")))
    assert views and views <= _route_ids()


def test_nav_sections_cover_every_route_except_home():
    block = APP_JS[APP_JS.index("const NAV_SECTIONS = {"):APP_JS.index("};", APP_JS.index("const NAV_SECTIONS = {"))]
    in_sections = set(re.findall(r"'([a-z0-9-]+)'", block))
    assert in_sections == _route_ids() - {"home"}


def test_removed_education_and_demo_screens_stay_removed():
    routes = _route_ids()
    for removed in ("quiz-home", "vocabulary-exam", "rag-chat", "learn-11", "technical-chart", "valuation",
                    "backtest", "pipeline", "quant-lean", "cross-validation", "huggingface", "tax-accounting"):
        assert removed not in routes
    assert "leverage-dca" in routes and "my-strategies" in routes


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/quiz/days", "/api/rag/status", "/api/lex/status", "/api/learn/doc/03", "/api/search?q=etf",
    "/api/quant/lean", "/api/tax/sample", "/api/ml/decision-boundary", "/api/macro/kospi-ex/meta",
    "/api/market/volume-cloud", "/api/market/dividend-history",
])
async def test_removed_apis_are_gone(client, path):
    response = await client.get(path)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_market_candle_failure_returns_reason_not_synthetic_prices(client, monkeypatch):
    async def no_data(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setitem(sys.modules, "yfinance", types.ModuleType("yfinance"))  # CI has no yfinance
    monkeypatch.setattr(main, "get_daily_ohlcv", no_data)
    response = await client.get("/api/home/market-candle?market=kospi&period=3mo")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "데이터 없음" in detail and "provider down" in detail and "조회 시각" in detail


def test_record_research_tabs_have_loaders_and_every_loader_exists():
    source = (FRONTEND / "js" / "views" / "strategyList.js").read_text(encoding="utf-8")
    tabs = set(re.findall(r'data-stg-tab="([a-z]+)"', source))
    panels = set(re.findall(r'data-stg-panel="([a-z]+)"', source))
    block = source[source.index("const STG_TAB_LOADERS = {"):source.index("};", source.index("const STG_TAB_LOADERS = {"))]
    loaders = dict(re.findall(r"^\s+([a-z]+): \(\) => \{ (.*) \},$", block, re.M))
    assert tabs == panels == set(loaders) and len(tabs) == 6
    called = {name for body in loaders.values() for name in re.findall(r"(load[A-Za-z]+)\(", body)}
    defined = set(re.findall(r"(?:async )?function (load[A-Za-z]+)\(", source))
    assert called and called <= defined
    assert source.count('class="stg-tab-panel"') == 6
