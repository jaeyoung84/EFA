"""US-MOM-v1 runner wiring on synthetic data: month-end signals at the next open, equal weights, membership,
SMA50/ATR exits, one-shot guard, lock check and verdict rules. Manual run:
PYTHONPATH=engine/kqs/src python -m pytest research/us_momentum_v1/test_run.py"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("us_mom_run", Path(__file__).with_name("run.py"))
mom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mom)
base = mom.base

DAYS = pd.bdate_range("2016-01-04", "2019-12-31")
TICKERS = {"UPA": 0.0012, "UPB": 0.0010, "UPC": 0.0011, "FLAT": 0.0, "DOWN": -0.0008, "OUT": 0.0012}


def make_snapshot(path, intervals):
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, split REAL, PRIMARY KEY (symbol, date));
        CREATE TABLE spy (date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL);
        CREATE TABLE intervals (symbol TEXT, start TEXT, end TEXT, excluded INTEGER, PRIMARY KEY (symbol, start));
        CREATE TABLE coverage (date TEXT PRIMARY KEY, members INTEGER, with_bar INTEGER, pct REAL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);""")
    rng = np.random.default_rng(7)
    for ticker, drift in TICKERS.items():
        closes = 50 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(len(DAYS))))
        c.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)", [
            (ticker, d.date().isoformat(), x * 0.999, x * 1.01, x * 0.99, x, 1e6, 1.0) for d, x in zip(DAYS, closes)])
    spy = 200 * np.exp(np.cumsum(0.0005 + 0.008 * rng.standard_normal(len(DAYS))))
    c.executemany("INSERT INTO spy VALUES (?,?,?,?,?,?)", [(d.date().isoformat(), x, x * 1.005, x * 0.995, x, 1e7) for d, x in zip(DAYS, spy)])
    c.executemany("INSERT INTO intervals VALUES (?,?,?,?)", intervals)
    c.executemany("INSERT INTO coverage VALUES (?,?,?,?)", [("2018-01-31", 6, 6, 100.0)])
    hashes = {t: base.table_sha(c, t) for t in base.SNAPSHOT_TABLES}
    c.executemany("INSERT INTO meta VALUES (?,?)", [("table_sha256", json.dumps(hashes)), ("membership_id", "test")])
    c.commit()
    c.close()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(mom, "RESULT", tmp_path / "RESULT.json")
    monkeypatch.setattr(mom, "HERE", tmp_path)
    monkeypatch.setattr(mom, "WINDOW", ("2017-03-01", "2019-12-31"))
    lock = tmp_path / "LOCK.json"
    monkeypatch.setattr(mom, "LOCK", lock)
    lock.write_text(json.dumps({k: base.sha256(p) for k, p in mom.LOCKED.items()}))
    snap = tmp_path / "snap.sqlite3"
    make_snapshot(snap, [(t, "2016-01-04", None, 0) for t in TICKERS if t != "OUT"] + [("OUT", "2016-01-04", None, 1)])
    return snap


def test_monthly_rebalance_rules(isolated):
    result = mom.main(str(isolated))
    trades = pd.read_csv(isolated.parent / "TRADES.csv", parse_dates=["entry_date", "exit_date"])
    months = pd.read_csv(isolated.parent / "REBALANCES.csv", parse_dates=["date"])
    assert len(trades) > 0 and "OUT" not in set(trades["ticker"])                # 제외 구간 종목은 진입 없음
    assert set(trades["exit_reason"]) <= {"sma50", "atr_stop", "rebalance", "window_end", "forced_close"}
    assert months["date"].iloc[0] == pd.Timestamp("2017-03-01")                  # 2월 말 신호 -> 창 첫 시가
    entries = trades["entry_date"].unique()
    first_days = {d for d in pd.bdate_range("2017-03-01", "2019-12-31") if (d - pd.offsets.BDay(1)).month != d.month}
    assert all(pd.Timestamp(d) in first_days for d in entries)                   # 진입은 월초 첫 거래일(전월 말 신호)에만
    assert result["verdict"] in {"PASS", "FAIL", "REJECT"} and set(result["checks"]) >= {"cagr_vs_spy", "bootstrap_pf_q"}
    assert "without_stress" in result["diagnostics"]
    with pytest.raises(mom.RunRefused, match="RESULT.json exists"):
        mom.main(str(isolated))


def test_cash_accounting_closes(isolated):
    data = base.load_snapshot(isolated)
    prepared, spy, last_bar = mom.prepare(data)
    sim = mom.simulate(prepared, spy, last_bar, pd.Timestamp("2017-03-01"), pd.Timestamp("2019-12-31"))
    final = sim["equity"]["equity"].iloc[-1]
    assert abs(final - (mom.CAPITAL + sim["trades"]["pnl_net"].sum())) < 1e-6     # 모든 현금 흐름이 거래로 설명된다
    assert (sim["months"]["held"] <= sim["months"]["target"] + 6).all()


def test_trade_metrics():
    df = pd.DataFrame({"ret": [10.0, -5.0, 3.0], "exit_date": pd.to_datetime(["2018-02-01", "2018-08-01", "2019-03-01"]),
                       "pnl_net": [1, -1, 1]})
    m = mom.trade_metrics(df)
    assert m["trades"] == 3 and m["profit_factor"] == round(13 / 5, 4) and m["window_count"] == 3


def test_lock_mismatch_refused(isolated):
    mom.LOCK.write_text(json.dumps({"run": "0" * 64}))
    with pytest.raises(mom.RunRefused, match="lock mismatch"):
        mom.main(str(isolated))


def test_verdict_rules():
    ok = {"metrics": {"trades": 100, "mean_net_return_pct": 1.0}, "checks": {"a": True},
          "diagnostics": {"delisting_forced_close": 0, "portfolio": {"cagr_pct": 5.0}}}
    assert mom.verdict(ok, []) == "PASS"
    assert mom.verdict({**ok, "checks": {"a": False}}, []) == "FAIL"
    assert mom.verdict({**ok, "diagnostics": {"delisting_forced_close": 0, "portfolio": {"cagr_pct": -1.0}}}, []) == "REJECT"
    assert mom.verdict(ok, ["2018-01-31"]) == "INVALID"
