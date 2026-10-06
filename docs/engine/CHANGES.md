# 엔진 변경/검증 근거 이력

과거 항목은 유지하고 후속 정정·검증 결과를 추가한다. 문서가 최신 원문 결정을 대체하지는 않는다.

## EINT-001 — 2026-09-29: 재검토 및 근거 기준선 보존

- 사용자 요청: 엔진 통합을 재검토하고 과거 실험 반복/이전 작업 재발을 막을 근거를 남긴다.
- 원본: Ubuntu-26.04 `/home/gram/korea-quant-system`, commit `0c6b2e8308cc783ea5c4a50c18ff213203631ba2`.
- 보존: 167개 원문 사본, 1,902개 자료 목록/해시, 45개 결정 색인. staged/unstaged 패치,
  저장소 밖 연구 러너/트랙 로그/forward 로그, 홈페이지 검토 코드도 보존.
- 재사용: D-3/D-4/D-7/D-20/D-21/K-5 기각/보류 근거; D-25/D-33 최신 기준선/개정;
  D-36 hashlock 개정5의 소비/INCONCLUSIVE; D-38~D-44 과거 연구/전향 운영 규약.
- 새 확인: 읽기 전용 DB·감사 기준일9/23 일치, 원본 파일 사전등록 해시9/9 일치,
  원본 Git 상태 수집 전후 동일. 당시 환경 버전도 수집(과거 실험 환경과 동일하다고 주장하지 않음).
- 과거 연구 재실행: **0회**. 수집 API·전향 러너·자동체결·모델 학습 실행도 없음.
- 변경: 근거 문서/보관본/검사 도구/격리 도구 테스트/개발 지침/근거 CI만 추가.
  기존 엔진 규칙·설정·원장 및 홈페이지 기능 코드는 변경하지 않음. 커밋/배포 없음.
- 새 검증 계획: 보관본 해시/원본 바이트/색인/참조 검사, 새 도구의 변조·누락 등 격리 테스트.
- 한계: raw DB/데이터/보고서/Git 이력 실제 비공개 백업, 실행 package 이전,
  기존 엔진 전체 현재 테스트, AWS 동작 확인은 완료하지 않았음.
- 후속: REVIEW의 P0 추천 snapshot/자동 체결 분리/holdout 개방 제한부터 통합에 반영.

## 이후 변경 기록 양식

```text
변경 ID / 날짜 / 요청:
대상 코드·규칙·트랙 / 변경 전후 버전·hash:
재사용하는 결정·원문·산출물 / 당시 검증 상태:
동결·소진·미개방·전향 데이터 구분:
새 검증이 필요한 이유 / 실행하지 않을 옛 실험:
실제 검증 명령 / 결과 / 실행횟수 / artifact hash:
데이터 기준일·snapshot / 비용·파라미터 / 의존성·난수:
표본 리셋·새 사전등록·승인 필요 여부:
사용자 수동 원장/주문·체결 경계 영향:
한계 / 미완료 / 다음 작업:
```

## EINT-001 검증 완료 추가 기록 — 2026-09-28 22:22:23 UTC

- `python3 scripts/check_engine_evidence.py`: exit0, 보관167개/결정45개, errors0,
  기존 사전등록 원본 해시9/9 일치. 검사 범위는 자료 무결성이지 현재 엔진 성과가 아니다.
- `python3 -m unittest discover -s scripts/tests -p 'test_engine_evidence.py' -v`:
  새 도구의 격리 테스트 **15개 통과**. backend fixture/DB/엔진/시장 API 사용 없음.
- 첫 무결성 검사에서6개 보관본의 혼합 CRLF 변환을 검출. 원본 CRLF 줄 위치를 명시하고
  **원래 바이트를 복원한 SHA/크기**까지 검증하도록 보완. 원본/보관 코드 내용은 수정하지 않았다.
- `--topic index-tsmom`: consumed/INCONCLUSIVE 및 후속 hashlock 개정을 검색으로 확인.
- `--require-full-backup`: exit1 **예상 결과**. 자료1,735개는 목록/해시만 있으며 백업 완료 아님.
- `git diff --check`: 기존 tracked 변경의 whitespace 검사 통과. 새 파일도 별도 검사한다.
- GitHub CI 설정은 추가했으나 원격 워크플로를 실행/배포하지 않았음. 커밋 없음.

## EINT-002 — 2026-09-29: 실행 핵심 패키지 이전 (변경 전 계획)

- 요청: 다음 통합 단계 진행, 모델 상향 후 재검토·진행.
- 범위: `engine/kqs` 독립 패키지에 strategy/eligibility/reversion_scanner 핵심을
  보관 기준선과 바이트 동일하게 이전. 새 추천 전용 adapter와 읽기 전용 SQLite CLI 추가.
- 재사용: D-1 시총 proxy 티어200/150, D-2 청산 RSI50, D-19 시장필터200/bearish,
  D-24 rank/symbol/swing 우선 전역3, D-27 감사 사유, D-28 식별자 연결 원칙.
  D-25/D-33/D-36 및 Pullback 소진/전향 규약은 그대로 보존하고 재평가하지 않는다.
- 경계: daily_picks_cli/paper_trading/연구 러너/DB 생성·수집 경로는 설치 대상에서 제외.
  원본 WSL, 홈페이지 dirty 코드, 수동 원장, 운영 배포는 수정하지 않는다.
- 새 동작: 날짜·자료 품질 검증, benchmark 기준일 검증, 순수 선정·실제 지표 설명,
  코드/설정/사용 자료 지문. 기존 auto 원장과 다른 수동 추천 프로토콜로 명시한다.
- 검증 이유: import/설치 경계와 새 adapter의 입력검증/순서/읽기전용성이 새 코드다.
  합성/임시 데이터만 사용해 테스트하고 이관 파일 SHA 및 독립 설치를 검증한다.
- 실행하지 않을 항목: 기존 성과 재생·파라미터 스윕·consumed holdout·전향 러너·자동체결.
- 데이터 수집/캘린더 운영·추천 API snapshot 저장/만료·AWS 배포는 후속 단계다.

## EINT-002 완료 검증 기록 — 2026-09-28T22:42:29.749564+00:00

- 버전: `iv-kqs-engine 0.1.0`, 새 프로토콜 `kqs-manual-recommendations/v1`.
  코드 SHA `ce271014f7b910e257f63e635f58fe5c273b99713d40ac41bff73dbd8c60c0d0`.
- 원본4파일(__init__/strategy/eligibility/reversion_scanner) source SHA와 바이트 일치.
  기존 __init__의 마지막 빈 줄도 그대로 보존한다. 연구용 OFF 기본값은 수정하지 않고
  adapter가 D-19의200/bearish를 명시한다. 연구 스윕/holdout/전향 러너 재실행 **0회**.
- 새 구현: 입력/ETF 기준일·누락·중복·미래·유효 숫자 검증; 전역3 및 동률 순서;
  swing/long_term 실제 지표 설명과 RSI50 청산 참고; 코드/설정/binary64 입력 지문;
  호출 문맥별 run_id/candidate_id. 수익률 예측/성과 재검증으로 주장하지 않는다.
- SQLite `mode=ro`/`query_only`/단일 읽기 transaction; stdout JSON만 출력.
  auto 원장/수동 원장/주문·자동체결/수집 API/모델 학습 호출 **0회**.
  auto 체결/연구/수집 모듈은 wheel에서 제외하고 설치 충돌도 감지한다.
- 검증 명령: `python -m unittest discover -s engine/kqs/tests -v`.
  **새 회귀32종류, Python3.14 임시 환경과 Python3.12.14 일회용 컨테이너의 실제 wheel에서
  각각32개 통과**. 동일 잠금 pandas2.3.3/numpy2.3.5 및 하위 의존성을 사용했다.
  최초 개발28개 실행 중 테스트 래퍼 인자명 충돌1건을 고치고 후속 검증 통과.
  총5회 실행은 새 변경의 회귀 검증이며 기존 금융 연구 반복이 아니다.
- `pip wheel --no-deps ./engine/kqs` / 임시환경 wheel 설치 / `pip check` 통과.
  wheel10개 module 바이트가 native src와 일치; cwd 밖 import/CLI 정상.
  wheel SHA `9884c650dd7bd98c75d9e183f760b6743652018cf93c1783d54b66b4c2a4f6e6`.
- `python3 engine/kqs/tools/check_migration.py`: 최초 이관4개, errors0.
  `python3 scripts/check_engine_evidence.py`: 보관167개/결정45개, errors0.
  근거 도구 격리 테스트15개도 통과. tracked whitespace 검사 통과.
  새 원본 __init__의 EOF 빈 줄 경고는 바이트 보존을 위한 의도적 예외다.
- 해시/환경/명령/실행범위/한계는 `verification/EINT-002.json`과 최초 이관 manifest에 보존.
  당시 전체 엔진 환경 pandas3.0.5/vectorbt1.1.0과 동일하다고 주장하지 않는다.
- 기존 WSL/DB/홈페이지 dirty 기능 변경·커밋·배포 없음. 원시 데이터1,735개 비공개 백업은
  여전히 미완료이며 원본 삭제 금지. 실시장 데이터 스캔/수집 성공도 이번 테스트로 주장하지 않는다.
- 후속: 저장 시 고정되는 생성·만료/기준 거래일 추천 snapshot과 기존 읽기 API 계약 연결.
  이후 캘린더/수집/운영 worker와 AWS PC 독립 운영, 수동 데이터/학습 잠금 순서로 진행.
  새 출력은 게시 준비 완료가 아니며 웹에서 그대로 노출하거나 옛 D-33/D-44 표본에 합산하지 않는다.

## EINT-003 — 2026-09-29: 불변 추천 게시 저장과 홈페이지 연결 (변경 전 계획)

- 요청: 이전에 안내한 다음 단계 진행/재개.
- 재사용: EINT-002 승인된 추천/실제 지표, D-19/D-24 선정 설정,
  D-27 최초 기록 보존/감사 식별자, D-28/D-44 엔진 auto 원장과 사용자 수동 원장 분리,
  D-30 추천0건 정상. 과거 연구/consumed holdout/전향 러너는 재실행하지 않는다.
- 범위: 별도 SQLite 게시 저장소, 고정 생성/만료와 run_id 멱등, 읽기전용 GET/기존
  사용자 판단 연결, 공통 v1 계약·패키지·화면 추적 정보·로컬/운영 구성의 준비 변경.
- 기존 dirty router/화면/테스트 내용은 그대로 유지하고 연결 지점만 수정한다.
  원본 WSL/가격 DB/운영 AWS/개인 원장 내용을 변경하지 않는다.
- 새 테스트 이유: 게시 저장/만료/동시 기록/최신 선택/변조/백엔드 연결이 새 동작이다.
  합성 데이터·임시 DB·격리 인증/원장 대역만 사용해 검증하고 실체결/수집/학습을 호출하지 않는다.
- 정책: 절대 만료시각과 예상 자료 기준일을 명시해야 게시 가능. 같은 run_id 재게시로
  만료 연장 금지, 오래된 자료 재활성화/이전 snapshot 자동 fallback 금지.
  검증된 KRX 거래일/공표 정책은 다음 단계까지 미완료이므로 날짜를 자동 추정하지 않는다.
- 산출물: 신규 runtime0.2.0/successor manifest와 EINT-003 검증 근거를 별도로 보존.
  EINT-001 archive/EINT-002 최초 manifest 및 검증 파일은 덮어쓰지 않는다.

## EINT-003 완료 검증 기록 — 2026-09-29

- 버전: `iv-kqs-engine 0.2.0`, 원본 core4파일 SHA/바이트 유지.
  현재 코드 SHA `a8d7daad1aea62025db74e07d656b130421e623a98999cb5b001796d2b0d62d8`.
  실제 설치 wheel SHA `ccdcebc5deaed271f7066302df1805320ff496ad39f2183c7a5695f37583d507`.
  EINT-002 최초 manifest/검증 및 EINT-001 원문 보관본은 덮어쓰지 않았다.
- 재사용: D-19/D-24 설정·선정, D-27 최초 기록/감사, D-28/D-44 auto 원장 분리,
  D-30 추천0건 정상. 과거 성과/기각/consumed holdout/전향 연구 재실행 **0회**.
- 구현: stdlib 공통 v1 계약과 독립 추천 SQLite 게시 저장. 같은 run_id의 최초 생성/만료/
  내용 유지, 동시 게시 transaction, UPDATE/DELETE 거부, 최신 기준일 우선,
  최신 만료/변조 시 이전 추천 fallback 금지. 시장/개인 DB 게시 거부, GET DB 생성/쓰기 없음.
- worker CLI는 기본 stdout만 출력하며 옵션3개를 모두 명시할 때만 별도 추천 DB에 추가.
  예상 자료 기준일/절대 시간대 만료 필요, 개인 제외 문맥의 전역 게시 거부.
  최대24시간/7달력일은 보수적 상한이며 KRX 캘린더/최신 거래일 검증을 대체하지 않는다.
- 홈페이지: 인증 GET `/api/kqs/recommendations`는 snapshot을 기본으로 읽고 no-store.
  bridge는 명시적 선택만 허용한다. 이유·실제 지표·버전·자료 기준일·생성/만료·코드/자료/
  실행/게시 ID 표시. 기존 사용자 수동 판단에 당시 features/engine/snapshot ID/시각 보존.
  입력 가격/수량은 사용자 기록이며 확인된 시장 체결이 아니다. 주문·자동 체결 없음.
- 기존 dirty router/화면/테스트 내용을 보존하고 연결 부분만 수정했다. 별도 레버리지 DCA/
  auth/strategy/scheduler/시장 calendar 작업은 수정하지 않았다. 원본 WSL/시장 DB/개인 원장은
  수정하지 않았으며 격리 API 검증은 가짜 원장만 사용했다.
- 실행 검증: 설치 wheel을 Python3.14.4 임시 환경 및 Python3.12.14 일회용 컨테이너에서
  각각 **엔진/게시57개 + 인증/수동 근거 API12개 + 후속 지문 guard5개 통과**.
  동일 worker 잠금 pandas2.3.3/numpy2.3.5, API 테스트 의존성은 별도 목록.
  변경 개발 중 엔진 suite7회, API suite9회(초기9개→추가11/12개)와 별도 환경 준비 실패1회.
  이는 새 코드 회귀 검증이며 기존 금융 연구 반복이 아니다.
- 최초53개 엔진 검증에서 합성 신규 세션과 생성일이 맞지 않는 fixture1건 수정.
  초기 API 격리 환경의 httpx 누락으로 import 실패1회 후 테스트 의존성 목록 추가.
  마지막 resource 경고는 테스트 SQLite context가 close하지 않아 발생하여 명시적으로 close.
  제품의 게시/조회 connection은 이미 finally에서 close하고 있었다.
