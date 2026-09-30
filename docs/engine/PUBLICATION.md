# EINT-003~006 — 추천 게시, 홈페이지 연결, KRX 세션 정책, 입력 품질 게이트, 단일 writer

개발 경로는 `/home/gram/IV-TEST`, 실행 패키지는 `engine/kqs`의 **0.6.0**이다. 일일 자동 실행·백업·모니터링은 [운영 절차](OPERATIONS.md)(EINT-007)를 따른다.
외부 WSL 브리지 없이 추천을 읽는 경로를 구현했다. 실제 시장 자료 준비·게시 작업·AWS
배포는 아직 실행하지 않았다. 기존 운영 홈페이지가 이미 이 코드로 바뀌었다는 뜻은 아니다.

## 데이터 흐름과 경계

```text
시장 SQLite(mode=ro) → 입력 품질 검증(kqs-verify-market-inputs) → 품질 run SQLite(불변)
        │                                  ↓ status=ready 보고서
        └────────(mode=ro)────→ kqs-publish-worker(단일 writer 잠금, 같은 run 재현 확인)
                                     → 추천 게시 SQLite(추가만 가능) + worker 시도 로그(불변)
                                              ↓ mode=ro
                                GET /api/kqs/recommendations
                                              ↓
                               추천 이유·지표·시점·버전 표시
                                              ↓ 사용자가 명시적으로 제출
                        기존 사용자 수동 판단 원장 + 당시 근거 snapshot
```

홈페이지 GET은 엔진 계산·DB 생성·게시·만료 연장·Mongo 접근을 하지 않는다.
주문·자동 모의체결·개인 포트폴리오 자동 변경은 없다. POST 수동 판단에 입력한 가격/수량은
사용자의 기록이지 거래소에서 확인한 체결이 아니다. 엔진 auto 원장과 합산하지 않는다.

추천은 최대3개, 추천0건도 정상이다. 실제 계산된 RSI·볼린저·OBV·거래대금·시장필터를
이유로 표시하며 선정 순위를 수익률 예측으로 해석하지 않는다. `exit_reference`는 RSI50
청산 참고일 뿐 자동 매도 신호/주문이 아니다. LLM으로 추천 이유를 만들어 내지 않는다.

## 설치와 명시적 게시

worker는 독립 환경의 `requirements.lock` 및 `./engine/kqs[worker]`를 사용한다.
홈페이지는 `pip install --no-deps ./engine/kqs`로 표준 라이브러리 게시 reader/validator만
사용한다. 홈페이지의 pandas 버전을 엔진 버전에 맞추려고 덮어쓰지 않는다.

`kqs-recommend`는 **미리보기 전용**이다. raw JSON만 stdout에 출력하고 `publication_ready=false`를
유지하며 게시 옵션이 없다. EINT-006부터 추천 게시는 **`kqs-publish-worker` 하나로만** 한다.
worker는 ① 게시 DB의 단일 writer 잠금(`<게시DB>.writer.lock`, 비차단 flock)을 잡고,
② 불변 품질 DB에 **기록된** ready 품질 run을 run_id로 읽어 같은 기준일인지 확인한 뒤,
③ 같은 시장 DB·coverage manifest·당시 관측 시각으로 품질 run을 다시 계산해 run_id가 같을 때만,
④ 추천을 계산해 게시하고, ⑤ 결과를 별도 불변 시도 로그에 남긴다. 잠금을 쥔 다른 worker가 있으면
기다리지 않고 `writer_busy`로 기록·종료한다. 같은 run을 다시 실행하면 `already_published`이며
최초 생성/만료 시각을 유지한다. 라이브러리의 `store.publish`도 같은 DB의 잠금을 보유해야만 쓴다.
다음은 형식 예시이며 날짜·시장 DB 경로·절대 만료시각을 실제로 확인하여 바꿔야 한다.
오래된 예시를 현재 추천으로 재게시하는 명령이 아니다.

