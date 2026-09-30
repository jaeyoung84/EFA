#!/usr/bin/env python3
"""KQS 사용자 판단 JSONL의 오프라인 기준 모델을 학습·평가한다.

이 도구는 추천·주문·모의체결을 수행하지 않는다. 기본값은 결과를 출력만 하는
dry-run이며, ``--record``가 있을 때만 홈페이지의 실험 이력 API에 결과를 기록한다.

EINT-009: 동결된 데이터셋 manifest의 train·validation만 사용한다. test(holdout)는
``--final-holdout --record``로 최종 모델을 사전등록한 뒤 manifest당 1회만 개봉·평가한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any, Callable

import requests
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score

KQS_SAFE_EXAMPLE_SCHEMA = "kqs-human-decisions/v3"
KQS_SAFE_FEATURE_SCHEMA = "kqs-engine-features/v1"
KQS_SAFE_FEATURE_SCHEMA_SHA256 = "dd69f54eaa79699ee98ded7090ae9851ff7cd11807733ed5780b752bf1346974"
KQS_LEARNING_CONSENT_POLICY_VERSION = "kqs-learning-consent/v1"
KQS_SAFE_FEATURE_FIELDS = frozenset({
    "data_as_of", "market", "signal_type", "selection_outcome", "selection_rank",
    "execution_session", "config_fingerprint", "reasons", "engine_id",
    "engine_git_sha", "dataset_hash",
})


def require_safe_features(item: dict, split: str) -> dict:
    features = item.get("features")
    if not isinstance(features, dict) or set(features) != KQS_SAFE_FEATURE_FIELDS:
        raise RuntimeError(f"{split} 표본에 허용되지 않은 피처/누락 피처가 있습니다. 사람 태그·메모·거래 결과는 학습할 수 없습니다.")
    if not isinstance(features["reasons"], list):
        raise RuntimeError(f"{split} 표본의 추천 근거 형식이 잘못됐습니다.")
    provenance = item.get("feature_provenance")
    if not isinstance(provenance, dict) or not all(provenance.get(key) for key in
            ("publication_snapshot_id", "recommendation_generated_at", "source_payload_sha256")):
        raise RuntimeError(f"{split} 표본의 추천 게시 시점 출처가 없습니다.")
    return features


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KQS 오프라인 기준 모델 실험")
    parser.add_argument("--api-base", default=os.getenv("KQS_API_BASE", "http://127.0.0.1:18000"))
    parser.add_argument("--token", default=os.getenv("KQS_API_TOKEN"), help="로그인 Bearer 토큰 (또는 KQS_API_TOKEN)")
    parser.add_argument("--model", choices=("majority_class_baseline", "logistic_regression"), default="majority_class_baseline")
    parser.add_argument("--version", default="baseline-v1", help="실험 기록에 남길 모델 버전")
    parser.add_argument("--manifest-id", help="동결 manifest id (기본: 가장 최근 manifest)")
    parser.add_argument("--record", action="store_true", help="성공한 결과를 /api/kqs/training-experiments에 기록")
    parser.add_argument("--final-holdout", action="store_true",
                        help="이 모델을 최종 모델로 사전등록하고 test를 1회 개봉·평가한다(되돌릴 수 없음, --record 필요)")
    return parser.parse_args(argv)


def api_get(base: str, token: str, path: str) -> Any:
    response = requests.get(f"{base.rstrip('/')}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if not response.ok:
        raise RuntimeError(f"GET {path} 실패 ({response.status_code}): {response.text[:400]}")
    return response.json()


def api_post(base: str, token: str, path: str, payload: dict) -> Any:
    response = requests.post(f"{base.rstrip('/')}{path}",
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                             json=payload, timeout=30)
    if not response.ok:
        raise RuntimeError(f"POST {path} 실패 ({response.status_code}): {response.text[:400]}")
    return response.json()


def require_active_learning_consent(base: str, token: str) -> dict:
    payload = api_get(base, token, "/api/kqs/learning-consent")
    current = payload.get("current") or {}
    if current.get("status") != "granted" or current.get("active") is not True or current.get("policy_version") != KQS_LEARNING_CONSENT_POLICY_VERSION:
        raise RuntimeError("학습 사용 동의가 활성화되지 않았습니다. 동의 없는 데이터셋은 내려받거나 학습할 수 없습니다.")
    return current


def load_split(base: str, token: str, manifest: dict, split: str) -> list[dict]:
    manifest_id = manifest["manifest_id"]
    response = requests.get(f"{base.rstrip('/')}/api/kqs/datasets/{manifest_id}/examples?split={split}",
                            headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if not response.ok:
        raise RuntimeError(f"{split} 데이터셋 다운로드 실패 ({response.status_code}): {response.text[:400]}")
    rows = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    header = rows[0] if rows else {}
    if (header.get("record_type") != "manifest"
            or header.get("manifest_sha256") != manifest["manifest_sha256"]
            or header.get("schema_version") != KQS_SAFE_EXAMPLE_SCHEMA
            or header.get("feature_schema") != KQS_SAFE_FEATURE_SCHEMA
            or header.get("feature_schema_sha256") != KQS_SAFE_FEATURE_SCHEMA_SHA256
            or header.get("feature_schema_sha256") != manifest.get("feature_schema_sha256")
            or (header.get("learning_consent") or {}).get("manifest", {}).get("status") != "granted"
            or (header.get("learning_consent") or {}).get("current", {}).get("active") is not True
            or header.get("training_eligible") is not True
            or header.get("hash_verified") is not True):
        raise RuntimeError(f"{split} 데이터셋 manifest/안전 피처 계약이 동결 기록과 다릅니다.")
    examples = [row for row in rows[1:] if row.get("record_type") == "example"]
    if len(examples) != len(rows) - 1:
        raise RuntimeError(f"{split} 데이터셋에 알 수 없는 행 형식이 있습니다.")
    for item in examples:
        require_safe_features(item, split)
    if len(examples) != manifest["counts"][split]:
        raise RuntimeError(f"{split} 표본 수가 manifest와 다릅니다 ({len(examples)} != {manifest['counts'][split]}).")
    return examples


def select_manifest(base: str, token: str, manifest_id: str | None) -> dict:
    items = api_get(base, token, "/api/kqs/datasets").get("items") or []
    if not items:
        raise RuntimeError("동결된 데이터셋 manifest가 없습니다. 홈페이지에서 먼저 데이터셋을 동결하세요.")
    manifest = next((item for item in items if item["manifest_id"] == manifest_id), None) if manifest_id else items[0]
    if not manifest:
        raise RuntimeError(f"manifest {manifest_id}를 찾지 못했습니다.")
    if (manifest.get("example_schema") != KQS_SAFE_EXAMPLE_SCHEMA
            or manifest.get("feature_schema") != KQS_SAFE_FEATURE_SCHEMA
            or manifest.get("feature_schema_sha256") != KQS_SAFE_FEATURE_SCHEMA_SHA256
            or (manifest.get("learning_consent") or {}).get("status") != "granted"):
        raise RuntimeError("이 manifest는 v3 엔진 시점 피처 계약이 아닙니다. 기존 v2는 감사 전용이며 학습/holdout을 실행하지 않습니다.")
    return manifest


def feature_row(item: dict) -> dict[str, Any]:
    """결정 시점 피처만 사용한다. 미래 수익률·실현손익·메모는 절대 넣지 않는다."""
    features = require_safe_features(item, "모델 입력")
    reasons = features["reasons"]
    return {"market": str(features.get("market") or "unknown"),
            "signal_type": str(features.get("signal_type") or "unknown"),
            "selection_outcome": str(features.get("selection_outcome") or "unknown"),
            "execution_session": str(features.get("execution_session") or "unknown"),
            "engine_id": str(features.get("engine_id") or "unknown"),
            "selection_rank": float(features.get("selection_rank") or 0),
            **{f"reason:{str(reason)}": 1 for reason in reasons[:30]}}


def label_row(item: dict) -> str:
    label = (item.get("labels") or {}).get("human_decision")
    if not isinstance(label, str) or not label:
        raise RuntimeError("human_decision 레이블이 없는 표본이 있습니다.")
    return label


def average_predicted_buy_return(rows: list[dict], predictions: list[str]) -> float | None:
    returns = []
    for row, prediction in zip(rows, predictions):
        horizon = ((row.get("labels") or {}).get("forward_returns") or {}).get("20") or {}
        value = horizon.get("return_pct")
        if prediction == "buy" and horizon.get("observed") and isinstance(value, (int, float)):
            returns.append(float(value))
    return round(sum(returns) / len(returns), 4) if returns else None


def metrics(rows: list[dict], predictions: list[str]) -> dict[str, int | float | None]:
    labels = [label_row(row) for row in rows]
    return {"sample_count": len(rows),
            "accuracy": round(float(accuracy_score(labels, predictions)), 6) if rows else None,
            "balanced_accuracy": round(float(balanced_accuracy_score(labels, predictions)), 6) if rows else None,
            "average_return_pct": average_predicted_buy_return(rows, predictions)}


def fit_model(model_name: str, train_rows: list[dict]) -> tuple[Callable[[list[dict]], list[str]], dict]:
    labels = [label_row(row) for row in train_rows]
    if not labels:
        raise RuntimeError("train 표본이 비어 있습니다.")
    if model_name == "majority_class_baseline":
        majority = Counter(labels).most_common(1)[0][0]
        return (lambda rows: [majority] * len(rows)), {"classifier": "most_frequent_human_decision", "majority_label": majority}
    if len(set(labels)) < 2:
        raise RuntimeError("로지스틱 회귀에는 train 구간에 서로 다른 판단 레이블이 2개 이상 필요합니다.")
    vectorizer = DictVectorizer(sparse=True)
    matrix = vectorizer.fit_transform([feature_row(row) for row in train_rows])
    classifier = LogisticRegression(max_iter=1_000, class_weight="balanced", random_state=42)
    classifier.fit(matrix, labels)

    def predict(rows: list[dict]) -> list[str]:
        return classifier.predict(vectorizer.transform([feature_row(row) for row in rows])).tolist() if rows else []
    return predict, {"classifier": "LogisticRegression", "max_iter": 1_000, "class_weight": "balanced",
                     "random_state": 42, "feature_count": len(vectorizer.feature_names_)}


def run(args: argparse.Namespace) -> dict:
    if args.final_holdout and not args.record:
        raise RuntimeError("--final-holdout은 결과를 반드시 기록해야 하므로 --record와 함께 써야 합니다.")
    manifest = select_manifest(args.api_base, args.token, args.manifest_id)
    require_active_learning_consent(args.api_base, args.token)
    holdout = (manifest.get("holdout") or {}).get("status")
    if args.final_holdout and holdout != "sealed":
        raise RuntimeError(f"이 manifest의 holdout은 개봉할 수 없습니다(상태: {holdout}).")
    datasets = {split: load_split(args.api_base, args.token, manifest, split) for split in ("train", "validation")}
    predict, config = fit_model(args.model, datasets["train"])
    config = {"example_schema": KQS_SAFE_EXAMPLE_SCHEMA,
              "feature_schema": KQS_SAFE_FEATURE_SCHEMA,
              "feature_schema_sha256": manifest["feature_schema_sha256"], **config}
    result = {"manifest_id": manifest["manifest_id"], "model_family": args.model, "model_version": args.version,
              "training_config": {"runner": "scripts/run_kqs_offline_baseline.py", **config},
              "train": metrics(datasets["train"], predict(datasets["train"])),
              "validation": metrics(datasets["validation"], predict(datasets["validation"])),
              "note": "동결 manifest 기반 오프라인 기준 모델. 모델 선택은 train·validation만 사용했습니다."}
    if args.final_holdout:
        # 사전등록(개봉 기록) → test 다운로드 → 같은 모델로 1회 평가 → 기록. 순서를 바꾸지 않는다.
        api_post(args.api_base, args.token, f"/api/kqs/datasets/{manifest['manifest_id']}/holdout", {
            "model_family": args.model, "model_version": args.version,
            "training_config": result["training_config"], "validation": result["validation"],
            "note": "scripts/run_kqs_offline_baseline.py --final-holdout"})
        test_rows = load_split(args.api_base, args.token, manifest, "test")
        result["test"] = metrics(test_rows, predict(test_rows))
        result["note"] += " test는 사전등록 후 1회 개봉해 평가했습니다."
    if args.record:
        saved = api_post(args.api_base, args.token, "/api/kqs/training-experiments", result)
        result["recorded_experiment_id"] = saved.get("item", {}).get("experiment_id")
    else:
        result["recorded_experiment_id"] = None
        result["next_action"] = ("검토 후 --record로 train·validation 결과를 기록하거나, 최종 모델 하나만 "
                                 "--final-holdout --record로 test를 1회 개봉해 평가합니다.")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.token:
        print("오류: --token 또는 KQS_API_TOKEN이 필요합니다.", file=sys.stderr)
        return 2
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, requests.RequestException, ValueError) as error:
        print(f"오류: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