- 실제 wheel14개 모듈 바이트와 native src 일치, `pip check` 통과.
  최초 이관4개/보관167개/결정45개 errors0, 기존 근거 도구15개 통과.
  후속 `check_revision.py`는 등록된 전체 실행 코드/초기 manifest/의존성/패키지 메타데이터를
  import 없이 확인하며 CI는 EINT-003을 명시적으로 선택한다. 변경 시 새 successor/CI 선택 필요.
- 화면 순수 V8 렌더3개(오류/0건/근거·참고·추적정보·수동 form·이스케이프) 통과.
  브라우저 E2E/실제 Node 실행은 하지 않았다. Node 회귀3개와 Python3.12/3.14 CI 설정 추가,
  원격 GitHub CI는 실행하지 않았다. 전체 backend pytest는 실제 DB 정리 fixture 때문에 실행하지 않았다.
- 구성: 두 Dockerfile은 stdlib reader 패키지 설치, compose는 추천 디렉터리 readonly mount,
  env example은 snapshot 기본, state/git/dockerignore 비공개 저장소 제외.
  로컬 compose 정적 검증 통과. 운영 compose 전체 검증은 로컬 `app/backend/.env`가 없어 실패;
  **env_file 항목만 제거한 복사본**의 구조 검증은 통과. 운영 준비 완료로 주장하지 않는다.
  실제 env/키 생성·실제 추천 게시·Docker rebuild/restart·AWS 배포·커밋/푸시는 없음.
- 기록: `engine/kqs/revisions/EINT-003.json`, `verification/EINT-003.json`,
  [게시/설정/한계 안내](PUBLICATION.md). tracked whitespace 검사 통과.
  원본 __init__의 마지막 빈 줄은 최초 바이트 보존을 위한 기존 예외다.
- 한계/다음: KRX 휴장일/공표/최신 수집 세션·운영 worker·AWS PC 독립 운영 미완료.
  기존 상태/후보 큐/전향/포트폴리오 대조는 bridge 의존. 기존 수동 매도도 현재 유효 signal을
  요구하므로 과거 매수 추천 만료 후 보유분 매도/부분 매도 연결은 후속 보완이다.
  해시는 서명/생산자 인증/허용 버전 목록이 아니다. SQLite는 단일 호스트 로컬 디스크 전제.
  실데이터 스캔/수집 성공/전략 성과/학습 안전성은 이번 합성 테스트로 증명하지 않는다.
  원시 자료1,735개 비공개 전체 백업/복구 검증은 미완료, 원본 삭제 금지.


## EINT-004 — 2026-09-29: KRX 거래 세션·공표 최신성 정책

- 요청: 다음 통합 단계 진행. REVIEW의 4단계 KRX 운영 신뢰성 중, 캘린더/공표 지연/
  게시 만료 정책을 먼저 분리 구현한다.
- 재사용: D-19/D-24 추천 설정은 변경하지 않음, D-27 감사 식별자, D-28/D-44 auto 원장과
  수동 원장 분리, D-30 무추천 정상. REVIEW의 2026-09-23 마지막 관측과 사용자 확인
  9/24·25 휴장을 운영 정책 근거로 기록. 전략 스윕/consumed holdout/전향 러너 재실행은 **0회**.
- 범위: `krx-equity-2026` 검토 config, KST 세션/공개 기대/다음 공개 전 만료 검증,
  native payload metadata/SHA, 웹 reader fail-closed, 코드 identity/후속 manifest guard.
  `app/backend/market_calendar.py`의 NYSE/레버리지 기능은 변경하지 않는다.
- 정책: KRX 거래일 공식 일반 규칙과 증시일정을 출처로 2026 휴장 예외를 명시하고,
  9/24·25 추석 휴장/9/28 재개를 회귀 검증한다. `06:00 KST`는 시스템의 보수적
  **공개 기대 시각**일 뿐 KRX나 공급자의 실제 파일 갱신 보증이 아니다.
  unknown year/캘린더 변조/휴장 자료/최신 기대 세션보다 오래된 자료는 거부한다.
- 만료: 생성 후 최대24시간이며, 다음 KRX 세션의 공개 기대 시각을 넘지 않는다.
  동일 run_id 첫 payload 고정은 유지한다. EINT-003 native snapshot에는 새 calendar metadata가
  없으므로 의도적으로 거부되며, 실제 재게시/기존 DB 변경은 이번 단계에서 하지 않는다.
- 경계: 주문·자동 모의체결·실거래·시장 수집·scheduler·AWS 배포·실제 추천 게시·개인 원장
  쓰기 모두 **0회**. 합성 가격과 임시 SQLite만 사용했다. 원본 WSL/시장 DB/기존 사용자
  dirty 파일/legacy bridge 상태 경로는 수정하지 않았다.
- 산출물: runtime `0.3.0`, calendar config와 공통 validator, `EINT-004.json` successor,
  `KRX_CALENDAR.md`. config raw SHA도 component digest에 포함해 코드/휴장일이 같이 추적된다.
  EINT-001 archive/EINT-002 최초 manifest/EINT-003 manifest·검증은 덮어쓰지 않는다.
- 검증: source 기반 engine/publication **60개**, API/manual evidence **12개**,
  successor guard는 config 변조를 포함해 **6개**를 실행 예정이다. wheel/Python3.12 교차
  설치 및 최종 artifact SHA/정적 구성 검증은 완료 기록에 추가한다.
- 다음: 연도별 공식 KRX 일정 갱신/승인 절차, collector의 가격/ETF 기준일·완전성·공표 지연
  run evidence, 그 다음 별도 단일 writer worker/영속 백업/AWS PC 독립 운영.
  캘린더만으로 실제 수집 성공/전략 성과/자동매매 준비를 주장하지 않는다.


## EINT-004 완료 검증 기록 — 2026-09-29

- 버전: `iv-kqs-engine 0.3.0`, 현재 실행 component SHA 묶음
  `bffa465eeeee518ae737ba943e16272fe1e5535f9a6b29c7eca541c9a70a70b1`.
  2026 캘린더 raw SHA는
  `c35228678b6f1059fcd944ec34f20f620207bf5b577d063d0cee52a3f8ab11b9`이며
  component digest에 포함된다. 실제 wheel SHA는
  `78c56320549a2f622d41cd62fbe6192d575df5fa409870224fe2ea11a011b0b8`.
- 구현: native payload에 검토 calendar ID/raw SHA/공개 기대 시각/정책 ID를 고정하고,
  worker와 reader가 같은 설치 config로 data_as_of·최신 기대 세션·만료 상한을 검증한다.
  2026-09-24·25 휴장, 09-28 재개, 09-29 06:00 KST 이후 09-23 자료 거부를 검사한다.
  EINT-003 native payload처럼 metadata가 없는 snapshot은 fail-closed다.
- `06:00 KST`는 시스템 정책의 데이터 공개 기대 시각이다. KRX/공급자의 수신 시각 또는
  market DB 완전성 증명이 아니며, 실제 collector 상태/ETFs/가격 행 검증은 하지 않았다.
  2027 이상이나 비등록 임시 휴장도 새 리뷰 calendar 전까지 게시/조회가 실패한다.
- 검증: source 및 실제 설치 wheel 환경 Python3.14.4, 읽기전용 일회용 Docker
  Python3.12.14에서 각각 engine/publication **60개**, API/manual evidence **12개**,
  revision guard **6개** 통과. `pip check` 통과, wheel에 calendar JSON 포함 확인.
  화면 순수 V8 **3개**(오류/0건/근거·버전·KRX trace/이스케이프) 통과.
- 개발 중 휴장 만료 test가 24시간 상한에 먼저 걸려 다음 세션 상한을 검증하지 못한 1회가
  있었다. 생성/만료를 24시간 이내의 경계로 고쳐 의도한 별도 정책을 검증했다.
  기존 연구/전향/holdout/실시세 스캔/시장 API 호출은 **0회**다.
- `check_revision.py`는 이제 package의 Python 파일뿐 아니라 calendar JSON도 지문에 포함한다.
  calendar raw 변경의 fail-closed 회귀를 추가했다. EINT-004 successor를 CI 기본 검사로 전환했다.
  최초 이관4파일/manifest와 EINT-003 revision·검증은 새 SHA로 덮어쓰지 않았다.
- 주문·자동 모의체결·실거래·실제 추천 게시·수집/스케줄러/AWS 배포·Docker rebuild/restart/
  커밋/푸시·실제 Mongo 수동 원장 쓰기 모두 하지 않았다. EINT-003의 bridge 부분 통합,
  만료된 매수 추천의 사후 매도/lot 처리, 전체 백엔드 pytest와 원격 CI 미실행도 그대로다.
- 다음: 공식 KRX 연간/임시 휴장 갱신의 승인 기록, 별도 collector의 기준일·시장/ETF 완전성·
  공표 지연 immutable run 증거, 이후 단일 writer worker/백업/AWS PC 독립 운영이다.
  캘린더 정책은 전략 성과·자동매매 준비·실제 시세 최신성 보증이 아니다.


## EINT-005 — 2026-09-29: KRX 시장 입력 완전성 검증과 게시 게이트

- 요청: EINT-005 “데이터 수집 검증 worker” 이어서 진행. 가격·ETF 기준일/행 수/중복·누락/
  KOSPI·KOSDAQ 기준일 일치 검사, 성공·실패 불변 run 기록, **검증 성공 데이터만 EINT-004 게시로 전달**.
- 재사용: EINT-004 KRX 세션·공표 기대 정책(`validate_observation_calendar`), D-19/D-24 추천 설정,
  D-27 감사 식별자, D-28/D-44 원장 분리, D-30 무추천 정상. 전략 스윕/consumed holdout/전향
  러너 재실행 **0회**. 새 연구가 아니라 운영 입력 검증 기능이므로 새 합성 회귀가 필요하다.
- 범위: 진행 중이던 `kqs_collection`(quality/store/cli, 0.4.0)을 마무리하고, 누락돼 있던
  **게시 게이트**를 `kqs_runtime.cli`에 연결했다. 게시(`--snapshot-db`)는 이제
  `--quality-report`, `--coverage-manifest`, `--quality-observed-at`까지 6개 옵션이 모두 필요하다.
  보고서 무결성 → 같은 as_of → status=ready 확인 후, 게시 직전 현재 시장 DB로 품질 run을
  **다시 계산해 run_id가 같을 때만** 게시한다. 검증 후 DB 행 변경, rejected, 변조, 다른 기준일은
  종료코드 2이며 게시 DB 파일을 만들지 않는다. 게시하지 않는 stdout 미리보기는 기존과 같다.
- 경계: 이것은 다운로드/적재 collector가 아니다. 이미 준비된 시장 DB를 `mode=ro`로 읽는다.
  품질 run DB는 시장/추천/개인 원장 DB와 분리되며 UPDATE/DELETE trigger로 불변이다.
  주문·자동 모의체결·실거래·실제 수집·scheduler·AWS 배포·실제 추천 게시·개인 원장 쓰기 **0회**.
  합성 가격과 임시 SQLite만 사용했다. 원본 WSL/기존 사용자 dirty 파일은 수정하지 않았다.
- 산출물: `engine/kqs/revisions/EINT-005.json`(EINT-004 successor, 20 components),
  `docs/engine/verification/EINT-005.json`, `check_revision.py` 기본 manifest를 EINT-005로 전환.
  EINT-001~004 manifest·검증 기록은 덮어쓰지 않았다(EINT-004 checker는 현재 코드와 의도적으로 불일치).
  PUBLICATION.md/engine README에 2단계(검증→게시) 명령과 coverage manifest 한계를 기록했다.
- 검증: 설치된 0.4.0 venv에서 engine **72개**(새 게이트 4개 포함), revision **6개**,
  API/manual evidence **12개**, evidence 도구 **15개** 통과. `pip check` 통과, 두 entry point를
  저장소 밖에서 실행 확인. source import 실행에서는 설치가 필요한 1개 외 71개 통과.
- 개발 중 실패: 버전이 이미 0.4.0으로 올라가 있었으나 계약 테스트가 0.3.0을 기대해 1회 실패,
  기대값을 0.4.0으로 수정했다.
- 한계/다음: coverage manifest의 기대 종목 수는 collector와 독립된 출처여야 하며 틀리면 검증도 틀린다.
  공급자 정정·액면분할 보정 오류·조용한 값 변화는 탐지하지 않는다. 게이트는 CLI 경로에 있으므로
  `kqs_publication.store.publish`를 직접 호출하는 코드는 우회 가능하다 — 단일 writer worker와
  잠금/멱등(EINT-006)에서 게시 경로를 하나로 묶어야 한다. 실제 시장 DB 스캔/게시/AWS는 미실행.


## EINT-006 — 2026-09-29: 단일 writer 추천 게시 worker, 잠금·멱등·시도 로그

- 요청: EINT-006 “단일 writer 추천 게시 worker 및 잠금/멱등 처리” 이어서 진행.
  EINT-005 한계(`store.publish` 직접 호출 시 품질 게이트 우회)를 게시 경로 단일화로 닫는다.
- 재사용: EINT-004 세션 정책, EINT-005 품질 검사·불변 품질 run 저장소, store의 run_id 최초값 고정과
  BEGIN IMMEDIATE 직렬화, D-27/D-28/D-30/D-44. 전략 스윕/consumed holdout/전향 러너 재실행 **0회**.
- 범위: 새 설치 진입점 `kqs-publish-worker`(`kqs_runtime.worker`)가 유일한 게시 경로다.
  순서: ① `<게시DB>.writer.lock` 비차단 배타 flock → ② 불변 품질 DB에서 run_id로 **기록된** ready run
  조회(느슨한 JSON 파일 불사용) → ③ 같은 as_of 확인 + 저장된 관측 시각으로 현재 시장 DB 재검사,
  run_id 동일할 때만 → ④ 추천 계산·게시 → ⑤ 별도 불변 시도 로그 기록. 상태는
  published/already_published/blocked_quality/writer_busy/failed, 종료코드 0/2/3.
  `store.publish`는 같은 게시 DB에 묶인 보유 중인 `WriterLock` 없이는 쓰지 않는다.
  `kqs-recommend`는 미리보기 전용으로 게시 옵션을 제거했다(EINT-005의 게시 게이트 옵션 포함).
- 멱등: 같은 run 재실행은 `already_published`, 최초 생성/만료 유지. 잠금 중이면 대기하지 않고
  `writer_busy`로 기록 후 종료. 실패해도 잠금은 해제되며, 잠금 파일은 경쟁을 피하려 삭제하지 않는다.
  시장·품질·게시·시도 로그 DB가 같은 파일이면 시작 전에 거부한다.
- 경계: 주문·자동 모의체결·실거래·수집·scheduler·AWS 배포·실제 게시·개인 원장 쓰기 **0회**.
  합성 가격과 임시 SQLite만 사용했다. 원본 WSL/기존 사용자 dirty 파일은 수정하지 않았다.
