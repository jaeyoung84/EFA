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

## COLLAB-001 — 2026-10-06: Codex·Claude Code 협업 변경 표기와 수정 승인

<!-- Codex 수정: COLLAB-001 (2026-10-06), 이 절 전체. -->

- 사용자 요청: Codex 수정임을 명확히 표기하고, Claude Code가 해당 변경을 수정하기 전에 사용자 승인을 받게 한다.
- 재사용 근거: 협업 표기는 전략·연구 결정이 아니다. check_engine_evidence.py --topic “협업 변경 표기”는 일치 결정 0건,
  보관본 167개·결정 45개 무결성 통과를 보고했다. 과거 연구·holdout·전향 관측은 실행하지 않았다.
- 변경 범위: AGENTS.md에 Codex 변경 표식·변경 기록·Claude 승인 조건을 추가하고, 루트 CLAUDE.md와
  docs/CODEX_CHANGES.md를 신설했다. 엔진·홈페이지 동작 코드, 게시본, 개인 원장, 레버리지 DCA는 수정하지 않았다.
- 검증: git diff --check 통과; python3 scripts/check_engine_evidence.py 결과 integrity_ok=true, errors=0,
  보관본 167개·결정 45개. 동작 변경이 없는 지침·문서 작업이므로 새 단위 테스트는 추가하지 않았다.
- 버전·한계: 엔진 패키지 버전 0.7.0과 추천 계약은 그대로다. 표식과 지침은 협업 규칙이며 파일 권한 같은
  기술적 차단 장치는 아니다. 2026-10-06 이전 코드의 작성자는 소급 추정하지 않는다.
- 후속: 향후 Codex 수정마다 변경 구역 표식과 docs/CODEX_CHANGES.md 범위를 함께 갱신한다.

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

## EINT-031 — 2026-10-07: 추천 게시를 KRX 자료 공개 시각(다음 거래일 아침)에 맞춤 — 밤사이 공백 제거

- 요청: "오늘의 추천 동작 안 되는 것 같다" 확인 → 사용자 승인("단계별로 수정할게 있으면 수정해").
- 원인(운영 기록 2026-09-29~10-07): KRX Open API 일봉은 다음 영업일 아침에야 나온다. EINT-028 이후 22:00·06:00 시도는
  매번 `collect_failed`("KRX returned no KOSPI rows")였고 성공은 모두 08:30 이후였다. v3 만료(다음 거래일 22:00) 때문에
  아침 게시가 그날 밤 22:00에 끝나 매일 22:00~다음 날 08:30 추천이 비었고, 실패 경보도 매일 울렸다.
- 재사용 결정: 첫 payload 고정·게시 후 남은 시도 생략(EINT-028)·휴장일 파일(SHA)·안전 상한 10일(EINT-029)은 그대로.
  v1~v3 게시본은 각자의 정책으로 계속 검증한다.
- 엔진: `krx-session-expiry/v4` — 세션 자료는 **다음 거래일 08:00 KST** 공개 기대(metadata `data_available_day=
  next_session_day`), 게시는 **그다음 세션 자료 공개 기대 + 3시간**(다음 거래일 11:00)까지 유효(`expiry=
  until_next_session_availability_plus_grace`, `expiry_grace_minutes=180`, 위조 거부). 새 추천이 나올 때까지 앞 추천이
  보여 밤사이·주말 공백이 없다(목요일 자료 금 08:20 게시 → 월 11:00까지, 금요일 자료는 월 08:00 이후 게시).
- 운영: 타이머 08:20·08:50·09:30·10:30 KST, 상태 경보 유예 12h → 3.5h(11:30 이후 경보), 웹 경고 문구 시각 수정.
- 검증: engine 141 OK(v4 테스트 추가: 10-06 22:00·10-07 07:59엔 10-02가 최신, 08:00부터 10-06; 휴장 뒤 10-02 자료는 10-06
  08:00 공개; 목→월 11:00 허용·+1분 거부·유예 위조 거부·공개 전 게시 거부·주말 실행은 목요일 자료; 연말 12-30 → 01-05
  11:00; 기존 픽스처의 생성 시각을 다음 날 07:00 → 09:00으로), backend·scripts·렌더 통과, check_revision(EINT-031).
- 한계: 08:00은 기대 시각이며 KRX가 늦으면 10:30 마지막 시도까지 재시도한다. 모두 실패하면 앞 게시는 11:00에 만료된다
  (오래된 자료를 더 보여 주지 않음). 배포 당일 이미 v3로 게시된 10-06 자료는 v3대로 10-07 22:00에 만료되므로 그날 밤
  한 번은 10-08 08:20 게시까지 빈다.
- 배포: 백엔드 자동 배포 + 서버 엔진 worker 재빌드(롤백 태그 `before-eint031`), 타이머·compose 설치.
- 번호: 열려 있던 연간 캘린더 PR #1이 같은 EINT-031을 쓰고 있어, 그 PR은 닫고 새 기준에서 다시 만든다.

## EINT-032 — 2026-10-07: 사전등록 관측 B-1 — 볼린저 RSI "반등 확인 후 진입" 전향 관측(관측 전용)

- 근거: 클로드코드·코덱스 합의안 3번(반등 확인은 한 가지 가설로 사전등록, 실제 추천·수동 원장·필터 OFF 표본과 분리,
  소진된 D-20·D-21 구간 재사용 금지) + 사용자 승인. 자료 감사(`AUDIT-2026-10-07-reversion-data.md`)의 비용 기준 사용.
- 사전등록: `docs/engine/PREREGISTRATION_B_rebound_confirmation_v1.md`(SHA-256을 모듈 상수·결과 파일에 고정, 테스트로 검사).
  대상 = 2026-10-07 이후 게시된 swing 선정 신호 전부. A = 다음 거래일(S1) 시가 진입(현재 규칙), B = S1 종가가 S1 볼린저 하단
  위이고 신호일 종가 위면 S2 시가 진입, 아니면 미진입(0). 청산은 둘 다 현재 규칙(종가 ≥ 중간선 또는 RSI ≥ 50 → 다음 시가).
  비용 매수 0.065%·매도 0.265%(거래세 0.20%). 주 지표 = 신호별 B − A 평균, 끝난 짝 100개 또는 2028-10-07에 부트스트랩
  95% 구간(10,000회, 시드 20261007)으로 PASS(하한 > 0)·FAIL(상한 < 0)·INCONCLUSIVE. PASS도 자동 채택이 아니다.
- 엔진: `kqs_runtime/rebound_shadow.py` + 파이프라인 게시 뒤 관측 단계(`state/rebound-shadow.json`, 실패해도 게시 불변).
  추천 규칙·순위·청산·유니버스·게시 시각·계약은 그대로.
- 웹: `GET /api/kqs/rebound-shadow`(로그인), "매매 기록·연구 → 성과" 아래 별도 칸(실제 추천 성과와 섞지 않음).
- 검증: engine OK(test_rebound_shadow 7개: 해시 고정, 비용식, 확인 시 S2 진입·청산 규칙, 미확인 0, 미래 봉 미사용,
  거래정지 제외·등록 전 신호 제외, 판정 시점·재현 가능한 부트스트랩), backend 469 passed, 렌더 통과.
- 배포: 백엔드 자동 배포 + 서버 엔진 worker 재빌드(롤백 태그 `before-eint032`).
- 번호: 연간 캘린더 PR #3(EINT-032)은 닫고 새 기준에서 다시 만든다.

## EINT-033 — 2026-10-07: 평균회귀 2종 신호 단계 계측과 관찰 후보(관측 전용)

- 근거: 볼린저 RSI가 9/29~10/6 5거래일 연속 0건. 코덱스 검토와 합의한 2·3단계(탈락 단계 계측 → 관찰 후보 분리 표시,
  규칙 완화는 그 결과를 본 뒤 별도 가설) + 사용자 승인. 확인 결과 그 기간의 "신호 발생" 8~12건은 모두 Pullback v4(일일
  상한 탈락)였고 볼린저 RSI가 걸러진 경우는 없었다. 10/6 적격 약 276종목 중 하단 이하 6·RSI≤30 3·동시 0(RSI≤35는 14).
- 엔진: `kqs_runtime/signal_funnel.py` + 파이프라인 게시 뒤 단계(`state/signal-funnel.json`, 실패해도 게시 불변).
  기본 적격 단계는 엔진이 저장한 스캔 감사 기록에서 그대로 세고, 적격 종목은 스캐너와 같은 리더(거래정지일 채움)와 같은
  지표 함수로 조건별(하단 이하·RSI≤30, RSI 30 상향 돌파·OBV 상승) 충족 수, 두 조건 동시, **새 진입 신호**(스캐너의 다음
  세션 투영과 동일 — 조건이 연속 충족되면 첫날만 신호), 선정 수를 센다. 최근 250거래일 이력을 누적한다.
- 관찰 후보(추천 아님, 표시 전용): 볼린저 하단 +3% 이내·RSI≤35, RSI-OBV는 돌파했지만 OBV 미확인 또는 OBV 상승 중 RSI
  27~30 — 각 10개, 미충족 조건 표시. 매수 추천·검증된 신호로 표시하지 않고 어떤 성과 표본에도 넣지 않는다.
- 웹: `GET /api/kqs/signal-funnel`(로그인), 전략 추천 화면 아래 "평균회귀 신호 단계 · 관찰 후보(추천 아님)".
- 검증: engine OK(무작위 80개 시계열에서 새 진입 판정 = 스캐너 투영, 파이프라인 단계·이력), backend 471 passed, 렌더 통과.
- 다음(4단계): 몇 주 이력이 쌓인 뒤 병목 조건 하나만 사전등록 가설로 검토한다. 지금은 기준을 바꾸지 않는다.
- 번호: 연간 캘린더 PR #4(EINT-033)는 닫고 새 기준에서 다시 만든다.

## CODX-SL-001 — 2026-10-08: 공매도 거래·대차잔고의 추천별 전향 참고자료 축적 (Codex 수정)

- 요청: 공매도·대차잔고 단독 신호를 만들지 않고, 나중에 다른 특징과 혼합해 검토할 수 있도록 원자료·시점별 특징을 누적한다.
- 재사용 결정: D-26(새 가설 사전등록·독립 표본), D-27(관측 비개입), D-29(설명 자료만 제공), D-30(신호 0건 허용), D-33(기존 전향 표본 보호), EINT-023(시장 게이트 OFF), EINT-033(관찰 후보와 실제 추천 분리). D-20/D-21 소진 구간은 재평가하지 않는다.
- 범위: 이미 게시된 선정 추천 종목에만 KIS 일별 공매도 **거래대금 비중**과 대차 **잔고주수**를 08:45·16:10 KST에 수집 시도한다. 게시 ID/종목/슬롯별 원응답 SHA-256, 정규화 90행 이내, 자료 기준일, 실제 수집 시각, 개장 전 가용 여부를 최초 기록으로 고정한다. 60행이 있어야 방향을 표기한다. 공매도는 20거래일 평균 비중 3구간의 단조 방향과 최근 20일 일별 비중 표준편차(%p), 대차는 최근 60행의 첫·끝 잔고주수 변화율이다. 이 둘은 **합성 점수·매수/매도 신호가 아니다**.
- 노출: 인증된 저장자료 조회 API와 추천 카드의 중립 참고 칩만 추가. GET은 공급자를 재호출하지 않는다. 추천 선정·순위·진입·청산·엔진 자동 모의원장·사용자 수동 원장·레버리지 DCA는 변경하지 않았다.
- 검증: `python3 -m unittest scripts.tests.test_short_lending_reference -v` 4개 통과(미래 행 제외, 결측·이상치, 첫 기록 보존, 중복 종목, 게시일 불일치, 읽기 전용). Python `py_compile` 통과, Windows Node `--check` 통과. 연구 러너·잠금 평가·전체 운영 파이프라인은 실행하지 않았다.
- 한계: KIS 일별 공매도 **거래** 자료는 공매도 순보유잔고가 아니며 대차잔고도 공매도 전용 물량이 아니다. 공급자 지연·누락·최소 60행 미달은 `자료 부족`으로 남긴다. 08:20 첫 게시가 늦으면 오전 수집이 빗나가 16:10 자료만 남을 수 있으므로 실제 수집 시각과 `available_before_open`을 별도로 기록한다. 선정 종목만 관측하므로 전체 유니버스 효과 추정에는 쓸 수 없다. 원자료 해시는 백업이 아니다. 실제 KIS 응답/운영 DB 수집·AWS 배포는 이번 로컬 검증에 포함되지 않았다.
- 후속: 운영 적용은 별도 승인·배포 검증이 필요하다. 누적 자료로 혼합 가설을 세울 때는 피처 정의·결측 처리·체결 시점·독립 표본·실패 기준을 먼저 사전등록하고, 추천 당시 가용했던 기록만 사용한다.

