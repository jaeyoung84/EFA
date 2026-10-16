"""Codex 수정: CODX-KRLC-001 (2026-10-16), 파일 전체."""
import unittest

from audit import compound, drawdown, require_coverage


class ArithmeticTests(unittest.TestCase):
    def test_compounding_is_not_sum(self):
        self.assertAlmostEqual(compound([10, -10]), -1)

    def test_drawdown_includes_initial_capital(self):
        self.assertAlmostEqual(drawdown([-10, 5]), -10)
        self.assertAlmostEqual(drawdown([10, -20, 25]), -20)

    def test_missing_member_cannot_be_silently_reweighted(self):
        with self.assertRaises(ValueError):
            require_coverage({"012510", "000270"}, {"000270"})
        require_coverage({"000270"}, {"000270"})


if __name__ == "__main__":
    unittest.main()