- 산출물: `engine/kqs/revisions/EINT-006.json`(23 components), `docs/engine/verification/EINT-006.json`,
  `check_revision.py` 기본 manifest EINT-006 전환, CI가 세 진입점 `--help`를 저장소 밖에서 확인.
  EINT-001~005 기록은 덮어쓰지 않았다. PUBLICATION.md/engine README에 worker 명령·종료코드 기록.
- 검증: 설치된 0.5.0 venv에서 engine **78개**(worker 10개, 잠금 3개 신규), API/manual evidence
  **12개**, revision **6개**, evidence 도구 **15개**, 백엔드 KQS pytest **51개** 통과. `pip check` 통과.
  EINT-005의 kqs-recommend 게시 게이트 CLI 테스트 6개는 worker 테스트로 대체했다.
- 개발 중 실패: `test_kqs_publication_api.py` 4개가 코드와 무관하게 실패했다. 고정 게시가
  2026-09-29 00:00 UTC 생성/02:00 만료인데 API reader·점검 스크립트가 실제 시계를 써서 02:00 UTC 이후
  항상 "만료"가 되는 기존 시한폭탄이었다. 테스트에서 `kqs_publication.store/contract` 시계를 고정하고
  점검 스크립트를 같은 시계 아래 in-process로 실행하도록 고쳤다(제품 코드 변경 없음).
- 한계/다음: flock은 권고 잠금이며 단일 호스트 로컬 디스크 전제다(NFS/다중 호스트 미지원).
  직접 `WriterLock`을 만들어 `store.publish`를 부르는 코드는 품질 게이트 없이 쓸 수 있다 — 잠금은
  동시 writer 1개를, 품질 게이트는 설치된 worker를 보장한다. 시도 로그 알림/모니터링과 정해진 시각의
  자동 실행, 영속 백업, AWS PC 독립 운영은 EINT-007이다.


## EINT-007 — 2026-09-29: PC 독립 운영 준비(수집기 이관·일일 파이프라인·백업·모니터링), 로컬 한정

- 요청/결정: EINT-007 이어서 진행. 사용자 선택 — 수집기 이관 + 초기 시장 DB 1회 업로드, 기존 EC2를
  t3.small로 증설, **이번에는 로컬 구현·검증까지만**(AWS 변경·배포는 결과 보고 후 별도 승인).
- 재사용: 보관본 `korea_quant/krx.py`(엔드포인트·정규화·스키마), D-31(인증키 env→.env, 노출 금지),
  D-11(미래 역채움 금지), D-44(기존 `daily_picks_cli`는 자동 모의체결 포함 → 새 파이프라인에서 실행 금지),
  EINT-004 캘린더, EINT-005 품질/불변 저장, EINT-006 단일 writer. 연구·holdout·전향 재실행 **0회**.
- **실데이터 발견과 EINT-005 규칙 수정**: 원본 DB를 `mode=ro`로 집계만 확인했다. 매 세션 KOSPI 약 28~30,
  KOSDAQ 약 85행이 거래정지·무거래(시가·고가·저가·거래량·거래대금 0, 종가·시총 양수)라 EINT-005 규칙은
  실데이터의 **모든 세션을 rejected**로 만들었다(합성 데이터로만 설계된 결함). 새 규칙은 이 패턴만 허용하고
  역전된 OHLC·가격 있는 거래량0·부분 0 패턴은 거부한다. 2026년 180세션 498,897행 오탐 0,
  2026-09-28 실세션 품질 run `ready`(KOSPI 942/KOSDAQ 1,821). 원본 DB 쓰기·추천 생성은 하지 않았다.
- 범위: `kqs_collection.krx`(표준 라이브러리 수집기, 검토된 세션만 호출, 0행 세션은 실패, 키는 repr/오류에서
  제외), `kqs_collection.coverage`(collection_log 행 수 + 직전 세션 대비 1%/최소5 밴드), `kqs-daily-pipeline`
  (수집→coverage→기록된 품질 run→단일 writer 게시, 불변 실행 로그+status JSON), `kqs_ops`(`kqs-backup`:
  SQLite 온라인 백업·복원 검사·SHA-256·보존 3개, `kqs-pipeline-status`: 기대 세션 게시 여부·6시간 유예 경보),
  `infra/`(worker 이미지 UID 10001·읽기전용 rootfs, engine 전용 compose profile, systemd 타이머 08:30/10:30
  파이프라인·11:30 백업·매시 점검, 실패 시 journal/SNS 경보 훅, 비공개 S3 업로드 훅), `docs/engine/OPERATIONS.md`.
  `.dockerignore`에 `engine/kqs/.venv/` 추가(196MB가 빌드 컨텍스트에 들어가지 않게).
- 경계: AWS 변경·배포·systemd 설치·실제 KRX API 호출·실제 게시·개인 원장 쓰기 **0회**. 원본 WSL 파이프라인과
  DB는 수정하지 않았다(읽기 전용 집계·품질 검사 1회, 임시 파일은 삭제).
- 산출물: `engine/kqs/revisions/EINT-007.json`(31 components), `docs/engine/verification/EINT-007.json`,
  revision checker 기본 EINT-007, CI에 새 진입점·ops 스크립트·compose 검증 추가. EINT-001~006 기록 유지.
- 검증: 설치된 0.6.0 venv engine **99개**(신규 21), source import 98개(설치 전용 1개 제외), worker 이미지
  (Python 3.12, UID 10001, 읽기전용, 네트워크 없음) 65/67(소스 트리 필요 2개는 설계상 제외),
  API **12**, revision **6**, evidence **15**, 백엔드 KQS **51** 통과. compose config·`sh -n`·systemd 달력 확인.
- 배포 시 주의: 다음 EC2 배포 tarball에는 `engine/`을 포함해야 한다(backend 이미지가 `engine/kqs`를 설치).
  WAL 시장 DB는 디렉터리가 쓰기 가능해야 읽기 전용 연결도 열린다.
- 한계/다음: coverage는 KRX 응답 행 수 기준이라 공급자의 소폭 누락은 못 잡는다. 품질은 형식·일관성 검사이며
  가격 정정은 증명하지 않는다. 수집기는 가짜 transport로만 검증했고 실제 API 호출은 배포 후 첫 실행에서 확인한다.
  2027 캘린더 미등록 시 2027 첫 세션부터 경보한다. 다음은 승인 후 OPERATIONS.md 1~9단계 실행, 그 뒤 EINT-008.

## EINT-007A — 2026-09-29: EC2 배포 실행과 실데이터로 드러난 결함 수정(EINT-007 보완 successor)

- 요청/결정: "한번에 진행하자" — OPERATIONS.md 배포 단계를 한 번에 실행. EINT-007 manifest/검증 기록은
  덮어쓰지 않고 successor `EINT-007A`로 남긴다(EINT-008 번호는 로드맵의 다음 단계용으로 비워 둔다).
- AWS 변경: EC2 `t3.micro→t3.small`(공인 IP **3.38.170.158** 로 변경, 사이트 `:8801`), 2GB swap
  (첫 실행 OOM exit 137·RSS 약 1.2GB), 비공개 S3 `kqs-state-125022369601`(퍼블릭 차단·버전·SSE·수명주기
  backups/ 30일·이전 버전 7일), 인스턴스 역할 inline 정책, SSM SecureString `/kqs/krx-auth-key`→`krx.env`
  (값 출력 0회), 초기 시장 DB 1회 이전(SHA-256 일치·integrity ok, 원본 무수정), backend 재배포(state 읽기 전용),
  worker 이미지 빌드, systemd 타이머 3개 활성화. SNS 경보는 **미설정**(journal만).
- 실데이터로 드러난 결함(합성 fixture에 없던 것):
  1. `Invalid KRX short code '00088K'` — 신규 상장 영숫자 코드. 보관본 `validate_krx_symbol`과 같이
     6자리 ASCII 영숫자를 허용(숫자 코드만 zero-fill).
  2. `Open must be finite` — 기간 첫 행부터 거래정지인 종목은 carry-forward가 불가능. 해당 행을 제외하고
     개수를 시도 기록 `input_adjustments`에 남긴다. 실제 영향 99종목·25,022행, 유동성 종목은 007340의
     첫 4행뿐이라 선정 결과 변화 없음.
- 실행 기록(불변 run log 그대로): 2026-09-28 세션 `publish_failed`×2(위 결함) → 수정 후 `published`
  (추천 0건) → systemd 서비스로 재실행 `already_published`. 실제 KRX API 재수집 KOSPI 942/KOSDAQ 1,821/
  ETF 1,171로 원본 DB와 동일.
- 병행 관찰 1일차: 원본 엔진도 같은 세션 선정 0건. 스캔 대상 원본 2,929 vs AWS 2,910 — 차이 19개는
  한 번도 거래되지 않아 제외된 종목(원본에서도 전부 `not_entry_signal`).
- 백업: 로컬 보존 **2개**(compose `--keep 2`, S3 30일), 시장 1.4GB 포함 백업을 S3에서 내려받아 복원 검사 ok.
- 산출물: `engine/kqs/revisions/EINT-007A.json`, `docs/engine/verification/EINT-007A.json`,
  revision checker 기본 EINT-007A. 테스트: engine **101**, API 12, revision 6, evidence 15, 백엔드 KQS 51 통과.
- 경계: 주문·자동 모의체결·실거래·개인 원장 쓰기 0회. 원본 WSL 파이프라인·DB 무수정(계속 동작 중).
- 다음: 며칠간 병행 관찰(세션별 선정 비교) 후 원본 중단 여부를 별도 결정. SNS 경보 이메일 설정(선택),
  2027 KRX 캘린더 검토·등록(2027 첫 세션 전 필수), 그 뒤 EINT-008.

## EINT-008 — 2026-09-29: 만료된 추천 보유분의 수동 매도·부분매도·FIFO lot 기록

- 요청/결정: 로드맵 4단계("다음 진행하자"). 사용자가 판단·기록하고 엔진은 추천만 한다는 전제(자동 체결 없음) 유지.
- 재사용: 75~77단계 개인 판단 원장(`kqs_paper_decisions`)·사후 성과(`kqs_decision_outcomes`)·성과 리포트,
  79/80단계 시간순 분할·실험 지문. 엔진 자동 모의원장(D-28/D-44)과는 합산·연결하지 않는다. 근거 검색
  (`매도`, `원장`, `lot`)에서 개인 수동 원장에 적용되는 선행 결정은 없었고, D-6/D-10은 엔진 백테스트 회계라 재사용하지 않는다.
- 결함(수정 전): ① 매도는 **현재 유효한** 추천의 signal_id로만 기록 가능 → 추천이 만료(최대 24시간)되면 보유분을
  매도할 방법이 없었다. ② 보유보다 많은 매도가 조용히 0으로 잘렸다. ③ 실현 손익이 매수 1건과 매도 1건을 수량과
  무관하게 짝지어 부분 매도·분할 매수에서 틀렸다.
- 범위(백엔드 `routers/kqs.py`, 화면 `strategyList.js`, 문서 `KQS_HUMAN_DECISION_DATASET.md`):
  FIFO lot 배분(`_build_kqs_lots`, 원장 무수정·매번 재계산), 보유 현황에 lot·원 추천·만료 여부,
  `POST /api/kqs/decisions/positions/{symbol_key}/sell`(보유 초과 409, 미래/매수일 이전 거래일 422, 경합 시 되돌림),
  추천 경로 매도에도 보유 검사, lot별 실현 손익(부분/전량, 비용 수량 비율 배분, 배분 내역), 청산 기록은
  `position_exit`로 판단 표본·전환율에서 제외, 내보내기 schema `kqs-human-decisions/v2`.
- 엔진 패키지·manifest 무변경(현재 revision EINT-007A 유지). 학습 분할·실험 지문(20일 레이블 기반)도 무변경.
- 검증: 백엔드 전체 **221** 통과(신규 EINT-008 6개: lot 배분·과매도 표시, 만료 후 부분→전량 매도와 손익,
  거래일 검증, 추천 경로 과매도 거부, 동시 과매도 되돌림, 학습·리포트 제외), 기존 KQS 51개 무수정 통과.
  JS 구문 검사(node 22 컨테이너, 이후 이미지 삭제). 엔진 101, revision/evidence 검사 오류 0.
- 한계: 과거에 이미 보유를 넘겨 기록된 매도는 되돌리지 않고 `data_issues`로만 드러낸다. 평균단가·손익은 사용자 입력
  체결가 기준이며 시세 평가는 없다. 경합 방지는 기록 후 재검사·되돌림 방식(단일 DB 전제)이다. 배포는 하지 않았다.
- 다음: 승인 시 EC2 배포, EINT-009(수동 판단 데이터셋 불변 manifest·학습/holdout 규약).

## EINT-009 — 2026-09-29: 수동 판단 데이터셋 불변 manifest·학습/holdout 규약

- 요청/결정: 로드맵 5단계. "다음 단계 완료 후 한번에 배포하자" — EINT-008과 함께 배포한다.
- 재사용: AGENTS.md("고정 manifest/분할/개방 이력 없이 테스트 구간 반복 평가 금지"), D-20(시간 holdout 소진 후 같은 구간
  재선택 금지), D-26(같은 구간 재선택 금지·사전등록부터), D-36(성과 출력 1회·구간 consumed 표기). 79~81단계 분할·실험·러너.
- 결함(수정 전):
  1. 분할을 요청마다 다시 계산해 데이터가 늘면 경계가 움직였다(옛 test가 train으로 이동).
  2. test JSONL을 무제한 내려받을 수 있었다.
  3. 실험 test 성과를 몇 번이든 기록할 수 있었다.
  4. 러너가 dry-run마다 test를 평가했다.
- 범위:
  - `POST /api/kqs/datasets`: 불변 manifest(표본 id·기준일 경계·표본별/전체/manifest SHA-256·이전 manifest 연결).
    후속 manifest는 이전 평가 구간 이후의 새 기준일만 validation·test로 쓴다.
  - `GET /api/kqs/datasets/{id}/examples`: 내려받을 때마다 해시를 대조하고 불일치면 409. test는 개봉 전 409, 개봉 후 접근 기록.
  - `POST /api/kqs/datasets/{id}/holdout`: 사전등록과 함께 manifest당 1회 개봉(유일 인덱스). 후속 동결 뒤에는 `retired_sealed`로 개봉 불가.
  - 실험 기록: `manifest_id` 필수, test는 사전등록 모델로 1회만(sparse 유일 인덱스).
  - `?split=` 구간 내보내기는 410. 전체 검토용 내보내기의 `dataset_split`은 동결 manifest 소속만 표시한다.
  - 러너는 train·validation만 쓰고, test는 `--final-holdout --record`에서만 개봉한다.
  - 화면: 동결 버튼, manifest 목록·holdout 상태, 실험 표의 탐색/최종 구분.