## CODX-EXIT-001 — 2026-10-12: 청산 시점·종가 경고 표시 1차 정정 (Codex 수정)

<!-- Codex 수정: CODX-EXIT-001 (2026-10-12), 이 절 전체. -->

- 요청: 추천 종목의 불합리한 청산 표시를 우선 수정하고, 전략 성과는 나중에 새 전향 자료로 재검증. Claude Code 인계를 위해 완료·미완료 범위를 명시한다.
- 재사용: D-2(장기 RSI50), D-3/D-4(평균회귀 가격·시간 손절 기각), D-21/D-26(소진 구간 재탐색 금지), D-33(평균회귀 기존 전향 표본 보호), D-43(Daily Trend Pullback v4는 PASS가 아닌 전향 검증 중), EINT-026(추천 카드 참고선 표시). `check_engine_evidence.py --topic 손절` 및 `--topic Pullback`으로 원문 연결·무결성을 확인했다.
- 범위: 웹 청산 카드가 '검증된 규칙'이라고 성과 검증 완료처럼 말하지 않도록 '현행 청산 참고 기준'으로 정정. v4 추천 시점 손절·추적선은 기준일 종가 근사값이며 실제 보유 기준선·다음 시가 체결/가격과 다름을 표시. 보유 종목 v4 손절·트레일링은 기존 `exit_signal` API 계약을 유지하되 `signal_basis=close_warning`을 추가해 화면에서 '가격선 주의'로 표시하고 확정 매도 신호 수에서는 제외. 시장 레짐·보유 15일 및 평균회귀 종가 조건은 `rule_observed`로 표시. 자동 주문·체결 없음.
- 엔진 선정·순위·실제 청산 판정 규칙·게시 계약·기존 전향 표본·사용자 원장·레버리지 DCA는 수정하지 않았다. 과거 연구·소진 holdout·운영 파이프라인·AWS 배포는 실행하지 않았다.
- 검증: `node scripts/tests/strategy_board_render.mjs` 통과. `python3 -m py_compile app/backend/kqs_strategy_board.py tests/test_kqs_strategy_board.py` 통과. `python3 -c` 합성 보유 데이터에서 stop 경고와 시장 조건 관측 구분 통과. Python `pytest`는 확인된 세 환경에 설치되지 않아 테스트 파일 전체를 실행하지 못했다. `node --check` 3개 파일, `git diff --check` 통과. `python3 scripts/check_engine_evidence.py`는 보관본 167개·결정 45개, errors 0으로 무결성 통과(성과 검증 아님).
- 한계: 백엔드 `state=exit_signal`은 호환을 위해 유지하므로 오래된 API 소비자는 `signal_basis`를 무시하면 v4 종가 경고를 확정 신호로 오인할 수 있다. 이것은 자동 체결을 뜻하지 않는다. 운영 화면 확인·실시장 자료·성과 비교는 미실시.
- 후속: [인계 문서](CODX_EXIT_001_HANDOFF.md)의 50SMA 2일 연속 이탈 별도 전향 관측은 **아직 사전등록 확정·코드화·수집되지 않았다**. 조건·비용·체결 시점·짝짓기·실패 기준을 먼저 확정하고 별도 표본만 사용한다. 기존 v4 추천/청산은 그때도 별도 승인 전까지 그대로 둔다.

## EINT-035 — 2026-10-12: 미국 데이터 경로 1단계 — 섹터 ETF 일봉 수집과 미국 섹터 순환(참고 표시)

- 요청: 미국 주식 추천으로 가기 전에 미국 데이터 파이프라인부터 단계별로 연다. 1단계는 생존 편향이 거의 없는 섹터 ETF로 데이터 경로를 검증하고, 결과를 한국과 같은 섹터 순환(RRG) 화면으로 보여 준다.
- 원천: Alpaca 시장데이터 `v2/stocks/bars`, `feed=sip`(무료 플랜의 과거 일봉, 2026-10-12 서버에서 2년치 조회 확인), 원시(`raw`)와 분할·배당 조정(`all`) 두 벌. 키는 레버리지 DCA와 같은 모의 계좌 키(환경변수)만 쓰며 응답·오류에 넣지 않는다. Yahoo 원시 종가로 최근 90일을 교차검증(`verify_close_overlap`, 20일 이상 겹치고 최대 괴리 기준 이내면 verified).
- 대상: SPY(비교 기준), SPDR 11개 섹터(XLK·XLC·XLY·XLP·XLV·XLF·XLI·XLE·XLB·XLU·XLRE), 반도체 산업 SMH. 약 3년(1,100일) 이력.
- 저장: 한국 엔진 시장 DB와 분리된 Mongo `us_daily_bars`(종목·날짜 유일). 원시 일봉은 첫 수집값을 `$setOnInsert`로 고정하고, 이후 값이 달라지면 덮어쓰지 않고 실행 기록에 `raw_revisions`로 남긴다. 조정 일봉은 공급자가 과거를 다시 계산하므로 매 실행 갱신하고 실행별 `adjusted_sha256`을 남긴다. 정규장 마감 20분 뒤부터 그 세션을 완성 일봉으로 보고, 장중 미완성 일봉은 저장하지 않는다(NYSE 캘린더). 실행마다 `us_market_runs`에 출처·완성 세션·종목별 건수/신규/수정·교차검증·해시를 한 건씩 남긴다.
- 일정: 화~토 KST 07:30, 10:30 재시도(백엔드 스케줄러). 같은 세션을 다시 돌아도 원시 값은 바뀌지 않는다.
- 섹터 순환: 한국 화면(EINT-021)과 같은 엔진 함수(`weekly_closes`, `rrg_series`, `quadrant`, EMA 4/13/4주)를 조정 종가에 적용해 `us_sector_rotation_snapshots`에 날짜별로 저장. 웹 `GET /api/market/us-sector-rotation`은 저장 스냅샷만 읽고 Alpaca를 부르지 않는다. 운영 확인용 `GET /api/market/us-data-status`(로그인 필요). 섹터 순환 화면에 한국/미국 전환을 추가.
- 경계: 매매 신호·추천이 아니다. 한국 추천·엔진·원장·레버리지 DCA·주문은 변경하지 않았다. 미국 개별 종목·전략 가설은 아직 없다.
- 검증: `tests/test_us_market_data.py` 7개(완성 세션만 저장, 원시 불변·사후 수정 기록, 장중 일봉 차단, 공급자 실패 시 부분 저장 없음, 교차검증 경고 시 partial, RRG 구역, 읽기 전용·로그인 API). 섹터 순환 렌더 테스트에 한국/미국 전환 추가. 백엔드 전체 484 통과(기존 `test_dart_market_activity` 날짜 고정 실패 1건은 이번 변경과 무관, Codex DART-001 범위).
- 다음(2단계): 미국 개별주 유니버스(S&P 500·나스닥100)의 시점별 구성 종목 처리 방식과 생존 편향 한계를 먼저 문서로 고정한 뒤, 개별주 일봉 수집을 같은 경로에 붙인다. 전략 후보는 그 뒤 하나만 사전등록한다.

## EINT-036 — 2026-10-12: 미국 데이터 경로 2단계 — S&P 500 시점별 구성 이력과 개별주 일봉(편출·폐지 포함)

- 원칙: [US_DATA_POLICY_v1.md](US_DATA_POLICY_v1.md)를 수집 전에 고정. 실행 기록마다 문서 SHA-256을 남긴다. 전략·수익률·신호는 계산하지 않는다.
- 확인(운영 서버, 2026-10-12): Alpaca SIP 일봉 2016-01-04부터, 폐지 종목(SIVB·TWTR·ATVI) 마지막 거래일까지 존재, 분할 조정 정상, 티커 변경 시 과거 전체가 새 티커로 조회(META), 합병 신설은 별개 종목(DISCA→WBD). 기업행위 API의 `name_change`로 FB→META, FLT→CPAY, RE→EG, SATS→ECHO 확인.
- 구성 이력: 위키백과 현재 구성표(503종목)와 "Historical components of the S&P 500" 변경표(411행)를 판 번호와 표 해시로 고정하고 날짜 역순으로 되돌린다. 티커는 Alpaca 티커 변경을 끝까지 따라가 현재 티커로 바꾼다. 로컬 실데이터 복원 결과: 723종목, 2016-01~2026-10 월말 구성 수 504~508(검토 0개월), 별칭 없을 때 이상 3건(FLT·RE·SATS)은 모두 티커 변경이었다.
- 일봉: 구성 이력에 나온 전 종목을 2016년부터 백필(요청당 10종목), 이후 매일 최근 14일 증분(요청당 50종목). Mongo `us_equity_bars`에 종목·연도 문서로 저장. 원시 일봉은 처음 값 고정(달라지면 `raw_revisions`), 분할·분할배당 조정은 일자별 계수만 저장. 최근 계수가 저장값과 다르면(새 분할·배당) 그 종목의 계수를 2016년부터 다시 받는다. 일봉이 없는 종목은 기록해 두고 매일 백필하지 않는다.
- 품질: 매 실행 월말 coverage(구성 종목 중 그날 일봉 보유 비율, 99% 미만 검토), 구성 기간 일봉 공백(`no_bars`/`starts_late`/`ends_early`), 고정 표본 20종목(티커 SHA-256 순) Yahoo 교차검증. 일정 화~토 KST 07:45, 10:45. 운영 상태는 `/api/market/us-data-status`의 `equities`.
- 경계: 화면 노출·추천·한국 엔진·원장·레버리지 DCA 변경 없음. 폐지 수익률 처리·홀드아웃·연구용 내보내기는 3단계 사전등록에서 정한다.
- 검증: `test_us_universe.py` 4개(rowspan 날짜 채움, 역순 복원, 별칭, 월말 검토), `test_us_equity_data.py` 5개(판 번호별 1회 저장, 폐지 종목 백필·공백 표시·coverage, 원시 불변·기업행위 계수 재수집, 실패 기록·로그인 API, 고정 표본). 백엔드 전체 493 통과(기존 DART 날짜 고정 실패 1건 제외).

## CODX-EXIT-002 — 2026-10-12: v4 첫 게시일 보유일 오인 수정·성과 재검증 상태 (Codex 수정)

<!-- Codex 수정: CODX-EXIT-002 (2026-10-12), 이 절 전체. -->

