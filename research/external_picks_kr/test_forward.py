"""KR-PRO-CONSENSUS-v1 실행기 회귀 테스트(합성 자료): 진입일, 확정·해시, 비용 포함 수익, 결측 월, 판정."""
import json
import sqlite3
import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forward as fw  # noqa: E402

DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-12-01", "2026-12-02", "2027-01-04"]


def krx(tmp_path, prices):
    db = tmp_path / "krx.sqlite3"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE daily_prices (trade_date TEXT, symbol TEXT, market TEXT, open REAL, close REAL, listed_shares REAL)")
        for d in DAYS:
            c.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?)", (d, "999999", "KOSPI", 1, 1, 1))
        for sym, series in prices.items():
            for d, (o, cl) in series.items():
                c.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?)", (d, sym, "KOSPI" if sym not in fw.ETFS.values() else "ETF", o, cl, 100))
    return db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "PRIVATE", tmp_path / "private")
    monkeypatch.setattr(fw, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(fw, "check_lock", lambda: None)
    return tmp_path


def lists(month, v, l, v_at="2026-10-31T15:00:00Z", l_at="2026-10-31T16:00:00Z"):
    return [{"source": fw.SOURCES["V"], "as_of": f"{month}-01", "created_at": v_at, "raw_sha256": "a", "items": [{"ticker": f"{c}.KS"} for c in v]},
            {"source": fw.SOURCES["L"], "as_of": f"{month}-01", "created_at": l_at, "raw_sha256": "b", "items": [{"ticker": f"A{c}"} for c in l]}]


def test_entry_session_before_and_after_open():
    assert fw.entry_session("2026-11", ["2026-11-01T10:00:00Z", "2026-11-01T23:59:00Z"], DAYS) == ("2026-11-02", False)  # 11/2 08:59 KST
    assert fw.entry_session("2026-11", ["2026-11-02T00:30:00Z", "2026-11-01T00:00:00Z"], DAYS) == ("2026-11-03", True)  # 09:30 KST → 다음날
    assert fw.entry_session("2026-11", ["2026-11-30T00:00:00Z"], DAYS) == (None, True)                                   # 한 명단만


def test_freeze_intersection_holdover_and_ledger(env):
    db = krx(env, {})
    for month, v, l in (("2026-11", ["000001", "000002"], ["000002", "000003"]), ("2026-12", ["000002", "000004"], ["000002", "000004"])):
        p = env / f"{month}.json"
        at = "2026-11-30T20:00:00Z" if month == "2026-12" else "2026-10-31T15:00:00Z"
        p.write_text(json.dumps(lists(month, v, l, at, at)))
        fw.cmd_freeze(Namespace(month=month, lists=str(p), krx=str(db)))
    snap = json.loads((env / "private" / "2026-12" / "snapshot.json").read_text())
    assert snap["S"] == ["000002", "000004"] and snap["holdover"]["S"] == ["000002"] and snap["entry_session"] == "2026-12-01"
    ledger = json.loads((env / "ledger.json").read_text())
    assert [e["n_S"] for e in ledger] == [1, 2] and len(ledger[0]["snapshot_sha256"]) == 64
    with pytest.raises(fw.Refused):
        fw.cmd_freeze(Namespace(month="2026-12", lists=str(env / "2026-12.json"), krx=str(db)))
    with pytest.raises(fw.Refused):
        fw.cmd_freeze(Namespace(month="2026-10", lists=str(env / "2026-12.json"), krx=str(db)))


def test_run_arm_costs_and_rebalance(env):
    prices = {"000001": {d: (100, 100) for d in DAYS}, "000002": {"2026-11-02": (100, 100), "2026-11-03": (110, 110), "2026-11-04": (110, 110),
                                                                 "2026-12-01": (120, 120), "2026-12-02": (120, 120), "2027-01-04": (120, 120)}}
    db = krx(env, prices)
    bars = fw.load_bars(db, set(prices), "2026-11-01")
    r, daily, issues = fw.run_arm([("2026-11-02", ["000001", "000002"]), ("2026-12-01", ["000001"])], DAYS, bars, "2027-01-04")
    first = (0.5 * 1 + 0.5 * 1.2) * (1 - fw.BUY) - 1                      # 진입 비용만
    assert abs(r[0] - first) < 1e-9 and not issues
    w2 = 0.6 / 1.1                                                          # 12/1 교체: 000002 전량 매도, 000001 비중 확대
    cost2 = (1 - (1 - w2)) * fw.BUY + w2 * fw.SELL
    assert abs(r[1] - ((1 - cost2) * (1 - fw.SELL) - 1)) < 1e-9          # 마지막 청산 비용 포함
    assert len(daily) == 5


def test_judge_rules():
    def ev(s, v, l, k200=1.0, mdd=-5.0, missing=()):
        arm = lambda m, cum=10.0, dd=-3.0: {"monthly_pct": m, "cum_pct": cum, "daily_mdd_pct": dd, "issues": []}  # noqa: E731
        return {"missing_months": list(missing), "open_last_period": False,
                "arms": {"S": arm(s, 50.0, -3.0), "V": arm(v), "L": arm(l), "KODEX200": arm([0] * 24, k200, mdd)}}
    up = [3.0 + (i % 3) * 0.2 for i in range(24)]
    assert fw.judge(ev(up[:12], up[:12], up[:12]))["verdict"] == "not_due"
    assert fw.judge(ev(up, up, up, missing=["2027-03"]))["verdict"] == "INVALID"
    assert fw.judge(ev(up, [x + 1 for x in up], [0.0] * 24))["verdict"] == "REJECT"
    assert fw.judge(ev(up, [0.0] * 24, [0.0] * 24))["verdict"] == "PASS_REVIEW_ONLY"
    assert fw.judge(ev(up, [0.0] * 24, [0.0] * 24, k200=80.0))["verdict"] == "INCONCLUSIVE"     # 시장 조건 미달


def test_freeze_then_evaluate_end_to_end(env):
    flat = {d: (100, 100) for d in DAYS}
    db = krx(env, {"000001": flat, "000002": flat, "000003": flat, "069500": flat, "252650": flat})
    for month in ("2026-11", "2026-12"):
        p = env / f"{month}.json"
        at = "2026-10-31T15:00:00Z" if month == "2026-11" else "2026-11-30T15:00:00Z"
        p.write_text(json.dumps(lists(month, ["000001", "000002"], ["000002", "000003"], at, at)))
        fw.cmd_freeze(Namespace(month=month, lists=str(p), krx=str(db)))
    ev = fw.evaluate(db)
    assert ev["months"] == ["2026-11", "2026-12"] and not ev["missing_months"] and ev["open_last_period"] is False
    assert ev["arms"]["KODEX200"]["cum_pct"] == 0 and ev["arms"]["S"]["cum_pct"] < 0     # 가격 불변 → 주식 팔은 비용만큼 손실
    assert ev["n_S"] == [1, 1] and fw.judge(ev)["verdict"] == "not_due"
