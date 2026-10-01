"""EINT-022 추천 종목 섹터 표시: KSIC → 섹터(가장 긴 접두어), 섹터 순환 구역 연결, 로그인 필요."""
import pytest

from app.backend import main
from app.backend.stock_sector import sector_for_ksic, sector_status

ROTATION = {"available": True, "latest_week": "2026-09-30", "sectors": [
    {"key": "semis", "quadrant": "leading", "quadrant_4w_ago": "improving", "relative_4w_pct": 11.3},
    {"key": "steel", "quadrant": "lagging", "quadrant_4w_ago": "leading", "relative_4w_pct": -8.5},
]}


@pytest.mark.parametrize("ksic,key", [
    ("2612", "semis"), ("264", "it_hw"), ("2629", "it_hw"), ("283", "machinery"), ("282", "battery"),
    ("24122", "steel"), ("31321", "defense"), ("31111", "shipbuilding"), ("30121", "auto"), ("63120", "internet"),
    ("62021", "it_sw"), ("21100", "healthcare"), ("20423", "cosmetics"), ("20111", "energy_chem"), ("70113", "healthcare"),
    ("64992", None), ("", None), (None, None),
])
def test_longest_prefix_mapping(ksic, key):
    assert sector_for_ksic(ksic) == key


def test_status_joins_rotation_quadrant():
    semis = sector_status("000660", "2612", ROTATION)
    assert (semis["sector_name"], semis["quadrant"], semis["quadrant_label"], semis["quadrant_4w_ago"]) == ("반도체", "leading", "주도", "improving")
    assert sector_status("105560", "64992", ROTATION)["note"] == "업종 분류 없음"
    assert sector_status("000000", None, ROTATION)["note"] == "업종 정보 없음"
    assert sector_status("086520", "282", ROTATION)["note"] == "섹터 순환 자료 없음"   # 2차전지 not in this rotation
    assert sector_status("000660", "2612", {"available": False})["quadrant"] is None


@pytest.mark.asyncio
async def test_endpoint_requires_login_and_limits_symbols(client, make_user, monkeypatch):
    monkeypatch.setattr(main, "_stock_ksic", lambda code: {"000660": "2612", "005160": "24122"}.get(code))
    monkeypatch.setattr(main, "read_sector_rotation", lambda: ROTATION)
    assert (await client.get("/api/market/stock-sectors", params={"symbols": "000660"})).status_code == 401
    headers = await make_user("sector-badge@example.com")
    data = (await client.get("/api/market/stock-sectors", headers=headers, params={"symbols": "000660,005160,bad!,000660"})).json()
    assert [(i["symbol"], i["sector_name"], i["quadrant_label"]) for i in data["items"]] == [("000660", "반도체", "주도"), ("005160", "철강", "부진")]
