"""EINT-019 forward outcomes: synthetic market DB and hand-built v2 snapshots, read-only, atomic file."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kqs_collection.krx import SCHEMA_SQL
from kqs_publication.contract import RECOMMENDATION_SCHEMA_V2, canonical_payload_digest
from kqs_publication.store import TABLE
from kqs_runtime.outcomes import HORIZONS, build_outcome_report, write_outcome_report

SESSIONS = [f"2026-10-{day:02d}" for day in range(1, 26)]  # 25 synthetic sessions


def bar(day, symbol, market, open_, close, low=None, high=None):
    return (day, symbol, symbol, market, "s", open_, high or max(open_, close), low or min(open_, close), close,
            1, 1, 1, 1, "t")


def market_db(path: Path, rows) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_SQL)
    connection.executemany("INSERT INTO daily_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


def payload(as_of, picks, schema=RECOMMENDATION_SCHEMA_V2, snapshot="s"):
    body = {"schema_version": schema, "engine": {"data_as_of": as_of},
            "publication": {"snapshot_id": f"kqs-{snapshot}-{as_of}"},
            "signals": [{"action": "entry", "selection_outcome": "selected", "signal_type": item,
                         "selection_rank": rank, "symbol": symbol, "name": symbol, "market": market}
                        for item, rank, symbol, market in picks]}
    body["integrity"] = {"payload_sha256": canonical_payload_digest(body)}
    return body


def snapshot_db(path: Path, payloads, tamper=()) -> None:
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE {TABLE} (snapshot_id TEXT PRIMARY KEY, run_id TEXT, data_as_of TEXT, "
                       "generated_at TEXT, expires_at TEXT, payload_sha256 TEXT, payload_json TEXT, "
                       "runtime_sha256 TEXT, runtime_json TEXT)")
    for index, body in enumerate(payloads):
        digest = "0" * 64 if index in tamper else body["integrity"]["payload_sha256"]
        connection.execute(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?)",
                           (body["publication"]["snapshot_id"], f"r{index}", body["engine"]["data_as_of"],
                            f"2026-10-01T00:00:{index:02d}", "x", digest, json.dumps(body), "", ""))
    connection.commit()
    connection.close()


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.market, self.snapshots = root / "market.sqlite3", root / "recommendations.sqlite3"
        rows = []
        for index, day in enumerate(SESSIONS):
            rows.append(bar(day, "AAA", "KOSDAQ", 1000 + 10 * index, 1005 + 10 * index, low=900 if index == 3 else None))
            rows.append(bar(day, "229200", "ETF", 100, 100 + index))  # benchmark: +index% at each close
            rows.append(bar(day, "069500", "ETF", 100, 100))
            if index != 2:  # BBB has no bar on the 3rd session (e.g. delisted)
                rows.append(bar(day, "BBB", "KOSPI", 500, 500))
            rows.append(bar(day, "CCC", "KOSPI", *((0, 0, 0, 0) if index in (0, 6) else (200, 210))))
        market_db(self.market, rows)

    def report(self, payloads, through=SESSIONS[-1], **kwargs):
        snapshot_db(self.snapshots, payloads, **kwargs)
        return build_outcome_report(self.snapshots, self.market, through)

    def test_returns_from_next_open_at_each_horizon_with_benchmark(self):
        before = hashlib.sha256(self.market.read_bytes()).hexdigest()
        report = self.report([payload("2026-09-30", [("pullback_v4", 1, "AAA", "KOSDAQ")])])
        row = report["rows"][0]
        self.assertEqual((row["entry_date"], row["entry_open"], row["status"]), ("2026-10-01", 1000, "observed"))
        five = row["horizons"]["5"]  # 5th session counting the entry session: 2026-10-05, close 1045
        self.assertEqual((five["target_date"], five["close"], five["return_pct"]), ("2026-10-05", 1045, 4.5))
        self.assertEqual((five["benchmark_return_pct"], five["excess_return_pct"]), (4.0, 0.5))
        self.assertEqual((five["max_drawdown_pct"], row["horizons"]["1"]["max_drawdown_pct"]), (-10.0, 0.0))
        self.assertEqual(sorted(row["horizons"], key=int), [str(h) for h in HORIZONS])
        self.assertTrue(all(h["observed"] for h in row["horizons"].values()))
        self.assertEqual(hashlib.sha256(self.market.read_bytes()).hexdigest(), before)  # read-only
        self.assertEqual((report["orders_enabled"], report["costs_included"]), (False, False))

    def test_unobserved_horizons_are_never_filled(self):
        row = self.report([payload("2026-10-14", [("swing", 1, "AAA", "KOSDAQ")])])["rows"][0]
        self.assertTrue(row["horizons"]["10"]["observed"])  # 10-24 is the 10th session after 10-14
        self.assertEqual(row["horizons"]["20"], {"observed": False})
        early = self.report([payload("2026-10-14", [("swing", 1, "AAA", "KOSDAQ")])], through="2026-10-14")
        self.assertEqual(early["rows"][0]["status"], "awaiting_entry")

    def test_missing_bar_stops_and_halts_carry_the_last_close(self):
        rows = self.report([payload("2026-09-30", [("swing", 1, "BBB", "KOSPI"), ("long_term", 1, "CCC", "KOSPI")])])["rows"]
        bbb = next(row for row in rows if row["symbol"] == "BBB")
        self.assertEqual((bbb["status"], bbb["horizons"]["1"]["observed"], bbb["horizons"]["5"]), ("missing_bar", True, {"observed": False}))
        ccc = next(row for row in rows if row["symbol"] == "CCC")
        self.assertEqual(ccc["status"], "entry_halted")  # halted on the entry session: no entry, no returns
        later = self.report([payload("2026-10-01", [("long_term", 1, "CCC", "KOSPI")])])["rows"][0]
        self.assertEqual((later["horizons"]["5"]["close"], later["horizons"]["5"]["halt_carried"]), (210, False))
        self.assertEqual((later["horizons"]["10"]["close"], later["horizons"]["10"]["halt_carried"]), (210, True))

    def test_only_verified_v2_snapshots_and_sample_gate(self):
        report = self.report([payload("2026-09-29", [("swing", 1, "AAA", "KOSDAQ")], schema="kqs-recommendations/v1"),
                              payload("2026-09-30", [("swing", 1, "AAA", "KOSDAQ"), ("pullback_v4", 1, "AAA", "KOSDAQ")]),
                              payload("2026-10-01", [("swing", 1, "BBB", "KOSPI")], snapshot="tampered"),
                              payload("2026-10-01", [("swing", 1, "AAA", "KOSDAQ")], snapshot="rerun")], tamper={2})
        self.assertEqual([(row["as_of"], row["item"]) for row in report["rows"]],
                         [("2026-09-30", "swing"), ("2026-09-30", "pullback_v4"), ("2026-10-01", "swing")])
        swing = report["summary"]["swing"]
        self.assertEqual((swing["pick_count"], swing["session_count"]), (2, 2))
        self.assertEqual(swing["horizons"]["1"]["observed_count"], 2)
        self.assertEqual(swing["horizons"]["1"]["sample_gate"], "insufficient_sample")
        self.assertEqual(report["summary"]["long_term"]["pick_count"], 0)

    def test_write_is_atomic_and_world_readable(self):
        target = Path(self.directory.name) / "state" / "recommendation-outcomes.json"
        write_outcome_report(target, self.report([payload("2026-09-30", [("swing", 1, "AAA", "KOSDAQ")])]))
        self.assertEqual(json.loads(target.read_text())["schema"], "kqs-recommendation-outcomes/v1")
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o644")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["recommendation-outcomes.json"])


if __name__ == "__main__":
    unittest.main()