- 검증:
  - 백엔드 전체 **225** 통과. 신규 EINT-009 4개: 동결 준비·봉인·410·타인 404, 해시 불일치 fail-closed,
    1회 개봉·사전등록 일치·test 1회, 후속 manifest 경계·봉인 폐기.
  - 러너 가드 4개(가짜 API): 기본 실행은 test 미접근, 사전등록→test→기록 순서, 조건 검사.
    CI에 `requests`·`scikit-learn` 설치 후 실행하는 단계를 추가했다. 엔진 venv처럼 의존성이 없으면 건너뛴다.
  - JS 구문 검사 통과, 엔진 101 통과, revision/evidence/migration 오류 0.
- 한계:
  - 원장 주인은 모든 사후 성과를 볼 수 있다. 규약은 test를 숨기지 않으며, test 보고가 사전등록된 한 모델뿐임을 검증 가능하게 남긴다.
  - 동결 전(80단계) 실험 기록은 manifest가 없어 test 보고로 인정하지 않는다.
  - 경계 겹침 제거는 20거래일 레이블의 target_date 기준이다.
- 엔진 패키지·revision 무변경(EINT-007A). 주문·자동 체결·실거래 없음.
- 배포(EINT-008+009 함께, 2026-09-29 07:3x UTC): 릴리스 `investing-app-20260929-073150`, backend 이미지만 재빌드
  (엔진 worker·타이머 무변경). health ok, 새 경로 무인증 401(없는 경로 404), 컨테이너 코드에 새 엔드포인트·동결 버튼 확인,
  state 읽기 전용 mount·엔진 0.6.0·kqs 타이머 유지. 운영 DB에 테스트 사용자를 만들지 않아 로그인 후 실제 흐름은 미확인.

## EINT-010 — 2026-09-29: 테스트·CI·배포 기준선 안전 보완(변경 전 계획)

- 요청: EINT-009 전체 검토 후 제안한 후속 단계를 순서대로 진행한다. 이번 범위는 EINT-010이다.
- 재사용: EINT-007A 운영 경로/engine revision, EINT-008 FIFO 수동 원장 회귀,
  EINT-009 manifest·holdout 회귀, D-28/D-44 원장 분리와 D-20/D-26/D-36 소진 규약.
- 범위: 테스트 MongoDB 허용 대상 검증(앱 import 전·정리 직전), 격리 KQS 백엔드 CI,
  배포 workflow 상태 경로/검증 게이트 정합성, 미커밋 기준선의 검사·분류 자료.
- 새 테스트 이유: 운영 DB 환경변수 상속/URI 위장/런타임 대상 변경 시 삭제를 거부하는지,
  CI가 EINT-008·009를 실행하는지, 배포가 기존 상태와 Compose 프로젝트/볼륨을 유지하는지 검증한다.
- 실행 경계: 임시 MongoDB·합성 가격·가짜 API만 사용한다. 실제 AWS 변경·재배포·커밋·푸시,
  운영/개인 DB 쓰기·주문·자동 모의체결·원본 WSL 변경·기존 연구/holdout 재실행은 하지 않는다.
- 보존: EINT-001~009 보관본/manifest/검증 기록은 덮어쓰지 않는다. 엔진 규칙 무변경으로
  runtime 0.6.0 / revision EINT-007A를 유지한다. 레버리지 DCA 기존 변경은 별개로 보존한다.
- 후속: 운영 경로/secrets/Compose 프로젝트·볼륨 식별자는 승인 배포 전 다시 확인한다.
  미커밋 정리는 검사·분류까지이며 Git 커밋·push는 별도 요청 후 진행한다.

### EINT-010 — 로컬 구현·검증 결과(자동배포 설정 승인 대기)

- 구현: 앱 import 전/DB 정리 직전 허용 대상 guard, 전용 MongoDB KQS CI(Python 3.12/3.14),
  worker와 동일 절대 경로·읽기 전용·자동 생성 금지 mount, 기존 환경/프로젝트/볼륨 읽기 전용 사전 검사,
  Git 필수 파일/미커밋 그룹/사적 파일 추적 여부 검사. [릴리스 준비](RELEASE_READINESS.md).
- 검증(Python 3.14.4): 백엔드 KQS 61개 통과(EINT-008·009 포함); 별도 깨끗한 임시 venv에서도
  최소 CI 의존성 설치·engine 0.6.0 설치·pip check 이후 동일 61개 통과.
  임시 MongoDB는 별도 포트·tmpfs이며 기존 DB 볼륨을 mount하지 않았다.
- 보조 테스트 60개 통과(skip 0): 기존 37개 + 신규 DB guard 10개/릴리스 구성 7개/가짜 Docker 사전 검사 6개.
  엔진 합성 회귀 101개 통과. 중복 실행은 별개 연구가 아니며 실제 과거 연구/holdout은 0회 재실행.
  Node 22 읽기 전용·네트워크 차단 컨테이너에서 추천 렌더링 검사 통과.
- evidence(167개 보관본·45개 결정), migration, EINT-007A revision 검사 통과. engine 0.6.0/revision 그대로.
  worker Compose·ops Bash 구문 검사 통과. 홈페이지 Compose는 로컬 backend .env가 없으므로
  파일을 만들지 않고 메모리에서 env_file 항목만 제외하여 구조 검증했다(운영 설정/동작 검증이 아님).
- 초기 검증 문제: 가짜 fixture decorator/예외 처리 회귀를 수정했고, 임시 환경의 누락 의존성과
  실제 CI 최소 목록의 python-multipart/시장 캘린더/SciPy/requests를 보완했다. 최종 새 회귀는 모두 통과.
- 보류: 자동배포 workflow 수정이 안전 검토에서 거부되어 원본을 유지했다.
  향후 main push가 AWS 재시작·재색인으로 연결될 수 있어 사용자에게 **파일 수정만** 별도 승인 요청했다.
  기존 workflow의 경로 차이·테스트 gate 부재·환경파일 덮어쓰기·볼륨 식별자 미검증은 남아 있다.
  `check_kqs_release.py` 기본 검사는 통과하지만 `--require-clean --require-deployment-ready`는
  미커밋/미추적 코드와 승인 보류 때문에 의도대로 실패한다. 전체 EINT-010/배포 준비 완료라고 표시하지 않는다.
- 한계: CI matrix 실제 원격 실행 및 Python 3.12 로컬 실행은 미확인. 홈페이지 전체 225개가 아닌
  관련 KQS 61개만 재검증했다. 기존 13개 deprecation 경고는 이번 범위에서 수정하지 않았다.
  AWS 실시간 운영/타이머/로그인 E2E·원시 자료 전체 백업·개인 Mongo 원장 백업은 확인/수행하지 않았다.
- 근거: 새 [EINT-010 검증](verification/EINT-010.json)에 명령/결과/파일 해시/보류 사유를 남긴다.
  EINT-001~009 보관/검증 자료, 엔진 규칙, 사용자 기존 DCA 변경은 보존했다.
- 실제 AWS 변경·배포·Git commit/push·회원/개인 원장 쓰기·주문·자동 체결은 0회.
- 다음: 승인 시 자동배포 파일만 보완해 EINT-010을 마무리한다. AWS 배포는 별도 승인이다.
  EINT-011(PC 브리지 의존 상태/후보 화면의 native 추천 경로 정합성)은 이번에 진행하지 않았다.

## EINT-011 — 2026-09-29: native 추천 기준의 상태·후보·근거 화면 정합성(변경 전 계획)

- 요청: "11 진행하자". EINT-010 자동배포 파일 승인은 주어지지 않았으므로 해당 파일은 그대로 보류한다.
- 재사용: EINT-003 불변 snapshot/읽기 전용/자동 fallback 금지, EINT-004 캘린더·만료,
  EINT-007A AWS 게시 경로와 엔진 0.6.0, EINT-008·009 수동 원장/학습 규약,
  D-27 최초 근거 보존, D-28/D-44 자동 원장 분리, D-30 무추천 정상.
- 범위: status/후보 큐/후보 패킷이 같은 검증된 추천 source를 사용하도록 웹 adapter를 연결한다.
  snapshot 기본에서 PC 브리지 접근은 하지 않고, bridge는 명시적 legacy 호환 모드로만 유지한다.
  게시 가용성과 운영 프로세스/타이머 건강을 구분하고, 자동 포지션 대조는 native에서 미사용으로 표시한다.
  현재 검토 메모·후보 이력은 source/snapshot을 구분해 옛 bridge 기록을 현재 근거로 오인하지 않게 한다.
- 새 회귀 이유: PC 꺼짐/잘못된 source/만료·누락·변조/0추천/알 수 없는 후보를 테스트하고,
  실제 추천 reasons·버전·기준일·생성/만료·snapshot의 상태/후보/패킷 간 일치와 읽기 전용성을 확인한다.
  합성 SQLite·가짜 API 또는 별도 임시 MongoDB만 사용한다. 렌더링/이스케이프도 회귀로 확인한다.
- 경계: 엔진 규칙·revision/연구 보관·과거 실험·원본 WSL·DCA·기존 개인 기록은 변경하지 않는다.
  주문·자동 모의체결·실제 시장 수집·AWS 변경/배포·Git commit/push 없음.
- 산출물: 웹 adapter/UI/격리 회귀와 새 EINT-011 검증 기록. EINT-001~010 검증 파일은 덮어쓰지 않는다.
  과거 연구 재실행 대신 기존 결과를 재사용하고 새 동작의 회귀만 실행한다.

## EINT-011 — 2026-09-29: native 상태·후보·근거 화면 로컬 검증 결과

- 재사용: D-27/D-28(동결 운영 선정/컨텍스트), D-30(추천 0건 정상), D-44(원장·판정 분리),
  EINT-003 게시 계약, EINT-007A 독립 엔진, EINT-008/009 개인 원장·불변 데이터셋.
  원문 및 최신 결정을 먼저 확인했다. 연구 스윕·소진 holdout은 다시 실행하지 않았다.
- 홈페이지 adapter에서 source 선택을 한곳으로 통일했다. 기본 snapshot 상태/후보/패킷/workflow는
  유효한 동일 게시를 읽고 실제 reasons/features·엔진 버전·기준일·원래 생성/만료·게시 ID를 표시한다.
  누락/만료/손상/잘못된 설정은 503, 현재 후보 밖은 404, bridge는 명시적 설정에서만 사용한다.
  GET은 추천을 생성하거나 만료를 연장하지 않는다.
- 게시 유효성과 운영 건강을 분리했다. 프로세스/타이머 상태는 미관측, 스캔 레짐 차단 총수는
  null/미제공이다. native 자동 포지션 대조와 해당 이력/저장은 미사용이며 legacy 자료를 합산하지 않는다.
- 검토 메모·패킷·일별 큐·후보 성과의 native Mongo 컬렉션 네 개를 분리했다.
  기존 legacy 자료를 삭제/복사/재라벨링하지 않는다. 현재 개인 검토는 snapshot ID를 확인하고,
  검토 시 실제 패킷을 append-only로 저장한다. 개인 수동 거래/데이터셋 계약은 변경하지 않았다.
- UI 순수 renderer와 회귀를 추가했다. 추천 이유·메모·오류를 HTML escape하고 이벤트 ID를 보존했다.
  EINT-010의 기존 fixture DB 안전 경계를 유지하며 CI에 새 native API/Mongo/Node 검증을 연결했다.
- 현재 검증:
  - `python -m pytest tests/test_kqs_bridge.py tests/test_kqs_contract.py tests/test_kqs_native_views.py -q -p no:cacheprovider`
    **65 passed**, skip 0, 기존 deprecation 경고 13개. 별도 MongoDB 7, loopback :32769,
    `jy_investing_test_eint011`, tmpfs, 실제 사용자/운영 볼륨 미사용.
  - `python -m unittest discover -s scripts/tests -v` **66 passed**, skip 0
    (신규 native API 6개, 임시 SQLite/가짜 API·Docker·합성 자료).
  - `python -m unittest discover -s engine/kqs/tests -v` **101 passed**, skip 0.
    역사 연구 재실행이 아닌 합성 기능 회귀다. 위 세 묶음 총 **232개 통과**, 신규 10개.
  - Node 22 `kqs_engine_views_render.mjs`와 `kqs_recommendations_render.mjs`, strategyList 문법 검사 통과.
    네트워크 없는 컨테이너/읽기 전용 rootfs와 저장소를 사용했다. 실제 브라우저 E2E는 하지 않았다.
  - 근거 167개/결정 45개, 초기 이관, revision EINT-007A, 릴리스 로컬 구성, `git diff --check` 통과.
    전체 원시 자료 1,735개는 여전히 해시 목록뿐이며 전체 백업 완료가 아니다.
  - 신규 Mongo 회귀의 최초 실행은 반환 키를 `snapshots`로 잘못 검사해 64 pass/1 fail이었다.
    실제 계약 `items`로 테스트를 수정하고 65/65를 다시 확인했다. 제품 오류를 숨기거나 skip하지 않았다.
- **미완료/승인 대기:** `app/backend/scheduler.py`의 후보 시세 대상 조회가 아직 legacy 큐를 읽는다.
  native 후보만의 사후 일봉 수집 편입이 누락될 수 있다. 해당 파일에는 별도 DCA 자동 실행 코드가 있어
  안전 검토가 파일 수정을 거절했다. 조회 경로만 수정하고 DCA/주기는 보존하는 승인 요청을 보냈다.
  우회 작업을 하지 않았고 파일은 수정 시도 전과 동일하다. 이 부분의 완료·운영 준비를 주장하지 않는다.
- 엔진 **0.6.0 / EINT-007A** 유지. 기존 revision·EINT-001~010 검증·보관본은 변경하지 않았다.
  신규 [EINT-011 검증](verification/EINT-011.json)에 산출물 지문/결과/한계를 남긴다.
  [화면/이력 계약](NATIVE_VIEWS.md)에 컬렉션 구분과 Mongo 백업 범위·일별 갱신 한계를 명시했다.
- 실제 사용자 원장 쓰기·주문·자동 모의체결·실거래·AWS 명령·운영 파이프라인·연구·실제 holdout 개봉·
  배포·커밋·push는 0회. CI Python 3.12/3.14는 설정만 했으며 로컬 검증은 Python 3.14다.
- 다음: 승인된 KQS 후보 시세 조회 보완/격리 회귀 후 EINT-012 수동 판단·거래 시각/중복 요청 정합성.
  EINT-010 자동배포 workflow 파일 변경 승인과 AWS 적용 승인은 별도이며 여전히 보류다.

## EINT-011A — 2026-09-29: 승인된 KQS 후보 시세 조회 보완(변경 전 계획)

- 승인: 사용자 "응 승인할게". 이어 "레버리지는 별개에 아이템이야 아무것도 변경하지마"를 재확인했다.
  승인 범위는 스케줄러의 KQS 후보 조회 source 연결뿐이다. DCA 파일·로직·설정·주기·작업 등록은 변경하지 않는다.
- 재사용: EINT-011 source 선택/네 native 컬렉션 분리, D-27 관측 비개입,
  D-28/D-44 엔진 자동 원장과 개인 원장 분리. 관련 원문과 최신 규약을 확인했다.
