# KQS 추천 전용 실행 패키지

실제 개발 경로: `/home/gram/IV-TEST/engine/kqs` (현재 `Ubuntu` 배포판).
외부 `Ubuntu-26.04` 엔진은 삭제하지 않았지만, 이 패키지를 실행하는 데 다른 배포판이나
외부 브리지는 필요 없다. **시장 DB 파일은 별도로 준비해야 한다.**

현재 버전: **0.6.0 / EINT-007A**. 운영 절차는 [OPERATIONS](../../docs/engine/OPERATIONS.md). [KRX 세션 정책](../../docs/engine/KRX_CALENDAR.md)과 [게시 안내](../../docs/engine/PUBLICATION.md).

## 완료 범위와 안전 경계

- `src/korea_quant`: 기준선의 전략/eligibility/평균회귀 스캐너와 초기화 파일4개를
  원본 SHA와 바이트 동일하게 이전했다. 추천에는 swing/long_term만 사용한다.
- `src/kqs_runtime`: 입력 품질·기준일 검증, 승인된 운영 설정, 전역0~3 선정,
  실제 지표 기반 설명, 감사 사유, 코드/설정/자료/호출 문맥 지문.
- 시장 SQLite는 기존 파일을 `mode=ro` + `query_only`로 열고 단일 읽기 transaction을 사용한다.
  시장 DB 생성/마이그레이션/수집/원장 저장/네트워크 호출은 없다.
- `src/kqs_publication`: 표준 라이브러리만 쓰는 v1 계약·게시 저장·읽기 모듈.
  기본 CLI는 stdout만 출력하고 게시 옵션을 명시한 별도 worker만 추천 DB에 추가한다.
  동일 run_id의 최초 생성/만료 고정, 변조/최신 만료 시 실패, 홈페이지 reader는 `mode=ro`.
  검토된 2026 KRX 휴장일/공개 기대 시각을 코드·config SHA에 포함하며 미지원 연도는 실패한다.
- `daily_picks_cli`, `paper_trading`, 연구 평가·학습·전향 러너는 설치되지 않는다.
  개인 모의체결·기존 자동 원장과 합산/소급 연결하지 않는다.
- 홈페이지 추천 API·근거/버전 화면·수동 판단 근거 저장 연결을 구현했다.
  **실제 추천 게시·수집·AWS 배포는 미완료.** EINT-004는 KRX 휴장/최신 세션 정책만
  검증했고, collector 건강이나 실제 시세 수신을 증명하지 않는다. raw 출력의
  `publication_ready=false`를 단순 변경하지 않고 별도 검증된 게시 계약으로 변환한다.
- `src/kqs_collection`(EINT-005): 기존 시장 DB를 `mode=ro`로 한 번 읽어 기준일 행 수·중복·
  누락·비양수 값·KOSPI/KOSDAQ 벤치마크(069500/229200)를 명시적 coverage manifest와 대조하고,
  ready/rejected 결과를 시장·추천·개인 원장 DB와 분리된 불변 품질 run DB에 기록한다.
  pandas/runtime을 import하지 않는다. 게시 worker는 기록된 ready run이 현재 DB에서 그대로 재현될 때만
  추천을 게시한다. 이것은 다운로드/적재 collector가 아니며 공급자 정정·장애를 증명하지 않는다.

## 설치 및 실행

저장소 루트에서 **별도** 환경에 설치한다. 기존 전체 KQS 패키지와 같은 환경에 설치하지 않는다.
기존 환경이 있으면 지우거나 `--clear` 하지 않고 그대로 재사용하거나 다른 경로를 선택한다.

```bash
python3 -m venv engine/kqs/.venv
engine/kqs/.venv/bin/python -m pip install -r engine/kqs/requirements.lock
engine/kqs/.venv/bin/python -m pip install -e './engine/kqs[worker]'
engine/kqs/.venv/bin/kqs-recommend --help
```

기존 KRX DB를 현재 배포판에서 읽을 수 있는 **실제 경로**에 두고 날짜를 명시한다.
아래 경로/날짜는 입력 예시이며, DB를 자동 복사하거나 최신일로 갱신하지 않는다.

```bash
engine/kqs/.venv/bin/kqs-recommend \
  --krx-db /absolute/path/to/krx_daily.sqlite3 \
  --history-start 2024-09-23 \
  --as-of 2026-09-23
```