- 요청: 배포는 Claude Code가 맡고 Codex는 청산 수정의 성과 재검증·남은 로컬 작업을 수행한다.
- 재사용: D-3/D-4 평균회귀 손절·기간 상한 기각, D-21/D-26 소진 자료 재선택 금지, D-43 v4 전향 표본과 불통과 힌트, EINT-026 청산 표시. `check_engine_evidence.py --topic 손절/Pullback` 결과와 원문을 재사용했다. 엔진 추천·청산 함수/설정·비용·수동 원장 변경 없음.
- 버그: `v4_holding_exit`에서 게시 시세 첫날과 수동 진입일이 같을 때 `holding_days=None`이 되어 15일 보유 상한을 즉시 충족한 것으로 오인했다. 비교 `<`를 `<=`로 고쳐 보유 1일로 계산하며, 시세 시작보다 진입일이 오래된 경우의 보수적 경고는 유지한다. 합성 회귀 테스트를 추가했다.
- 성과: 로컬 `state/kqs`에는 실관측 자료가 없어 새 청산 가설 성과를 계산하지 않았다. 운영 AWS 전향 표본 수는 미확인. 이미 소진된 Pullback 2022-01-01~2026-09-08 관측 #1~#5 및 D-20/D-21을 재실행하지 않았다. v4의 2일 연속 시장 이탈 가설은 아직 확정 사전등록·관측·성과 판정 전이며 현재 운영 규칙은 1일 그대로다. 상세: [성과 상태](CODX_EXIT_002_FORWARD_STATUS.md).
- 검증: 수정 전 합성 1일 시계열에서 잘못된 `holding_limit` 확인; 수정 후 동일 시계열은 `hold/holding_days=1`, 진입일이 게시 시계열보다 오래된 경우는 종전대로 `holding_limit`. `node scripts/tests/strategy_board_render.mjs`, Python `py_compile`, `git diff --check` 통과. `check_engine_evidence.py` 보관본 167개·결정 45개·오류 0(성과 검증 아님). Python `pytest`는 로컬 환경에 없어 테스트 파일 전체는 미실행.
- 버전·지문: 엔진 코드·설정·비용은 변경하지 않았다(엔진 pullback_v4.py SHA-256 `acc95cfdc1f70a8e70661c841dd7018314a1a77e782664f25075e5941c3b9430`). 웹 청산 계산 코드 SHA-256 `607ec492b29e62c92fea77a6c647374833c6005b3ae281b8df08232461602572`. 기준일은 합성 2026-10-12이며 실시장 자료·비용 입력은 없어 자료 해시·비용 산출값은 해당 없음. 전체 저장소/연구 성과의 지문으로 쓰지 않는다.
- 경계/후속: AWS 배포 없음. 새 shadow 수집·성과 비교에는 사전등록과 미래 독립 표본이 먼저 필요하다. Claude Code는 배포를 맡되 Codex 표식 구역을 편집하려면 파일·범위별 사용자 명시적 승인을 받아야 한다.

## EINT-037 — 2026-10-12: 미국 3단계 — 사전등록 US-PB-v1(한국 Pullback v4 규칙의 미국 대형주 이식, 실행 전 고정)

- 문서: [research/us_pullback_v1/PREREGISTRATION_US_PB_v1.md](../../research/us_pullback_v1/PREREGISTRATION_US_PB_v1.md). 미국 3단계의 유일한 후보. 작성 시점까지 미국 개별주의 수익률·신호·백테스트 결과를 보지 않았다(데이터 품질 지표만 열람).
- 규칙: `kqs_runtime/pullback_v4.py` 함수를 수정 없이 호출(신규 파라미터 0). 미국 대응만 고정: SPY 단일 게이트, 신호일 S&P 500 구성 종목만 진입(EINT-036 공백 표시 구간 전체 제외), 거래대금 하한 0, 비용 진입 5bp·청산 5.3bp, 분할 조정 가격(배당 제외), 자본 100,000 USD.
- 창·횟수: 2017-03-01~2026-09-30 한 번. 결과 파일이 있으면 실행기가 거부한다.
- 판정: 한국 TrendGoal 그대로(거래 ≥ 30, 기대값 > 0, PF ≥ 1.3, Payoff ≥ 2.0, 6개월 이익 구간 비율 ≥ 0.5) + 부트스트랩 PF 95% 하한 > 1. 평균 ≤ 0 REJECT, 일부 미달 FAIL(힌트), 전부 충족 PASS(과거) → 전향 관측(완결 100건, 시한 2028-10-12) 뒤에만 추천 표시. 폐지 forced_close > 2%·월말 coverage < 95%·해시 불일치는 INVALID.
- 잠금: `research/us_pullback_v1/LOCK.json`(pullback_v4.py `acc95cfd…`, run.py `717bea15…`, 사전등록 `b877ec9a…`).
- 검증: `research/us_pullback_v1/test_run.py` 4개(합성 자료: 구성 구간·제외 구간 진입 차단, 미국 비용, 1회 실행 거부, 잠금·스냅샷 해시 불일치 거부, 대응을 중립으로 두면 원래 v4 엔진과 거래 동일, 판정 규칙).
- 실행 경로: 운영 서버(RAM 2GB) 대신 로컬에서 실행. 서버 Mongo의 미국 컬렉션 4개만 덤프 → 비공개 백업 버킷 → 로컬 복원 → `export_snapshot.py`로 해시가 붙은 SQLite 스냅샷 → `run.py` 1회. 아직 실행하지 않았다.
- **실행·판정(2026-10-12 05:22 UTC, 1회, 27초, 로컬)**: 잠금 3/3 일치, 스냅샷 표 해시 bars `7c169ae5…`·spy `cc7c56a9…`·intervals `0fdfd54b…`·coverage `caba536e…`, 구성 이력 `1379247819:1379179365`, 제외 구간 17개, 창 내 월말 coverage 최저 97.83%(95% 미만 0개월), 폐지 forced_close 1건(0.1%) → INVALID 아님. 거래 1,030건, 평균 순수익률 **−0.054%**, 기대값 −0.054, PF 0.97, Payoff 1.32, 6개월 이익 구간 0.45(20개), 부트스트랩 PF 하한 0.81 → **REJECT**(평균 ≤ 0). 진단(판정 미사용): CAGR −1.6% vs 같은 창 SPY 12.8%, 최대 낙폭 −30.9%, 청산 market_regime 313·holding_limit 300·stop 276·trailing 136·forced_close 5. 거래 해시 `657f0014…`. 결과 `research/us_pullback_v1/RESULT.json`, 거래 `TRADES.csv`.
- 결론: 한국 v4 규칙은 미국 대형주에서 비용 차감 후 기대값이 음수였다. 사전등록 §4·§5대로 트랙 종료, 같은 창 재실행·파라미터 변형 금지. 한국 v4 판정·전향 관측은 바꾸지 않는다. 미국 데이터 경로(EINT-035/036)와 섹터 순환은 그대로 유지한다.
- 실행 경로 기록: 첫 스냅샷은 SPY가 2023-10-05부터라 실행 전에 폐기, ETF 수집 시작을 2016-01-04로 고쳐(`a44ed0d`) 다시 내보냈다(개별주 해시 동일). 서버 덤프는 비공개 버킷 `research-exports/us-equity/20261012T052111Z/`(회원 데이터 없음).
- **사후 탐색(2026-10-12, 사용자 요청, 판정 불변)**: 재실행 없이 기존 거래 1,030건만 나눠 봤다. 코로나(2020-02-20~06-30)·2022~23 하락장(2022-01-03~2023-12-29)과 보유 기간이 겹치는 거래 223건을 빼면 807건, 평균 −0.089%, PF 0.95, Payoff 1.27, 이익 반기 7/16으로 **오히려 나빠졌다**. 뺀 223건은 평균 +0.074%, PF 1.04(시장 게이트·국면 이탈 청산이 급락장 손실을 막은 효과로 보임). 같은 구간을 뺀 SPY 연환산은 12.9% → 18.9%. 연도별로는 2023·2024·2025만 플러스. 결론: "급락장 때문에 실패"가 아니라 평상시 상승장에서 비용 차감 후 기대값이 0 이하다. 구간 선택이 결과를 본 뒤 이뤄진 탐색이라 어떤 판정 근거로도 쓰지 않는다.

## CODX-USPORT-001 — 2026-10-12: 내 자산 한국·미국 합산 및 미국 뉴스·SEC 공시 참고자료 (Codex 수정)

<!-- Codex 수정: CODX-USPORT-001 (2026-10-12), 이 절 전체. -->

- 요청: 개인 포트폴리오에 미국 주식을 포함하고 한국 주식과 함께 원화 환산 총자산으로 표시하며, 미국 보유종목도 뉴스와 공시를 확인할 수 있게 한다.
- 근거·경계: `check_engine_evidence.py --topic "미국 주식 포트폴리오 뉴스 SEC 공시 자산 합산"`은 보관본 167개·결정 45개·오류 0, 관련 결정 일치 0건이었다. 자산·뉴스·공시는 사용자 판단 참고자료이며 추천·청산·순위·자동 모의원장·사용자 수동 원장·주문·레버리지 DCA를 변경하지 않았다. 과거 연구·holdout·운영 파이프라인은 실행하지 않았다.
- 평가: 기존 USD 시세와 `KRW=X` 환율 경로를 재사용한다. 종목별 `market=KR/US`와 시장별 보유/평가 건수·평가금액·취득금액을 반환하고, 화면은 한국·미국 소계와 현금을 원화로 합산한다. 일부 시세가 없으면 전체를 확정 합계로 표시하지 않고 확인된 시장 소계에 `일부`를 표시한다. 저장 직후 환율을 모르는 장부 단계에서는 한국 취득금액(KRW)과 미국 취득금액(USD)을 혼합 합산하지 않는다.
- 뉴스: 국내 한투·네이버 캐시는 유지하고 미국 보유종목은 Yahoo Finance 검색 응답을 시간당 백그라운드 수집해 별도 캐시에 둔다. 화면 GET은 외부 공급자를 호출하지 않는다. 관련 티커가 명시된 뉴스는 실제 보유 티커 일치 시에만 저장하고, 국내·미국 자료가 모두 있으면 각 시장에 최대 10개 슬롯을 배정한다.
- 공시: 미국 보유종목의 SEC `company_tickers.json`과 `data.sec.gov/submissions/CIK##########.json`에서 10-K·10-Q·8-K·6-K·20-F·DEF 14A·S-1·13D/G·Form 4 및 수정본을 읽어 EDGAR 원문 링크와 함께 캐시한다. SEC 공정접근 정책에 따라 연락 이메일이 포함된 `SEC_USER_AGENT`가 없으면 SEC 수집만 비활성화한다. 순차 호출과 0.12초 간격으로 공식 10 req/s 한도보다 낮게 유지한다.
- 화면: 내 보유자산에 한국·미국 원화 평가 소계, 통화 노출, 뉴스·SEC 제출자료를 표시한다. 대시보드 뉴스에도 `한국/미국` 출처를 구분한다. 국내 일반 공시는 기존 DART 화면을 계속 사용한다. 뉴스·공시는 매매 신호가 아님을 명시한다.
- 검증: Python `py_compile` 통과, Windows 번들 Node `--check` 2개 통과, `scripts/tests/portfolio_us_render.mjs` 통과, `git diff --check` 통과. `tests/test_us_portfolio_updates.py`에 환율 합산·Yahoo 관련성/안전 URL·SEC CIK/폼/링크/User-Agent·사용자별 캐시 API·스케줄 등록 회귀를 추가했다. 현 환경에는 pytest와 앱 Python 의존성이 없어 pytest 파일 전체는 실행하지 못했다.
- 운영 한계: `SEC_USER_AGENT`에 실제 운영 연락 이메일을 설정해야 SEC가 활성화된다. Yahoo 뉴스 검색은 공식 장기 보장 API가 아니므로 공급자 변경·누락 시 마지막 캐시와 오류를 유지한다. 미국 뉴스 비중은 뉴스 정렬용 미국 보유분 취득금액 비중이며 한국·미국 전체 평가 비중으로 해석하지 않는다. 운영 Mongo 수집·실제 외부 호출·AWS 배포는 수행하지 않았다.
- 인계: [배포·운영 확인](CODX_USPORT_001_HANDOFF.md). Claude Code가 이 Codex 표식 범위를 수정하려면 대상 파일·범위를 적어 사용자의 명시적 승인을 먼저 받아야 한다.

