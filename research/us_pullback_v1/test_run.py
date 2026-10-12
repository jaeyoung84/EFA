"""US-PB-v1 runner wiring on synthetic data: membership mask, US costs, one-shot, lock and snapshot checks,
and equivalence with the plain v4 engine when the adaptations are neutral. Manual run (outside the suites):
PYTHONPATH=engine/kqs/src python -m pytest research/us_pullback_v1/test_run.py"""
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

import run as runner
from run import pv

DAYS = pd.bdate_range("2016-01-04", "2019-06-28")
TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "XXX"]


def series(seed: int, drift: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(drift + 0.015 * rng.standard_normal(len(DAYS))))


def make_snapshot(path, intervals):
    connection = sqlite3.connect(path)
    connection.executescript("""CREATE TABLE bars (symbol TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, split REAL, PRIMARY KEY (symbol, date));
        CREATE TABLE spy (date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL);
        CREATE TABLE intervals (symbol TEXT, start TEXT, end TEXT, excluded INTEGER, PRIMARY KEY (symbol, start));
        CREATE TABLE coverage (date TEXT PRIMARY KEY, members INTEGER, with_bar INTEGER, pct REAL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);""")
    for n, ticker in enumerate(TICKERS):
        closes = series(n, 0.0009)
        connection.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)", [
            (ticker, d.date().isoformat(), c * 0.998, c * 1.01, c * 0.99, c, 1e6, 1.0) for d, c in zip(DAYS, closes)])
    spy = series(99, 0.0006)
    connection.executemany("INSERT INTO spy VALUES (?,?,?,?,?,?)", [
        (d.date().isoformat(), c * 0.999, c * 1.005, c * 0.995, c, 1e7) for d, c in zip(DAYS, spy)])
    connection.executemany("INSERT INTO intervals VALUES (?,?,?,?)", intervals)
    connection.executemany("INSERT INTO coverage VALUES (?,?,?,?)", [("2018-01-31", 5, 5, 100.0)])
    hashes = {t: runner.table_sha(connection, t) for t in runner.SNAPSHOT_TABLES}
    connection.executemany("INSERT INTO meta VALUES (?,?)", [("table_sha256", json.dumps(hashes)), ("membership_id", "test")])
    connection.commit()
    connection.close()


ALL_MEMBERS = [(t, "2016-01-04", None, 0) for t in TICKERS]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RESULT", tmp_path / "RESULT.json")
    monkeypatch.setattr(runner, "HERE", tmp_path)
    monkeypatch.setattr(runner, "WINDOW", ("2016-11-01", "2019-06-28"))
    for name in ("ENTRY_COST", "EXIT_COST", "entry_signals"):
        monkeypatch.setattr(pv, name, getattr(pv, name))       # restored after each test
    lock = tmp_path / "LOCK.json"
    monkeypatch.setattr(runner, "LOCK", lock)
    lock.write_text(json.dumps({k: runner.sha256(p) for k, p in runner.LOCKED.items()}))
    return tmp_path


def test_membership_mask_and_us_costs(isolated):
    snap = isolated / "snap.sqlite3"
    make_snapshot(snap, [("AAA", "2016-01-04", None, 0), ("BBB", "2016-01-04", None, 0), ("CCC", "2016-01-04", None, 0),
                         ("DDD", "2018-01-02", None, 0), ("EEE", "2016-01-04", "2017-06-01", 0), ("XXX", "2016-01-04", None, 1)])
    result = runner.main(str(snap))
    trades = pd.read_csv(isolated / "TRADES.csv", parse_dates=["entry_date"])
    assert len(trades) > 0 and result["metrics"]["trades"] == len(trades)
    assert "XXX" not in set(trades["ticker"])                                      # 공백 표시 구간 제외
    assert (trades.loc[trades["ticker"] == "DDD", "entry_date"] > "2018-01-02").all()   # 편입 전 진입 없음
    assert (trades.loc[trades["ticker"] == "EEE", "entry_date"] <= "2017-06-02").all()  # 편출 뒤 신규 진입 없음
    assert pv.ENTRY_COST == runner.US_ENTRY_COST and result["costs"]["exit"] == runner.US_EXIT_COST
    assert result["verdict"] in {"PASS", "FAIL", "REJECT"} and len(result["trades_sha256"]) == 64
    with pytest.raises(runner.RunRefused, match="RESULT.json exists"):
        runner.main(str(snap))


def test_neutral_adaptations_equal_the_plain_engine(isolated):
    snap = isolated / "snap.sqlite3"
    make_snapshot(snap, ALL_MEMBERS)
    data = runner.load_snapshot(snap)
    spy = runner.market_frame(data["spy"])
    frames = pv.frames_from_history(runner.split_adjusted(data["bars"]), spy)
    start, end = pd.Timestamp("2016-11-01"), pd.Timestamp("2019-06-28")
    _, plain = pv.run_period(frames, spy, start, end, runner.CONFIG, initial_capital=runner.CAPITAL)
    runner.install_us_adaptations(data["intervals"])
    pv.ENTRY_COST, pv.EXIT_COST = (1.5 + 5) / 10_000, (1.5 + 18 + 5) / 10_000      # 비용만 원래대로
    _, wrapped = pv.run_period(frames, spy, start, end, runner.CONFIG, initial_capital=runner.CAPITAL)
    pd.testing.assert_frame_equal(plain, wrapped)


def test_refuses_on_lock_or_snapshot_mismatch(isolated):
    snap = isolated / "snap.sqlite3"
    make_snapshot(snap, ALL_MEMBERS)
    connection = sqlite3.connect(snap)
    connection.execute("UPDATE bars SET close = close * 1.01 WHERE symbol = 'AAA' AND date = '2018-03-01'")
    connection.commit()
    connection.close()
    with pytest.raises(runner.RunRefused, match="snapshot hash"):
        runner.main(str(snap))
    runner.LOCK.write_text(json.dumps({"run": "0" * 64}))
    with pytest.raises(runner.RunRefused, match="lock mismatch"):
        runner.main(str(snap))


def test_verdict_rules():
    base = {"metrics": {"trades": 100, "mean_net_return_pct": 0.5}, "checks": {"a": True}, "diagnostics": {"delisting_forced_close": 0}}
    assert runner.verdict(base, []) == "PASS"
    assert runner.verdict({**base, "checks": {"a": False}}, []) == "FAIL"
    assert runner.verdict({**base, "metrics": {"trades": 100, "mean_net_return_pct": -0.1}}, []) == "REJECT"
    assert runner.verdict(base, ["2018-01-31"]) == "INVALID"
    assert runner.verdict({**base, "diagnostics": {"delisting_forced_close": 3}}, []) == "INVALID"