```bash
# 1) 입력 완전성 검증과 불변 run 증거 기록(시장 DB는 mode=ro, 품질 DB는 별도 파일)
engine/kqs/.venv/bin/kqs-verify-market-inputs \
  --krx-db /absolute/path/to/krx_daily.sqlite3 \
  --coverage-manifest /absolute/path/to/coverage-2026-09-23.json \
  --as-of 2026-09-23 \
  --observed-at 2026-09-24T07:00:00+09:00 \
  --quality-db /home/gram/IV-TEST/state/kqs/market-input-quality.sqlite3
# 출력 JSON의 run_id와 status=ready를 확인한다.

# 2) 단일 writer가 기록된 ready run을 재현 확인한 뒤에만 게시
engine/kqs/.venv/bin/kqs-publish-worker \
  --krx-db /absolute/path/to/krx_daily.sqlite3 \
  --history-start 2024-09-23 \
  --as-of 2026-09-23 \
  --expected-as-of 2026-09-23 \
  --expires-at 2026-09-24T09:00:00+09:00 \
  --snapshot-db /home/gram/IV-TEST/state/kqs/recommendations.sqlite3 \
  --quality-db /home/gram/IV-TEST/state/kqs/market-input-quality.sqlite3 \
  --quality-run-id <1단계 출력의 run_id> \
  --coverage-manifest /absolute/path/to/coverage-2026-09-23.json \
  --attempts-db /home/gram/IV-TEST/state/kqs/publication-attempts.sqlite3
```

종료 코드: 0 = `published`/`already_published`, 2 = `blocked_quality`/`failed`/인자·경로 오류,
3 = `writer_busy`. 시장·품질·게시·시도 로그 DB는 모두 서로 다른 파일이어야 한다.

coverage manifest(`kqs-market-coverage/v1`)는 시장별 기대 종목 수와 승인된 벤치마크 ETF
(KOSPI `069500`, KOSDAQ `229200`)를 명시한다. 기대 종목 수는 수집 공급자와 별개로 확인한
값이어야 하며, 이 manifest가 틀리면 검증도 틀린다. 검증은 행 수·중복·양수/유한값·
벤치마크1행·세션 정책만 본다. 가격 정정/공급자 장애/액면분할 조정의 정확성은 증명하지 않는다.

가격 DB와 게시 DB는 반드시 별도다. 가격 DB와 같은 경로/실제 파일을 게시 대상으로 지정하거나
다른 테이블이 있는 DB에 쓰면 거부한다. 개인 제외 문맥을 전역 추천으로 게시하지 않는다.
새 게시 DB는0600으로 만들며 전체 감사 입력도 저장하므로 비공개·Git 제외 대상으로 취급한다.
컨테이너 reader의 UID/권한은 실제 배포 때 확인해야 한다. 광범위한 공개 권한으로 풀지 않는다.

## 저장·최신성 규칙

- `snapshot_id=kqs-<run_id>`이며 같은 run_id는 최초 생성/만료/본문을 유지한다.
  재실행으로 만료를 연장하지 않는다. 같은 ID에 다른 감사 내용을 쓰면 거부한다.
- SQLite transaction과 고유 키로 동시 게시를 직렬화하며 UPDATE/DELETE trigger로
  일반적인 행 변경을 거부한다. DB 관리자에 의한 trigger 제거까지 막는 보안 서명은 아니다.
- 최신 자료 기준일→생성시각→snapshot_id 순으로 조회한다. 최신 건이 만료·변조되면503이며
  더 오래된 유효 추천으로 자동 fallback하지 않는다. 오래된 기준일의 신규 게시도 거부한다.
- 예상 자료 기준일과 시간대가 포함된 절대 만료를 반드시 지정한다. native 게시에는 검토된
  2026 KRX 캘린더 SHA와 공표 기대 정책이 포함된다. 생성 시점에 공개 기대 시각이 지난
  **가장 최신 세션**만 허용하고, 만료는 생성 후24시간 및 다음 세션 공개 기대 시각 이전이다.
  9/24·25 추석 휴장/9/28 재개도 명시되어 있으며 미지원 연도는 fail-closed다.
  세부 기준과 한계는 [KRX 세션 정책](KRX_CALENDAR.md)을 따른다.