## EINT-038 — 2026-10-12: 미국 3단계 두 번째 관측 — 사전등록 US-MOM-v1(멀티 타임프레임 추세·모멘텀, 월 리밸런싱)

- 문서: [research/us_momentum_v1/PREREGISTRATION_US_MOM_v1.md](../../research/us_momentum_v1/PREREGISTRATION_US_MOM_v1.md). 사용자가 전달한 외부 도구 룰셋(6개월 +15%, 3개월 +5%, 1주 상승, 52주 고점 −5% 이내, RSI 50~70, ADX > 20, 50일선 이탈·ATR 손절, 동일 비중, 월 1회 리밸런싱)을 임계값 그대로 쓴다. 제안의 종목 목록은 쓰지 않는다.
- 모호한 항목을 실행 전에 고정: 월말 종가 판정·다음 시가 매매, 통과 종목 전부 동일 비중(상한 없음), 손절 = 최초 진입 시가 − 2×ATR(재설정 없음), 월중 청산 종목은 다음 리밸런싱에만 재진입, 시가총액 $10B 대신 시점별 S&P 500 구성.
- 같은 창(2017-03-01~2026-09-30)의 **두 번째 선언 관측**(US-PB-v1 REJECT를 본 뒤)이라 부트스트랩 PF 하한을 1.25% 분위로 보정. 판정 = 한국 TrendGoal 5기준 + 보정 하한 > 1 + 포트폴리오 CAGR > SPY CAGR. 급락 구간 제외 지표는 이번엔 **사전 선언 진단**으로 넣었다(판정 미사용).
- 잠금 `research/us_momentum_v1/LOCK.json`(지표 원본 pullback_v4.py, 스냅샷 로더, 실행기, 사전등록). 테스트 5개(월말 신호 → 창 첫 시가, 제외 구간 진입 없음, 현금 흐름 = 거래 손익 합, 1회 실행·잠금 거부, 판정 규칙). 아직 실행하지 않았다.
- **실행·판정(2026-10-12, 1회, 33초, 로컬, US-PB-v1과 같은 스냅샷 해시)**: 거래 2,313건, 평균 순수익률 **−0.15%**, PF 0.94, Payoff 1.51, 이익 반기 0.40, 보정 부트스트랩 PF 0.82 → **REJECT**(거래 평균 ≤ 0). 포트폴리오 CAGR 8.2%(SPY 13.0%), 최대 낙폭 −27.5%(SPY −34.2%), 샤프 0.65(SPY 0.77) — 금액 기준으론 벌었지만 손실 거래가 많고 SPY에 뒤졌다. 월평균 대상 23.4종목, 현금 100% 3개월, 청산 atr_stop 895·rebalance 882·sma50 532. 연도별 손익 2022 −13.1k·2023 −14.0k, 2021 +53.8k·2026 +36.3k. 사전 선언 진단: 급락 구간 거래만 PF 0.78(평균 −0.76%), 급락 구간을 뺀 날 연환산 13.0% vs SPY 19.1%, 뺀 거래 PF 0.98(평균 −0.05%). 결론: 평상시에도 SPY 보유보다 낮고, 급락장에서는 더 크게 진다. 트랙 종료. 표기 결함: RESULT.json의 `checks.cagr_vs_spy`가 numpy 불리언이라 문자열 "False"로 저장됐다(값은 False, 판정 영향 없음, 재실행하지 않음).

## USPORT 보완 — 2026-10-12: 미국 보유종목 네이버 뉴스·새 종목 즉시 수집 (Claude Code, 사용자 승인)

- 사용자 승인("응 승인해줄게 변경해 그리고 미국 종목에 네이버뉴스도 추가해")으로 Codex CODX-USPORT-001 범위(`app/backend/us_portfolio_updates.py`)를 보완했다. Codex 표식과 등록 행은 그대로 두고 추가 부분에 `Claude Code 수정` 주석을 달았다.
- 원인: 미국 뉴스는 1시간 주기 수집 캐시만 읽어, 배포 직후 수집(보유 0종목) 뒤에 저장한 TSLA는 다음 주기까지 뉴스가 비어 있었다(운영에서 수동 1회 수집으로 즉시 해소).
- 즉시 수집: 화면이 캐시 없는 미국 종목을 만나면 그 종목만 백그라운드로 한 번 수집한다(진행 중인 종목은 다시 예약하지 않음, 국내 `schedule_missing`과 같은 방식).
- 네이버 뉴스: 한글 별칭(TSLA→테슬라), ETF는 티커, 그 밖에는 영문 회사명으로 네이버 뉴스 검색(최신순 20건) 후 제목에 이름·티커가 있는 기사만 남긴다. Yahoo(영문)와 번갈아 저장해 종목당 3건 표시에도 두 출처가 섞인다. 한쪽 출처만 실패하면 저장하고 `partial_error`로 남긴다. 국내 `NaverNewsSearch.search`에 선택 인자(display·sort)만 추가했다.
- 검증: `tests/test_us_portfolio_naver.py` 5개(검색어 규칙, 제목 필터·링크 안전, 번갈아 합치기, 한쪽 실패 저장, 캐시 없을 때 1회만 예약). 테스트 DB 정리 목록에 미국 뉴스·SEC 캐시 추가. 백엔드 594 통과(기존 DART 날짜 실패 1건 제외).

## EINT-039 — 2026-10-14: 외부 추천 기록(인베스팅닷컴 ProPicks·저평가 명단의 전향 성과 추적) — 자체 퀀트 1단계

- 요청: 인베스팅닷컴 저평가주·AI 추천 명단을 매달 편하게 가져와 우리 퀀트에 쓰기. 자동 수집(스크래핑)은 이용약관(자동 수집·재게시 금지)과 봇 차단 때문에 하지 않고, 사용자가 CSV를 올리거나 표를 복사해 붙이면 나머지를 자동화했다.
- 인식: 머리글에 Symbol·Ticker·종목코드 열이 있으면 그 열, 없으면 대문자 단어·6자리 코드를 후보로 뽑고(AI·EPS 등 흔한 단어 제외) 저장 전 체크박스 미리보기로 확정. 미국은 Alpaca 거래 가능 종목 목록(7일 캐시), 국내는 KRX 목록으로 검증. 한 명단 최대 60종목, 미래 날짜 금지.
- 성과: 기록일 **다음 거래일 시가**에 동일 비중 진입으로 보고 분할·배당 조정 가격으로 계산(미국 Alpaca, 국내 기존 검증 일봉). 벤치마크 미국 SPY·국내 KODEX 200. 명단별 수익률·초과수익·벤치마크를 이긴 비율·1/3/6/12개월, 출처별로 명단을 갈아탄 연결 성과(교체당 0.1% 차감). 시세는 08:40·16:40 KST와 저장 직후 즉시 수집.
- 경계: 과거 추천 기록이 없으므로 백테스트가 아니라 기록 이후의 전향 관측. 본인 계정 전용(다른 회원에게 보이지 않음), 원문은 SHA-256만 저장, 수정 불가·삭제만 가능. 추천·주문·원장과 연결하지 않는다. 12개월 미만은 판단 근거로 쓰지 말라는 안내를 화면에 둔다.
- 화면: KQS 전략 > 외부 추천 기록. 모든 `efa-page`를 한 열 고정 그리드로 바꿔 표 최소 폭이 페이지를 넓히는 모바일 넘침을 사이트 전체에서 예방.
- 검증: `tests/test_external_picks.py` 6개(열·토큰·국내 코드 인식, 다음 거래일 시가 진입·기간 수익률, 명단·연결 성과, 저장 검증·본인 전용 API, 시세 수집 대상·벤치마크), `scripts/tests/external_picks_render.mjs`, 390px 모바일 렌더(넘침 0). 백엔드 602 통과.
- 다음(2단계): SEC EDGAR 재무 데이터 파이프라인 → 공시 제출일 기준 시점별 가치·퀄리티 팩터 → 사전등록 후 2017~2026 1회 검증. 외부 추천의 전향 성과는 그 팩터와 비교하는 기준으로 쓴다.

## EINT-040 — 2026-10-15: 선물·수급(일별) — 코스피200 선물·코스피 현물 투자자별 수급 3개월 보관 (Codex 미완 작업 이어받음)

- 요청: "한국투자증권이나 KRX에서 선물 데이터를 가져와 홈페이지에 최대 3개월치 보관" — Codex가 조사 단계(2026-10-15 19:02~19:08)에서 멈춘 작업을 사용자 지시로 Claude Code가 이어받았다. Codex는 파일을 바꾸지 않았다.
- 출처 확인(운영 서버에서 한투 조회 TR 직접 호출, 주문 없음): 시장별 투자자매매동향(일별) FHPTJ04040000에 `FID_INPUT_ISCD_1=K2I, FID_INPUT_ISCD_2=F001`을 넣으면 **코스피200 선물** 투자자별(외국인·기관·개인 등 12구분) 일별 순매수가 지정일까지 약 300거래일 온다. `KSP/0001`은 코스피 현물. 단위: 선물 수량 계약, 금액 백만 원(10/14 외국인 3,557계약 = 844,524백만 원 ≈ 944 × 25만 원 × 계약으로 검증). 선물 일봉은 FHKIF03020100(최근월물 단축코드, 예 A01612), 미결제약정·베이시스·코스피200은 현재가 FHMIF10000000에만 있어 과거 값이 없다. KRX 경로는 필요 없었다.
- 저장: `app/backend/futures_flows.py`, 컬렉션 `market_futures_daily`(날짜별 1문서), 실행 기록 `market_futures_runs`. 평일 16:20·16:40·18:20·18:40 KST와 서버 시작 2분 뒤 수집(호출 4건). 92일 지난 날짜만 삭제, 실패·빈 응답은 기존 값을 덮지 않음. 미결제약정은 장 마감(15:45) 뒤 그날 수급이 나온 경우에만 그날 값으로 기록(장중·휴장일 기록 방지). 현물 수량 단위는 확인하지 못해 화면에 쓰지 않는다.
- 화면: 시장 리뷰 > 선물·수급. 요약(외국인 선물 최근일·5/20일 누적, 외국인 현물, 미결제약정), 외국인 선물 일별 순매수 막대(빨강 매수·파랑 매도), 외국인 선물 vs 현물 3개월 누적선(같은 억 원 단위 한 축, 색은 팔레트 검사 통과), 일별 표. 공개 API `GET /api/market/futures-flows`(시장 자료, 개인 정보 없음).
- 한계: 선물 일봉은 '현재 최근월물' 기준이라 월물 교체 전 날짜도 같은 월물 가격. 미결제약정은 수집 시작일부터 쌓인다. 참고자료이며 추천·주문과 연결하지 않는다.
- 검증: `tests/test_futures_flows.py` 5개(응답 해석·단위, 빈 값 미덮어쓰기, 날짜 병합·장 마감 미결제약정·보관 기간 삭제, 실패·장중 시 기존 유지, 공개 API), `scripts/tests/futures_flows_render.mjs`, 1280px·390px 렌더(가로 넘침 0). 백엔드 609 통과.

## EINT-041 — 2026-10-16: 급락 뒤 반등과 외국인 선물 수급 — 탐색 분석 + 사전등록 전향 관측(KR-CRASH-REBOUND-v1)

