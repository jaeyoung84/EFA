"""Synthetic migration regressions only. No consumed research data/backend fixture."""

import hashlib
import importlib.util
import runpy
import shutil
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from kqs_runtime.cli import main
from kqs_runtime.inputs import normalize_symbol, parse_date, validated_inputs
from kqs_runtime.recommendations import APPROVED_CONFIG, generate_recommendations, selection_audit
from kqs_runtime.provenance import code_identity
from kqs_runtime.sqlite_reader import fill_halted_trading_days, read_inputs

ROOT = Path(__file__).resolve().parents[3]


def synthetic_inputs(kind="swing"):
    if kind == "long_term":
        values = [100.0] * 280 + [float(v) for v in range(99, 79, -1)] + [85.0]
    else:
        values = [100.0] * 299 + [85.0 if kind == "swing" else 100.0]
    dates = pd.bdate_range("2025-01-01", periods=len(values))
    frames = []
    for symbol, market in [("005930", "KOSPI"), ("035420", "KOSDAQ")]:
        volume = [1000.0] * len(values)
        if kind == "long_term":
            volume[-1] = 1_000_000.0
        frames.append(pd.DataFrame({"Date": dates, "Symbol": symbol, "Name": "합성종목" + symbol,
                                    "Market": market, "Open": values, "High": values, "Low": values,
                                    "Close": values, "Volume": volume, "TradingValue": 6e9,
                                    "MarketCap": 1e12}))
    benchmarks = {market: pd.Series([200.0 - i / 5 for i in range(len(values))], index=dates)
                  for market in ("KOSPI", "KOSDAQ")}
    return pd.concat(frames, ignore_index=True), benchmarks, dates[-1].date().isoformat()


def candidate(symbol, rank=1, signal="swing", state="entry_due_next_session"):
    return dict(symbol=symbol, market_cap_rank=rank, signal_type=signal, state=state)


def create_db(path: Path):
    history, benchmarks, as_of = synthetic_inputs()
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE daily_prices (trade_date TEXT, symbol TEXT, name TEXT, "
                           "market TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, "
                           "trading_value REAL, market_cap REAL)")
        for row in history.itertuples(index=False, name=None):
            connection.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (row[0].date().isoformat(), *row[1:]))
        for market, symbol in [("KOSPI", "069500"), ("KOSDAQ", "229200")]:
            for session, close in benchmarks[market].items():
                connection.execute("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (session.date().isoformat(), symbol, "합성ETF", "ETF",
                                    close, close, close, close, 1000.0, 6e9, 1e12))
        connection.commit()
    finally:
        connection.close()
    return as_of


