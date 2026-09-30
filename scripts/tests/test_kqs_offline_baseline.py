"""EINT-009 offline runner guard tests: fake API only, no network or real ledger."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("kqs_offline_baseline", ROOT / "scripts/run_kqs_offline_baseline.py")
RUNNER = importlib.util.module_from_spec(SPEC)
try:
    SPEC.loader.exec_module(RUNNER)
    RUNNER_ERROR = None
except ImportError as exc:  # sklearn/requests are research-only dependencies, not engine runtime ones
    RUNNER_ERROR = exc


def example(index, split):
    decision = ("buy", "hold")[index % 2]
    return {"record_type": "example", "dataset_split": split,
            "features": {"data_as_of": "2026-09-28", "market": "KOSDAQ",
                         "signal_type": "swing", "selection_outcome": "selected",
                         "selection_rank": index % 3, "execution_session": "next_session",
                         "config_fingerprint": "cfg", "reasons": [{"code": "trend"}],
                         "engine_id": "kqs", "engine_git_sha": "abc", "dataset_hash": "data"},
            "feature_provenance": {"publication_snapshot_id": "synthetic-snapshot",
                                   "recommendation_generated_at": "2026-09-29T00:00:00+00:00",
                                   "source_payload_sha256": "f" * 64},
            "human_context": {"evidence_tags": [decision], "memo": decision},
            "labels": {"human_decision": decision,
                       "forward_returns": {"20": {"observed": True, "return_pct": 1.0}}}}


class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body


class FakeApi:
    def __init__(self, holdout="sealed", schema="kqs-human-decisions/v3", leak=False, consent=True):
        self.calls = []
        self.leak = leak
        self.consent = consent
        self.manifest = {"manifest_id": "kqs-ds-1", "manifest_sha256": "m" * 64, "holdout": {"status": holdout},
                         "example_schema": schema, "feature_schema": "kqs-engine-features/v1",
                         "feature_schema_sha256": "dd69f54eaa79699ee98ded7090ae9851ff7cd11807733ed5780b752bf1346974",
                         "learning_consent": {"status": "granted", "policy_version": "kqs-learning-consent/v1"},
                         "counts": {"train": 8, "validation": 4, "test": 4}}

    def get(self, url, **_):
        path = url.split("http://api", 1)[1]
        self.calls.append(("GET", path))
        if path == "/api/kqs/datasets":
            return Response(200, {"items": [self.manifest]})
        if path == "/api/kqs/learning-consent":
            return Response(200, {"current": {"status": "granted" if self.consent else "revoked", "active": self.consent,
                                              "policy_version": "kqs-learning-consent/v1"}})
        split = path.rsplit("split=", 1)[1]
        if split == "test" and not any(call[0] == "POST" and call[1].endswith("/holdout") for call in self.calls):
            return Response(409, {"detail": "sealed"})
        header = {"record_type": "manifest", "manifest_sha256": self.manifest["manifest_sha256"],
                  "schema_version": self.manifest["example_schema"], "feature_schema": self.manifest["feature_schema"],
                  "feature_schema_sha256": self.manifest["feature_schema_sha256"],
                  "training_eligible": self.manifest["example_schema"] == "kqs-human-decisions/v3",
                  "learning_consent": {"manifest": self.manifest["learning_consent"],
                                       "current": {"active": self.consent}},
                  "hash_verified": True}
        rows = [header] + [example(i, split) for i in range(self.manifest["counts"][split])]
        if self.leak:
            rows[1]["features"]["evidence_tags"] = ["buy"]
        return Response(200, "\n".join(json.dumps(row) for row in rows))

    def post(self, url, json=None, **_):
        path = url.split("http://api", 1)[1]
        self.calls.append(("POST", path, json))
        return Response(201, {"item": {"experiment_id": "exp-1"}})


@unittest.skipIf(RUNNER_ERROR is not None, f"offline runner dependencies missing: {RUNNER_ERROR}")
class OfflineBaselineTests(unittest.TestCase):
    def run_with(self, api, *argv):
        original_get, original_post = RUNNER.requests.get, RUNNER.requests.post
        RUNNER.requests.get, RUNNER.requests.post = api.get, api.post
        try:
            return RUNNER.run(RUNNER.parse_args(["--api-base", "http://api", "--token", "t", *argv]))
        finally:
            RUNNER.requests.get, RUNNER.requests.post = original_get, original_post

    def test_default_run_never_touches_test(self):
        api = FakeApi()
        result = self.run_with(api, "--model", "logistic_regression")
        self.assertNotIn("test", result)
        self.assertFalse(any("split=test" in call[1] for call in api.calls))
        self.assertFalse(any(call[0] == "POST" for call in api.calls))

    def test_record_without_holdout_sends_no_test_metrics(self):
        api = FakeApi()
        self.run_with(api, "--record")
        posted = [call for call in api.calls if call[0] == "POST"]
        self.assertEqual([call[1] for call in posted], ["/api/kqs/training-experiments"])
        self.assertNotIn("test", posted[0][2])
        self.assertEqual(posted[0][2]["manifest_id"], "kqs-ds-1")

    def test_final_holdout_preregisters_before_reading_test(self):
        api = FakeApi()
        result = self.run_with(api, "--model", "logistic_regression", "--final-holdout", "--record")
        order = [(call[0], call[1]) for call in api.calls]
        prereg = order.index(("POST", "/api/kqs/datasets/kqs-ds-1/holdout"))
        test_read = order.index(("GET", "/api/kqs/datasets/kqs-ds-1/examples?split=test"))
        record = order.index(("POST", "/api/kqs/training-experiments"))
        self.assertLess(prereg, test_read)
        self.assertLess(test_read, record)
        prereg_body, record_body = api.calls[prereg][2], api.calls[record][2]
        self.assertEqual(prereg_body["training_config"], record_body["training_config"])
        self.assertEqual(prereg_body["validation"], record_body["validation"])
        self.assertEqual(result["test"]["sample_count"], 4)

    def test_v2_is_rejected_before_downloading_or_opening_holdout(self):
        api = FakeApi(schema="kqs-human-decisions/v2")
        with self.assertRaisesRegex(RuntimeError, "v3"):
            self.run_with(api, "--final-holdout", "--record")
        self.assertEqual(api.calls, [("GET", "/api/kqs/datasets")])

    def test_missing_or_revoked_consent_fails_before_downloading_data_or_recording(self):
        api = FakeApi(consent=False)
        with self.assertRaisesRegex(RuntimeError, "동의"):
            self.run_with(api, "--final-holdout", "--record")
        self.assertEqual(api.calls, [("GET", "/api/kqs/datasets"), ("GET", "/api/kqs/learning-consent")])

    def test_user_label_tag_cannot_reach_any_model_or_holdout(self):
        api = FakeApi(leak=True)
        with self.assertRaisesRegex(RuntimeError, "허용되지 않은 피처"):
            self.run_with(api, "--model", "majority_class_baseline", "--final-holdout", "--record")
        self.assertFalse(any(call[0] == "POST" or "split=test" in call[1] for call in api.calls))

    def test_changed_feature_policy_hash_fails_before_data_or_holdout(self):
        api = FakeApi()
        api.manifest["feature_schema_sha256"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "v3"):
            self.run_with(api, "--final-holdout", "--record")
        self.assertEqual(api.calls, [("GET", "/api/kqs/datasets")])

    def test_human_context_is_not_vectorized(self):
        row = example(0, "train")
        fields = RUNNER.feature_row(row)
        self.assertFalse(any("evidence" in key or "memo" in key for key in fields))
        self.assertEqual(fields["market"], "KOSDAQ")

    def test_final_holdout_requires_record_and_a_sealed_manifest(self):
        with self.assertRaisesRegex(RuntimeError, "--record"):
            self.run_with(FakeApi(), "--final-holdout")
        for status in ("opened", "retired_sealed"):
            with self.assertRaisesRegex(RuntimeError, status):
                self.run_with(FakeApi(holdout=status), "--final-holdout", "--record")


if __name__ == "__main__":
    unittest.main()
