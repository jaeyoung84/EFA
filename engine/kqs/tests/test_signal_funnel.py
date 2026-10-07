"""EINT-033 signal funnel: condition flags agree with the scanner, watch candidates exclude real signals."""

import unittest

import numpy as np
import pandas as pd

from korea_quant.reversion_scanner import next_session_projection
from korea_quant.strategy import bollinger_rsi_signals, rsi_obv_signals
from kqs_runtime import signal_funnel as funnel


def frame(closes, volumes=None):
    index = pd.bdate_range("2025-01-01", periods=len(closes))
    volumes = volumes if volumes is not None else [1_000_000] * len(closes)
    return pd.DataFrame({"Close": closes, "Volume": volumes, "Name": "x", "Market": "KOSPI"}, index=index)


class SignalFunnelTests(unittest.TestCase):
    def test_swing_flags_match_the_scanner_projection(self):
        rng = np.random.default_rng(7)
        for trial in range(40):
            walk = 100 * np.cumprod(1 + rng.normal(0, 0.02, 200))
            walk[-5:] *= np.linspace(1, 0.85 if trial % 2 else 1.05, 5)      # half end in a sharp drop
            prices = frame(list(walk))
            row = funnel._swing_row(prices)
            projected = bool(next_session_projection(prices["Close"], bollinger_rsi_signals)["entries"])
            self.assertEqual(row["new_entry"], projected, trial)
            if row["new_entry"]:                       # a new entry always meets both conditions
                self.assertTrue(row["below_band"] and row["rsi_ok"])

    def test_long_term_flags_match_the_scanner_projection(self):
        rng = np.random.default_rng(11)
        for trial in range(40):
            walk = 100 * np.cumprod(1 + rng.normal(0, 0.02, 200))
            volumes = list(rng.integers(500_000, 2_000_000, 200))
            prices = frame(list(walk), volumes)
            row = funnel._long_row(prices)
            projected = bool(next_session_projection(prices[["Close", "Volume"]], rsi_obv_signals)["entries"])
            self.assertEqual(row["new_entry"], projected, trial)
            if row["new_entry"]:
                self.assertTrue(row["rsi_cross"] and row["obv_rising"])


if __name__ == "__main__":
    unittest.main()
