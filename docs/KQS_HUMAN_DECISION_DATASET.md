# KQS 사용자 판단 데이터셋 v1

개인 판단 원장의 학습 전 검토용 다운로드 계약이다. 자동 학습이나 매매 실행은 포함하지 않는다.

## API

`GET /api/kqs/decisions/training-dataset` (로그인 필요)

응답은 UTF-8 JSON Lines 파일이다. 첫 줄은 manifest, 이후 각 줄은 한 건의 사용자 판단이다.
다른 사용자의 데이터와 계정 식별자는 내보내지 않는다. 자유 메모는 기본 제외되며,
필요한 경우에만 `?include_notes=true`로 포함한다.

## 예시 구조

```json
{"record_type":"manifest","schema_version":"kqs-human-decisions/v1","example_count":1,"features_exclude_future_outcomes":true}
{"record_type":"example","features":{"data_as_of":"2026-09-26","signal_type":"swing","reasons":[]},"labels":{"human_decision":"hold","forward_returns":{"1":{"observed":false,"return_pct":null}}}}
```

- `features`: 결정 시점에 알 수 있었던 추천 근거·엔진 메타데이터와 사용자 근거 태그
- `labels.human_decision`: 사용자가 남긴 buy/hold/exclude/sell/review_again 판단
- `labels.forward_returns`: 기준일 뒤 확정된 1·5·20 거래일 성과. 미관측은 `observed=false`, 수익률은 `null`
- `labels.realized_trade`: 사용자가 입력한 매수·매도 기록을 매칭한 실현 손익. 매칭 전에는 `null`
- `label_quality`: 표본별 20일·실현 손익 관측 여부

미관측 성과를 0으로 채우지 않는다. 미래 성과는 피처에 포함하지 않는다. 메모는 자유형
텍스트라 기본 학습 입력에서 제외한다. 이 파일은 사용자 개인 기록이므로 안전한 위치에 보관한다.
실제 모델 학습은 별도의 데이터 품질·표본 수·시간순 분리 검토가 끝난 뒤 진행한다.

## 79단계: 시간순 학습 분할

> EINT-009로 대체: 아래의 `?split=` 구간별 내보내기는 이제 410이다. 분할은 동결 manifest로만 제공한다(맨 아래 절).

`GET /api/kqs/decisions/training-readiness`는 학습 시작 전의 표본 상태를 반환한다.

- 20거래일 결과가 확정된 표본만 학습 분할 후보가 된다.
- 기준일 순서로 train 60%, validation 20%, test 20%를 배정한다.
- train의 20일 결과가 validation 기간에 닿거나 validation 결과가 test 기간에 닿는 표본은 제외한다.
- 기본 기준은 확정 표본 120건 이상, 각 분할 20건 이상, 서로 다른 기준일 5일 이상이다.

준비 상태가 충족되면 다음 파일을 받을 수 있다.

```text
GET /api/kqs/decisions/training-dataset?split=train
GET /api/kqs/decisions/training-dataset?split=validation
GET /api/kqs/decisions/training-dataset?split=test
```

각 파일의 manifest에는 적용한 분할 정책과 제외 표본 수가 남는다. 이 기준은 학습 코드가
검증·테스트 구간을 보고 파라미터를 고르는 일을 막기 위한 최소 조건이며, 모델 성과를 보장하지 않는다.

## 80단계: 오프라인 기준 모델 실험 기록

> EINT-009로 변경: 실험은 `manifest_id`가 필수이고, test 성과는 holdout 개봉 후 사전등록한 모델로 manifest당 1회만 기록한다.

`POST /api/kqs/training-experiments`는 외부 또는 별도 연구 환경에서 수행한 모델 실험의
재현 메타데이터와 구간별 성과를 기록한다. 이 API는 79단계 준비 기준을 통과한 경우에만
동작하며, 기록하는 `train`·`validation`·`test`의 `sample_count`도 현재 고정 분할과 같아야 한다.

- 허용 모델 구분: `majority_class_baseline`, `logistic_regression`, `tree_ensemble`, `external_offline`
- 각 실험에는 모델 버전, 단순 학습 설정, 구간별 표본 수·정확도·균형 정확도·평균 수익률을 남긴다.
- 서버는 사용된 학습 표본, 20일 레이블, 시간 분할 정책의 SHA-256 지문을 계산해 결과에 묶는다.
- `GET /api/kqs/training-experiments`는 본인 실험 이력만 반환한다.

