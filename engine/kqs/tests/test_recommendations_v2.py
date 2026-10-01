"""EINT-017: three items (swing / long_term / pullback_v4), up to 5 each, v2 contract and exit monitor."""

import copy
import tempfile
import unittest
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import pullback_v4_fixture as fx
from kqs_publication.calendar import calendar_for_session
from kqs_publication.contract import (
    KqsContractError, MAX_SELECTED_PER_ITEM, canonical_payload_digest, validate_recommendation_payload,
)
from kqs_publication.publisher import build_payload
from kqs_publication.store import recommended_symbols
from kqs_runtime import pullback_v4 as v4
from kqs_runtime.recommendations import ITEMS, generate_recommendations
from test_publication import publish


def _relabelled_inputs():
    """fx data cut at the first day with v4 candidates, positionally moved onto a 2026 KRX session."""
    ohlcv, benchmarks, market_map = fx.build()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for day in pd.bdate_range("2023-02-01", "2023-06-30"):
            if not v4.scan_candidates(ohlcv, benchmarks, market_map, day).empty:
                break
        else:
            raise AssertionError("fixture has no v4 candidate day")
    target = next(d for d in pd.bdate_range("2026-03-02", "2026-06-30")
                  if d.weekday() == day.weekday() and calendar_for_session(d.date()).is_session(d.date()))
    source_dates = pd.bdate_range(fx.START, day)
    new_dates = pd.bdate_range(end=target, periods=len(source_dates))
    remap = dict(zip(source_dates, new_dates))
    rows = []
    for k, (ticker, frame) in enumerate(ohlcv.items()):
        frame = frame.loc[frame.index <= day]
        if frame.empty or frame.index[-1] != day:
            continue  # the delisted fixture name has no bar on the session
        market = market_map[ticker] if isinstance(market_map[ticker], str) else v4.market_of(market_map, ticker, day)
        rows.append(pd.DataFrame({
            "Date": [remap[d] for d in frame.index], "Symbol": ticker, "Name": f"합성{k}", "Market": market,
            "Open": frame["Open"].to_numpy(), "High": frame["High"].to_numpy(), "Low": frame["Low"].to_numpy(),
            "Close": frame["Close"].to_numpy(), "Volume": frame["Volume"].to_numpy(),
            "TradingValue": frame["Turnover"].to_numpy(), "MarketCap": 1e12 - k * 1e9}))
    history = pd.concat(rows, ignore_index=True).assign(Halted=False)
    bench = {market: pd.Series(frame.loc[frame.index <= day, "Close"].to_numpy(),
                               index=[remap[d] for d in frame.index[frame.index <= day]])
             for market, frame in benchmarks.items()}
    return history, bench, target.date().isoformat()