- 코드 범위: `scheduler.py`의 두 import에 공통 후보 컬렉션 선택 함수를 추가하고,
  후보 조회 한 줄을 바꾼다. 그 외 보유/전략/개인 판단 수집과 모든 스케줄 함수는 바이트 그대로 보존한다.
- 새 검증: 기본/명시적 snapshot은 native 큐만, bridge는 legacy 큐만 읽고 잘못된 source는 실패해야 한다.
  기존 공통 수집 대상·한국시장 suffix·중복 제거·DB 읽기 전용성도 격리 Mongo로 확인한다.
  실제 시세 요청·스케줄 시작·DCA 실행 함수는 호출 금지 대역으로 막는다.
- 기존 232개 EINT-011 결과는 당시 기록으로 보존한다. 변경 관련 웹/Mongo 회귀와 scripts 안전 검증만 재실행하고
  엔진 규칙·연구가 바뀌지 않았으므로 과거 연구/실제 holdout/101개 엔진 suite는 재실행하지 않는다.
- 산출물: EINT-011A 후속 검증 파일과 변경 전후 hash/정확한 차이/DCA 불변 확인.
  EINT-011 검증 파일은 덮어쓰지 않는다. EINT-010 자동배포 파일 승인은 이 승인에 포함되지 않는다.
- AWS·운영/개인 DB·원본 WSL·서비스 재시작·커밋/push·주문/자동체결 없음.

### EINT-011A — 승인 후 로컬 구현·검증 완료

- 사용자 승인과 별도 레버리지 완전 보존 요청을 반영했다.
  `app/backend/scheduler.py`에서 KQS 컬렉션 선택 import 2줄과 후보 조회 1줄만 수정했다.
  기본/명시적 snapshot은 native 후보 큐, 명시적 bridge는 기존 큐를 읽고 잘못된 source는 실패한다.
  모든 나머지 스케줄러 라인·DCA 실행 함수·시작/정지 함수·작업 등록/주기는 동일함을 대조했다.
  DCA 전략/라우터/화면 파일 해시도 변경 전후 동일하다.
- 신규 조회 회귀 4개(기본 native/명시적 native/bridge/오설정)를 기존 CI 실행 대상
  `tests/test_kqs_native_views.py`에 추가했다. 기존 보유·전략·개인 판단 수집 대상과
  KOSPI/KOSDAQ suffix·중복 제거·Mongo 문서 읽기 전용성을 확인했다.
  실제 quote/일봉 제공자·스케줄 시작·DCA 호출은 금지 대역으로 차단했다.
- 검증(Python 3.14.4):
  - `python -m pytest tests/test_kqs_bridge.py tests/test_kqs_contract.py tests/test_kqs_native_views.py -q -p no:cacheprovider`:
    **69 passed**, skip 0, 기존 deprecation 경고 13개. 별도 MongoDB 7 임시 컨테이너,
    loopback :32770 / `jy_investing_test_eint011a` / tmpfs, 기존 DB·볼륨 미사용.
  - `python -m unittest discover -s scripts/tests -v`: **66 passed**, skip 0.
    위 두 묶음 현재 **135개 통과**. 최초 실행 모두 통과했다.
  - 근거 보관167개/결정45개, EINT-007A revision, 릴리스 로컬 구성, `git diff --check` 통과.
  - 엔진 101개/Node 렌더링 결과는 EINT-011 당시 자료를 재사용한다.
    엔진 코드·UI가 바뀌지 않았으므로 해당 suite와 과거 연구는 재실행하지 않았다.
- 스케줄러 SHA: 변경 전 `2ea4b4970e7ec70c3ae8bc51dd15b8eb69b2e12e25555f2e0e93d319def083d3`,
  변경 후 `aef47e6da0e9e752ae8ad1e6317c1338b312dd96d9e7ceba926f7a42e06291ce`.
  현재 engine 0.6.0/revision EINT-007A는 그대로다.
- 신규 [EINT-011A 검증](verification/EINT-011A.json)에 정확한 세 줄 차이·승인·보존 지문·회귀·한계를 기록한다.
  EINT-001~011 검증 파일과 역사 보관본은 수정하지 않는다.
  이전 EINT-011 승인 보류는 당시 기록이며 이번 successor에서 해결됐음을 연결했다.
- native 후보 시세 **대상 편입**은 로컬 검증 완료다. 실제 운영에서 일별 큐 생성·제공자 수신·
  사후 성과가 성공했다는 뜻은 아니다. 가격이 없을 때 추정값/0으로 채우지 않는 기존 규칙을 유지한다.
- 임시 테스트 컨테이너만 종료·제거했다. 실제 사용자 DB/원장/볼륨 변경, 시장 API, DCA 실행,
  운영 스케줄 시작, 원본 WSL, AWS/배포/재시작, 주문·자동체결, 연구/실제 holdout, commit/push 모두 0회.
- 한계: 원격 CI·Python 3.12·브라우저/운영 E2E는 이번에 실행하지 않았다.
  EINT-010 자동배포 파일 승인은 별개로 계속 보류다. 기존 raw 자료/개인 DB 비공개 전체 백업도 미완료다.
- 다음은 EINT-012 수동 판단·거래 시각·중복 요청·원장 정합성 보완이며 이번 승인에 따라 자동 진행하지 않는다.

## EINT-012 — 2026-09-30: 수동 거래 시각·중복 요청·원장 쓰기 정합성(변경 전 계획)

- 요청: "12진행하자". 레버리지 DCA는 별도 항목이며 코드·설정·주기·파일·스케줄러를 전혀 수정하지 않는다.
- 재사용: D-27/D-28/D-44 관측/원장 분리, EINT-008 FIFO lot/만료 후 수동 매도,
  EINT-009 불변 데이터셋/holdout, EINT-011·011A native 추천/별도 이력. 연구/holdout은 재실행하지 않는다.
- 발견: 재전송 식별자 없음, 거래/기록 시각 미분리, 동시 매도의 저장 후 삭제 방식,
  미래/역순 거래 및 NaN/무한대/금액 overflow 검증 부족.
- 범위: KQS 수동 입력·저장/표시만. 선택적 request_id를 사용자별로 원장 자체의 결정적 _id에 연결한다.
  같은 요청 재시도는 같은 원본 기록을 돌려주고, 같은 ID의 다른 입력은 409로 거부한다.
  사용자별 Mongo _id 배타 잠금으로 쓰기를 직렬화하고 수량/시간을 저장 전에 검증한다.
  프로세스 종료로 남은 잠금은 자동 만료하지 않고 명시적 운영 복구 대상으로 문서화한다.
- 거래 시각(UTC 저장/KST 표시), 거래일, 기록 시각, 시각 출처를 분리한다. 날짜만 입력하면 정확한 시각은 미상이다.
  기존 FIFO 기록 순서는 보존하고 종목의 마지막 거래 이전으로 소급 삽입하지 않는다. 과거 원장은 수정하지 않는다.
- 화면은 동일 본문 재시도에 같은 request_id를 유지하고 변경된 입력에는 새 ID를 발급한다.
  매도 버튼 연타도 막는다. 초기 API 호환을 위해 request_id는 선택적이며 없으면 재전송 중복 방지는 보장되지 않는다.
- 새 회귀: 중복/입력 불일치/사용자 분리/만료 후 재시도/동시 매도/잠금 해제/시각·금액 검증,
  날짜-only·과거 원장 호환·학습 근거 보존, UI KST·중복 키·escaping.
  임시 Mongo/합성 게시·가짜 audit만 사용한다. 실제 사용자 원장·시세·주문·DCA·AWS·Git 쓰기 없음.
- EINT-001~011A 검증 파일은 유지하고 EINT-012 산출물 해시/실행 결과/한계를 별도로 기록한다.


## EINT-012 — 2026-09-30: 수동 원장 정합성 로컬 구현·검증 완료

- 사용자 요청 "12진행하자". 별개 레버리지 완전 보존 조건을 유지한다.
  EINT-010 자동배포 workflow 수정/AWS 적용 승인은 포함하지 않는다.
- 구현: 선택적 request_id와 정규화 입력 지문/사용자별 유일 저장 키로 같은 요청을 재생한다.
  추천 만료/전량 청산 뒤에도 성공했던 동일 요청만 재생하며 다른 입력/용도는 409다.
  ID 없는 구 클라이언트는 호환하지만 재전송 중복 방지는 보장하지 않는다.
- 서버 기록 시각과 입력 거래 시각/거래일·출처(user_time/user_date_only/record_time_default/
  legacy_unspecified)를 분리했다. UTC 저장/KST 표시, 과거 DB 재기록 없음.
  미래/추천 생성 전/기존 종목 거래보다 앞선 입력, NaN/Infinity/overflow/1e-8 이하 수량을 차단한다.
- 사용자별 Mongo 잠금 안에서 검증 후 저장해 초과 매도를 사전 거부한다.
  새 API는 저장 후 원장 삭제/보상 재삽입을 하지 않는다. FIFO 저장 순서와 기존 manifest v2를 유지한다.
  동일 밀리초 정렬 보조값 recorded_order_us를 추가하고 거래 10,000건 한도에서 부분 계산 대신 중단한다.
- 화면: 선택적 KST 시각 입력, 서버 기록 시각/거래 시각·기본값 표시, 같은 폼 입력의 요청 ID 유지,
  저장 중 중복 제출 차단, 추천 폼의 명시적 새 기록 시작, 근거 태그 HTML escape.
- 검증:
  - Python 3.14.4 별도 test venv의 웹/API **89 passed** = 기존 69 + 새 사례 20,
    skip 0, 기존 deprecation 경고 13. 실제 사용자 DB 아닌 MongoDB 7 tmpfs 전용 컨테이너,
    loopback :32771 / jy_investing_test_eint012만 사용했다.
  - scripts **66 passed**, skip 0. 합성 API 잠금/조회/count 대역을 갱신했으며 기존 회귀를 빼지 않았다.
  - Node 22 네트워크 차단·읽기 전용 컨테이너에서 수동 기록/추천/native renderer 3검사와
    strategyList 문법 통과. 실제 브라우저/AWS E2E 검증은 아니다.
  - 근거 167개/결정 45개, 최초 이관 4파일, revision EINT-007A, 릴리스 로컬 구성,
    git diff --check 통과. deployment_configuration_ready는 여전히 false다.
- 발견/수정: 초기 86개 실행은 84 pass / 2 fail이었다. raw NaN/Infinity 검증 오류의 입력값이
  JSON 오류 응답을 깨뜨렸다. 저장 전 422로 보완하고 이후 87/87, 최종 89/89 통과.
  초소량 수량·동일 날짜 역순·원장 한도 경계 회귀도 추가했다. 테스트 skip/운영 원장 수정을 하지 않았다.
- 재사용: D-27/D-28/D-44, EINT-008/009 수동 원장·고정 데이터셋, EINT-011A 조회 경계.
  엔진 규칙이 같으므로 EINT-011 당시 엔진 합성 101개 결과는 재사용만 하고 신규 155개에 합산하지 않았다.
  과거 연구·실제 holdout·모델 학습은 0회. 엔진 0.6.0 / EINT-007A 그대로다.
- 보존: DCA 전략/라우터/화면, scheduler, market_calendar, 기존 deploy workflow는 단계 시작 전 SHA와 같다.
  기존 011A 검증·엔진 revision은 덮어쓰지 않았다. dirty 사용자 변경을 되돌리지 않았다.
- 한계: 화면 요청 ID는 폼 메모리만으로 새로고침 복원을 지원하지 않는다. 날짜만/과거 미상 시각의
  같은 날 순서는 증명하지 못한다. 잠금 TTL은 없고 장애 잔존 시 운영자 확인·별도 권한 복구가 필요하다.
  감사 기록은 best-effort이며 outbox/재전송은 없다. 개인 원장 10,000건 처리/백업·복구는 후속 설계가 필요하다.
- 산출물: [MANUAL_RECORDS.md](MANUAL_RECORDS.md), 새 [검증 JSON](verification/EINT-012.json),
  수동 원장 helper/회귀/검증 CI 연결. 원문 보관본과 과거 검증 자료는 유지한다.
- 실제 사용자 원장 쓰기·주문·자동 모의체결·실시장 요청·AWS 배포·서비스 재시작·
  원격 CI·Git commit/push는 0회. 일회용 Mongo만 종료·제거하여 tmpfs 합성 데이터는 폐기했다.
- 다음 제안 EINT-013: 추천 시점 엔진 피처와 사후 사용자 판단 태그/메모/거래 결과를 분리하고
  새 스키마·지문·누수 회귀를 정의한다. 기존 manifest/holdout를 변경하거나 새 모델 학습을 시작하지 않는다.

## EINT-013 — 2026-09-30: 학습 피처 출처 분리(변경 전 계획)

- 요청: "13 진행하자". D-20/D-26 소진 holdout·재탐색 금지, D-27 관측 비개입,
  D-28/D-44 엔진/사용자 원장 분리, EINT-009 동결/1회 개봉을 재사용한다.
- 확인한 결함: v2 JSONL의 `features.evidence_tags`는 추천 당시 엔진 정보가 아니라 사람이
  판단 후 입력한 값인데, 오프라인 러너가 이를 실제 학습 피처로 사용한다. `include_notes`를 켜면
  `human_note`도 features에 들어간다. 분류 목표 `human_decision`을 암시해 검증 수치가 부풀 수 있다.
- 범위: 이전 v2 manifest/해시/기존 개봉·실험 이력은 변경하거나 재평가하지 않는다.
  새 동결은 별도 v3 예제·명시적 엔진 시점 피처 지문/출처와 인간 주석 영역을 사용한다.
  러너는 v3만 허용하고 허용 목록 밖/인간 주석이 features에 섞이면 데이터 다운로드 이후에도
  학습/사전등록 전에 차단한다. v2는 재현·감사용으로 남기되 새 모델 승인 근거로 쓰지 않는다.
- 합성 Mongo/가짜 API에서 v3 누수 차단·해시 변조·v2 불변 검증·후속 manifest/holdout 경계와
  UI 구분을 테스트한다. 과거 실제 holdout, 연구 스윕, 실제 사용자 원장/모델/주문/자동체결,
  AWS·원본 WSL·레버리지 DCA·기존 배포 workflow는 실행/수정하지 않는다.

## EINT-013 — 2026-09-30: v3 출처 분리 로컬 구현·검증 완료

- 확인한 누수: v2 `features.evidence_tags`와 옵션 `features.human_note`는 추천 게시 이후
  사용자가 작성한 값이며, 기존 러너가 evidence_tags를 분류 피처로 사용했다.
  과거 v2 학습/정확도를 누수 없는 결과로 해석하지 않는다. 옛 결과를 새 성과로 재포장하지 않는다.
