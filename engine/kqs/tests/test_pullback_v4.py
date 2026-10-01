"""EINT-017: Pullback v4 port equals the archived research code; live scan/exit helpers keep its rules."""

import hashlib
import unittest
import warnings

import numpy as np
import pandas as pd

import pullback_v4_fixture as fx
from kqs_runtime import pullback_v4 as v4


def _fingerprint(equity, trades):
    return {"trades": len(trades),
            "exit_reasons": dict(sorted(trades["exit_reason"].value_counts().to_dict().items())),
            "trades_sha16": hashlib.sha256(trades[v4.TRADE_COLUMNS].to_csv(index=False).encode()).hexdigest()[:16],
            "equity_sha16": hashlib.sha256(equity.to_csv().encode()).hexdigest()[:16]}


class PullbackV4BacktestEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ohlcv, cls.benchmarks, cls.market_map = fx.build()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.equity, cls.trades = v4.run_period(cls.ohlcv, cls.benchmarks["KOSPI"], fx.PERIOD_START, fx.PERIOD_END,
                                                   v4.FIXED_CONFIG, benchmarks=cls.benchmarks, market_map=cls.market_map)

    def test_matches_archived_strategy_validation_v4(self):
        self.assertEqual(_fingerprint(self.equity, self.trades), fx.GOLDEN)

    def test_every_exit_reason_is_exercised_and_valid(self):
        reasons = set(self.trades["exit_reason"])
        self.assertTrue(reasons <= v4.VALID_EXIT_REASONS)
        self.assertEqual(reasons, v4.VALID_EXIT_REASONS)

    def test_portfolio_invariants(self):
        self.assertLessEqual(int(self.equity["positions"].max()), v4.FIXED_CONFIG["max_positions"])
        self.assertTrue((self.equity["cash"] >= 0).all())
        self.assertTrue((self.trades["holding_days"] <= v4.FIXED_CONFIG["holding_limit"]).all())
        self.assertTrue((self.trades["exit_date"] >= self.trades["entry_date"]).all())

    def test_pre_registered_parameters_are_unchanged(self):
        self.assertEqual(v4.RANK_WEIGHTS, {"dist_ema10": 0.40, "adx": 0.30, "rsi": 0.30})
        self.assertEqual((v4.CONFIG["max_positions"], v4.CONFIG["holding_limit"], v4.CONFIG["atr_multiple"],
                          v4.CONFIG["trailing_atr_multiple"], v4.CONFIG["market_sma"], v4.CONFIG["min_turnover"]),
                         (5, 15, 1.5, 2.0, 50, 5_000_000_000))
        self.assertTrue(v4.CONFIG["regime_exit"])