- 현재 자료/코드/설정 해시는 추적·손상 검출용이다. 원격 생산자 인증/HMAC 서명과 다르다.
  웹의 허용 코드 목록/서명 검증 및 여러 서버 간 배포는 별도 후속 설계다.

SQLite는 같은 호스트의 로컬 디스크를 전제로 한다. NFS 공유·다중 호스트 AWS·자동 수집
worker/scheduler까지 완료한 것으로 취급하지 않는다. 추천 저장소 백업도 별도로 필요하다.

## 홈페이지 설정과 점검

```dotenv
KQS_RECOMMENDATION_SOURCE=snapshot
KQS_SNAPSHOT_DB=/absolute/path/to/recommendations.sqlite3
```

로컬 기본 경로는 `state/kqs/recommendations.sqlite3`, Docker 기본 경로는
`/app/state/kqs/recommendations.sqlite3`다. compose는 게시 디렉터리를 `:ro`로 연결한다.
설정 파일만 준비했으며 이미지 재빌드/컨테이너 재시작/실제 추천 게시를 실행하지 않았다.
첫 게시 전에는 API가503을 반환하는 것이 정상이다. 기존 bridge 경로는 명시적으로
`KQS_RECOMMENDATION_SOURCE=bridge`를 선택한 경우에만 사용하며 자동 fallback하지 않는다.

```bash
python3 scripts/check_kqs_recommendation_contract.py --source snapshot \
  --snapshot-db /absolute/path/to/recommendations.sqlite3
python3 scripts/check_engine_evidence.py
python3 engine/kqs/tools/check_migration.py
python3 engine/kqs/tools/check_revision.py
```

점검 도구도 DB 생성/게시/만료 갱신을 하지 않는다. 실제 홈페이지 API에는 기존 사용자 인증이
필요하고 `Cache-Control: no-store`를 사용한다. 수동 판단에는 snapshot ID, 당시 signal/features,
engine, 추천 생성/만료가 같이 남는다. 사후 가격/성과를 추천 시점 특징에 섞지 않는다.

## 이번 단계의 미완료 항목

1. 실제 collector(다운로드/적재) 자체와 공급자 장애·정정 감지, coverage manifest 자동 산출,
   2027 이후 KRX 일정 검토/갱신, 정해진 시각의 자동 실행(scheduler)과 AWS 운영(EINT-007).
   입력 완전성 게이트(EINT-005)와 단일 writer worker·잠금·멱등·시도 로그(EINT-006)는 추가됨.
2. AWS PC 독립 실행, 권한/개인 백업/실제 배포 검증. 로컬에는 운영 env 파일도 없다.
3. 기존 후보 큐·상태·전향/포트폴리오 대조 화면은 여전히 외부 bridge에 의존한다.
   이번 통합은 **추천 조회와 그 추천에 대한 수동 판단 기록** 경로다.
4. 기존 수동 매도 입력도 현재 유효한 signal을 요구한다. 만료된 매수 추천에 연결된
   보유분의 나중 매도·부분 매도·lot 매칭은 별도 수동 원장 보완이 필요하다.
5. 기존 holdout 소진/학습 데이터 개방 잠금, 독립 학습 manifest/검증. 모델 학습 없음.
6. 원시 자료1,735개는 metadata-only다. 전체 비공개 백업/복구 확인 전 원본 삭제 금지.

근거는 [변경 이력](CHANGES.md), [검증 기록](verification/EINT-006.json),
[후속 버전 manifest](../../engine/kqs/revisions/EINT-006.json)에 보존한다.
EINT-005 이전 기록은 덮어쓰지 않고 그대로 유지한다.