- 요청: 외국인 선물 수급에 따른 현물 반응으로 코스피 반등 확률 추정 → "커밋하고 관측하도록해".
- 탐색(`research/futures_flows_kr/rebound_study.py`): 한투 일별 투자자 수급을 300일씩 거슬러 2000-06~2026-10-15(6,487거래일) 확보. 외국인 선물 순매수(직전 250일 z)와 이후 1~60일 코스피 수익의 순위상관 0.00~0.03 — 예측력 없음. 당일 −6% 이하 폭락 26일: 20거래일 안 +5% 반등 88%, 20·60거래일 뒤 상승 58%. 폭락일 대부분 외국인 선물은 순매수라 구분력이 없다. 사전등록 아님.
- 사전등록(`PREREGISTRATION_CRASH_REBOUND_v1.md`, LOCK.json 2026-10-16): 사건 A(당일 −6%)·B(당일 −4% 또는 5일 −8%), 예측 P1~P5, 윌슨 95% 구간 판정, 2026-10-15 이후 사건만, 최종 판정은 독립 구간 10개 또는 2028-10-15. 판정은 `observe.py`가 한투 자료를 다시 받아 계산.
- 서버 기록: `futures_flows.update_crash_events` — 수집 때마다 사건 B를 `market_crash_events`에 처음 값 그대로 영구 기록하고, 사건 뒤 종가를 문서에 쌓아(92일 보관 삭제와 무관) 20일 안 +5% 반등·20/60일 수익을 채운다. 화면 선물·수급에 '급락 관측' 표.
- 검증: `tests/test_futures_flows.py` 7개(사건 규칙·결과, 한 번만 기록·여러 수집에 걸친 결과 채움·보관 삭제 뒤 유지 추가), 렌더 테스트.

## CODX-MA-001 — 2026-10-16: RSI-OBV + 신호일 종가>SMA60 탐색 비교 (Codex 수정)

<!-- Codex 수정: CODX-MA-001 (2026-10-16), 이 절 전체. -->

- 요청: 현재 RSI-OBV와 `RSI-OBV + 신호 확인일 종가>SMA60`을 동일 백데이터로 비교했다. 실행 전에
  [사전 기록](../../research/rsi_obv_sma60_exploratory/PREREGISTRATION.md)에 규칙·비용·적격·열람 지표를
  고정했다. 2016-08-29 이후 창은 기존 평균회귀 연구에서 이미 사용됐으므로 이번 결과는 탐색 진단이고
  PASS·운영 채택 근거가 아니다.
- 공통 조건: RSI(14)가 30을 상향 돌파하고 OBV가 OBV 9일 평균 위에서 당일 증가하면 다음 거래일
  시가 진입, RSI>=50이면 다음 거래일 시가 청산. 시장 SMA200 게이트 OFF. 시점별 시총 KOSPI 200위·
  KOSDAQ 150위, 20일 평균 거래대금 50억원, 이력 260세션, ±50% 급변 뒤 60세션 제외. 비용은 매수
  6.5bp·매도 24.5bp. 변형은 이 중 신호 확인일 `종가>SMA60`만 추가했다.
- 자료·잠금: DB 7,577,245행·4,656종목, 2016-08-29~2026-10-13, SHA-256
  `1037737ef17104d0faed0ad5cba8e32bf44f6dfa5801b20227936444fc341b98`. 측정 진입 구간은
  252세션 워밍업 뒤 2017-09-05~2026-10-13. 실행기 해시 `6541200198ed093c…`, 사전 기록 해시
  `eabf1a535b2ee191…`가 [결과](../../research/rsi_obv_sma60_exploratory/RESULT.json)와 일치한다.
- 결과: 원본은 완결 1,002건·미완결 4건, 승률 68.86%, 비용 후 거래당 평균 +1.97%, 중앙값 +3.69%,
  PF 1.57, 손익비 0.71, 하위 5% −22.02%, 평균 보유 17.82세션, 63세션 창 이익 비율 75.0%였다.
  SMA60 변형은 완결 **6건**뿐이며 승률 33.33%, 평균 −4.63%, 중앙값 −7.20%, PF 0.54, 하위 5%
  −27.29%, 평균 보유 22.83세션이었다. 원본 진입 1,006건 중 같은 진입은 6건, 원본만 1,000건,
  변형만의 후속 진입은 0건으로, 필터가 원본 진입의 **99.4%**를 제거했다.
- 15슬롯·일 3종목·시총 우선·365일 평가 상한 진단: 원본 CAGR +2.95%, 실현 이벤트 기준 MDD
  −28.19%, 배치 680건, 노출 92.90%. 변형 CAGR −0.21%, MDD −3.03%, 배치 6건, 노출 4.36%.
  변형 MDD가 작은 것은 위험 개선 증거가 아니라 거의 투자하지 않은 결과다. 실현 이벤트 MDD는 보유 중
  평가손실을 보지 않아 실제 일별 MDD보다 작게 측정될 수 있다.
- 해석: RSI 30 회복은 중기 평균선 아래에서 발생하는 평균회귀 신호라 같은 날 `종가>SMA60`과 구조적으로
  충돌한다. 6건은 성과 판단이 불가능하며 가설은 운영 후보에서 제외한다. 공식 과거 KOSPI200·KOSDAQ150
  구성표 대신 당시 시장별 시총 순위를 재구성했다는 한계도 있다.
- 검증: 합성 단위검사 3개(양방향 비용, 관측일 필터/다음 시가 정렬, 매도 불가 시 청산 이월) 통과,
  실행기·사전기록 해시 일치 확인. 추천·순위·청산·시장 게이트·기존 전향 카운터·원장·주문·AWS는
  변경하지 않았다.
- 후속: 같은 날 가격 위치를 강제하는 대신 `신호 20거래일 전 종가>SMA60`처럼 과매도 이전 추세를 보는
  후보가 구조적으로 더 양립한다. 이것도 새 가설이므로 별도 사전등록과 새 전향 표본 없이는 채택하지 않는다.

## CODX-MA-002 — 2026-10-16: RSI-OBV + SMA60 상승 방향 단일 전향 가설 (Codex 수정)

<!-- Codex 수정: CODX-MA-002 (2026-10-16), 이 절 전체. -->

- 요청·가설: 현행 RSI-OBV 장기 엔진에 이동평균의 가격 위치를 다시 붙이지 않고, 신호 확인일
  `SMA60[t] > SMA60[t-1]`인지 하나만 독립 전향 관측한다. 상승률 문턱·연속 상승일·SMA20/200·시장
  필터는 추가하지 않는다. 세부 규칙과 판정은
  [사전등록](PREREGISTRATION_RSI_OBV_SMA60_SLOPE_v1.md)에 고정했다.
- 재사용·근거: `check_engine_evidence.py --topic "RSI-OBV SMA60 상승 기울기 전향 관측"`은 보관본
  167개·결정 45개·오류 0, 직접 일치 0건이었다. CODX-MA-001에서 같은 날 `종가>SMA60`이 진입의
  99.4%를 제거한 결과는 새 가설의 성과 근거로 재사용하지 않고, 구조 충돌을 피하기 위한 설계 근거로만 썼다.
- 구현: 2026-10-16 이후 실제 게시된 `long_term` 선택 신호마다 A(현행)와 B(SMA60 상승일만 현행과
  같은 다음 시가 진입)를 별도 JSON에 누적한다. B가 제외한 신호는 현금 0%로 기록하고, 두 팔 모두 현행
  RSI 50 종가 확인 후 다음 거래 가능일 시가 청산과 같은 비용(매수 6.5bp·매도 24.5bp)을 쓴다.
- 판정: B 완결 100건·A 대비 보존율 20%가 최소 문턱이다. 대응 차이 부트스트랩 95% 구간, PF, 하위
  5%를 사전 고정했으며 2028-10-16까지 문턱 미달이면 `REJECT_TOO_SPARSE`이다. 종료 전은 `not_due`이고
  중간 결과로 조건 변경·채택을 하지 않는다.
- 경계: 추천·순위·청산·원장·주문·기존 전향 표본·레버리지 DCA를 바꾸지 않았다. AWS 배포·과거
  백테스트·운영 자료 소급 적재를 하지 않았다. 일일 파이프라인 실패 비차단 참고 단계로만 연결했다.
- 검증 상태: 합성 회귀 테스트를 작성했지만, 사용자 지시에 따라 성과 백테스트와 검증 실행은 Claude Code에
  인계한다. Codex 실행 시도 환경에는 pytest 및 pandas/numpy가 없어 테스트가 시작되지 않았으며 결과로
  간주하지 않는다. Claude Code는 아래 Codex 표식 파일을 수정하려면 사용자 승인을 먼저 받아야 하지만,
  **내용을 수정하지 않는 테스트 실행은 가능**하다.
- 인계 명령: `engine/kqs/.venv/bin/python -m pytest engine/kqs/tests/test_rsi_obv_sma60_slope_shadow.py
  engine/kqs/tests/test_pipeline.py -q`; 이어서 `python3 scripts/check_engine_evidence.py --topic
  "RSI-OBV SMA60 상승 기울기 전향 관측"`와 `git diff --check`. 배포 후 첫 신규 게시 신호부터 표본이
  시작되며 배포 전에는 관측이 시작되지 않는다.

## EINT-042 — 2026-10-16: CODX-MA-002 엔진 코드의 개정 등록과 인계 검증 (Claude Code)

- 배경: Codex가 97765ef(CODX-MA-002, RSI-OBV SMA60 상승 기울기 그림자 관측)에서 엔진 실행 코드(`kqs_runtime/pipeline.py`, `kqs_ops/paths.py`, 새 `kqs_runtime/rsi_obv_sma60_slope_shadow.py`)를 바꿨지만 후속 개정을 등록하지 않아 `check_revision`이 EINT-033 대비 실패했다(`scripts/tests/test_kqs_revision.py` 1건, 배포 CI 차단 원인).
- 조치: `engine/kqs/tools/new_revision.py --change-id EINT-042`로 현재 엔진 바이트를 등록(`engine/kqs/revisions/EINT-042.json`, `docs/engine/verification/EINT-042.json`, `check_revision.py` 기본값 전환). Codex 표시 파일은 수정하지 않았다.
- 인계 검증(CODX-MA-002 절의 인계 명령): `engine/kqs/.venv`에는 pytest가 없어 테스트용 환경으로 실행 — `test_rsi_obv_sma60_slope_shadow.py`·`test_pipeline.py` 14 통과. `check_engine_evidence.py --topic "RSI-OBV SMA60 상승 기울기 전향 관측"` 무결성 정상·직접 일치 0. 전체 `tests`·`scripts/tests`·`engine/kqs/tests`는 Codex 커밋 이전에도 실패하던 환경 의존 2건(`test_runtime.py` 저장소 밖 import·미설치 모듈 검사)을 빼고 통과.
- 줄끝 정리(사용자 승인 "줄바꿈 정리하고", CODX-MA-001 범위): `RESULT.json` CRLF→LF·끝 줄바꿈(JSON 값 동일), `test_run.py` 끝 빈 줄 제거.
  `PREREGISTRATION.md`·`run.py`는 해시가 RESULT.json과 CODX-MA-001 절에 기록돼 있어 바이트를 바꾸지 않았다(끝 빈 줄 1개 남음).
- CODX-MA-001 결과 확인: 같은 날 `종가>SMA60`은 원본 진입 1,006건 중 6건만 남김(독립 재계산: 대형주 RSI 30 상향 돌파 6,538회 중 종가>SMA60 8회, 0.12% — 돌파일 종가는 SMA60보다 중앙값 12% 아래).

## EINT-043 — 2026-10-16: RSI-OBV + SMA60 상승 기울기 과거 참고 백테스트 (CODX-MA-002 인계, 판정 미사용)