- 신규 v3: `kqs-human-decisions/v3` 및 `kqs-engine-features/v1` 정적 정책 지문을 도입.
  게시 시점 추천/엔진 원본의 11개 허용 필드만 `features`에 보낸다.
  게시 ID·생성 시각·원장에 복사된 스냅샷 SHA는 별도 `feature_provenance`,
  인간 태그/메모/거래 입력은 `human_context`, 판단/사후 성과는 `labels`에 둔다.
  원본 식별자·종목·시장·기준일·게시 출처 누락/불일치면 v3 준비도·동결을 중단한다.
- v3 manifest는 정책 지문과 표본별 엔진 피처/게시 출처/사람 판단/확정20일 레이블을
  새 해시로 잠근다. 원본 스냅샷 변조는 다운로드/holdout 전 409로 드러난다.
  인간 주석은 피처/manifest 지문에서 제외하고 명시적 별도 영역으로 제공한다.
- 이전 v2 manifest/해시 함수·문서·개봉/실험 이력은 변경하지 않는다.
  v2 train/validation과 이미 개봉된 test는 인증/해시 검증 후 감사용으로 읽을 수 있다.
  단 새 v2 holdout 개봉/실험 기록은 409, 러너는 v2를 다운로드 전 거부한다.
  v2→v3 successor의 기존 EINT-009 날짜 경계와 봉인 폐기 규칙은 유지한다.
- 전체 검토 JSONL 기본은 v3(비동결/비학습), `?schema=v2`만 옛 형식 확인용.
  화면은 v3 신규 동결과 v2 보관 전용을 구분한다.
- 러너는 v3/정적 정책 SHA·필드 정확한 허용 집합·출처를 train/validation 다운로드 시 검증하고,
  사전등록/holdout 전에 오류를 반환한다. 다수 클래스 기준선도 검사 예외가 아니다.
  기존 모델 선정 및 사전등록→test→실험기록 순서는 보존했다.
- 격리 검증: Python 3.14.4 별도 venv의 KQS 웹/API **95 passed**(기존 89 + 새 6,
  skip0, 기존 deprecation 경고13), MongoDB7 tmpfs loopback :32772 / jy_investing_test_eint013.
  scripts **70 passed**(기존 66 + 신규 러너 안전 사례4, skip0).
  합계 **165**이며 연구 성과 시험은 아니다. Node syntax/기존 KQS renderer,
  revision·근거·릴리스 무결성 검사 결과는 신규 EINT-013 검증 파일에 기록한다.
- 최초 신규 backend 94개 실행에서 테스트 코드의 `kqs.ObjectId` 잘못된 참조 1건 실패(93pass/1fail).
  `bson.ObjectId` import로 테스트를 고치고 94/94, 새 v2 이미개봉 사례 추가 후 95/95 통과했다.
  제품 오류나 실제 사용자 기록은 수정하지 않았고 테스트 skip은 없다.
- 재사용: D-20(랭킹 holdout 소비), D-26(새 표본·사전등록), D-27(선정 비개입),
  D-28/D-44(엔진/수동 원장 구분), EINT-009(분할·동결/holdout), EINT-012(시각/원장 출처).
  기존 엔진 synthetic101 결과는 엔진 불변이어서 과거 결과로만 참조한다.
  과거 연구/실제 holdout/운영 모델 학습·파라미터 탐색은 0회.
- 한계: 출처 SHA는 원장 복사본의 동결 지문이지 과거 게시 SQLite 원본과 독립 대조한 증거가 아니다.
  오래된 원장에 게시 메타데이터가 부족하면 추정 보완하지 않아 새 v3 동결이 불가능하다.
  실제 모델 품질·클래스 불균형/표본 독립성·운영 개인자료 백업/복구·동의/보존 정책은 별도 과제다.
- 별개 레버리지 DCA, scheduler, market_calendar, 기존 자동배포 workflow,
  엔진 0.6.0/EINT-007A 및 과거 EINT-012 검증은 보존한다.
  실제 사용자 원장 쓰기·개봉·주문/자동 체결·실시장 호출·AWS 배포·서비스 재시작·
  Git commit/push 없음. 사용한 임시 테스트 Mongo만 종료·제거한다.
- [피처 출처/버전 계약](LEARNING_FEATURE_PROVENANCE.md),
  [신규 검증](verification/EINT-013.json). 다음은 실제 데이터 품질·모델 실험 전
  승인/개인 데이터 보호·운영 백업 경계 점검이며 자동 시작하지 않는다.

### EINT-013 최종 점검 중 별개 DCA 병행 변경 관측

- 이 단계에서 수정하지 않은 `app/backend/alpaca_broker.py`,
  `app/backend/leverage_dca_strategy.py`, `app/backend/routers/strategy.py`,
  `app/frontend/js/views/leverageDca.js`가 최종 지문 확인 시점에 별도 변경된 것을 발견했다.
  EINT-013 시작 당시의 세 DCA 보호 파일 지문과 최종 관측 지문이 다르다.
  누가 왜 변경했는지 확인하지 않았으므로 EINT-013의 변경/승인/검증 성과로 합산하지 않는다.
- 해당 파일을 되돌리거나 편집하지 않았다. 사용자 지시대로 DCA는 독립 작업으로 남긴다.
  위의 "별개 레버리지 DCA 보존"은 **이 단계가 수정하지 않았다는 의미**이며,
  최종 작업 트리에서 DCA 바이트가 시작 전과 동일하다는 뜻으로 읽으면 안 된다.
- KQS 전용 95개 회귀는 병행 변경 후 새로운 일회용 MongoDB에서 재검증했고 모두 통과했다.
  DCA 기능 자체의 품질·안전성은 EINT-013 범위 밖이며 통과를 주장하지 않는다.

## EINT-014·015 — 2026-09-30: 학습 준비도·개인 데이터 보호(변경 전 계획)

- 요청: "14.15 동시 진행해서 완료하고 검증완료되면 배포까지 진행하자". EINT-014는 실제 모델을 학습하기 전의 데이터 품질/적합성 읽기 게이트, EINT-015는 학습 사용 동의·보존 경계와 KQS 복구 준비로 구분한다. 같은 동결/내보내기 경계를 공유하므로 구현과 회귀는 함께 진행한다.
- 재사용: D-20/D-26/D-36의 소진 holdout·사후 재선택 금지, D-27의 추천 비개입, D-28/D-44의 엔진 자동 원장과 사용자 수동 원장 분리, EINT-009의 불변 manifest/1회 holdout, EINT-013의 v3 추천시점 피처·출처 분리를 유지한다.
- 범위: 사용자별 읽기 전용 데이터 품질 리포트(클래스/날짜/출처/20일 라벨), 기본 거부인 학습 사용 동의와 철회 이력, 동의 없는 신규 v3 동결·내보내기·holdout·실험 차단, KQS 전용 백업/복구 준비 문서와 합성 회귀다. 기존 manifest/holdout/실험은 수정·삭제·재해석하지 않는다.
- 경계: 실제 모델 학습·실제 holdout 개봉·주문/자동 모의체결·사용자 원장 삭제·실사용 Mongo 백업/복구·원본 WSL·엔진 규칙은 실행하지 않는다. 별도 레버리지 DCA, `routers/strategy.py`, scheduler, market calendar, 기존 deploy workflow는 수정하지 않는다. 실제 배포는 로컬 검증과 별도 운영 사전검사를 모두 통과한 뒤에만 수행한다.

## EINT-016 — 2026-09-30: 2027 KRX 캘린더와 연도 경계 세션 처리

- 요청: "27년 거래 캘린더 미리 만들어놔". 재사용: EINT-004 캘린더 정책(연도별 검토 파일·SHA·fail-closed), EINT-007A.
- 새 파일 `krx_equity_2027.json`(KRX 공식 2027 일정 공표 전 검토본, 휴장 16일). 근거와 교차 확인은 KRX_CALENDAR.md.
- 실데이터 확인(원본 DB 읽기 전용): 2026년 실제 휴장 평일이 `python-holidays`와 일치했고, 2026-07-17(제헌절)·
  2026-05-01(노동절)은 거래가 없었다 → 2026 개정법(제헌절·노동절 대체공휴일)을 2027에 적용(5/3, 7/19).
- 결함 수정(2027 파일만으로는 부족했음):
  1. 12/30 세션의 다음 세션 계산이 연도를 넘지 못해 오류.
  2. 1월 초 "가장 최신 공개 세션"이 전년으로 거슬러 가지 못해 오류.
  3. 게시 만료 상한 계산이 다음 해 세션을 올해 캘린더로 확인해 오류(새 연도 경계 게시 테스트로 발견).
  인접 연도의 검토 파일로만 넘어가며 검토 파일이 없는 연도는 계속 실패한다.
- 품질 run 코드 식별에 설치된 캘린더 파일 전체를 포함.
- 2026 캘린더 파일은 바꾸지 않았다(2026-07-17 누락은 과거 날짜라 운영 영향 없음, SHA 유지). 문서에 기록.
- 산출물: `engine/kqs/revisions/EINT-016.json`(successor), `docs/engine/verification/EINT-016.json`, revision checker 기본 EINT-016.
- 검증: 엔진 **104**(신규 3: 2027 휴장·대체휴일, 연도 경계 세션 이동과 2028 거부, 2026-12-30 자료를 2027-01-04에 게시하고
  만료 상한 확인), 수집기 테스트의 "미검토 연도" 예시를 2028로 변경. 백엔드·스크립트·근거 검사는 아래 검증 파일 참고.
- 배포: 백엔드는 main 푸시 자동 배포, 엔진 worker 이미지는 별도 재빌드 필요(12/30 게시 전까지).
- 한계: 2027은 공식 일정 전 검토본이다. 12월 KRX 공표 후 대조가 필요하다. 임시 휴장(선거 등)은 예측하지 않는다.

## EINT-017 — 2026-10-01: 3개 아이템 전략 추천(아이템별 최대 5개) + Trend Pullback v4 이식

- 요청: 일봉 매수·매도 신호 엔진을 한 화면에 모은다. 아이템은 Bollinger-RSI · RSI-OBV · Trend Pullback v4
  3개, 하루 아이템당 최대 5개, 순위는 각 아이템의 기존 규칙, 아이템 간 중복은 양쪽 표시, 매도 신호는 내 보유 종목만,
  기존 필터 유지. **사용자 운영 방침이며 검증된 성과 개선이 아니다.**
- 대상: `kqs_runtime.pullback_v4`(신규), `kqs_runtime.recommendations`, `sqlite_reader`(정지일 표시), `worker`,
  `kqs_publication.contract/publisher/store`, 웹 `/api/kqs/strategy-board`·전략 추천 화면. 0.6.0 → 0.7.0,
  code_sha256 `83096007…`(EINT-017 manifest). 이관 핵심 4모듈(korea_quant)은 바이트 동일 유지.
- 재사용 결정: D-1(시총 proxy 범위)·D-2·D-8/D-19(200일 레짐)·D-24(평균회귀 순서, 이제 아이템 내부)·
  D-42/D-43(v4 규칙·"불통과 힌트" 상태)·D-44(3트랙 구성). D-9의 전역 3개 상한과 swing 우선 중복 제거는 게시 목록에서
  사용자 방침으로 대체한다. D-20/D-21 소진 구간에서 새 순위·상한을 고르지 않았다(상한 5는 사용자 지정).
- v4 이식 검증(구현 동치만): ① 보관 원본을 scratch에 복사해 합성 데이터에서 1회 실행 → 66거래·반올림 해시 `c32f51df…`/`941334b9…`
  이식본과 동일(원시 해시는 기기별 부동소수점 끝자리 차이로 CI에서 달라 반올림 지문으로 고정). ② 실제 DB 사본(S3 백업 9/30, sha256 `f9c4a2ce…`)으로 관측 #5 창 2022-01-01~2026-09-08 재현 →
  521거래·청산 분포·trades `77ee8e1492dc3ea6`·equity `fca541d9f626eebd` **관측 #5와 완전 일치**.
  해당 창은 관측 #5로 이미 소진됐으므로 표시된 성과(CAGR 1.93%, MDD −11.03%, PF 1.08, 승률 35.9%)는 표본 내 값이며
  규칙 선택·조정 근거가 아니다. 2026-09-09 이후 전향 구간은 평가하지 않았다.
- 표본 경계: 상한·중복 규칙이 바뀌었으므로 이후 홈페이지 추천은 D-33·D-42·D-43 전향 관측 표본과 합치지 않는다.
  원본 엔진·보관본·수동 원장·레버리지 DCA는 변경하지 않았다. 주문·자동 모의체결 없음.
- 매도 신호: 엔진은 사용자 원장을 읽지 않는다. 자신이 게시했던 종목만 청산 모니터로 게시(평균회귀 400일,
  v4 60일, 이전 세션만 → 재실행 run_id 동일)하고, 웹이 사용자 lot과 합쳐 계산한다. v4 손절·트레일링은 다음 날 시가 대신
  기준일 종가로 미리 알린다(보유 상한·국면 이탈은 정확). 엔진 `holding_exit`와 웹 계산 54건 일치 테스트.
- 실제 검증: 엔진 122 OK, scripts 75 OK, backend 287 passed, 프런트 문법·렌더 4종 OK, check_migration/
  check_revision(EINT-017)/check_engine_evidence/check_kqs_release 통과. 9/29 실데이터 실행: 평균회귀 0(레짐 차단),
  v4 후보 12 → 5개 선정, 최대 메모리 1.50GB(EINT-016과 동일), 시간 18초 → 59초.
- 한계·후속: worker 이미지 수동 재빌드와 v2 첫 게시가 필요(백엔드는 v1·v2 모두 읽음). v4 순위 점수·상한 5는 성과 검증이
  아니다. 추적 기간 밖·비추천 매수는 매도 신호가 없다.

## EINT-018 — 2026-10-01: 웹 종목명 검색용 KRX 상장 목록 게시

- 요청: 기업 재무·DART·산업 비교를 종목코드 없이 종목명만으로 검색. 원인 조사: AWS에서 KRX KIND가 403으로 막혀
  서버 검색이 정적 별칭 약 40개뿐이었고, 서버 OpenDART 키는 status 010(사용자가 새 키로 교체, 값 비노출).
- 변경: 파이프라인이 품질 게이트 통과 후 `state/instrument-catalog.json`(해당 세션 거래 KOSPI/KOSDAQ 종목의 코드·이름·시장·
  시가총액)을 원자적으로 쓴다. 실패해도 추천 게시는 막지 않고 실행 기록에 남긴다. 추천 규칙·순위·계약은 변경 없음.
- 웹: 이 목록을 1순위 출처로 이름·별칭·코드 검색(`/api/market/instruments`, 시총 순), 기업 재무 자동완성, DART 검색은
  현재 상장 종목만·시장(.KS/.KQ) 정확, 산업 비교는 이름만 입력, DART 키 오류는 안내 문구, 상승·소부장 순위 60초 캐시.
- 검증: 엔진 125 OK, backend 295 passed, scripts 75 OK, 프런트 문법·렌더 OK, check_revision(EINT-018).
- 한계: 그날 거래정지 종목은 다음 거래 때까지 목록에서 빠진다. 별칭표는 소규모 수동 목록이다.