class MigrationTests(unittest.TestCase):
    def test_four_migrated_files_preserve_original_bytes(self):
        manifest = json.loads((ROOT / "docs/engine/source_manifest.json").read_text())
        for name in ("__init__.py", "strategy.py", "eligibility.py", "reversion_scanner.py"):
            archive = "docs/engine/snapshots/2026-09-29-0c6b2e8308cc/repo/src/korea_quant/" + name + ".txt"
            item = next(row for row in manifest["files"] if row.get("archive_path") == archive)
            actual = hashlib.sha256((ROOT / "engine/kqs/src/korea_quant" / name).read_bytes()).hexdigest()
            self.assertEqual(actual, item["source_sha256"], name)

    def test_automatic_fill_and_research_modules_are_not_installed(self):
        for name in ("paper_trading", "daily_picks_cli", "krx", "reversion_validation", "capital_metrics"):
            self.assertIsNone(importlib.util.find_spec("korea_quant." + name), name)
        self.assertFalse(any(name.startswith("app.backend") for name in sys.modules))

    def test_approved_runtime_defaults_not_research_default(self):
        self.assertEqual(APPROVED_CONFIG.market_filter_window, 200)
        self.assertTrue(APPROVED_CONFIG.market_filter_inverted)
        self.assertEqual(dict(APPROVED_CONFIG.market_cap_tiers), {"KOSPI": 200, "KOSDAQ": 150})

    def test_legacy_package_path_conflict_is_not_silently_certified(self):
        import korea_quant.strategy
        with patch.object(korea_quant.strategy, "__file__", "/tmp/legacy/korea_quant/strategy.py"):
            with self.assertRaisesRegex(ValueError, "Conflicting legacy"):
                code_identity()

    def test_migration_checker_accepts_initial_baseline(self):
        checker = runpy.run_path(str(ROOT / "engine/kqs/tools/check_migration.py"))["verify"]
        self.assertTrue(checker(ROOT)["integrity_ok"])

    def test_migration_checker_detects_core_tampering_and_forbidden_imports(self):
        checker = runpy.run_path(str(ROOT / "engine/kqs/tools/check_migration.py"))["verify"]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            for folder in ("engine/kqs/src/korea_quant", "engine/kqs/src/kqs_runtime"):
                (temporary / folder).mkdir(parents=True)
            (temporary / "docs/engine").mkdir(parents=True)
            shutil.copy(ROOT / "docs/engine/source_manifest.json", temporary / "docs/engine/source_manifest.json")
            shutil.copy(ROOT / "engine/kqs/migration_manifest.json", temporary / "engine/kqs/migration_manifest.json")
            for path in (ROOT / "engine/kqs/src/korea_quant").glob("*.py"):
                shutil.copy(path, temporary / "engine/kqs/src/korea_quant" / path.name)
            # Test tampering uses copied files only, never the archive/native source.
            with (temporary / "engine/kqs/src/korea_quant/__init__.py").open("a") as stream:
                stream.write("\nimport korea_quant.paper_trading\n")
            result = checker(temporary)
            self.assertFalse(result["integrity_ok"])
            self.assertTrue(any("differs" in error for error in result["errors"]))
            self.assertTrue(any("Forbidden runtime import" in error for error in result["errors"]))

    def test_package_import_from_outside_repository(self):
        result = subprocess.run([sys.executable, "-c", "import kqs_runtime, korea_quant.reversion_scanner"],
                                cwd="/tmp", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class SelectionTests(unittest.TestCase):
    def test_global_cap_duplicate_and_deterministic_ties(self):
        rows = [candidate("005930", 1, "long_term"), candidate("035420", 1),
                candidate("005930", 1), candidate("000660", 2), candidate("051910", 3)]
        result = selection_audit(pd.DataFrame(rows))
        selected = [(row["symbol"], row["signal_type"]) for row in result if row["selection_outcome"] == "selected"]
        self.assertEqual(selected, [("005930", "swing"), ("035420", "swing"), ("000660", "swing")])
        self.assertEqual([row["selection_outcome"] for row in result].count("duplicate_symbol"), 1)
        self.assertEqual([row["selection_outcome"] for row in result].count("daily_cap"), 1)
        self.assertEqual(result, selection_audit(pd.DataFrame(list(reversed(rows)))))

    def test_excluded_context_and_non_entry_reasons(self):
        result = selection_audit(pd.DataFrame([candidate("005930"), candidate("035420", signal="unknown"),
                                              candidate("000660", state="no_signal"),
                                              candidate("051910", state="regime_gate_blocked")]), ("5930.0",))
        outcomes = {row["symbol"]: row["selection_outcome"] for row in result}
        self.assertEqual(outcomes, {"005930": "already_tracked", "035420": "unsupported_signal_type",
                                    "000660": "not_entry_signal", "051910": "regime_gate_blocked"})

    def test_symbols_normalize_numeric_csv_without_accepting_arbitrary_codes(self):
        self.assertEqual(normalize_symbol(5930.0), "005930")
        for value in ("../../env", "nan", "005930.KS", "１２３４５６", "0088K", "00088KX", "0008 K"):
            with self.assertRaises(ValueError):
                normalize_symbol(value)

    def test_alphanumeric_krx_codes_from_real_listings_are_accepted(self):
        """Real KRX sessions include codes such as 00088K; the archived validator accepts them."""
        self.assertEqual(normalize_symbol("00088K"), "00088K")
        self.assertEqual(normalize_symbol("0030r0"), "0030R0")
        self.assertEqual(normalize_symbol(" 00104K "), "00104K")
        history, benchmarks, as_of = synthetic_inputs()
        history.loc[history["Symbol"] == "035420", "Symbol"] = "00088K"
        result = generate_recommendations(history, benchmarks, as_of=as_of)
        self.assertIn("00088K", {row["symbol"] for row in result["audit"]})


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.history, self.benchmarks, self.as_of = synthetic_inputs()

    def generate(self, **kwargs):
        return generate_recommendations(self.history, self.benchmarks, as_of=self.as_of, **kwargs)

    def test_swing_reasons_use_actual_indicators(self):
        result = self.generate()
        self.assertEqual(len(result["recommendations"]), 2)
        for row in result["recommendations"]:
            features = row["features"]
            self.assertLessEqual(features["close"], features["bb_lower20"])
            self.assertLessEqual(features["rsi14"], 30)
            self.assertLess(features["benchmark_close"], features["benchmark_sma200"])
            self.assertIn("RSI ≥ 50", row["exit_reference"])
        self.assertFalse(result["orders_enabled"])
        self.assertFalse(result["publication_ready"])
        self.assertIsNone(result["cost_model"])
        json.dumps(result, allow_nan=False)

    def test_long_term_recovery_reasons_and_current_exit(self):
        history, benchmarks, as_of = synthetic_inputs("long_term")
        result = generate_recommendations(history, benchmarks, as_of=as_of)
        self.assertEqual(len(result["recommendations"]), 2)
        for row in result["recommendations"]:
            self.assertEqual(row["signal_type"], "long_term")
            features = row["features"]
            self.assertLessEqual(features["previous_rsi14"], 30)
            self.assertGreater(features["rsi14"], 30)
            self.assertGreater(features["obv"], features["obv_sma9"])
            self.assertGreater(features["obv_delta"], 0)
            self.assertNotIn("70", row["exit_reference"])

    def test_no_signal_is_valid_and_not_a_failure(self):
        history, benchmarks, as_of = synthetic_inputs("flat")
        result = generate_recommendations(history, benchmarks, as_of=as_of)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["status"], "no_recommendations")
        self.assertEqual({row["state"] for row in result["audit"]}, {"no_signal"})

    def test_closed_market_gate_records_real_blocked_signal(self):
        for market, series in self.benchmarks.items():
            self.benchmarks[market] = 400.0 - series
        result = self.generate()
        self.assertEqual(result["recommendations"], [])
        self.assertEqual({row["selection_outcome"] for row in result["audit"]}, {"regime_gate_blocked"})

    def test_liquidity_rejection_is_retained_in_audit(self):
        self.history["TradingValue"] = 4e9
        result = self.generate()
        self.assertEqual(result["recommendations"], [])
        self.assertEqual({row["state"] for row in result["audit"]}, {"low_liquidity"})

    def test_input_order_does_not_change_identity(self):
        first = self.generate()
        shuffled = generate_recommendations(self.history.iloc[::-1],
                                           {key: series.iloc[::-1] for key, series in self.benchmarks.items()},
                                           as_of=self.as_of)
        self.assertEqual(first, shuffled)

    def test_data_hash_preserves_one_float_ulp(self):
        before = self.generate()
        self.history.loc[0, "TradingValue"] = np.nextafter(self.history.loc[0, "TradingValue"], np.inf)
        after = self.generate()
        self.assertNotEqual(before["input_sha256"], after["input_sha256"])
        self.assertNotEqual(before["run_id"], after["run_id"])
        self.assertEqual(before["config_sha256"], after["config_sha256"])

    def test_context_has_its_own_identity_and_not_an_automatic_ledger(self):
        before = self.generate()
        after = self.generate(excluded_symbols=("005930",))
        self.assertNotEqual(before["run_id"], after["run_id"])
        self.assertEqual(before["input_sha256"], after["input_sha256"])
        self.assertEqual(len(after["recommendations"]), 1)
        self.assertEqual(after["excluded_symbols"], ["005930"])

    def test_code_change_affects_run_identity_separately_from_config(self):
        before = self.generate()
        identity = dict(before["engine"], code_sha256="f" * 64)
        with patch("kqs_runtime.recommendations.code_identity", return_value=identity):
            after = self.generate()
        self.assertNotEqual(before["run_id"], after["run_id"])
        self.assertEqual(before["config_sha256"], after["config_sha256"])

    def test_inputs_remain_unchanged(self):
        history_before = self.history.copy(deep=True)
        benchmark_before = {key: value.copy(deep=True) for key, value in self.benchmarks.items()}
        self.generate()
        pd.testing.assert_frame_equal(self.history, history_before)
        for key, value in benchmark_before.items():
            pd.testing.assert_series_equal(value, self.benchmarks[key])

    def test_invalid_dates_are_rejected(self):
        for value in ("20260101", "2026-01-01T12:00:00", "bad-date"):
            with self.assertRaises(ValueError):
                parse_date(value)

    def test_stale_or_short_or_duplicate_benchmarks_fail_closed(self):
        original = self.benchmarks["KOSPI"]
        for invalid in (original.iloc[:-1], original.iloc[-100:], pd.concat([original, original.iloc[-1:]])):
            self.benchmarks["KOSPI"] = invalid
            with self.assertRaises(ValueError):
                self.generate()

    def test_missing_or_nonfinite_benchmark_fail_closed(self):
        for invalid in (None, np.nan, np.inf, 0.0):
            benchmarks = {key: value.copy() for key, value in self.benchmarks.items()}
            if invalid is None:
                del benchmarks["KOSDAQ"]
            else:
                benchmarks["KOSDAQ"].iloc[-1] = invalid
            with self.assertRaises(ValueError):
                generate_recommendations(self.history, benchmarks, as_of=self.as_of)

    def test_future_history_or_benchmark_is_not_used(self):
        future = pd.Timestamp(self.as_of) + pd.Timedelta(days=1)
        history = self.history.copy()
        history.loc[0, "Date"] = future
        with self.assertRaisesRegex(ValueError, "Future"):
            generate_recommendations(history, self.benchmarks, as_of=self.as_of)
        self.benchmarks["KOSPI"].loc[future] = 100.0
        with self.assertRaisesRegex(ValueError, "Future"):
            self.generate()

    def test_partial_market_history_does_not_look_current(self):
        self.history = self.history[~(self.history["Market"].eq("KOSDAQ") &
                                      self.history["Date"].eq(pd.Timestamp(self.as_of)))]
        with self.assertRaisesRegex(ValueError, "KOSDAQ equity history"):
            self.generate()

    def test_duplicate_or_invalid_price_history_is_rejected(self):
        original = self.history.copy()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            generate_recommendations(pd.concat([original, original.iloc[:1]]), self.benchmarks, as_of=self.as_of)
        for column, value in [("Close", 0), ("TradingValue", np.nan), ("Volume", -1), ("MarketCap", np.inf),
                              ("High", 1)]:
            with self.subTest(column=column):
                self.history = original.copy()
                self.history.loc[0, column] = value
                with self.assertRaises(ValueError):
                    self.generate()