기록된 성과는 사용자가 보고한 연구 결과이며 자동 재검증된 모델 성과가 아니다. 모든 실험은
`recorded_offline` 및 `not_requested` 상태로 저장되며, 이 단계에서는 추천 생성, 모델 승인,
포트폴리오 변경, 모의체결, 주문 및 자동매매 연결을 제공하지 않는다.

## 81단계: 오프라인 기준 모델 러너

> EINT-009로 변경: 러너는 동결 manifest의 train·validation만 읽는다. test는 `--final-holdout --record`에서만 1회 개봉한다.

`scripts/run_kqs_offline_baseline.py`는 인증된 API에서 고정된 세 분할을 직접 내려받아
기준 모델을 학습하고 평가한다. 데이터 준비 기준이 미충족이면 중단하며, 내려받은 표본 수가
준비도 API와 다르면 결과를 기록하지 않는다.

```bash
export KQS_API_TOKEN='로그인_토큰'
python scripts/run_kqs_offline_baseline.py --model majority_class_baseline --version baseline-v1
python scripts/run_kqs_offline_baseline.py --model logistic_regression --version logistic-v1
```

기본 실행은 결과 JSON만 출력하는 dry-run이다. 검토가 끝난 결과만 다음처럼 명시적으로 기록한다.

```bash
python scripts/run_kqs_offline_baseline.py --model logistic_regression --version logistic-v1 --record
```

- `majority_class_baseline`: train 구간에서 가장 많이 나온 사용자 판단 하나를 항상 예측한다.
- `logistic_regression`: 시장·신호·선정 상태·추천 근거 태그 등 결정 시점 피처만 사용한다.
- 모델 선택과 설정 변경은 train·validation까지만 사용해야 하며, test는 최종 1회 보고용이다.
- `average_return_pct`는 모델이 `buy`로 예측한 표본의 확정 20거래일 사후 수익률일 뿐, 매수 지시나 예상 수익률이 아니다.

이 도구는 모델 파일을 추천 엔진이나 홈페이지에 배포하지 않으며, 자동 추천·자동 체결·주문을 수행하지 않는다.

## EINT-008: 만료된 추천 보유분의 매도·부분매도와 lot

추천은 하루 만에 만료되지만 보유는 계속된다. 그래서 매도는 추천 화면이 아니라 보유 현황에서 기록한다.

- `GET /api/kqs/decisions/positions`: 매수 1건 = lot 1개. 매도는 오래된 lot부터(FIFO) 수량 단위로 배분한다.
  종목별 잔여 수량·평균단가(잔여 lot 원가 기준)·lot 목록(원 추천 `signal_id`, 매수일, 잔여, `recommendation_expired`)을 준다.
  보유 수량을 넘는 과거 매도 기록은 `data_issues`로 드러내고 lot에 배분하지 않는다.
- `POST /api/kqs/decisions/positions/{symbol_key}/sell`: 수량·체결가·수수료·세금·거래일(`trade_date`, 기본 오늘 KST)·메모.
  보유 수량 초과는 409, 미래 거래일이나 가장 오래된 보유 lot 매수일 이전 거래일은 422다. 동시에 들어온 매도가 합쳐
  보유를 넘으면 응답 전에 되돌리고 409를 준다. 기록은 `record_origin: "position_exit"`, `signal_id: null`이다.
- 추천 카드에서 하는 기존 `sell` 판단도 이제 보유 수량을 넘으면 409다.
- 실현 손익(`realized`)은 매수 lot별로 계산한다. 부분 매도는 매도분만 실현하고, 매수·매도 비용은 수량 비율로
  나눈다. `status`는 `partial`/`closed`, `allocations`에 배분 내역이 남는다. 수익률 분모는 매도된 수량의 매수 원가다.
- 보유 청산 기록은 추천에 대한 판단이 아니므로 학습 판단 표본과 판단 전환율 통계에서 빠진다
  (`position_exit_records_excluded`, `position_exit_count`). 효과는 해당 매수 표본의 `realized_trade` 레이블로만 반영된다.
- 내보내기 manifest는 `kqs-human-decisions/v2`다(v1과 차이: 부분 lot 레이블, 청산 기록 제외).
  학습 분할과 실험 지문(80단계)은 20일 레이블만 쓰므로 바뀌지 않는다.

## EINT-009: 불변 데이터셋 manifest와 학습/holdout 규약

문제: 79~81단계 분할은 요청마다 원장 전체로 다시 계산돼, 데이터가 늘면 경계가 움직여 옛 test 표본이 train으로
넘어갔다. test 파일도 몇 번이든 받아 평가할 수 있었고, 러너는 dry-run마다 test 성과를 출력했다. AGENTS.md
("고정 manifest/분할/개방 이력 없이 테스트 구간을 반복 평가하지 않는다")와 D-20/D-26(소진 구간 재사용 금지) 위반이다.

