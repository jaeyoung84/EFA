"""Codex 수정: CODX-SL-001 (2026-10-08), 수급 관측의 격리 회귀 테스트."""
import importlib.util
import sys
import types
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app/backend/short_lending_reference.py"


def load_isolated():
    package = types.ModuleType("sl_test_backend")
    package.__path__ = []
    db = types.ModuleType("sl_test_backend.db")
    db.get_db = lambda: None
    kis = types.ModuleType("sl_test_backend.kis_quotes")
    kis.kis_service = lambda: None
    recommendations = types.ModuleType("sl_test_backend.kqs_recommendations")
    recommendations.read_local_recommendations = lambda: None
    modules = {"sl_test_backend": package, "sl_test_backend.db": db,
               "sl_test_backend.kis_quotes": kis,
               "sl_test_backend.kqs_recommendations": recommendations}
    spec = importlib.util.spec_from_file_location("sl_test_backend.short_lending_reference", SOURCE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


sl = load_isolated()


def rows():
    dates = []
    day = date(2026, 6, 1)
    while len(dates) < 60:
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)
    short = []
    loan = []
    for index, session in enumerate(dates):
        ratio = [2, 3, 4][index // 20]
        short.append({"stck_bsop_date": session.strftime("%Y%m%d"),
                      "ssts_tr_pbmn": str(ratio), "acml_tr_pbmn": "100"})
        loan.append({"bsop_date": session.strftime("%Y%m%d"),
                     "rmnd_stcn": str(1000 + index), "new_stcn": "10", "rdmp_stcn": "9"})
    return dates[-1].isoformat(), short, loan


class FakeService:
    def __init__(self, short, loan):
        self.short = short
        self.loan = loan
        self.calls = []

    async def _rest_get(self, path, tr_id, params):
        self.calls.append((path, tr_id, params))
        if path.endswith("daily-short-sale"):
            return {"rt_cd": "0", "output1": {}, "output2": self.short}
        return {"rt_cd": "0", "output1": self.loan}


class FakeCursor:
    def __init__(self, records):
        self.records = records

    async def to_list(self, length):
        return self.records[:length]


class FakeCollection:
    def __init__(self):
        self.records = {}

    async def create_index(self, *args):
        return None

    async def find_one(self, query, projection=None):
        for row in self.records.values():
            if all(row.get(key) == value for key, value in query.items()):
                return row
        return None

    async def update_one(self, query, update, upsert=False):
        self.records.setdefault(query["_id"], update["$setOnInsert"])

    def find(self, query, projection=None):
        return FakeCursor([row for row in self.records.values()
                           if row.get("snapshot_id") == query["snapshot_id"]])


class ReferenceTests(unittest.TestCase):
    def test_normalize_and_describe_without_future_rows(self):
        as_of, short, loan = rows()
        short.append({"stck_bsop_date": "20990101", "ssts_tr_pbmn": "90",
                      "acml_tr_pbmn": "100"})
        loan.append({"bsop_date": "20990101", "rmnd_stcn": "999999"})
        normalized_short = sl.short_rows({"output2": short}, as_of)
        normalized_loan = sl.loan_rows({"output1": loan}, as_of)
        self.assertEqual(len(normalized_short), 60)
        self.assertEqual(len(normalized_loan), 60)
        self.assertEqual(sl.short_summary(normalized_short)["blocks_20_pct"], [2, 3, 4])
        self.assertEqual(sl.short_summary(normalized_short)["direction_60"], "increase")
        self.assertEqual(sl.short_summary(normalized_short)["volatility_20_pp"], 0)
        self.assertEqual(sl.loan_summary(normalized_loan)["direction_60"], "increase")
        self.assertEqual(sl.short_summary(normalized_short[:59])["status"], "insufficient")

    def test_invalid_ratios_and_missing_balance_are_excluded(self):
        self.assertEqual(sl.short_rows({"output2": [
            {"stck_bsop_date": "20261001", "ssts_tr_pbmn": "200", "acml_tr_pbmn": "100"},
            {"stck_bsop_date": "20261002", "ssts_tr_pbmn": "5", "acml_tr_pbmn": "100"}]}, "2026-10-02"),
            [{"date": "2026-10-02", "short_value": 5.0, "total_value": 100.0, "ratio_pct": 5.0}])
        self.assertEqual(sl.loan_rows({"output1": [
            {"bsop_date": "20261001", "rmnd_stcn": ""},
            {"bsop_date": "20261002", "rmnd_stcn": "-2"}]}, "2026-10-02"), [])


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_storage_is_insert_only_and_read_only(self):
        as_of, short, loan = rows()
        service = FakeService(short, loan)
        collection = FakeCollection()
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        payload = {"snapshot_id": "test-snapshot", "generated_at": now.isoformat(),
                   "engine": {"data_as_of": as_of}, "signals": [
                       {"symbol": "005930", "action": "entry", "selection_outcome": "selected",
                        "signal_id": "s1", "name": "sample", "market": "KOSPI"},
                       {"symbol": "005930", "action": "entry", "selection_outcome": "selected",
                        "signal_id": "s2", "name": "sample", "market": "KOSPI"}]}
        first = await sl.collect_short_lending_references(
            now=now, payload=payload, service=service, collection=collection)
        second = await sl.collect_short_lending_references(
            now=now, payload=payload, service=service, collection=collection)
        self.assertEqual((first["count"], second["count"]), (1, 0))
        self.assertEqual(len(service.calls), 2)
        self.assertEqual(len(collection.records), 1)
        record = next(iter(collection.records.values()))
        self.assertEqual(record["signal_ids"], ["s1", "s2"])
        self.assertEqual(len(record["short"]["raw_sha256"]), 64)
        result = await sl.read_short_lending_references(payload=payload, collection=collection)
        self.assertEqual(result["items"]["005930"]["status"], "available")
        self.assertNotIn("rows", result["items"]["005930"]["short"])
        self.assertNotIn("raw_sha256", result["items"]["005930"]["loan"])

    async def test_no_new_publication_does_not_call_provider(self):
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        old = now - timedelta(days=1)
        service = FakeService([], [])
        result = await sl.collect_short_lending_references(
            now=now, payload={"generated_at": old.isoformat()}, service=service,
            collection=FakeCollection())
        self.assertEqual(result["status"], "no_new_publication")
        self.assertEqual(service.calls, [])
