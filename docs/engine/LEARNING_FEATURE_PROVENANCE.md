# EINT-013 — KQS 학습 피처 출처 분리

2026-09-30 로컬 구현·검증. 퀀트 엔진 0.6.0 / EINT-007A와 별개 레버리지 DCA는 변경하지 않는다.
이 단계는 **추천 → 사람 판단·수동 기록 → 장래의 학습**을 안전하게 준비하는 데이터 계약이다.
실사용 데이터 모델 학습, 자동 추천 조정, 주문·자동 모의체결, AWS 배포는 실행하지 않는다.
러너의 가짜 API 단위 테스트에는 소량 합성 표본으로 적합하는 경우가 있어, 학습 실행 자체가 절대 0회였다는 뜻은 아니다.

## 발견한 누수와 현재 조치

기존 `kqs-human-decisions/v2` JSONL은 사용자가 판단과 함께 적는 `evidence_tags`를
`features`에 넣었다. 오프라인 로지스틱 러너가 그 태그를 실제 설명 변수로 벡터화했다.
예를 들어 사람이 `buy` 결정을 남기며 `["buy"]` 태그를 적으면 모델이 목표 레이블을
다시 입력으로 보는 셈이다. `include_notes=true`에서는 자유 메모도 `features.human_note`에 들어갔다.
따라서 예전 v2의 정확도·수익률을 누수 없는 예측 성과로 해석하면 안 된다.

새 동결은 `kqs-human-decisions/v3`이고, `features`에는 게시된 추천/엔진
스냅샷에서 복사한 필드만 둔다. 사람 태그·메모·거래 입력은 `human_context`,
사람 판단과 사후 수익률/실현손익은 `labels`에 분리한다.
러너는 v3 스키마·고정 피처 계약 지문·필드 허용 목록·게시 출처를 확인한 뒤에만
train/validation을 내려받아 학습한다. 한 표본이라도 예상치 못한 필드가 섞이면
다수 클래스 기준선까지 포함해 학습과 holdout 사전등록 전에 중단한다.

| 영역 | v3 필드·출처 | 학습 입력 여부 |
| --- | --- | --- |
| `features` | 추천 당시의 `recommendation_snapshot`, `engine_snapshot`에서 나온 기준일·시장·신호·선정 사유/순위·실행 세션·엔진/설정/자료 지문 | 허용 목록만 가능 |
| `feature_provenance` | 원장의 게시 ID·생성 시각, 저장된 추천/엔진 원본 스냅샷의 SHA-256 | 학습 입력 아님, 동결 지문 대상 |
| `human_context` | 사람이 선택한 태그와 수동 수량·가격·비용·거래일/시각, 요청 시 메모 | 학습 입력 금지 |
| `labels` | 사람 판단, 1·5·20거래일 사후 결과, 수동 lot 실현 손익 | 정답/사후 관측, 피처 금지 |

`features` 정확한 허용 키는 `data_as_of`, `market`, `signal_type`,
`selection_outcome`, `selection_rank`, `execution_session`,
`config_fingerprint`, `reasons`, `engine_id`, `engine_git_sha`,
`dataset_hash`다. 허용 목록은 기록/평가 가능성을 위한 계약이지 경제적 효용이
확인됐다는 주장이 아니다. 러너의 현재 로지스틱 기준선은 이 중 일부 범주값과
추천 사유만 벡터화한다. `evidence_tags`, `memo`, `human_decision`,
수량·가격·수수료·세금, 사후 수익률·실현 손익은 벡터화하지 않는다.

## v2 보존과 v3 동결 경계

- 기존 v2 manifest 문서·표본 SHA·전체 SHA·이전 manifest 연결·개봉/실험 이력은
  수정·삭제·재계산하지 않았다. v2 해시 검증 함수도 그대로 남겨 당시 JSONL을
  감사 목적으로 읽을 수 있다. 이미 개봉된 v2 test 조회는 기존처럼 접근 로그가 남는다.
- v2는 `training_eligible=false`, `legacy_feature_leakage_warning=true`로 표시한다.
  새 v2 holdout 개봉과 v2 신규 학습 실험 기록은 409로 거부한다.
  오프라인 기준선 러너는 v2를 구간 다운로드 전부터 거부한다.
  과거 이미 기록된 v2 성과는 지우지 않지만 v3 성과와 합산/비교해 개선을 주장하지 않는다.