- 요청: "이제 백테스트 진행하자" — CODX-MA-002가 Claude Code에 넘긴 성과 백테스트. 사전 기록 `research/rsi_obv_sma60_slope_backtest/PREREGISTRATION.md` 뒤 1회 실행.
- 방법: A = CODX-MA-001 실행기의 baseline을 코드 수정 없이 불러 재현(완결 1,002건, 15슬롯 CAGR 2.95%, 실현 MDD −28.2% — CODX-MA-001 결과와 일치). B = A의 각 거래 중 신호일 SMA60 > 신호 전일 SMA60인 것만 같은 가격·시점으로 진입, 나머지 현금 0%.
- 결과(2017-09-05~2026-10-13): B 완결 56건(보존율 **5.6%**), 승률 67.9%, 평균 +1.40%, PF 1.33(A 1.57), 하위 5% −24.1%(A −22.0%). 신호 단위 B−A 평균 −1.90%p, 부트스트랩 95% [−2.64, −1.17]. 15슬롯 CAGR 0.26%(노출 39%), MDD −6.3%. 연도별로 B가 A보다 나은 해는 2017·2024~2026(각 2~12건)뿐.
- CODX-MA-002 판정 문장을 이 과거 표본에 그대로 적용하면 `REJECT_TOO_SPARSE`(B 100건·보존율 20% 미달). **참고 표시이며 CODX-MA-002 판정에 쓰지 않는다.**
- 시사점: RSI가 30을 상향 돌파하는 날은 대부분 SMA60이 내려가는 중이라 기울기 조건도 신호를 크게 줄인다. 과거 보존율 5.6%면 전향 관측의 B 100건에는 A 신호 약 1,800건이 필요해, 2028-10-16까지 표본 문턱을 못 채울 가능성이 크다.

## EINT-044 — 2026-10-16: 전향 관측 등록부 기한 점검(Codex CODX-OBS-001 이어받음)

- 배경: 사용자 "현재 관측하고 있는 것들도 판정 여부·기간을 정하자, 누락되지 않게 이번에 다 정리하자" → Codex가 `docs/engine/observation_registry.json`(CODX-OBS-001: 성과 가설 7·참고 관측 5·시작 전 1·비활성 명시 2, 각 시작일·성숙 조건·판정 시한·미성숙 처리·적용 정책·다음 무결성 점검일)을 작성하던 중 사용량 한도로 멈췄다(2026-10-16 10:26). 등록부 내용은 Claude Code가 바꾸지 않았고, Codex가 계획한 "기한 45일 전 경고" 장치만 구현했다. `docs/CODEX_CHANGES.md`의 CODX-OBS-001 행은 Codex 범위라 추가하지 않았다(Codex 재개 시 등록 필요).
- 구현: `app/backend/observation_registry.py` — 형식 검증(필수 항목·날짜·id 중복·근거 파일 존재), 다음 무결성 점검일·중간 보고일·판정 시한이 45일 안이면 주의, 지나면 오류. 홈페이지 상단 알림(`kqs_pipeline_health`)에 연결해 화면을 열 때마다 검사(월 1회 작업보다 누락이 적음). `scripts/check_observation_registry.py`는 같은 검사를 명령줄로, 형식 오류·근거 누락·기한 지남이면 종료 코드 1.
- 대조: D-33 2028-08-28, Pullback v2/v4 2028-09-08은 evidence_registry와 일치. 현재 임박: D-33·Pullback v2·v4 무결성 점검 2026-10-31, KR-LV-v2 첫 명단 점검 2026-11-10.
- 테스트: `tests/test_observation_registry.py` 5개(실제 등록부 형식·근거, 45일·지남 판정, 형식 오류, 알림 수준·파일 없음, 엔진 상태 연결). 엔진 상태 기존 테스트가 실제 날짜 알림에 흔들리지 않도록 `tests/conftest.py`에서 관측 알림을 기본으로 끔.

## EINT-045 — 2026-10-16: 시장 전체 평균회귀 신호 밀도(≥9) 전향 그림자 관측 (Codex CODX-DIR-001/002 방향, 사용자 시작 승인)

- 승인: 사용자 "둘다 승인할고 푸시해"(밀도 관측 시작 + CODX-OBS-001 등록). 방향은 Codex 문서 그대로 구현.
- 사전등록: `docs/engine/PREREGISTRATION_SIGNAL_DENSITY_v1.md`(해시를 모듈 상수로 고정). 밀도 = 엔진 스캔 기록의 swing·long_term `entry_due_next_session` 행 수(게시 상한 전, 날짜별 첫 기록 고정), 임계값 9 하나.
- 구현: `kqs_runtime/signal_density_shadow.py` — A = 게시된 두 항목 선정 진입 전부(다음 시가, 항목별 현행 청산), B = 밀도 ≥ 9인 날의 A만. 두 팔 같은 15슬롯·하루 신규 3개·종가 평가 포트폴리오. D0 = 운영 첫 실행일(파일에 한 번 기록). 판정은 D0+24개월·B 완결 100건을 모두 채운 뒤 첫 월말 자료로 한 번(36개월 시한 → REJECT_TOO_SPARSE) 후 고정. PASS_CANDIDATE = B CAGR > A, 일별 MDD 비악화, 보존율 ≥ 20%, 월수익 6개월 블록 부트스트랩 CAGR 차이 95% 하한 > 0. 판정 전에는 건수만 기록(중간 수익률 미계산). 일일 파이프라인 마지막 참고 단계(실패해도 게시 무관).
- 엔진 개정: EINT-045(`engine/kqs/revisions/EINT-045.json`). Codex 표시 구역(CODX-MA-002 import·단계·경로)은 그대로 두고 아래에 별도 단계로 추가.
- 등록부: `observation_registry.json`의 밀도 항목을 `approved_awaiting_deployment`로(배포 뒤 첫 무결성 점검에서 D0와 함께 활성 목록으로 이동). `docs/CODEX_CHANGES.md`에 CODX-OBS-001 행 등록(사용자 승인).
- 검증: `engine/kqs/tests/test_signal_density_shadow.py` 7개(사전등록 해시, 밀도 정의, 판정 시점·시한, 판정 문장, 포트폴리오 슬롯·상한·평가, D0·청산·행 구성, 첫 기록 밀도 고정), `test_pipeline.py`에 단계 기록 확인 추가.

## EINT-046 — 2026-10-16: 저장소 공개 전환 후 첫 배포 점검 — 장중 잠정치 기록 수정

- 저장소 공개(사용자 지시) 뒤 GitHub Actions가 다시 돌아 EINT-040~045가 처음 배포됐다. 첫 배포에서 두 가지를 고쳤다.
  1. CI `recommendation-only (3.12)` 실패: EINT-044가 `tests/conftest.py`에 넣은 `import pytest`를 DB 안전 검사(pytest 없이 conftest를 연다)가 찾지 못함 → `pytest_asyncio.fixture`로 대체(001a05b).
  2. 서버가 장중(11:1x KST)에 시작되며 선물·수급 수집이 돌아, 한투가 주는 **오늘 장중 잠정치**로 10/16 행을 저장하고 급락 사건(B, 5일 −14.11%)을 기록했다. 사건은 "첫 기록 고정"이라 장중 값이 남을 뻔했다.
- 수정: 15:45 전 수집에서는 오늘 행을 저장하지 않는다. 사건은 확정(마감 뒤) 값만 기록하고 `final: true`를 붙인다. 확정 표시 없이 사건일 장중에 기록된 문서는 마감 뒤 첫 수집에서 확정 값으로 한 번 고치고, 마감 값으로 조건에 맞지 않으면 지우지 않고 `withdrawn_intraday`로 표시(화면·API에서 제외). 사전등록 판정(`observe.py`)은 한투 자료를 다시 받아 계산하므로 영향 없음.
- 테스트: `tests/test_futures_flows.py`에 장중 미저장·잠정 사건 수정·취소 표시·확정 뒤 고정 1건 추가.

## EINT-047 — 2026-10-16: 한국 섹터 순환(RRG) 기준을 KODEX 200 동일가중으로, 대형주 쏠림 안내·실제 등락·시총가중 참고 보기

- 계기: 사용자 "일주일 폭락이 왔는데 부진이 하나도 없다". 점검 결과 계산 오류는 없었다. 10/8→10/15 KODEX 200 −17.6%를 삼성전자 −19.8%·SK하이닉스 −24.9%가 주도했고 대부분 섹터가 덜 빠져 KODEX 200 대비로는 21개 중 주도 14·개선 7·부진 0이었다.
- 진단(2025-10~2026-10 주간): 시총가중 KODEX 200 기준은 대형주 강세장에서 섹터-주의 42%가 부진, 이번 폭락에서는 전부 주도로 쏠렸다(부진 0개인 주 4회, 동일가중 기준 0회). 두 기준 분면 일치율 58%. 섹터 1주 상대수익의 부호가 다음 주에 뒤집히는 비율 평균 45%.
- 변경(사용자 승인 "수정안대로 진행해"): ① 기본 기준을 KODEX 200 동일가중(252650)으로 — "보통 종목 대비 어느 섹터가 강한가". ② KODEX 200 기준 분면은 접어 둔 참고 보기로만. ③ 기준 대비 1주 값은 넣지 않고 실제 1주·4주 등락률만 작게(1주 값은 흐름 판단에 쓰지 말라는 안내). ④ 시총가중과 동일가중의 4주 수익률 차이가 5%p 이상이면 "대형주 쏠림 주의" 안내(폭락·폭등 모두). 미국 RRG(SPY 기준)는 이번에 바꾸지 않음.
- 구현: 엔진 `kqs_collection/sector_rotation.py`(엔진 개정 EINT-047), 웹 `sector_rotation.py`(새 항목 전달, 이전 형식 파일 호환), 화면 `sectorRotation.js`(안내·참고 보기·실제 등락 열, 한국 자료에만 표시). 10/13 실제 자료: 동일가중 기준 주도 4·개선 5·약화 5·부진 7, KODEX 200 참고 주도 11·개선 8·약화 2·부진 0, 쏠림 차이 −7.3%p(안내 표시).
- 반영 시점: 서버 화면은 엔진의 다음 일일 실행에서 새 형식 파일이 생긴 뒤 바뀐다(그 전엔 이전 화면 그대로).

## EINT-048 — 2026-10-16: 선물·수급 화면 모바일 표 수정

- 사용자: "선물 PC는 괜찮은데 모바일은 화면이 다 안 보여". 실제 사이트를 iPhone 13 크기로 확인하니 급락 관측 표·일별 표의 폭이 608px로 화면(316px)의 두 배라 옆으로 밀어야만 외국인 현물 등이 보였고, 일별 표의 2단 머리글이 모바일에서 숨긴 열과 어긋났다. (첫 출시 때 캡처는 화면 맨 위만 찍혀 놓쳤다.)
- 수정: 일별 표는 한 줄 머리글로 바꾸고 모바일에서는 날짜(월-일)·코스피(등락)·외국인 선물(계약)·외국인 현물(억)만, 급락 관측 표는 날짜·구분·당일·20일 안 +5%·20일 뒤만 보이게 했다(PC는 전체 열). 360px 이하 화면은 글자·여백을 더 줄였다.
- 확인: 실제 사이트 자료로 iPhone 13(390px)·Galaxy S9+(320px)에서 두 표 모두 화면 폭 안(표 폭 = 보이는 폭), 렌더 테스트 통과.
- 후속(같은 날, 사용자 "계약 건수로는 현물과 비교가 어렵다, 금액으로"): 모바일 일별 표의 외국인 선물을 계약 대신 억 원으로 바꿔 외국인 현물(억 원)과 같은 단위로 나란히 보이게 했다. PC는 계약·억 원 두 열 모두 유지.

## EINT-049 — 2026-10-16: 금일 소부장 — 대표 20종목(Yahoo) → 소부장 전체(네이버 테마, 거래소 등락률)

