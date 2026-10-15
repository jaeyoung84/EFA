"""EINT-040 선물·수급 3개월 보관: 응답 해석, 날짜별 병합, 실패 시 기존 유지, 보관 기간, 장 마감 미결제약정, 공개 API."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.backend import futures_flows as ff
from app.backend.db import get_db

KST = ZoneInfo("Asia/Seoul")


def flow_row(day, frgn, amount, index=6008.81):
    return {"stck_bsop_date": day, "bstp_nmix_prpr": str(index), "bstp_nmix_prdy_ctrt": "-0.44", "frgn_ntby_qty": str(frgn),
            "frgn_ntby_tr_pbmn": str(amount), "orgn_ntby_qty": "-1983", "orgn_ntby_tr_pbmn": "-470000", "prsn_ntby_qty": "-1363",
            "pe_fund_ntby_vol": "12", "pe_fund_ntby_tr_pbmn": "3"}


class FakeKis:
    enabled = True

    def __init__(self, futures=None, spot=None, fail=()):
        self.futures, self.spot, self.fail, self.calls = futures or [], spot or [], set(fail), []

    async def _rest_get(self, path, tr, params):
        self.calls.append(tr)
        if tr in self.fail:
            raise RuntimeError("network")
        if tr == ff.FLOW_TR:
            return {"rt_cd": "0", "output": self.futures if params["FID_INPUT_ISCD_1"] == "K2I" else self.spot}
        if tr == ff.PRICE_TR:
            return {"rt_cd": "0", "output1": {"futs_shrn_iscd": "A01612", "hts_kor_isnm": "F 202612", "futs_prpr": "861.30",
                                              "hts_otst_stpl_qty": "154419", "otst_stpl_qty_icdc": "6654", "mrkt_basis": "0.60",
                                              "basis": "3.74", "futs_last_tr_date": "20261210"},
                    "output3": {"bstp_nmix_prpr": "860.70"}}
        if tr == ff.CHART_TR:
            return {"rt_cd": "0", "output2": [{"stck_bsop_date": "20261014", "futs_prpr": "944.20", "futs_oprc": "950.80",
                                               "futs_hgpr": "972.55", "futs_lwpr": "930.75", "acml_vol": "126572", "acml_tr_pbmn": "30220393648"}]}
        raise AssertionError(tr)


def test_parse_flow_rows_keeps_units_and_investor_keys():
    out = ff.parse_flow_rows([flow_row("20261014", 3557, 844524), {"stck_bsop_date": "bad"}])
    day = out["2026-10-14"]
    assert list(out) == ["2026-10-14"] and day["index"] == 6008.81
    assert day["investors"]["foreign"] == {"qty": 3557.0, "amount_mn": 844524.0}
    assert day["investors"]["private_fund"] == {"qty": 12.0, "amount_mn": 3.0}      # 사모펀드는 _vol 이름


def test_merge_never_overwrites_with_empty():
    old = {"futures_flows": {"index": 1}, "futures_bar": {"close": 2}}
    assert ff.merge_day(old, futures={}, spot=None, bar=None) == old
    assert ff.merge_day(old, spot={"index": 3})["spot_flows"] == {"index": 3}


@pytest.mark.asyncio
async def test_collect_merges_days_records_close_open_interest_and_prunes():
    db = get_db()
    await db[ff.COLLECTION].insert_one({"_id": "2026-06-01", "date": "2026-06-01"})          # 보관 기간(92일) 밖
    kis = FakeKis(futures=[flow_row("20261015", 5544, 1210112), flow_row("20261014", 3557, 844524)],
                  spot=[flow_row("20261015", -12992, -3792685), flow_row("20261014", -4060, -900000)])
    now = datetime(2026, 10, 15, 16, 20, tzinfo=KST)
    result = await ff.collect_futures_flows(now, service=kis, db=db)
    assert result["status"] == "ok" and result["days"] == 2 and result["pruned"] == 1 and result["open_interest_recorded"]
    today = await db[ff.COLLECTION].find_one({"_id": "2026-10-15"})
    assert today["close_snapshot"]["open_interest"] == 154419 and today["close_snapshot"]["kospi200"] == 860.70
    yday = await db[ff.COLLECTION].find_one({"_id": "2026-10-14"})
    assert "close_snapshot" not in yday                                          # 과거 날짜엔 오늘 값을 쓰지 않음
    assert yday["futures_bar"]["close"] == 944.20 and yday["bar_contract"]["code"] == "A01612"
    assert yday["spot_flows"]["investors"]["foreign"]["amount_mn"] == -900000


@pytest.mark.asyncio
async def test_intraday_or_failed_collection_keeps_existing_data():
    db = get_db()
    ok = FakeKis(futures=[flow_row("20261015", 5544, 1)], spot=[flow_row("20261015", 1, 1)])
    await ff.collect_futures_flows(datetime(2026, 10, 15, 16, 20, tzinfo=KST), service=ok, db=db)
    before = await db[ff.COLLECTION].find_one({"_id": "2026-10-15"})
    broken = FakeKis(fail={ff.FLOW_TR, ff.PRICE_TR})
    result = await ff.collect_futures_flows(datetime(2026, 10, 15, 18, 40, tzinfo=KST), service=broken, db=db)
    assert result["status"] == "error" and len(result["errors"]) == 3
    assert await db[ff.COLLECTION].find_one({"_id": "2026-10-15"}) == before
    intraday = FakeKis(futures=[flow_row("20261016", 1, 1)], spot=[])
    result = await ff.collect_futures_flows(datetime(2026, 10, 16, 11, 0, tzinfo=KST), service=intraday, db=db)
    assert not result["open_interest_recorded"]                                   # 장중 값은 미결제약정으로 기록하지 않음


@pytest.mark.asyncio
async def test_public_api_returns_sorted_rows(client):
    db = get_db()
    for day in ("2026-10-14", "2026-10-13"):
        await db[ff.COLLECTION].insert_one({"_id": day, "date": day, "futures_flows": {"index": 1}})
    res = await client.get("/api/market/futures-flows")
    body = res.json()
    assert res.status_code == 200 and [r["date"] for r in body["rows"]] == ["2026-10-13", "2026-10-14"]
    assert body["retain_days"] == 92 and body["units"]["futures_qty"] == "계약"
