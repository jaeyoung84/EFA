"""Synthetic regression checks for CODX-MA-001.

Codex 수정: CODX-MA-001 (2026-10-16), 파일 전체.
"""

import importlib.util
import unittest
from pathlib import Path

import pandas as pd


PATH = Path(__file__).with_name("run.py")
SPEC = importlib.util.spec_from_file_location("codx_ma_001", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class Sma60ExploratoryTests(unittest.TestCase):
    def test_costs_are_applied_on_both_sides(self):
        self.assertLess(MODULE.costed_return(100, 100), 0)
        expected = 100 * (1 - 0.0005) * (1 - 0.00015 - 0.0018) / (
            100 * (1 + 0.0005) * (1 + 0.00015)
        ) - 1
        self.assertAlmostEqual(MODULE.costed_return(100, 100), expected)

    def test_filter_is_checked_on_observation_day_before_next_open(self):
        frame = pd.DataFrame({"Close": [100.0] * 60 + [80.0, 81.0], "Volume": [100.0] * 62})
        baseline, _, sma = MODULE.signals(frame, False)
        filtered, _, _ = MODULE.signals(frame, True)
        self.assertEqual(len(baseline), len(filtered))
        self.assertTrue(pd.notna(sma.iloc[-1]))
        self.assertFalse(bool(filtered.iloc[0]))

    def test_blocked_exit_moves_forward_one_session(self):
        exits = pd.Series([False, True, False, False])
        blocked = pd.Series([False, True, False, False])
        self.assertEqual(MODULE.delayed_exits(exits, blocked).tolist(), [False, False, True, False])


if __name__ == "__main__":
    unittest.main()