- 사용자: "원익IPS 현재가는 맞는데 퍼센트가 안 맞아, 대표 20종목만 보지 말고 소부장 전체로". 원인: 예전 화면은 대표 20종목 표본을 Yahoo `fast_info`로 계산했는데, Yahoo의 전일 종가가 코스닥 종목에서 이틀 전 값으로 와서 등락률이 틀렸다(10/16 원익IPS: Yahoo 이전 종가 100,600 → +6.16%, 거래소 102,700 → +3.41%).
- 변경: `app/backend/sobujang.py` — 네이버 증권 테마 '반도체 장비'(97)·'반도체 재료/부품'(71)·'반도체 기판'(14) 합집합 약 173종목(삼성전자·SK하이닉스 같은 완제품 업체 제외), 네이버가 주는 거래소 등락률·현재가·거래대금·시가총액 그대로. 여러 테마에 속한 종목은 한 줄로 합치고 테마를 모두 표시. 30초 캐시, 실패 시 10분 이내 마지막 값을 '지연'으로, 그 밖에는 503(값을 만들지 않음). `/api/market/top-sobujang?limit=0`이면 전체.
- 화면: 기본 상위 20개 + "전체 N종목 보기" 버튼, 상승·하락·보합 수와 등락률 중간값(테마별) 요약. 공용 순위 컴포넌트의 종목명·티커를 escape(외부 데이터). 예전 Yahoo 표본 코드(`SOBUJANG_COMPONENTS`, `_market_top_ranked_components`, 60초 순위 캐시)는 삭제.
- 한계: 테마 분류는 네이버 기준(공식 업종 아님), 네이버 공개 API가 아님(금일 상승종목과 같은 출처).
- 검증: `tests/test_sobujang.py` 3개(병합·거래소 등락률·정렬·요약, 상위/전체·캐시·지연, API 200/503), `scripts/tests/sobujang_render.mjs`, 실제 자료로 PC·모바일(넘침 0)·전체 보기 20→173행 확인.

## CODX-KRLC-001 — 2026-10-16: 대장주 가치주 기존 백테스트 누락 보완·저평가주 비교 (Codex 수정)

<!-- Codex 수정: CODX-KRLC-001 (2026-10-16), 이 절 전체. -->

- 요청: 대장주만 백테스트하고 완료된 저평가 자료를 찾아 비교. 기존 `research/external_picks_kr/out/monthly_performance.csv`·`stock_month_returns.csv`와 조사 중 발견한 `compare_*` 산출물을 재사용했다. 새 저평가 백테스트·전략 스윕·holdout·운영 파이프라인은 실행하지 않았다. 관련 근거 검색은 무결성 정상·직접 일치 0건. 기존 KR-VQM-v1 기각 및 KR-LV-v2 전향 잠금은 보존한다.
- 범위: `research/kr_leaders_value_comparison/`에 독립 PDF 명단 대조, 개별 수익 재집계, 산술 회귀 테스트, 방법·보고서·결과 지문. 기존 사용자 파일·원본 PDF·기존 산출물·엔진·DCA·AWS는 수정하지 않았다. 엔진 개정 불필요(엔진 코드 변경 없음).
- 결함: 기존 대장주 2026-02가 더존비즈온(012510) 누락으로 19종목 평균이었다. 기존 KRX 스냅샷 읽기 전용 조회에서 2026-02-02 시가 94,500원→02-27 종가 118,400원(+25.2910%)으로 보완. 월중 17세션 전부 존재, 상장주식 수 일정. 이 1건은 현금배당 조정 미확인 가격수익률이며 Yahoo 조정수익과 출처 차이를 공시했다. 대장주 480/480·저평가 360/360건 완전성 및 기존 저평가 월별 평균 일치 검증.
- 결과: 2024-10~2026-09 완결 24개월, 월초 시가→월말 종가·월별 동일 비중·비용 세금 제외. 대장주 누적 +129.43%(보완 전 +128.73%), 저평가 기존 +97.22%, KODEX200 +249.01%, 200동일가중 +56.99%. 원금 포함 월말 MDD -8.60% / -15.29%. 대장주가 저평가를 이긴 달 13/24, 월차이 t≈0.70(설명 통계).
- 한계: PDF 실제 발표시각·시점정합 미확인, 월 경계 야간 수익 제외, 월말 MDD는 일별 MDD 아님. 2026-10 수집시각 차이로 제외. 소급 탐색 비교이며 운영 채택·기존 잠금 변경에 쓰지 않는다.
- 검증: `PYTHONPATH=/tmp/codex_pdf_lib python3 research/kr_leaders_value_comparison/audit.py` 전수 대조 통과; `python3 -m unittest discover -s research/kr_leaders_value_comparison -p 'test_*.py' -v` 3개 통과; `python3 scripts/check_engine_evidence.py --topic "외부 추천 한국 가치주 월별 백테스트"` 통과. 코드·방법·설정·입력 파일/조회행 지문, 비용 가정·기준일·Python 버전은 RESULT.json에 분리 기록.
- 인계: [비교 보고서](../../research/kr_leaders_value_comparison/REPORT.md). 보완 배당 처리와 PDF 공개시각 확인은 미해결이며 새 전향 연구와 별도다. 원본 시장 DB·유료 PDF는 새 Git 산출물에 복사하지 않았다.

## EINT-050 — 2026-10-16: KR-PRO-CONSENSUS-v1 사전등록·잠금 — InvestingPro 두 한국 명단 공통 종목 전향 검증

- 사용자: "기준은 매달 1일 리밸런싱, 11월에 두 명단 올려줄게, 커밋 다 하고 잠글 거 잠그고 기반 공사까지, 그전에 두 가설과 추출 가능한 조건 재점검".
  방향은 Codex `docs/handoff/codex/2026-10-16-kr-value-compare.md`. Claude Code 의견(대장주 2개월 연속)은 사후 선택 위험 때문에 보조 기록으로만.
- 재점검: 두 명단은 시총 ≥ 약 0.7조원·일평균 거래대금 ≥ $10만에서 출발. V(저평가)는 흑자·PER ≤ 약 40배·ROE>0·FCF>0 필터, L(대장주)은 적자 13%·금융 22% 포함.
  Codex가 지적한 "L 7월 지표 = V 5월 지표"는 분기 재무가 같은 정상 현상으로 확인(같은 달 공통 133건 재무값 전부 일치, 다른 달 일치는 ±1~3개월).
  우리가 직접 계산 가능한 조건: 시총·거래대금·PER·P/S·ROE·영업이익률·52주 고점 대비·FCF 수익률·현금비율(마지막 둘은 DART 현금흐름·재무상태표 필요). 재현 불가: InvestingPro 적정가치·목표가·라벨.
- 규칙(잠금): S = V∩L 동일 비중, 매월 첫 KRX 거래일 시가 진입(두 명단 모두 그날 09:00 KST 전 서버 수신, 늦으면 다음 거래일), 비용 매수 6.5bp·매도 24.5bp, 비교 V·L·KODEX200·200동일가중, 보유 2026-11~2028-10. 판정: 비용 후 S−V·S−L 단측 Newey–West(3) t·Holm 5% + S 누적 ≥ KODEX200 + 일별 MDD 비악화 → PASS_REVIEW_ONLY, 결측 월은 INVALID. 연속 편입은 기록만.
- 구현: `research/external_picks_kr/forward.py`(freeze·evaluate·judge, KRX 일봉 분할 보정, 일별 평가), `test_forward.py` 5개, `OPERATIONS.md`, `LOCK.json`. 명단 원문·확정 스냅샷은 비공개(`private/`, Git 제외), 확정 해시만 `ledger_hashes.json`으로 진입 전 커밋. 명단 입력은 기존 사이트 '외부 추천 기록'(서버 수신 시각 기록)을 사용 — Codex 문서의 새 API·화면은 이번에 만들지 않음.
- 관측 등록부: `KR-PRO-CONSENSUS-v1` 1건 추가(CODX-OBS-001 범위, 사용자 승인 "잠글 거 잠그고"). 기존 항목 불변. 첫 점검 2026-11-10.

## EINT-051 — 2026-10-16: 기반 공사 — DART 정기공시 시점(PIT) 보관

- 사용자 "기반 공사까지 다 하자"(Codex 분석 의견: 공시 접수일·정정 이력·수집 시각을 보존해야 재무 모델을 정직하게 검증 가능). KR-VQM-v1은 DART 재무 API가 정정 뒤 최신 값만 줘서 '분기 말+45일' 법정 기한으로 공개 시점을 대신했다.
- `app/backend/dart_pit.py`: ① 색인 `dart_filings` — 공시목록 API(정기공시 A, 유가 Y·코스닥 K, 정정 포함 `last_reprt_at=N`), 상장사만, 접수번호를 키로 첫 기록 유지(보고서명→종류·기간·정정 표지·12월 결산 기준 재무 API 코드). 매 실행 최근 7일 + 과거 2015-01부터 6개월씩(약 12일에 완료). ② 스냅샷 `dart_pit_financials` — 원본·[기재정정] 정기공시를 **접수 후 7일 안에** 전체 재무제표(손익·재무상태·현금흐름, 연결 우선)로 받아 접수번호·수집 시각·접수 후 경과일·해시와 함께 저장. 7일이 지난 공시(과거 색인 포함)는 'historical'(접수일만), 첨부 공시는 'attachment', 비12월 결산은 'unmapped'. 실행당 DART 호출 1,500회 상한·0.15초 간격.
- 일정: 매일 19:30·07:30 KST(`collect_dart_pit`). 상태: `GET /api/market/dart-pit-status`(건수·진행·마지막 실행만, 공시 내용 없음).
- 의미: 앞으로 정기공시마다 "그때 알 수 있었던 재무"가 쌓인다(전향). 과거는 접수일·정정 이력까지만 정확하고 숫자는 정정 후 값이다.
- 테스트: `tests/test_dart_pit.py` 3개(보고서명 해석, 색인·스냅샷·historical·첨부·비12월·첫 기록 유지·과거 색인 진행, 호출 한도·상태 API).

## EINT-052 — 2026-10-16: 외부 추천 기록 — PDF 명단 올리기

- 사용자 "외부추천에 PDF로 올릴수있게 변경해". InvestingPro 한국 명단은 PDF(한 파일에 여러 달, 페이지마다 'YYYY년 M월' 제목)로 내려받는다.
- `app/backend/external_picks.py` `pdf_sections()`: pypdf로 글자를 뽑아 'YYYY년 M월' 제목별로 나눈다(제목 없는 쪽은 앞 달에 붙임, 최신 달 먼저). 8MB·120쪽 이하, 암호·스캔 이미지·손상 PDF는 422로 안내. `POST /api/external-picks/parse-pdf`(로그인 필요, 본문 `application/pdf` 그대로라 multipart 의존성 없음)는 달별 글자만 돌려주고 **PDF를 저장하지 않는다**. 종목 인식은 기존 `/parse`, 저장 시 고른 달 글자의 SHA-256만 남는다.
- 화면 `externalPicks.js`: 파일 선택이 PDF·CSV를 받는다. PDF면 달 선택 상자(여러 달일 때), 고른 달 글자를 입력란에 넣고 명단 이름 'YYYY년 M월', 기록 날짜 = 그달 1일(미래면 오늘). 파일 이름에 '대장주'/'저평가'가 있으면 출처를 `InvestingPro 한국 대장주 가치`/`InvestingPro 한국 저평가 가치`로 맞춘다(KR-PRO-CONSENSUS-v1 출처 이름과 같음). 두 출처를 목록 앞에 추가.
- 확인: 실제 두 PDF(로컬, 비공개) 25개월 모두 월별 종목 수 20/15, 기존 인식기 결과가 PDF의 'A+6자리' 코드와 25개월 전부 일치. 의존성 `pypdf==6.19.0`(requirements·lock·backend 테스트).
- 테스트: `tests/test_external_picks.py` +3(실제 PDF 추출·잘못된 파일, 달 구역 분리, 로그인 전용 API), `scripts/tests/external_picks_render.mjs`(출처 추정·달 목록·기록 날짜). 캐시 `styles.css?v=58`, `app.js?v=46`.

