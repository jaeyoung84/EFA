# EINT-011 — 통합 추천 상태·후보·근거 화면

2026-09-29. **EINT-011 화면/API 및 EINT-011A 후보 시세 조회 연결을 로컬 검증했다.**
AWS 배포·운영 확인·서비스 재시작은 하지 않았다. 엔진은 0.6.0 / EINT-007A 그대로다.

## 화면이 읽는 자료

기본 `KQS_RECOMMENDATION_SOURCE=snapshot`에서는 홈페이지가 게시 SQLite를 읽기 전용으로
검증한 뒤 아래 화면을 만든다. 후보를 다시 생성하거나 PC 브리지로 자동 대체하지 않는다.

| 읽기 전용 API | 표시 내용 |
| --- | --- |
| `/api/kqs/recommendations` | 기존 표준 추천 계약 |
| `/api/kqs/status` | 유효 게시 여부·선정 진입 수·엔진 버전·데이터 기준일 |
| `/api/kqs/review-candidates` | 같은 게시의 선정 진입 후보·실제 추천 이유·개인 검토 메모 |
| `/api/kqs/review-candidates/{symbol}/packet` | 당시 이유·지표·청산 참고·코드/자료 해시·실행 ID |
| `/api/kqs/candidate-queue/review-workflow` | 과거 저장 큐가 아니라 현재 유효 게시에 대한 개인 검토 현황 |

각 응답의 `snapshot_id`, `generated_at`, `expires_at`, `engine.data_as_of`는
원래 게시 값을 유지한다. 조회가 만료를 연장하지 않는다. 서로 다른 요청 사이에 새 게시가
발행되면 ID가 달라질 수 있으며, 여러 API 조회를 하나의 원자적 트랜잭션으로 묶지는 않는다.

- 추천 0건은 정상 결과다(D-30). 누락·만료·손상·잘못된 source 설정은 503이며 정상 0건이 아니다.
- 현재 큐 밖의 종목 패킷은 404다. 인증 없이 게시 파일이나 개인 검토 기록을 읽지 않는다.
- 게시 유효성과 운영 타이머/프로세스 건강은 다르다. `operations.verified=false`이고
  화면에는 별도 점검 필요로 표시한다. AWS 상태 검사는 [운영 절차](OPERATIONS.md)의 별도 작업이다.
- 전체 스캔의 레짐 차단 수는 공개 추천 계약에 없다. `blocked_signal_count=null` /
  화면 미제공으로 남기며 0으로 꾸미지 않는다.
- native 자동 모의 포지션·포트폴리오 대조 및 대조 이력은 `not_applicable`이다.
  자동 체결 장부를 사용자 보유분으로 합산하거나 신규 대조 기준선을 저장하지 않는다.
- 검토 완료/보류 버튼은 메모와 당시 근거만 저장한다. 주문·체결·개인 매수매도 원장 변경이 아니다.
- `KQS_RECOMMENDATION_SOURCE=bridge`를 명시한 경우에만 예전 읽기 전용 브리지 경로를 쓴다.

## 과거 자료와 새 이력 분리

기존 문서를 삭제·이동·native로 재라벨링하지 않는다. native 모드에서는 다음 네 컬렉션에
새 자료를 저장하고 조회한다. bridge 모드는 기존 이름을 그대로 사용한다.

| native | 보존되는 bridge 이력 |
| --- | --- |
| `kqs_native_candidate_reviews` | `kqs_candidate_reviews` |
| `kqs_native_candidate_review_snapshots` | `kqs_candidate_review_snapshots` |
| `kqs_native_candidate_queue_snapshots` | `kqs_candidate_queue_snapshots` |
| `kqs_native_candidate_outcomes` | `kqs_candidate_outcomes` |

현재 후보/패킷/검토 workflow의 개인 메모는 기준일·종목뿐 아니라
`publication_snapshot_id`도 일치해야 표시한다. 같은 기준일의 예전 게시 메모는 현재 판단으로
간주하지 않는다. 검토 저장 때마다 실제 게시 근거 패킷을 append-only 이력에 남긴다.