class PullbackV4Rules(unittest.TestCase):
    def test_ranking_prefers_near_anchor_strong_trend_and_cooler_rsi(self):
        ranked = v4.rank_candidates([
            {"ticker": "A", "dist_ema10": 0.01, "adx": 40, "rsi": 52},
            {"ticker": "B", "dist_ema10": 0.05, "adx": 22, "rsi": 69},
            {"ticker": "C", "dist_ema10": 0.03, "adx": 30, "rsi": 60},
        ])
        self.assertEqual(list(ranked["ticker"]), ["A", "C", "B"])

    def test_exit_priority_stop_first_then_limit_trailing_regime(self):
        row = pd.Series({"ATR": 1.0})
        pos = {"stop_price": 95.0, "highest_close": 110.0}
        cfg = v4.FIXED_CONFIG
        self.assertEqual(v4.exit_signal(row, pos, 20, 90.0, cfg, regime_off=True), (True, "stop"))
        self.assertEqual(v4.exit_signal(row, pos, 15, 120.0, cfg, regime_off=True), (True, "holding_limit"))
        self.assertEqual(v4.exit_signal(row, pos, 3, 107.0, cfg, regime_off=True), (True, "trailing"))
        self.assertEqual(v4.exit_signal(row, pos, 3, 120.0, cfg, regime_off=True), (True, "market_regime"))
        self.assertEqual(v4.exit_signal(row, pos, 3, 120.0, cfg, regime_off=False), (False, ""))

    def test_regime_gate_missing_data_blocks_entry_but_keeps_position(self):
        gf = pd.DataFrame({"Close": [10.0], v4.SMA_COL: [np.nan]}, index=pd.DatetimeIndex(["2024-01-02"]))
        self.assertFalse(v4.gate_ok(gf, pd.Timestamp("2024-01-02")))
        self.assertFalse(v4.regime_off(gf, pd.Timestamp("2024-01-02")))
        self.assertFalse(v4.gate_ok(gf, pd.Timestamp("2024-01-03")))
        self.assertFalse(v4.regime_off(gf, pd.Timestamp("2024-01-03")))

    def test_market_of_follows_transfer_segments(self):
        mmap = {"X": [("2021-01-01", "KOSDAQ"), ("2023-03-02", "KOSPI")]}
        self.assertEqual(v4.market_of(mmap, "X", pd.Timestamp("2022-12-30")), "KOSDAQ")
        self.assertEqual(v4.market_of(mmap, "X", pd.Timestamp("2023-03-02")), "KOSPI")
        with self.assertRaises(RuntimeError):
            v4.validate_market_map(["Y"], {"Y": [("2021-01-01", "KOSDAQ"), ("2022-01-01", "KOSPI"),
                                                ("2023-01-02", "KOSDAQ")]}, {"KOSPI": None, "KOSDAQ": None})

    def test_halted_zero_price_rows_are_dropped_like_the_archived_loader(self):
        frame = pd.DataFrame({"Open": [10, 0, 11], "High": [11, 0, 12], "Low": [9, 0, 10], "Close": [10, 0, 11],
                              "Volume": [1, 0, 1]}, index=pd.bdate_range("2024-01-01", periods=3))
        self.assertEqual(len(v4.clean_ohlcv(frame)), 2)


class PullbackV4Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ohlcv, cls.benchmarks, cls.market_map = fx.build()

    def test_scan_matches_the_backtest_signal_and_gate_on_that_day(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            found = False
            for as_of in pd.bdate_range("2023-02-01", "2023-06-30"):
                scan = v4.scan_candidates(self.ohlcv, self.benchmarks, self.market_map, as_of)
                if scan.empty:
                    continue
                found = True
                for _, row in scan.iterrows():
                    frame = self.ohlcv[row["ticker"]]
                    prepared = v4.compute_indicators(v4.attach_warmup(frame, as_of, as_of))
                    self.assertTrue(bool(v4.entry_signals({row["ticker"]: prepared}, v4.FIXED_CONFIG)[row["ticker"]]["full"].loc[as_of]))
                    bench = self.benchmarks[row["market"]].loc[:as_of].copy()
                    bench[v4.SMA_COL] = bench["Close"].rolling(50).mean()
                    self.assertTrue(v4.gate_ok(bench, as_of))
                self.assertTrue(scan["score"].is_monotonic_decreasing)
                break
            self.assertTrue(found, "fixture should produce at least one live candidate")

    def test_holding_exit_reports_holding_limit_and_levels(self):
        ticker = next(iter(self.ohlcv))
        frame = self.ohlcv[ticker]
        entry_date = frame.index[400]
        as_of = frame.index[400 + 14]
        state = v4.holding_exit(frame, self.benchmarks["KOSPI"], entry_date, float(frame["Open"].iloc[400]), as_of)
        self.assertEqual(state["holding_days"], 15)
        self.assertEqual(state["state"], "exit_signal")
        self.assertIn(state["reason"], {"stop", "holding_limit", "trailing", "market_regime"})
        early = v4.holding_exit(frame, self.benchmarks["KOSPI"], entry_date, float(frame["Open"].iloc[400]), frame.index[401])
        self.assertEqual(early["holding_days"], 2)
        self.assertIn(early["state"], {"hold", "exit_signal"})


if __name__ == "__main__":
    unittest.main()