## EINT-019 — 2026-10-01: 3개 아이템 추천의 전향 성과 관측

- 요청: 매일 게시되는 3개 아이템 추천이 실제로 어땠는지 기록. 근거: v4 백테스트(관측 #5)는 소진 창의 표본 내 결과
  (PF 1.08)라 성능 근거가 못 되고, 새로 나오는 추천만 전향 표본이다(D-44 전향 관측 취지 재사용, 실행 권한 아님).
  기존 `kqs_candidate_outcomes`는 옛 브리지 후보 큐 + 웹 yfinance 일봉이라 v2 아이템과 연결되지 않는다.
- 변경: `kqs_runtime.outcomes` — 검증된 v2 게시본의 선정 종목을 다음 거래일 시가에 산다고 가정하고 1·5·10·20번째
  거래일 종가 수익률, 시장 ETF(069500/229200) 대비, 최대 낙폭·상승폭을 계산. 미관측 기간은 `observed=false`로 두고
  채우지 않는다. 거래정지일은 직전 종가 유지, 시세 없음(상장폐지 등)은 그 지점에서 중단. 아이템·기간별 20건 미만은
  `insufficient_sample`. 파이프라인이 게시 뒤 `state/recommendation-outcomes.json`을 원자적으로 쓰며 실패해도 게시 상태는
  바뀌지 않는다. 웹 `GET /api/kqs/recommendation-outcomes`, "매매 기록·연구 → 성과" 탭 상단 표시.
- 검증: 엔진 130 OK(test_outcomes 5 신규, 파이프라인 파일 생성 확인), backend 298 passed(엔진 빌더로 만든 파일을 웹이
  읽는 일치 테스트 포함), scripts 75 OK, 프런트 문법·렌더 5종 OK, check_revision(EINT-019)/check_engine_evidence/
  check_kqs_release/check_migration 통과.
- 한계: 비용·세금·슬리피지 없음, 고정 보유기간만(아이템 청산 규칙 적용은 모의체결에 해당해 제외), 20건 기준은 표시용
  문턱이지 사전등록 검정이 아니다. v1 게시분(9/30 이전)은 추적하지 않는다.


## EINT-020 — 2026-10-01: 추천 카드에서 바로 판단 기록 + 엔진 상태 경고(웹만, 엔진 코드 변경 없음)

- 요청: 추천을 보고 바로 매수 기록, 엔진 실행 이상을 화면에서 알기. 엔진 패키지·계약·revision(EINT-019) 변경 없음.
- 판단 기록: "전략 추천" 카드에 매수 기록(수량·체결가·거래일·수수료·메모)·관망·제외 버튼. 기존 `POST /api/kqs/decisions`를
  그대로 쓰며(현재 유효 추천만, 재시도 request_id, 거래일 검증 동일) 자동 주문·체결 없음. 카드에 내가 남긴 판단을 표시
  (`strategy-board`의 `my_decisions`, 본인 기록만). 관망·제외도 AI 학습용 판단 레이블이 된다.
- 엔진 상태: 엔진의 `kqs_ops.status.check_health`(기대 세션 게시 여부·6시간 유예)를 웹에서 읽기 전용으로 재사용하고,
  최근 실행 실패, 30시간 넘은 실행 기록, 보조 단계(종목 목록·추천 성과) 실패를 경고로 묶는다. 게시 누락은 오류, 나머지는
  주의. `GET /api/kqs/pipeline-health`, 전략 추천 화면 상단 배너. 실행·재시도는 하지 않는다.
- 검증: backend 신규 7(정상·실행 실패·오래된 실행·보조 단계 실패·게시 누락·기록 없음·카드 판단 표시/본인 한정/로그인),
  렌더 테스트(판단 배지·버튼·경고 배너·escape).
- 한계: 알림은 사이트 안에서만 보인다(메일·문자 등 외부 알림 없음). 매도는 기존대로 보유 현황 화면에서 기록한다.

## EINT-021 — 2026-10-02: 섹터 순환(RRG) 참고 화면

- 요청: 지금 주도 섹터와 주도권 이동을 한눈에(참고용). 자료: 이미 수집 중인 KRX 일봉의 섹터 ETF(2016-08~), 새 출처 없음.
- 변경: `kqs_collection.sector_rotation` — 국내 섹터 ETF 19종 ÷ KODEX 200의 주간 상대강도. RS-Ratio = 100×EMA4/EMA13,
  RS-Momentum = 100×Ratio/EMA4(Ratio), 26주 예열, 12주 궤적, 현재·4주 전 구역(주도·약화·부진·개선), 4·12주 수익률.
  기준일 시세가 없거나 이력이 짧은 섹터는 skipped로 남긴다. 파이프라인이 품질 게이트 뒤 `state/sector-rotation.json`을
  원자적으로 쓰고 실패해도 게시는 막지 않는다. 웹 `GET /api/market/sector-rotation`, "시장 리뷰 → 섹터 순환(RRG)" 화면.
- 처음 쓴 10주 z-점수 방식은 실제 데이터에서 궤적이 지그재그로 엉켜 배포 전에 EMA 방식으로 바꿨다.
- 검증: 엔진 테스트 6 신규, backend 3 신규, 렌더 테스트 1 신규. 서버 실데이터(2026-09-30) 읽기 전용 실행 19/19 섹터.
- 한계: RRG 공개 근사식(JdK 원식 아님), KODEX 200의 반도체 비중 영향, 분배금 미반영, 현재 상태 설명용이며 추천·순위·
  청산·계약에는 쓰지 않는다. 매매에 쓰려면 사전등록과 새 전향 표본이 필요하다.

## EINT-022 — 2026-10-02: 추천 종목에 섹터·섹터 순환 구역 표시(참고)

- 요청: 아이템별 Top5 추천 종목 아래에 섹터와 그 섹터가 주도·개선·약화·부진 중 어디인지 표시.
- 엔진: 섹터 순환에 IT하드웨어(266370)·기계장비(102960) 추가(21개). 실제 추천 종목의 업종(283·2629 등)을 받기 위함.
  추천 게시본·순위·청산·계약은 바꾸지 않았다.
- 웹: `GET /api/market/stock-sectors`(로그인, 최대 30종목) — DART 기업개황 업종코드(KSIC)를 가장 긴 접두어로 섹터에
  연결하고 섹터 순환 현재·4주 전 구역을 붙인다. 지주회사(64992)·미분류 코드는 '업종 분류 없음'. 전략 추천 카드,
  대시보드 브리핑·의사결정 큐의 종목 아래에 배지로 표시(추천을 다 그린 뒤 비동기로 채움).
- 검증: backend 353 passed(매핑 20), 렌더 7종, 서버 실데이터 21/21 섹터.
- 한계: DART 주업종이 시장의 통념과 다를 수 있다(삼성전자 264 → IT하드웨어). 참고 표시이며 추천에 쓰지 않는다.

## EINT-023 — 2026-10-02: 평균회귀 2종의 시장 조건(레짐 게이트) 해제 — 사용자 결정

- 결정: 사용자 지시("시장조건 삭제 … 시장 상태는 내가 판단하고 그 기록으로 학습, 제거해서 테스트")로 D-19 라이브 규칙
  (W=200, bearish ON)을 Bollinger-RSI·RSI-OBV에서 끈다(`market_filter_window=0`). 검증된 개선이 아니라 사용자 선택이다.
- 결정 전 제시한 근거(재실행 없음): D-8 PF 1.59→1.76 / 1.56→2.33(게이트 ON), D-17 combined CAGR 0.38%(OFF) vs 2.88%(ON),
  MDD −45.7% vs −25.9%, D-18 격차의 상당 부분은 2020·2022 폭락 구간. 당시 상태: KODEX 200 +14.3%(1년 내내 차단),
  KODEX 코스닥150 −9.1%(열림, 10/1 적격 104종목 신호 0).
- 시장 상태는 근거 마지막 줄에 참고로 남긴다: "참고: 시장 ETF 종가 · 200일 평균 (±%, 위/아래) — 매수 조건 아님".
  근거 코드 bearish_regime200 → market_context200. 신호 규칙·유니버스·필터·순위·상한·청산·v4·계약은 그대로.
- 전향 관측: D-33의 게이트 ON 스트림은 2026-10-01 세션으로 끝난다. 배포일부터 게이트 OFF 스트림을 새로 쌓고 두 스트림을
  합치지 않는다. 표본 기준(swing 100 / long_term 30 closed)은 새 스트림에서 0부터 적용한다.
- 검증: 엔진 136 OK, backend 353 passed.

## EINT-024 — 2026-10-02: 국내 종목 현재가를 한국투자증권(KIS) 체결가로(웹만, 엔진 코드 변경 없음)

- 요청: 실시간 대응이 필요한 국내 개별 종목(포트폴리오 보유, KQS 추천, 관심종목)은 한투 API, 15분 지연이 괜찮은
  거시 지표(KOSPI·KOSDAQ 일봉, S&P500, 나스닥, USD/KRW)는 기존 Yahoo로 역할 분리. 사용자 선택: 서버 WebSocket + 화면 3초
  갱신, 체결가만(호가 없음). 한투 키는 아직 없음 → 키가 없으면 기존 경로 그대로.
- 재사용 결정: 관련 엔진 결정 없음(`check_engine_evidence --topic "한투 시세"` 0건). 엔진·추천 계약·게시본·학습 데이터는
  바꾸지 않는다. 공용 `quote_cache.fetch_quote_cached`(전략 모의·레버리지 DCA가 함께 씀)는 그대로 두고 새 경로만 추가했다.
  레버리지 DCA(미국 종목·Alpaca)는 영향 없음.
- 웹: `app/backend/kis_quotes.py` — 접근토큰(만료 10분 전 재발급, 실패 시 65초 대기), REST 주식현재가(FHKST01010100,
  5초 캐시, 실전 초당 15건·모의 2건), 장중(평일 08:55~15:35 KST) WebSocket 실시간 체결(H0STCNT0) — 최근 10분 안에 화면이
  요청한 종목 중 최근 순 40개만 등록(세션 한도 41), 거절된 종목은 10분간 재등록 안 함, PINGPONG 응답, 끊기면 재연결
  (5→120초). 주문·잔고·계좌 API는 쓰지 않는다. `live_quotes.py` — 국내 종목은 한투, 실패하거나 해외·지수·환율이면
  Yahoo(실패 사유 `fallback_reason` 표시). `POST /api/market/live-quotes`(로그인, 최대 40종목, 장중 한투 사용 시
  refresh 3초·그 외 30초). 포트폴리오 평가는 국내 종목에 한투 체결가가 일봉 캐시보다 우선이고 장중이면 화면이 3초마다
  다시 평가. KQS 추천 카드(전략 보드·대시보드 브리핑·의사결정 큐)는 기준일 종가 아래 현재가와 출처를 표시. 관심종목은
  현재가만 주기적으로 다시 읽는다.
- 설정: 서버 `app/backend/.env`에 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ENV`(real 기본 | mock). Git에 넣지 않는다.
- 검증: `tests/test_kis_quotes.py` 13 신규(파싱·등록 계획·캐시/토큰 재발급·오류·실시간 우선·세션·API 로그인/대체·포트폴리오
  우선순위·키 없을 때 기존 경로) — 한투 서버에 접속하지 않는 가짜 전송으로만. backend 373 passed, 렌더 테스트 전부 통과.
- 한계: 실제 한투 응답으로는 아직 검증 못 함(키 없음) — 키 입력 후 장중 실서버 확인 필요. 휴장일에도 장 시간에는
  WebSocket을 연다(체결 없음, 무해). 한 서버 프로세스(uvicorn 1 worker) 전제. 40종목을 넘는 요청은 REST로 채운다.
- 2026-10-02 후속: 사용자가 서버 `.env`에 실전 키를 넣고 backend를 재생성했다. 장중 컨테이너 안에서 시험(값 출력 없음):
  REST 현재가 005930·247540 정상, WebSocket 12초 안에 005930 실시간 체결 수신, 오류 없음. 실시간 체결 저장값에 종목코드가
  중복으로 남던 점을 고쳤다(동작 영향 없음).
- 2026-10-02 후속 2(평가액이 실시간↔전일 종가로 오가는 문제): 서버 실측에서 보유 11종목을 한 번에 조회하면 4~5개가
  `EGW00201 초당 거래건수 초과`로 거절됐고, 그 종목은 일봉 캐시(전일 종가)로 평가돼 총액이 오갔다. 수정 —
  REST 기본 초당 5건(`KIS_REST_PER_SECOND`로 조정), 초과 응답 시 속도 절반 + 최대 2회 재시도, 30초 무사고마다 1건씩
  회복; 같은 종목 동시 요청은 한 번만 조회; 실패 시 10분 안의 마지막 한투 시세(실시간 체결 포함)를 `stale_seconds`와
  함께 유지; 그래도 없으면 전일 종가보다 Yahoo 장중 지연 시세를 먼저 쓴다. 테스트 4 신규(backend 377 passed).
- 2026-10-02 후속 3: 접근토큰(24시간)을 DB `kis_access_tokens`에 저장해 재시작·배포·다른 프로세스가 재발급 없이 함께 쓴다
  (한투 발급 1분 1회 제한·잦은 발급 제한 대응). 앱키는 저장하지 않고 해시만 문서 키로 쓴다. 만료 10분 전이면 새로 발급.
  테스트 2 신규(backend 379 passed).
- 2026-10-02 후속 4: 서버 실측(토큰 공유 후) 11종목 모두 한투 시세(일부는 몇 분 내 마지막 값)로 평가, 오류 0. 다만 실제
  허용 속도가 초당 1~2건 수준까지 내려가 REST 기본값을 초당 3건으로 낮췄다. 공개 상태 확인
  `GET /api/market/quote-provider-status`(키·오류 내용 없음) 추가. backend 380 passed.

## EINT-025 — 2026-10-02: 추천 종목 위험 공시·거래소 지정 상태 표시(참고, 웹만)

- 요청: 보완 4단계 — 추천 종목에 위험 공시 경고. 재사용 결정: 관련 엔진 결정 없음(`check_engine_evidence --topic "공시"`
  결과 D-33은 표본 대기 규칙으로 무관). 추천 게시본·규칙·순위·청산·계약·학습 데이터는 바꾸지 않는다(EINT-022와 같은 참고 표시).
- 웹: `app/backend/stock_risk.py` — DART 최근 30일 공시 제목 분류(높음: 상장폐지·실질심사·관리종목·거래정지·횡령배임·
  회생파산·감자·불성실공시·감사의견 비적정·투자주의환기 / 주의: CB·BW·EB·유상증자(유무상 포함)·최대주주 변경·소송·
  조회공시). '해제' 공시와 무상증자, 일상 보고(최대주주등소유주식변동신고서 등)는 제외. 한투 현재가 응답의 종목 상태
  (iscd_stat_cls_code·mrkt_warn_cls_code·관리/정지/정리매매/단기과열/투자유의 Y/N) — 서버에서 필드 실재 확인. 현재가 조회
  응답을 10분 재사용. `GET /api/market/stock-risk`(로그인, 최대 30종목, 성공만 30분 캐시, 출처별 확인 실패를 따로 표시).
  화면: 전략 추천·대시보드 브리핑은 요약 배지, 의사결정 큐·전략 추천 펼침은 DART 원문 링크 목록.
- 검증: `tests/test_stock_risk.py` 20 신규 + 렌더 테스트, backend 401 passed. 미리보기 화면 확인(가짜 데이터).
- 한계: 공시 제목 기반 분류라 본문 내용(예: 감사의견)을 읽지 않는다. 30일 창 밖 위험은 보이지 않는다. 한투 상태 코드 표는
  공개 문서 기준이며 지정 종목 실례로는 아직 확인 못 함. 참고 표시이며 매매 판단·학습 피처로 쓰려면 사전등록이 필요하다.
- 후속(실데이터 확인): 코스닥 60종목 실제 공시로 점검해 6종목에서 신호, DART 오류 0. 이를 바탕으로 '관리종목지정우려'를
  지정과 구분, 병합·분할에 따른 거래정지는 '주의'로, 종속회사의 유상증자·사채 발행은 제외, 원본·정정 공시는 같은 날
  같은 유형이면 한 번만 세고, 공시 제목의 긴 공백을 정리했다. 실제 제목 회귀 테스트 추가(backend 407 passed).
- 2026-10-02 장애(약 14:10~14:28 KST, 사이트 응답 없음) 원인·조치: 배포 직후 새 프로세스에서 위험 공시·섹터 배지가 동시에
  호출되며 여러 스레드가 DART 회사코드 목록(약 10만 개 XML)을 각자 내려받아 해석해 backend 메모리가 1.25GB까지 늘고
  서버(1.9GB) 전체가 멈췄다. backend 재시작으로 복구. 수정 — 회사코드 목록 로드에 잠금(한 번만 로드), XML을 스트리밍으로
  해석, 위험 공시의 DART 동시 조회 4개 제한, 화면은 같은 종목의 섹터·위험 조회를 5분간 한 번만 요청, backend 컨테이너
  메모리 상한 1GB(넘으면 그 컨테이너만 재시작). 동시 첫 사용 회귀 테스트 추가(잠금을 빼면 실패함을 확인). backend 409 passed.

## EINT-026 — 2026-10-03: 추천 카드에 '검증된 청산 규칙'을 가격·수치로 표시(웹만, 엔진 코드 변경 없음)

- 요청: 진입 시점에 퇴출 계획을 수치로 보이게. 사용자가 제안한 '손절가·1차 목표가·손익비'는 그대로 만들지 않기로
  합의 — 평균회귀 2종은 연구 결정상 가격 손절·보유기간 상한이 없고(D-3·D-4, 청산 D-2), 세 아이템 모두 목표가 규칙이
  없어 손익비는 근거 없는 숫자가 된다. 기존 규칙만 추천 시점 값으로 바꿔 보여 준다.
- 재사용 결정: D-2/D-3/D-4(회귀 청산), Pullback v4 고정 청산(1.5×ATR 손절·최고 종가−2×ATR·15거래일·시장 50일선).
  게시본 features(close·bb_middle20·rsi14·atr)만 쓰고 계약·게시본·순위·학습 데이터는 바꾸지 않는다.
- 웹: `kqs_strategy_board.exit_plan()` → 보드 pick에 `exit_plan`. Bollinger-RSI: 볼린저 중간선 가격·괴리율 + RSI(현재값)≥50.
  RSI-OBV: RSI(현재값)≥50만(공개 값으로 가격 환산 불가 → 만들지 않음). v4: 손절·추적 기준 가격(기준일 종가로 계산한
  근사, 실제는 매수가 기준임을 명시)·15거래일·시장 조건. 의사결정 큐 카드와 전략 추천 펼침에 표시.
- 검증: backend 446 passed(신규 2: 아이템별 값·목표가/손익비 미생성·features 없을 때 미표시), 렌더 테스트 통과, 미리보기 확인.
- 한계: v4 손절·추적 값은 진입가(다음 날 시가)가 아니라 기준일 종가 기준 근사다. 매도 신호 판정은 기존 보유 종목 매도
  신호 기능이 맡는다.

## EINT-027 — 2026-10-03: 추천 카드 권장 매수 수량(변동성 기반 포지션 사이징, 웹만·참고 계산)

- 요청: 변동성이 다른 종목을 같은 금액으로 사지 않도록 '1회 손실 한도' 기준 수량 제시. 사용자 지시로 적용 후 유지 여부를
  사용자가 판단(되돌리기: 이 커밋 revert).
- 재사용 결정: 관련 사이징 결정 없음(`--topic 포지션/사이징/ATR`). D-3(회귀 전략 가격 손절 없음)을 지키려고 평균회귀
  2종은 손절가를 만들지 않고 '2×ATR 하락'을 사이징 전용 위험 단위로 가정(화면에 '청산 규칙 아님' 명시). v4는 검증된
  손절 폭 1.5×ATR(게시본 ATR) 그대로. 엔진·계약·순위·청산·학습 데이터 변경 없음, 주문 없음.
- 웹: `position_sizing.py`(Wilder ATR14 — 엔진 compute_indicators와 같은 식, 위험 단위, 수량), 한투 일봉 TR(FHKST03010100)로
  회귀 종목 ATR을 추천 기준일까지만 계산(종목·기준일당 1회 캐시). `GET /api/kqs/position-sizing?risk_pct=`(로그인):
  총자산(현금 포함 평가액, 일부 시세 없으면 확인된 자산 기준 표시) × 한도% ÷ 위험 단위, 리스크 정책 종목당 최대 비중
  (기본 35%)으로 제한, 현금으로 살 수 있는 수량 별도 표시. 대시보드 의사결정 큐: '1회 손실 한도'(0.25~3%, 기본 1%,
  브라우저 저장) + 카드별 권장 수량·금액·비중·손실액·근거.
- 검증: backend 450 passed(신규 4 — ATR이 엔진 지표와 1e-12까지 일치, 위험 단위·수량·비중 제한·현금 부족, API 로그인·
  자산 없음·실계산·기준일 이후 일봉 제외·ATR 캐시·한도 범위), 렌더 테스트 통과, 미리보기 확인.
- 한계: 진입가는 현재가(없으면 기준일 종가) 근사, 수수료·세금·호가 미반영. 회귀 종목 2×ATR은 검증된 손실 분포가 아닌
  가정이다. 수량은 정수 주로 내림.
- 2026-10-03 되돌림: 적용 결과(총자산 약 40.4억 원 기준 종목별 권장 수량, 소형주는 일평균 거래대금 대비 큰 수량)를 보고
  사용자가 "권장수량 되돌려"로 결정. 커밋 d6ac82d를 revert해 코드·화면·API·테스트를 모두 제거했다. EINT-026(청산 기준
  표시)은 별개라 유지. 다시 도입하려면 유동성 상한(예: 20일 평균 거래대금의 1% 이하)부터 검토한다.

## EINT-028 — 2026-10-06: 장 마감 후 저녁 게시(22:00) + 06:00·08:30 재시도, 게시되면 남은 시도 생략

- 요청: "오후 10시에 한번, 오전 6시에 한번, 오전 8시 30분에 한번 — 안되면 순차적으로 확인해서 업데이트하고, 업데이트되면
  나머지는 진행하지 않도록". 배경: 10/3~10/6 KRX 코스피 일별매매정보 401로 매일 `collect_failed`(사용자 재승인 후 복구).
- 재사용 결정: EINT-004의 `06:00 KST`는 보수적 시스템 기대 시각(KRX 보증·연구 결과 아님)이라 새 게시 정책으로 대체.
  첫 payload 고정·24시간 만료 상한·휴장일 검토 파일(SHA)은 그대로. 추천 규칙·유니버스·순위·청산·v4·계약 형태 변경 없음.
  `check_engine_evidence --topic 캘린더/공개 기대 시각/만료`: 관련 연구 결정 없음.
- 엔진: `krx-session-expiry/v2` — 세션 자료는 같은 날 21:30 KST 공개 기대. 새 게시는 v2, v1 metadata로 이미 게시된
  추천은 v1으로 계속 검증(10/6 복구 게시 포함), 모르는 정책·시각 위조는 거부. 파이프라인은 그 거래일이 이미 게시됐으면
  KRX를 부르지 않고 `already_published`로 끝난다(refresh 제외). 상태 점검 유예 6→12시간.
- 운영: 타이머 22:00·06:00·08:30 KST(기존 08:30·10:30), `kqs-status --grace-hours 12`. 웹 상태 경고는 유예 안의
  수집 실패(다음 시도 대기)를 띄우지 않고 유예가 지나면 기존처럼 오류. 안내 문구의 예약 시각 갱신.
- 검증: engine 138 OK(21:29/21:31 경계, v1 호환, 위조·미지원 정책 거부, 22:00 실패→재시도 게시→이후 시도 KRX 0회,
  refresh 미생략, 연말·만료 테스트를 v2 상한으로 이동), backend 447 passed, scripts OK, 렌더 통과,
  check_revision(EINT-028)·migration·release·evidence 통과. 계약 검사 스크립트는 로컬에 게시 스냅샷이 없어
  변경 전과 같은 이유로 실행 불가(서버 게시본으로 확인). 기본 검사 manifest를 EINT-028로 전환.
- 한계: 21:30도 기대 시각일 뿐 — KRX의 실제 당일 저녁 공개는 첫 22:00 실행에서 확인한다(너무 이르면 빈 응답은 저장하지
  않고 다음 시도가 받는다). 금요일 게시는 24시간 상한으로 토요일 밤 만료(월요일 장 전 유효 추천 없음은 v1에서도 같았음).
- 배포: 백엔드(웹 reader·상태 점검) 자동 배포 + 서버 엔진 worker 이미지 재빌드(롤백 태그 `before-eint028`)·타이머·compose 설치.

## EINT-029 — 2026-10-06: 금요일 밤 게시가 월요일 밤 새 게시까지 유지(다음 거래일 22:00까지 유효)

- 요청: "금요일 밤 10시 이후 게시된 건 월요일 밤 10시 첫 데이터 업데이트까지 유지되도록". 배경: EINT-028까지 24시간
  만료 상한 때문에 금요일 게시가 토요일 밤에 만료돼 월요일 장 전 유효 추천이 없었다(v1부터 같던 한계).
- 재사용 결정: EINT-028 저녁 일정(22:00·06:00·08:30)과 게시 후 생략은 그대로. EINT-004의 24시간 상한은 새 게시에
  한해 '다음 세션 공개 기대 시각까지'(안전 상한 10일)로 대체. 첫 payload 고정·휴장일 파일(SHA)은 그대로.
- 엔진: `krx-session-expiry/v3` — 세션 자료는 같은 날 22:00 KST 공개 기대, 게시는 다음 거래일 22:00까지 유효
  (metadata `expiry=until_next_session_availability`). 같은 날 아침 배포한 v2(21:30·24시간)는 오늘 22:00 실행이 쓸 수
  있어 고치지 않고 v3를 추가했다. v1·v2 게시본은 각자의 정책·24시간 상한으로 계속 검증한다.
- 검증: engine 139 OK(금 22:03 게시→월 22:00 만료 허용, +1분 거부, v2 metadata는 24시간 상한 유지, v3 표기 위조 거부,
  추석 09-23→09-28 22:00, 파이프라인 만료=다음 세션 공개 기대 시각, 연말 상한 22:00), backend 447 passed(상태 경고 유예
  테스트 시각을 v3 기준으로 조정), scripts OK, 렌더 통과, check_revision(EINT-029)·migration·release·evidence 통과.
- 한계: 다음 거래일 22:00 만료부터 새 게시까지 몇 분은 유효 추천이 없다. 다음 거래일 시도가 모두 실패하면 이전 게시는 그
  거래일 22:00에 만료된다(오래된 자료를 더 보여 주지 않음). 22:00도 기대 시각이며 KRX 당일 저녁 공개는 첫 실행에서 확인.
- 배포: 백엔드 자동 배포 + 서버 엔진 worker 재빌드(롤백 태그 `before-eint029`). 타이머·compose는 EINT-028과 같다.

## EINT-030 — 2026-10-06: KRX 휴장일 캘린더 연간 자동 준비(12월 15일, PR 승인 후 반영)

- 요청: "매년 12월 15일쯤 자동으로 캘린더 업데이트". 사용자 선택: 자동 준비 + 승인 후 반영, 천문연 API 함께 사용.
- 재사용 결정: EINT-004/016 캘린더 규칙(검토된 연도별 파일, 등록되지 않은 해는 fail-closed, 실행 중인 해 파일 불변)과
  EINT-029 게시 정책 그대로. 엔진 실행 코드(src) 변경 없음 → 새 개정 manifest 없음(도구·테스트·워크플로만).
- 추가: `scripts/kqs_calendar_prepare.py`(천문연 특일 정보 주 출처 + python-holidays 교차 확인 + KRX 규칙: 평일 공휴일·
  노동절·그해 마지막 평일; 출처가 다른 날짜는 자동 선택하지 않고 보고; 새 해 파일 생성·등록, 기존 잠정본 대조·갱신, 실행
  중인 해 거부), `engine/kqs/tools/new_revision.py`(다음 EINT 개정·검증 기록 생성, 덮어쓰기 금지, 기본 검사 전환),
  `.github/workflows/kqs-calendar-prepare.yml`(12/15 09:00 KST + 수동 실행: 준비→개정 기록→CHANGES 초안→엔진 테스트·
  무결성 검사→PR, main 자동 반영 없음), 홈페이지 알림(12월 1일~ 내년 캘린더 미등록, 20일~ 잠정본).
- 테스트: 등록된 모든 캘린더의 기본 KRX 규칙 검사 추가; '등록되지 않은 해' 검사 2곳을 '등록된 마지막 해의 다음 해'로
  바꿔 새 해가 등록돼도 의미 유지. 이 PC의 임시 작업 폴더에서 2028 생성→등록→EINT-030 개정 기록→엔진 테스트 OK→
  check_revision OK까지 연간 작업 전체를 모의 실행했다. 같은 규칙으로 기존 2027 잠정본 16일을 그대로 재현, 2026은 파일에서
  빠진 7/17(제헌절)을 잡아냈다(문서의 알려진 차이와 일치). engine OK, backend 450 passed, scripts OK.
- 한계: 천문연 키(`KASI_HOLIDAY_API_KEY`)와 Actions PR 생성 허용 설정이 있어야 동작한다. 임시공휴일이 12월 15일 뒤에
  지정되면 자동 작업이 잡지 못한다(수동 실행 가능). KRX만의 추가 휴장(시장 사정)은 공휴일 데이터에 없으므로 PR 검토에서
  공식 KRX 일정과 대조한다. 병합 후 서버 엔진 worker 재빌드는 수동이다.