class ReadonlyCliTests(unittest.TestCase):
    def test_missing_database_is_not_created_and_stdout_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing.sqlite3"
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["--krx-db", str(target), "--history-start", "2025-01-01", "--as-of", "2026-01-01"])
            self.assertEqual(status, 2)
            self.assertFalse(target.exists())
            self.assertEqual(stdout.getvalue(), "")
            self.assertFalse(json.loads(stderr.getvalue())["orders_enabled"])

    def test_invalid_database_schema_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.sqlite3"
            sqlite3.connect(path).close()
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main(["--krx-db", str(path), "--history-start", "2025-01-01", "--as-of", "2026-01-01"])
            self.assertEqual(status, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("daily_prices", json.loads(stderr.getvalue())["error"])

    def test_readonly_cli_does_not_change_database_or_create_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market # input.sqlite3"
            as_of = create_db(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = main(["--krx-db", str(path), "--history-start", "2025-01-01", "--as-of", as_of])
            self.assertEqual(status, 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(len(result["recommendations"]), 2)
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual([file.name for file in Path(directory).iterdir()], [path.name])

    def test_database_is_opened_in_readonly_query_only_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            as_of = create_db(path)
            connect = sqlite3.connect
            observed = []
            def tracked_connect(database_uri, **kwargs):
                self.assertTrue(database_uri.endswith("?mode=ro"))
                self.assertTrue(kwargs["uri"])
                connection = connect(database_uri, **kwargs)
                connection.set_trace_callback(observed.append)
                return connection
            with patch("kqs_runtime.sqlite_reader.sqlite3.connect", side_effect=tracked_connect):
                read_inputs(path, history_start="2025-01-01", as_of=as_of)
            self.assertIn("PRAGMA query_only = ON", observed)
            self.assertIn("BEGIN", observed)
            self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE", "CREATE")) for query in observed))

    def test_boundaries_are_explicit_and_reversed_window_is_rejected(self):
        with self.assertRaises(ValueError):
            read_inputs(Path("not-created.sqlite3"), history_start="2026-02-01", as_of="2026-01-01")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main([])
        self.assertEqual(caught.exception.code, 2)

    def test_leading_unfillable_halt_rows_are_dropped_and_recorded_not_fatal(self):
        """Real KRX windows start with ~99 symbols halted; one must not fail the whole session."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            as_of = create_db(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                first_days = [row[0] for row in connection.execute(
                    "SELECT trade_date FROM daily_prices WHERE symbol='035420' ORDER BY trade_date LIMIT 5")]
                connection.executemany(
                    "UPDATE daily_prices SET open=0, high=0, low=0, volume=0, trading_value=0 "
                    "WHERE symbol='035420' AND trade_date=?", [(day,) for day in first_days])
            history, benchmarks = read_inputs(path, history_start="2025-01-01", as_of=as_of)
            self.assertEqual(history.attrs["dropped_unfillable_halted_rows"], {"rows": 5, "symbols": ["035420"]})
            self.assertFalse(history[["Open", "High", "Low", "Close"]].isna().any().any())
            result = generate_recommendations(history, benchmarks, as_of=as_of)
            self.assertEqual(result["as_of"], as_of)

    def test_halt_carry_forward_does_not_mutate_input(self):
        history, _, _ = synthetic_inputs()
        history.loc[1, ["Open", "High", "Low", "Close"]] = 0.0
        original = history.copy(deep=True)
        filled = fill_halted_trading_days(history)
        pd.testing.assert_frame_equal(history, original)
        row = filled[(filled["Symbol"] == history.loc[1, "Symbol"]) & (filled["Date"] == history.loc[1, "Date"])].iloc[0]
        self.assertEqual([row[key] for key in ("Open", "High", "Low", "Close")], [100.0] * 4)


if __name__ == "__main__":
    unittest.main()