1. **동결** — `POST /api/kqs/datasets` (홈페이지 "데이터셋 동결")
   - 20일 레이블이 확정된 판단 표본을 분할해 불변 manifest로 저장한다. 수정·삭제 API는 없다.
   - 저장 내용: 표본 id, 기준일, 표본별 내용 SHA-256, 구간별 기준일 경계, 정책, 전체 해시(`dataset_sha256`),
     manifest 해시(`manifest_sha256`), 이전 manifest id·해시.
   - 해시 범위: 결정 시점 피처 + 사람 판단 + 확정 20일 레이블. 메모와 실현 손익은 제외한다(이후 매도로 늘어나는 값).
   - 첫 manifest는 기준일 순서 60/20/20이다. 이후 manifest는 **이전 manifest들이 덮은 마지막 기준일 이후의 새 기준일만**
     validation(앞 절반)·test(뒤 절반)로 쓰고, 그 이전 기준일은 모두 train이다. 새 기준일은 최소 2일, 구간별 최소 20건이다.
2. **내려받기** — `GET /api/kqs/datasets/{id}/examples?split=train|validation|test`
   - 내려받을 때마다 현재 원장을 해시와 대조한다. 한 건이라도 다르면(예: 시세 정정으로 20일 수익률이 바뀜) 409이며,
     그 manifest는 쓰지 않고 새로 동결한다.
   - test는 개봉 전 409다. 개봉 후 test 다운로드는 접근 기록(`kqs_dataset_access_log`)에 남는다.
3. **개봉** — `POST /api/kqs/datasets/{id}/holdout`
   - 최종 모델(계열·버전·설정·validation 성과)을 사전등록하면 test를 **manifest당 1회** 개봉한다(유일 인덱스로 강제).
   - 이미 후속 manifest가 동결됐다면 개봉할 수 없다(`retired_sealed`). 그 평가 기간이 후속 manifest의 train에 들어갔기 때문이다.
4. **실험 기록** — `POST /api/kqs/training-experiments`
   - `manifest_id`가 필수이고, train·validation 표본 수가 manifest와 같아야 한다.
   - test 성과는 개봉 뒤, 사전등록과 계열·버전·설정 해시가 같은 모델로 manifest당 1회만 기록한다.
   - 그 밖의 실험은 `test` 없이 기록한다(탐색).
5. **러너**
   - `python scripts/run_kqs_offline_baseline.py --model logistic_regression --version logit-v1` → train·validation만 사용하는 dry-run.
   - `... --record` → 탐색 실험으로 기록.
   - `... --final-holdout --record` → 사전등록, test 개봉, 같은 모델로 1회 평가, 기록. 되돌릴 수 없다.

한계: 원장 주인은 홈페이지에서 모든 사후 성과를 볼 수 있다. 이 규약은 test를 숨기는 장치가 아니라, test 성과 보고가
사전등록된 모델 하나뿐임을 검증 가능하게 남기는 장치다. 옛(동결 전) 실험 기록은 `manifest_id`가 없고, test 보고로 인정하지 않는다.


## EINT-012: 수동 기록 시각·재시도·원장 잠금

EINT-008의 “먼저 저장한 후 동시 초과 매도면 되돌린다”는 동작은 새 API에서 더 이상 사용하지 않는다.
사용자별 DB 잠금 안에서 보유량/시간을 검증한 후 한 번 저장하며 성공 기록을 소급 삭제하지 않는다.
`request_id`가 같은 동일 사용자/정규화 입력의 재시도는 최초 결과를 재생하고 다른 입력은 409다.
기존 ID 없는 요청은 호환하지만 재전송 중복 방지는 보장하지 않는다.

API/목록에 `recorded_at`, `trade_at`, `trade_date`, `trade_time_source`, `request_id`를 제공한다.
거래 시각 입력은 시간대가 있어야 하며 화면은 KST다. 거래일만 입력하면 시각은 null,
둘 다 없으면 서버 기록 시각을 기본값으로 쓰고 `record_time_default`로 구분한다.
과거 기록에 없는 시각을 만들어 채우지 않는다. 같은 날 시각 미상의 선후 관계는 검증할 수 없다.

FIFO는 기록 순서를 유지하며 기존 lot·추천/청산 분리·manifest `kqs-human-decisions/v2`와
동결 지문/holdout 규약은 바꾸지 않았다. 새 시각/요청 출처가 곧 학습 피처 추가를 뜻하지 않는다.
과거 manifest를 새 필드로 소급 재계산하거나 수정하지 않았다.