일별 큐는 기존처럼 KST 하루 한 건으로 갱신하므로 모든 재게시의 불변 보관소는 아니다.
개인 최신 메모도 기준일·종목 단위로 갱신되며 이전 값은 검토 패킷 이력에서 확인한다.
회고 집계는 일별 관측 자료에 대한 집계이지 현재 유효 게시의 수익 예측이 아니다.

`kqs_paper_decisions` 등 개인 수동 원장·데이터셋 컬렉션의 이름과 계약은 변경하지 않았다.
향후 비공개 Mongo 백업/복원 범위에는 **네 native 컬렉션과 기존 legacy 컬렉션 모두** 포함해야 한다.
이번에는 실제 사용자 DB 백업·마이그레이션·삭제를 하지 않았다.

## 시세 수집 연결 — EINT-011A 승인 후 보완 완료

사용자 승인("응 승인할게")과 레버리지는 별도 항목으로 아무것도 바꾸지 말라는 지시에 따라,
`app/backend/scheduler.py`의 KQS 후보 조회만 공통 source 선택 함수에 연결했다.
기본/명시적 snapshot은 `kqs_native_candidate_queue_snapshots`,
명시적 bridge는 `kqs_candidate_queue_snapshots`만 읽는다. 잘못된 source는 실패하며 자동 대체하지 않는다.

코드 차이는 KQS import 두 줄과 후보 조회 한 줄뿐이다. 나머지 스케줄러 코드와
DCA 파일·로직·설정·주기·작업 등록은 변경 전과 같다. 기존 보유분·전략·개인 판단 원장 기반
수집 대상과 한국시장 ticker suffix/중복 제거도 유지했다. 후보 조회 자체는 DB를 읽기만 한다.

EINT-011에서 보류했던 native 후보만의 사후 일봉 수집 **대상 편입**을 로컬에서 검증했다.
실제 일봉 다운로드·운영 수집·사후 수익률 확보까지 성공했다는 뜻은 아니다. 가격이 없으면
미확보로 남기며 추정값/0으로 채우지 않는다. 후보 일별 스냅샷이 먼저 저장돼 있어야 조회 대상이 된다.

EINT-011 검증의 승인 보류는 당시 사실로 보존하고, [EINT-011A 후속 검증](verification/EINT-011A.json)에
변경 전후 해시·DCA 보존·사용자 승인을 별도로 기록했다. AWS 적용/재시작 승인은 별개다.

## 이번 검증과 후속

- EINT-011 당시: 격리 MongoDB 기반 웹/API 65개, scripts 격리 검사 66개, 엔진 합성 회귀 101개: **232개 통과**.
  이 중 신규 검증은 native API 6개·native Mongo 웹 흐름 4개다.
- EINT-011A 현재: 웹/API **69개**(조회 경로 신규 회귀 4개 포함) + scripts **66개**, 총 **135개 통과**.
  기본 native/명시적 native/bridge/잘못된 source와 DB 읽기 전용성을 검증했다.
  시세 요청·스케줄 시작·DCA 실행을 금지 대역으로 막았다.
  엔진은 변경하지 않았으므로 101개 엔진 회귀는 재실행하지 않고 기존 결과와 revision 무결성을 확인했다.
- EINT-011 당시 Node 22 순수 렌더링 두 검사와 화면 모듈 문법 검사 통과. 이유·메모·에러의 HTML escape 확인.
- 원문 보관본 167개/결정 45개 무결성, 초기 이관, 현재 revision 검사 통과.
- 실제 시장 수집·연구 스윕·실제 holdout 개봉·주문·자동 모의체결·AWS 명령은 0회.
- 로컬 Python 3.14만 실행했다. CI 3.12/3.14 설정은 추가했지만 원격 CI/브라우저 E2E는 실행하지 않았다.
- [검증 기록](verification/EINT-011.json)에 파일 해시·환경·승인 보류를 남겼다.
  EINT-001~010 자료를 이번 성공으로 다시 인증하거나 덮어쓰지 않는다.

다음은 EINT-012에서 사용자 수동 판단·거래 시각/중복 요청/원장 일관성을 점검하는 순서다.
EINT-010 자동배포 workflow 승인 대기와 AWS 재검증은 계속 별도다.
