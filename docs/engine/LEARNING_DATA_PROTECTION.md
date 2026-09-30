# EINT-014·015 — KQS 학습 준비도·개인 데이터 보호 계약

2026-09-30 로컬 구현 기준이다. 이 문서는 추천·수동 판단 기록을 장래의 **오프라인** 모델 학습에 사용할 때의 최소 경계를 정의한다. 모델 학습, 자동 추천 변경, 주문·자동 모의체결·실거래는 이 계약으로 허용되지 않는다.

## EINT-014: 데이터 품질 게이트

`GET /api/kqs/decisions/training-readiness`는 사용자 본인의 후보 표본을 읽기 전용으로 점검한다. 신규 v3 manifest 동결은 기존의 표본 수·시간 분할·20일 결과·추천시점 출처 규칙에 더해 다음 조건을 모두 만족해야 한다.

- 서로 다른 판단 레이블이 전체와 각 train/validation/test 구간에 최소 2개 있어야 한다.
- 존재하는 각 판단 레이블은 전체 후보에 최소 10건 있어야 한다.
- 기준일은 최소 10일, 첫·마지막 기준일 간 최소 180일이어야 한다.
- 한 기준일의 표본 비중은 40%를 넘을 수 없다.
- 추천 게시 시점의 시장·신호·엔진 식별자와 출처가 완전해야 하며, 20거래일 레이블은 관측되어야 한다.

리포트는 레이블 수, 날짜 범위·집중도, 구간별 분포, 출처 불일치, 실현 거래 관측 수를 보인다. 이는 형식·표본 편향을 발견하는 최소 장치일 뿐 표본 독립성, 경제적 유효성, 예측력, 투자 적합성이나 자동매매 가능을 증명하지 않는다.

## EINT-015: 학습 사용 동의와 철회

기본값은 **미동의**다. 사용자는 화면 또는 `PUT /api/kqs/learning-consent`에서 `KQS_LEARNING_DATA_ONLY` 확인과 함께 명시적으로 동의해야 한다. 동의 이벤트에는 정책 버전, 시각, 목적(`offline_learning_only`)만 기록하며 추천·주문·자동체결·실거래 권한은 절대 포함하지 않는다.

활성 동의가 없거나 철회되면 다음을 fail-closed로 차단한다.

- 신규 v3 dataset manifest 동결
- v3 전체 검토 JSONL 및 v3 manifest 구간 내보내기
- v3 holdout 개봉과 신규 오프라인 실험 기록
- 오프라인 기준 러너의 데이터 내려받기·적합

철회는 즉시 이후 사용을 차단한다. 이미 동결된 manifest·holdout 개봉·실험 기록은 변경·삭제·재해석하지 않고 감사 자료로 보존한다. 따라서 철회 뒤에는 새로 동의하고 새 v3 successor를 동결해야 한다. 보존 기간, 물리 삭제, 법적 요청 처리는 운영/법률 정책을 확정하기 전에는 자동화하지 않는다.

## KQS 백업·복구 범위

개인 Mongo 논리 백업에는 최소한 다음 KQS 컬렉션이 포함돼야 한다.

```text
kqs_paper_decisions
kqs_decision_outcomes
kqs_manual_write_locks
kqs_learning_consents
kqs_dataset_manifests
kqs_dataset_holdout_openings
kqs_dataset_access_log
kqs_training_experiments
kqs_native_candidate_reviews
kqs_native_candidate_review_snapshots
kqs_native_candidate_queue_snapshots
kqs_native_candidate_outcomes
```

현재 `scripts/mongo_backup.sh`와 `scripts/mongo_restore_drill.sh`는 전체 Mongo DB의 압축 아카이브·SHA-256·임시 DB 복구 훈련을 제공한다. KQS는 다른 사용자 데이터와 원장을 포함할 수 있으므로 아카이브를 Git·공개 로그에 넣지 않고 암호화된 비공개 저장소에 보관해야 한다. 실제 운영 백업 또는 복구 훈련 전에는 [기존 런북](../../BACKUP_RESTORE_RUNBOOK.md)을 따르고, SHA-256 검증과 임시 DB 복구만 수행한다. 운영 DB 덮어쓰기·자동 삭제·자동 복구는 이 단계의 범위가 아니다.

## 모델 실험 전 운영 체크

1. 활성 동의와 데이터 품질 게이트를 다시 확인한다.
2. 개인 Mongo 백업의 SHA-256과 임시 DB 복구 훈련 결과를 별도 비공개 기록으로 남긴다.
3. 새 v3 manifest만 동결하고 train/validation에서만 모델을 정한다.
4. 최종 모델 하나를 사전등록한 뒤에만 test를 한 번 개봉한다.
5. 결과를 추천·주문·자동체결과 연결하지 않는다. 그러한 제품 변경은 별도 설계·승인이 필요하다.

관련 근거: D-20/D-26/D-36의 소진 구간 재사용 금지, D-27 추천 비개입, D-28/D-44 수동·엔진 원장 분리, EINT-009 불변 manifest/1회 holdout, EINT-013 피처 출처 분리.