두 시장 및 ETF069500/229200이 모두 지정한 기준일에 있어야 한다. 필터용 ETF는200개 이상의
유효 관측, 개별 주식은 스캐너에서260개 관측이 필요하다. 기준일을 오늘로 추정하지 않으며
부분 수집·누락 ETF·NaN/무한대·미래/중복 자료는 오류로 종료한다. 추천0건은 정상 출력이다.
거래정지 OHLC0은 원본 KRX reader와 같은 직전 종가 보정을 적용하며 보정할 값이 없으면 거부한다.

`--exclude-symbol 005930`은 명시적인 제외 문맥이다. 기본값은 비어 있으며 사용자의 수동 보유분이나
기존 엔진 자동 원장을 몰래 읽지 않는다. 제외 여부에 따라 run_id가 달라진다.

## 동결 설정과 설명

운영 설정은 D-19의200일/bearish를 **명시적으로** 사용한다. 연구 dataclass의 OFF 기본값을
변경하지 않았다. 시총 proxy200/150, 20일 평균 거래대금50억원, 최소260관측,
급변50%/60관측 제외를 유지한다. D-24의 시장별 시총 순위→종목코드→swing 우선 순서와
중복 제거/전역3개 상한은 수익률 예측 랭킹이 아니다. 실제 지수 구성종목 명부가 아니다.

- swing: 종가≤볼린저 하단(20,2σ) AND RSI14≤30.
- long_term: RSI14가30을 상향 돌파 AND OBV가9일 평균 이상/상승(실제 규칙은 엄격한 `>`).
- 청산 참고는 현재 원문의 RSI50 규칙이다. 옛 RSI70/9개월 보유 설명을 재사용하지 않는다.
  자동 청산하지 않으며 승률/수익률을 보장하지 않는다.

run_id는 코드/설정/정확한 입력행/의존성/Python/제외 문맥으로 정해진다. candidate_id는 이를
종목/신호에 연결한다. ConfigFingerprint 하나로 코드/데이터 동일성을 주장하지 않는다.
입력 지문은 정렬된 날짜·심볼·문자열·binary64 값을 스트리밍해 JSON 반올림이나 전체 자료의
거대한 dict 생성을 피한다. DB 파일 전체의 백업/해시가 아니라 **사용한 보정 입력**의 지문이다.

`kqs-recommend`는 미리보기 전용이다. 게시는 단일 writer `kqs-publish-worker`로만 하며(EINT-006),
게시 DB 잠금 → 불변 품질 DB에 기록된 ready run 재현 확인 → 계산·게시 → 불변 시도 로그 순서다.
명령과 종료 코드는 [게시 안내](../../docs/engine/PUBLICATION.md)를 따른다.
EINT-004는 `data_as_of`를 검토된 최신 KRX 세션과 대조하고 다음 기대 공개 시각 전으로
만료를 제한한다. 기본 2026 정책의 공개 기대 시각은06:00 KST이며, 실제 수집 성공 증명이 아니다.
2027 등 미지원 연도는 명시적 새 캘린더 전까지 실패한다. GET으로 갱신하지 않는다.
과거 연구용 자동 체결 D-33/D-44 표본에 새 수동 추천 결과를 합산하지 않는다.

## 검증과 이후 변경

```bash
python3 scripts/check_engine_evidence.py
python3 engine/kqs/tools/check_migration.py
python3 engine/kqs/tools/check_revision.py
engine/kqs/.venv/bin/python -m unittest discover -s engine/kqs/tests -v
```

합성·임시 DB로 이관/순수 실행/설명/선정/입력/읽기전용/설치를 검증한다. 이전 holdout,
전향 러너와 기존 성과 스윕은 실행하지 않는다. 루트 backend pytest fixture도 사용하지 않는다.
이 테스트는 전략 성과나 현재 실데이터 수집 성공을 인증하지 않는다.

출처는 `migration_manifest.json`, 변경·검증 근거는 `../../docs/engine/CHANGES.md`.
`check_migration.py`는 **최초 이관 기준선** 검사다. 이후 엔진 개선은 금지가 아니다.
새 변경 ID/버전/근거/새 회귀 검증 및 별도 successor manifest를 먼저 만들고 CI 기준을 명시적으로
전환한다. 현재 CI는 EINT-007A successor의 코드/의존성/메타데이터도 검사한다.
과거 archive와 최초 manifest를 새 코드 해시로 조용히 덮어쓰면 안 된다.
raw DB/원장/학습 자료는 Git 밖에 두고 원본 폐기는 별도 비공개 백업·복구 검증 후 결정한다.