폼 메모리의 요청 ID는 새로고침 이후 자동 복원하지 않는다. 장애 잔존 잠금은 자동 만료시키지 않으며
확인된 운영자 복구가 필요하다. 원장 저장 후 감사 로그는 best-effort로 실패할 수 있다.
거래 10,000건 한도에서 새 거래를 중단하며 별도 집계/페이지 처리가 후속 과제다.
구체적인 계약·복구 절차·검증 범위는 [수동 원장 문서](engine/MANUAL_RECORDS.md)와
[EINT-012 검증](engine/verification/EINT-012.json)에 남긴다.

다음 EINT-013 제안은 추천 당시 엔진 피처와 사후 사용자 태그/메모/거래 결과의 분리다.
이번에 모델 학습·holdout 개봉·추천/자동체결 연결은 하지 않았다.

## EINT-013: 추천 당시 피처와 사용자 사후 정보 분리

기존 v2에는 사용자가 판단을 적으면서 넣은 `evidence_tags`가 `features`에 있었다.
`include_notes=true`에서는 메모도 `features`에 들어갔다. 따라서 위 v1/v2 설명과
옛 오프라인 러너 문구는 **당시 계약**이며 누수 없는 학습 가능성의 근거가 아니다.

새 `POST /api/kqs/datasets`는 `kqs-human-decisions/v3`만 동결한다.
`features`는 게시 시점 추천·엔진 스냅샷에서 출처가 확인된 필드만,
`human_context`는 사람이 쓴 태그·메모·수동 거래 입력,
`labels`는 사람 판단·사후 수익률/실현 손익만 포함한다.
v3 표본 해시는 허용된 피처·게시 출처·사람 판단·확정 20일 레이블에 묶이고,
피처 정책 자체도 `feature_schema_sha256`으로 잠근다.
메모·사람 태그·실현 손익은 학습 피처에도 v3 동결 지문에도 포함하지 않는다.

예전 v2 동결 문서·해시·개봉/실험 이력은 수정하지 않는다.
v2의 train/validation 및 **이미 개봉된** test는 감사용으로 조회 가능하지만,
새 v2 holdout 개봉·실험 기록과 오프라인 러너의 v2 학습은 차단한다.
v3 successor는 예전 구간을 새 test로 다시 쓰지 않고 EINT-009 기간 규칙을 따른다.
기본 전체 검토 JSONL은 v3이며 `?schema=v2`는 명시적인 과거 형식 확인용이다.
둘 다 동결 전 전체 검토 파일이므로 직접 학습/성능 비교용이 아니다.

필수 추천 ID·종목·시장·엔진 자료 기준일·게시 ID/시각이 누락되거나 불일치하면
v3 준비도/동결에서 차단한다. 과거 빈 필드를 추정으로 채우지 않는다.
[피처·출처 상세 계약](engine/LEARNING_FEATURE_PROVENANCE.md)과
[EINT-013 검증](engine/verification/EINT-013.json)을 참고한다.
실제 모델 학습·실제 holdout 개봉·자동 체결은 이번 단계에서 수행하지 않았다.

## EINT-014·015: 학습 준비도와 사용 동의

v3 신규 동결은 기존 시간 분할·20일 label·추천시점 출처 검사 외에 판단 레이블 분포, 기준일 수·기간·집중도, 각 분할의 최소 두 레이블을 통과해야 한다. 이 검사는 표본·누수 위험을 줄이기 위한 형식 게이트이며 전략 성과나 자동매매 안전성을 보장하지 않는다.

학습 사용 동의는 기본 미동의다. 사용자가 PUT /api/kqs/learning-consent로 KQS_LEARNING_DATA_ONLY 확인과 함께 명시적으로 활성화해야만 v3 manifest 동결, v3 검토/구간 JSONL 내보내기, holdout 개봉, 신규 실험 기록을 할 수 있다. 동의는 오프라인 학습 데이터 사용에만 적용되며 추천·주문·자동체결·실거래 권한을 뜻하지 않는다.

철회하면 이후 v3 사용은 즉시 차단된다. 기존 manifest·holdout·실험 기록은 불변 감사 자료로 남기고 수정·삭제하지 않는다. 보존기한·물리 삭제와 실사용 Mongo 백업·복구는 별도 운영 정책 및 승인 없이는 자동 수행하지 않는다. 상세 계약과 백업 범위는 학습 데이터 보호 문서(docs/engine/LEARNING_DATA_PROTECTION.md)를 따른다.