## EINT-053 — 2026-10-19: 슈퍼트렌드 독립 차트(종목 분석 메뉴)

- 사용자 "PC 코덱스에서 슈퍼트렌드 관련 채팅 읽어와서 코드로 만들어". 근거: PC Codex 대화 'Pine Script 슈퍼트렌드 지표 작성'(일봉 ST ATR10×3, `[1]+lookahead_on` 확정 주봉, 확정 전환 BUY/SELL)과 Codex 설계 `docs/handoff/codex/2026-10-19-supertrend-chart-design.md`(CODX-ST-DESIGN-001, 읽기만 하고 수정하지 않음).
- 재사용 결정: 엔진·추천·청산·원장·주문·관측 등록부와 연결하지 않는 별도 화면. 시세는 기존 `market_data.get_daily_ohlcv`(검증 저장본, 수정주가) 그대로. 새 전략·백테스트·성과 주장 없음.
- `app/backend/supertrend_chart.py`: TradingView `ta.supertrend` 재현(hl2, 첫 봉 TR=고가−저가, RMA ATR, nz 밴드 재귀, `na(atr[1])`이면 하락 시작, `prevST==prevUpper` 분기·엄격 부등호). 주봉은 일봉 원 OHLC를 ISO 주로 묶어 독립 계산하고 각 일봉엔 **직전 완료 주** 값만. 확정 = 거래소 정규장 마감(KR 15:30 KST, 미국 16:00 ET)+30분 이후. 전환은 확정 봉에서만, 주봉 필터 없이 BUY·SELL 모두 보존(주봉 일치 여부는 별도 필드). ADX14(Wilder), 거래량 배율(직전 20일 평균, 당일 제외, 결측·0평균·미확정 봉은 None), 최근 20거래일 전환 수, 직전 20/60일 고저(당일 제외), 추세선 거리 %·ATR 단위, ATR%, 시가 갭(전일 ATR 기준), 전환 후 가격 변화, 장중 이탈(미확정 봉이 전일 확정 상승선 아래), SMA20/60/200, 비교 지수(KS→^KS11, KQ→^KQ11, 그 외 ^GSPC) 일봉 ST 방향.
- `app/backend/routers/charts.py`: `GET /api/chart/supertrend?ticker&atr(1–100)&factor(0.1–20)` 공개 읽기 API. 5년+30일 이력으로 계산해 전 봉 반환(표시 구간은 화면에서 자름 → 6개월/1년/3년 전환에도 값 불변). 시세 없으면 503+원인(가짜 캔들 없음). `main.py`에 라우터 등록 3줄.
- 화면 `app/frontend/js/views/supertrendChart.js`(메뉴: 종목 분석 › 슈퍼트렌드 차트, `supertrend-chart`): 상태 줄(확정 일봉·직전 확정 주봉과 기준 주·최근 전환·비교 지수·마지막 봉 확정 여부), 주봉×일봉 4조합 설명, 캔들+초록/빨강 ST선+미확정 점선+모든 전환 라벨(주봉 일치=채움, 반대=테두리), 확대·이동, 선택 MA·직전 20일 고저, 거래량·ADX(+DI/−DI 선택, 20·25 눈금) 패널, 날짜 선택 상태표(◀ ▶·최신), 전환 기록 30개, 가격 거리 계산(P·S·Q·T → (P−S)×Q, S<P<T일 때만 R배수; 저장 안 함, `data-local-state="off"`). 종목·설정·구간만 브라우저에 기억.
- 설계와 다르게/미구현: 금요일 마감 직후 '이번 주 마감값' 별도 표시 없음(직전 주 참조만). 휴장·조기마감 달력은 쓰지 않고 마감 시각+여유로만 확정 판정(조기마감일은 보수적으로 늦게 확정). 주 묶음은 ISO 주(월~일)로, 미국·한국 정규장 일봉만 가정. 알림·스캐너·성과표는 후속.
- 확인: 실제 005930.KS 5년 일봉(yfinance)으로 계산 실행 — 1220봉, 장중 당일 봉 미확정 처리, 최근 전환 2026-10-12 SELL(직전 주봉 하락과 일치). TradingView 화면과의 날짜 대조는 하지 않음.
- 테스트: `tests/test_supertrend_chart.py` 13개(Pine 원문 그대로 옮긴 대조 구현과 무작위 401봉 일치, 초기값, 전환 조건, 직전 완료 주 매핑·월/금 동일·휴장 주, 주봉 상승 중 SELL 보존, 이력 추가 불변, 미확정 봉 무전환·장중 이탈·거래량 배율 제외, 거래소 마감 판정, 거래량 결측, 고저·거리·ADX 준비 기간, API 200/503/422). `tests/test_site_scope.py`·`test_web_security.py` 통과. 캐시 `styles.css?v=59`, `app.js?v=47`.
- 2026-10-19 수정(배포 후 사용자 "차트 데이터없음 알람뜸"): ApexCharts가 `tooltip.shared`+기본 `intersect`를 막대 차트에서 예외로 던져 load()의 catch가 '차트 데이터 없음'으로 표시함. `intersect: false` 명시, 그리기 오류는 '차트 그리기 오류'로 분리(상태표·전환 기록 유지), 추세선 색 명시(회색으로 그려지던 문제), ADX 20·25 라벨 겹침 제거. 운영 API에 붙인 로컬 정적 서버 + 헤드리스 Chrome으로 렌더링 확인.

## EINT-054 — 2026-10-19: 추천 종목에 슈퍼트렌드 참고 칩(조건 아님)

- 사용자 "이걸 우리 추천종목에 추가사항으로 넣어줘 조건은 아니고 참조사항으로". Codex 설계(CODX-ST-DESIGN-001)는 독립 화면을 '엔진 추천에 연결하지 않는다'로 범위를 정했으나, 사용자가 표시 전용 참고로 명시 요청 → 추천 선정·순서·청산·원장·학습 데이터에는 여전히 연결하지 않고 카드에 칩만 표시.
- API `GET /api/chart/supertrend-reference?tickers=`(최대 30, 공개 읽기, ATR 10×3 고정, 10분 메모리 캐시, 동시 4): 종목별 마지막 **확정** 일봉의 방향·직전 확정 주봉·오늘/최근 전환·지속일·추세선 거리·20일 전환 수·ADX·장중 이탈. 실패는 `unavailable`, 이력 부족은 `insufficient`.
- 화면: `app/frontend/js/utils/supertrendRef.js`(새 파일) — 전략 추천(`strategyBoard.js` 펼친 카드)과 대시보드 추천 대기열(`dashboard.js`)의 기존 판단 근거 칩 줄 아래에 'ST 일봉 ▲상승 · 주봉 ▼하락 · 상승 전환 3일째 · 선까지 4.2% (10/16)' 칩. 툴팁에 기준일·세부값·"추천 규칙·순서에 반영되지 않습니다", 누르면 해당 종목·기본 설정으로 슈퍼트렌드 차트 이동. 기존 Codex 구역(pickContext.js 수급 칩 CODX-SL-001, strategyBoard·dashboard의 CODX-EXIT-001 경고 표시)은 수정하지 않음.
- 시점: 칩은 조회 시점의 최신 확정 일봉 기준(추천 기준일과 다를 수 있어 날짜를 함께 표시)이며 저장하지 않는다. 추천 시점 특징으로 기록·학습하지 않는다.
- 테스트: `tests/test_supertrend_chart.py` +2(확정 봉 기준 요약·이력 부족, 일괄 API·잘못된 티커 제외·실패 종목·캐시), `scripts/tests/supertrend_ref_render.mjs`(티커 변환·문구·escape·실패 표시). 기존 `strategy_board_render.mjs`·`efa_design_render.mjs`의 import 가짜 구현 줄에 새 함수 2개 추가(Codex 표시 테스트 블록은 미변경). 캐시 `styles.css?v=60`, `app.js?v=48`.

## EINT-055 — 2026-10-19: 대시보드 — 보유종목 슈퍼트렌드 추세 전환 알림(전환일에만)

- 사용자 "메인화면 오늘의 투자 브리핑 및 내 자산에 내 보유종목이 슈퍼트렌드 추세 전환하면 추세 전환하는 날만 알려줘".
- 정의: 보유종목(현금 제외, 최대 30)의 마지막 **확정** 일봉에서 ATR 10×3 슈퍼트렌드 방향이 바뀌었으면(`flip_today`) 그 종목만 알림. 확정 = 정규장 마감+30분(KR 15:30 KST, 미국 16:00 ET) 이후이므로, 전환일 장 마감 후부터 다음 거래일 종가 확정 전까지 보인다. 전환이 없는 날은 배너·배지가 나타나지 않는다. 기존 `GET /api/chart/supertrend-reference` 재사용(백엔드 변경 없음), 국내·미국 보유종목 모두.
- `dashboard.js`: 브리핑에 '추세 전환' 탭(전환한 종목만, 전환일에만 탭 숫자), 내 자산 트리맵 위 '오늘 슈퍼트렌드 전환' 알림 줄과 해당 타일 `ST▲/▼` 표시. 누르면 슈퍼트렌드 차트로 이동. 기존 Codex 구역(CODX-EXIT-001 매도 신호 탭·배지, CODX-USPORT-001 뉴스 표기)은 미변경. `supertrendRef.js`에 `holdingFlips`·`holdingFlipsHtml`·`holdingFlipsBannerHtml`·`bindSupertrendLinks`·`openSupertrendChart` 추가.
- 참고 정보이며 매매 지시·추천 규칙·원장·자동매매와 연결하지 않음. 알림은 화면 표시만(푸시·메일 없음), 저장하지 않음.
- 테스트: `scripts/tests/supertrend_ref_render.mjs` 확장(전환 종목만 추림, 현금·형식 오류 제외, 조회 실패 수, 전환 없는 날 배너 없음, escape). 대시보드 `assetsHtml`·`briefingHtml` 렌더는 임시 스크립트로 확인(배너·타일 표시, 무전환 시 배너 없음). 기존 `efa_design_render.mjs`·`strategy_board_render.mjs` 통과. 캐시 `styles.css?v=61`, `app.js?v=49`.

## EINT-056 — 2026-10-19: PC 대시보드 브리핑·내 자산 카드 크기 고정

- 사용자 "오늘의 투자 브리핑 크기에 따른 내 자산 크기도 같이 변해, 둘 다 PC는 보유종목 뉴스 기준으로 고정해". 내 자산 카드가 인사+브리핑 줄 높이에 맞춰 늘어나므로, 브리핑 탭(뉴스·매도 신호·추세 전환·추천 성과·엔진 상태)마다 내용 높이가 달라 두 카드가 함께 커졌다 줄었다.
- `styles.css` 끝에 1025px 이상 전용 규칙: 브리핑 본문(`[data-dash-brief]`)을 보유종목 뉴스 탭 최대 높이(목록 330px + 수집 안내)인 372px로 고정하고 안에서 스크롤, 안쪽 목록의 개별 max-height 해제, 뉴스 안내 문구가 다른 탭에서 숨겨져도 자리 유지. 모바일·태블릿(1024px 이하)은 기존 그대로. 기존 Codex CSS 구역(CODX-USPORT-001 뉴스 목록)은 수정하지 않고 뒤에서 덮어씀. 캐시 `styles.css?v=62`.
- 확인: 로컬 정적 서버(운영 API 연결) + 헤드리스 Chrome 1600px 스크린샷으로 브리핑 본문 고정 높이 확인(로그인 전 화면).