class RecommendationsV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history, cls.benchmarks, cls.as_of = _relabelled_inputs()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.runtime = generate_recommendations(cls.history, cls.benchmarks, as_of=cls.as_of)
        cls.generated = datetime.combine(date.fromisoformat(cls.as_of) + timedelta(days=1), datetime.min.time(),
                                         tzinfo=ZoneInfo("Asia/Seoul")).replace(hour=7)

    def payload(self):
        return build_payload(self.runtime, generated_at=self.generated, expires_at=self.generated + timedelta(hours=4),
                             expected_as_of=self.as_of)

    def test_pullback_v4_item_is_published_with_reasons_and_score_order(self):
        rows = [row for row in self.runtime["recommendations"] if row["signal_type"] == "pullback_v4"]
        self.assertTrue(rows)
        self.assertLessEqual(len(rows), MAX_SELECTED_PER_ITEM)
        self.assertEqual(len(rows[0]["reasons"]), 5)
        scores = [row["features"]["score"] for row in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        audit = [row for row in self.runtime["audit"] if row["signal_type"] == "pullback_v4"]
        self.assertEqual(sum(row["selection_outcome"] == "selected" for row in audit), len(rows))

    def test_every_item_is_capped_and_ranked_independently(self):
        safe = validate_recommendation_payload(self.payload(), now=self.generated)
        self.assertEqual(safe["schema_version"], "kqs-recommendations/v2")
        for item in ITEMS:
            ranks = [s["selection_rank"] for s in safe["signals"] if s["signal_type"] == item]
            self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
            self.assertLessEqual(len(ranks), MAX_SELECTED_PER_ITEM)
            self.assertEqual(safe["items"][item]["selected_count"], len(ranks))

    def test_contract_rejects_rank_overflow_duplicates_and_unknown_items(self):
        payload = self.payload()
        target = next(i for i, s in enumerate(payload["signals"]) if s["signal_type"] == "pullback_v4")
        cases = []
        overflow = copy.deepcopy(payload); overflow["signals"][target]["selection_rank"] = 6; cases.append(overflow)
        unknown = copy.deepcopy(payload); unknown["signals"][target]["signal_type"] = "turtle"; cases.append(unknown)
        dup = copy.deepcopy(payload); extra = copy.deepcopy(dup["signals"][target])
        extra["signal_id"] = "x" * 64; extra["selection_rank"] = 99; dup["signals"].append(extra); cases.append(dup)
        same = copy.deepcopy(payload); twin = copy.deepcopy(same["signals"][target])
        twin["signal_id"] = "y" * 64; twin["selection_rank"] = 1 + sum(
            s["signal_type"] == "pullback_v4" for s in same["signals"]); same["signals"].append(twin); cases.append(same)
        for changed in cases:
            changed["integrity"]["payload_sha256"] = canonical_payload_digest(changed)
            with self.assertRaises(KqsContractError):
                validate_recommendation_payload(changed, now=self.generated)

    def test_same_symbol_may_appear_in_two_items(self):
        payload = self.payload()
        signal = copy.deepcopy(next(s for s in payload["signals"] if s["signal_type"] == "pullback_v4"))
        count = sum(s["signal_type"] == "swing" for s in payload["signals"])
        if count >= MAX_SELECTED_PER_ITEM:
            self.skipTest("swing item already full in this fixture")
        signal.update(signal_id="z" * 64, signal_type="swing", selection_rank=count + 1)
        payload["signals"].append(signal)
        payload["integrity"]["payload_sha256"] = canonical_payload_digest(payload)
        validate_recommendation_payload(payload, now=self.generated)

    def test_exit_monitor_reports_reversion_state_and_v4_series(self):
        symbol = next(row["symbol"] for row in self.runtime["recommendations"] if row["signal_type"] == "pullback_v4")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            runtime = generate_recommendations(self.history, self.benchmarks, as_of=self.as_of,
                                               tracked=((symbol, "pullback_v4"), (symbol, "swing"), ("999999", "swing")))
        rows = {(row["symbol"], row["signal_type"]): row for row in runtime["exit_monitor"]}
        self.assertIn(rows[(symbol, "swing")]["state"], {"hold", "exit_signal"})
        self.assertEqual(rows[("999999", "swing")]["state"], "no_current_bar")
        v4_row = rows[(symbol, "pullback_v4")]
        self.assertEqual(v4_row["state"], "series")
        self.assertEqual(v4_row["series"][-1]["date"], self.as_of)
        self.assertNotEqual(runtime["run_id"], self.runtime["run_id"])  # tracked context is part of the identity
        safe = validate_recommendation_payload(build_payload(
            runtime, generated_at=self.generated, expires_at=self.generated + timedelta(hours=4),
            expected_as_of=self.as_of), now=self.generated)
        self.assertEqual(len(safe["exit_monitor"]), 3)

    def test_tracking_reads_only_earlier_sessions_so_reruns_stay_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recommendations.sqlite3"
            publish(path, self.runtime, expected_as_of=self.as_of, generated_at=self.generated,
                    expires_at=self.generated + timedelta(hours=4))
            since = {item: "2020-01-01" for item in ITEMS}
            self.assertEqual(recommended_symbols(path, since=since, before=self.as_of), [])
            later = (date.fromisoformat(self.as_of) + timedelta(days=1)).isoformat()
            pairs = recommended_symbols(path, since=since, before=later)
            expected = sorted({(row["symbol"], row["signal_type"]) for row in self.runtime["recommendations"]})
            self.assertEqual(pairs, expected)


if __name__ == "__main__":
    unittest.main()
