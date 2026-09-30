# KQS 추천 읽기 전용 계약 v1

홈페이지는 KQS 엔진의 추천·근거를 **표시만** 한다. 이 계약은 모의 주문, 모의 체결,
실거래 주문, 엔진 파일·DB 수정 권한을 만들지 않는다.

## EINT-003/EINT-004 통합 엔진 경로

현재 기본 공급자는 외부 브리지가 아닌 별도 추천 게시 SQLite다.
홈페이지 인증 API는 `GET /api/kqs/recommendations`, engine/publication 공통 계약은 계속
`kqs-recommendations/v1`이다. 자세한 정책과 미완료 범위는 [게시 안내](engine/PUBLICATION.md)를 따른다.

- `KQS_RECOMMENDATION_SOURCE=snapshot`이 기본. `bridge`를 명시한 경우에만 아래 legacy
  `/api/v1/recommendations`를 읽는다. 자동 fallback/GET에서 엔진 실행·게시·만료 연장 없음.
- 통합 엔진은 `engine_id=iv-kqs-engine`, `trust_mode=component_sha256`, 버전0.3.0,
  개별 실행 파일 SHA와 합성 코드 SHA·설정 SHA·입력 SHA를 기록한다.
  `git_sha/source_commit`은 **이관 원본 commit**이며 현재 홈페이지 clean commit 증명이 아니다.
  `dirty=null`로 알 수 없는 상태를 표시한다. legacy는 여전히 `dirty=false`만 허용한다.
- `publication.snapshot_id=kqs-<run_id>`, 예상 자료 기준일·절대 만료를 고정하고 최초 게시를
  유지한다. native payload에는 설치된 검토 KRX 캘린더 SHA·정책도 고정한다. 공개 기대 시각이
  지난 최신 세션만 허용하고 다음 기대 공개 전으로 만료한다. 최고 기준일의 최신 snapshot이
  만료되면503이다. 추천0건은 유효한 정상 결과다. 미지원 연도/변조 캘린더도 fail-closed다.
- payload/component SHA는 손상·일관성 검출이며 생산자 신뢰 서명/허용 버전 인증은 아니다.
  로컬 파일 권한·읽기전용 mount를 전제한다. HMAC/다중 호스트 AWS 보완은 미완료다.
  캘린더 SHA는 KRX/시장 데이터 수신의 암호학적 증명이나 collector 완전성 보증이 아니다.
- 수동 판단 원장에는 당시 signal/features·engine·추천 생성/만료·publication snapshot ID를
  저장한다. 자동 체결 없음. 과거 매수 추천이 만료된 후의 매도 기록 경로는 별도 보완이 필요하다.

아래 JSON 예시는 기존 bridge 계약을 설명하는 **역사/호환 예시**다. 현재 기준일·만료로
복사하거나 실추천 데이터라고 사용하지 않는다.

## 역할 분리

- KQS 엔진: 전략 계산, 후보 선정, 엔진 자체 연구·모의원장
- KQS 브리지: 엔진의 추천·근거·상태를 읽기 전용 JSON으로 제공
- 홈페이지: 사용자의 매수·관망·매도 판단을 다음 단계에서 별도 원장으로 기록

엔진의 모의원장과 사용자의 수동 모의원장은 합산하지 않는다. 전자는 전략의 연구 기준,
후자는 개인 의사결정 학습 데이터다.

## 엔드포인트

`GET /api/v1/recommendations`

브리지는 `127.0.0.1:18100`에만 바인딩하고, 응답 크기를 제한한다. 홈페이지는 만료·dirty
엔진·해시 불일치·계약 위반 시 추천을 표시해 매수 판단에 쓰지 않는다.

```json
{
  "schema_version": "kqs-recommendations/v1",
  "generated_at": "2026-09-28T08:00:00+00:00",
  "expires_at": "2026-09-29T08:00:00+00:00",
  "engine": {
    "engine_id": "korea-quant-system",
    "run_id": "2026-09-28-daily-1",
    "git_sha": "abcdef1",
    "dirty": false,
    "data_as_of": "2026-09-26",
    "dataset_hash": "sha256:..."
  },
  "signals": [
    {
      "signal_id": "2026-09-26-005930-entry",
      "action": "entry",
      "side": "buy",
      "symbol": "005930",
      "market": "KOSPI",
      "name": "삼성전자",
      "signal_type": "swing",
      "selection_rank": 1,
      "selection_outcome": "selected",
      "execution_session": "next_trading_session",
      "config_fingerprint": "...",
      "audit_row_hash": "sha256:...",
      "reasons": [
        {"code": "trend", "label": "추세 조건 충족", "evidence": "엔진이 계산한 사실 기반 조건"}
      ]
    }
  ],
  "integrity": {"payload_sha256": "SHA-256 of the JSON object excluding integrity"}
}
```

## 필수 규칙

- 선정된 `entry` 신호는 하루 0~3개이며 동일 종목을 중복 선정하지 않는다.
- 0개는 정상 결과다. 홈페이지가 빈 슬롯을 채우거나 순위를 바꾸지 않는다.
- `entry`는 `buy`, `exit`는 `sell` 신호만 사용한다.
- 각 신호에는 사람이 검토할 수 있는 구조화된 근거를 최소 하나 포함한다. LLM이 근거를
  만들어 내거나 수익을 예측한 문구를 넣지 않는다.
- legacy `dirty=true`, 만료된 응답, 데이터 기준일 불명, 해시 불일치는 fail-closed다.
- v1에서는 `payload_sha256`으로 전송 손상을 검출한다. 브리지 복구 후에는 만료 시각,
  `run_id`, 요청 경로를 포함한 HMAC 서명을 추가해 재전송 공격도 차단한다.

## 점검 명령

기본 게시 저장소를 읽는다. 파일이 없으면 생성하지 않고 실패한다. legacy 점검은 명시적으로 선택한다.

```bash
python3 scripts/check_kqs_recommendation_contract.py --source snapshot \
  --snapshot-db /absolute/path/to/recommendations.sqlite3
# legacy bridge를 점검할 때만:
python3 scripts/check_kqs_recommendation_contract.py --source bridge
```

이 명령이 성공하기 전에는 홈페이지에서 KQS 추천을 매수 판단용으로 활성화하지 않는다.