- 새 `POST /api/kqs/datasets`는 v3만 만든다. `example_schema`,
  `feature_schema=kqs-engine-features/v1`, 정적 피처 정책 SHA-256,
  표본별 출처·피처·사람 판단·확정 20일 레이블을 새 manifest에 잠근다.
  런타임 러너도 동일 정책 SHA를 고정 검증한다.
- 추천 ID·종목·시장·자료 기준일이 원장과 저장된 추천/엔진 스냅샷에서
  일치해야 한다. 게시 ID·생성 시각·추천 이유/엔진 식별자가 빠진 표본은
  v3 준비도에서 불일치 건수로 보여주고 동결을 422로 중단한다.
  과거 원장을 추정값으로 메우지 않는다.
- 기존 v2가 있다면 v3는 EINT-009의 동일한 successor 규약을 쓴다.
  이전 평가 구간 뒤의 **새 기준일만** validation/test에 간다.
  이전 sealed v2 holdout은 successor가 생기면 `retired_sealed`로 남고 다시 열지 않는다.
  이전 날짜가 successor의 train에 들어가는 것은 원래 규약이며, 소진된 예전
  test를 새 test처럼 다시 쓰는 것이 아니다.

동결 전 전체 검토 파일 `GET /api/kqs/decisions/training-dataset`은 기본 v3이며
분할 없는 **검토용**이므로 `training_eligible=false`다.
`?schema=v2`는 당시 구조를 살펴보는 명시적 감사 경로다.
학습은 반드시 동결한 v3 manifest의 train/validation 파일을 사용한다.
`?include_notes=true`의 메모도 v3에서는 `human_context.memo`에만 나타난다.
v2감사용 내려받기를 학습 파일로 사용하지 않는다.
홈페이지는 v3 새 동결과 v2 보관 전용을 구분해 표시한다.

## 검증·남은 한계

- 합성 MongoDB 회귀: v3 태그/메모/거래 입력 분리, 피처 허용 키,
  원본 스냅샷 변조 시 409, 출처 누락 시 준비도·동결 차단,
  과거 v2 해시/이미 개봉된 test 감사 조회, v2→v3 successor와 기간 경계.
- 가짜 API 러너 회귀: v2·정책 지문 불일치·주석이 섞인 피처를
  데이터/holdout 개방 전에 거부; 사용자 주석을 벡터화하지 않음;
  원래 사전등록→test→실험 기록 순서 유지.
- 기존 KQS 관련 backend 95개, 보조 70개 통과. 실제 개인 자료·실제 holdout은
  열지 않았고 장기 전략 연구 결과를 다시 계산하지 않았다.
  자세한 명령·파일 SHA·환경·초기 테스트 실수는 [EINT-013 검증](verification/EINT-013.json)에 남긴다.

`feature_provenance.source_payload_sha256`는 원장에 복사된 추천/엔진 스냅샷을 동결할 때
계산한다. 원래 게시 SQLite의 과거 원본 바이트와 독립적으로 대조한 증거는 아니다.
즉 수동 판단 전의 출처가 신뢰할 수 있게 기록됐는지를 검사하지만,
DB가 동결 전에 변조되지 않았음을 암호학적으로 증명하지는 않는다.
기존 기록 중 게시 ID나 엔진 기준일이 없으면 v3 동결을 진행할 수 없다.
기존 v2에서 나온 테스트 결과를 v3로 “정정”하거나 holdout 소비를 되돌리지 않는다.
학습을 실제로 시작하기 전에는 개인 원장 비공개 백업/복구,
데이터량·클래스 불균형·표본 독립성과 동의/보존 정책도 별도 확인이 필요하다.

관련 과거 결정 D-20/D-26의 소진 구간 재실험 금지, D-27 추천 비개입,
D-28/D-44 엔진 자동 원장과 사용자 수동 원장 분리, EINT-009 불변 분할/1회 개봉은 유지한다.
DCA·스케줄·주문/자동체결 경로는 수정하지 않았다.
