# EFA — Economic Freedom Automation

**https://efa-quant.com** · 개인 투자 판단·포트폴리오 관리 홈페이지

KQS 퀀트 엔진이 매일 장 마감 후 추천 종목과 근거를 게시하면, 사용자가 직접 매수·매도를 판단하고
기록합니다. 내 보유자산은 실시간 시세로 평가하고, 보유종목 뉴스·리스크·시장 데이터를 한 화면에서 봅니다.
기록한 판단과 이후 성과는 나중에 AI 트레이닝 매니저의 학습 데이터가 됩니다.

> **자동 주문·자동 체결·실거래는 하지 않습니다.** 추천은 판단 참고용이며 투자 결과의 책임은 이용자에게 있습니다.
> 예외는 레버리지 DCA의 Alpaca Paper(모의) 계좌뿐이며, 사용자 승인 없이 바꾸지 않습니다.

## 화면 구성

| 메뉴 | 내용 |
| --- | --- |
| **대시보드** | 오늘의 투자 브리핑(**내 보유종목 주요 뉴스**, 보유 비중 순), 내 자산 요약, KQS 의사결정 큐, 시장 흐름·지수 차트, 데이터·시스템 상태 |
| **종목 분석** | 기업 재무, DART 기업 검색, DART 재무 분석, 자사주·내부자 공시, 산업 비교 |
| **포트폴리오** | 내 보유자산(실시간 평가), 보유자산 리스크, 자산 조합(상관관계) |
| **KQS 전략** | 전략 추천(근거·리스크 배지·**청산 계획가**), 매매 기록·연구(판단, FIFO lot, 부분매도, 학습 데이터셋), 실제 가격 백테스트, 레버리지 DCA(모의) |
| **시장 리뷰** | 시장 현황, 섹터 순환(RRG), 자산군 차트, 오늘의 상승 종목, 소부장 종목, 거시경제 |
| **관심종목 · 리서치 노트** | 개인별 관심종목과 메모 |
| **시스템 상태** | 엔진 게시 상태, 데이터 수집·서버 자원 |

- **회원별 기록**: 네이버 로그인으로 사용자마다 보유자산·판단 기록·관심종목이 따로 저장됩니다.
- **알림**: 로그인하면 상단 종(🔔)에서 엔진 실행·추천 게시 이상과 다음 해 휴장일 캘린더 준비 필요를 알립니다.
- **데이터 정직성**: 시세를 못 가져오면 가짜 데이터를 만들지 않고 **데이터 없음 · 원인 · 조회 시각**을 표시합니다.

## KQS 추천 게시 일정

| 항목 | 규칙 |
| --- | --- |
| 게시 시도 | 매 거래일 **22:00**, 실패 시 다음 날 **06:00**, **08:30** 순서로 재시도. 한 번 게시되면 나머지는 건너뜀 |
| 유효 기간 | 다음 거래일 22:00까지(금요일 밤 게시분은 **월요일 22:00**까지), 최대 10일 |
| 휴장일 캘린더 | 해마다 **12월 15일** 자동 작업이 천문연 공휴일 API로 다음 해 KRX 캘린더를 준비해 PR을 엽니다. 사람이 공식 KRX 일정과 대조해 병합해야 반영됩니다 |

엔진은 웹과 분리된 worker(Docker, systemd 타이머)에서 실행되며, 웹은 게시된 스냅샷을 읽기 전용으로 검증해
표시합니다. 엔진 규칙·개정 기록(EINT)은 [docs/engine](docs/engine/README.md)을 참고하세요.

## 데이터 출처

| 데이터 | 출처 |
| --- | --- |
| 국내 주식 시세·보유종목 뉴스 | 한국투자증권 Open API(KIS, 조회 전용) |
| 뉴스 원문 링크 | 네이버 검색 API(API HUB) |
| 지수·환율·해외 자산 | Yahoo Finance |
| KQS 엔진 일봉 | KRX Open API |
| 공시·재무 | OpenDART |
| 거시경제 | FRED |
| 휴장일 | 한국천문연구원 특일 정보 + KRX 규칙 |

## 기술 스택

- **백엔드**: FastAPI + Uvicorn, MongoDB, APScheduler(시세 캐시, 보유종목 뉴스 10분 주기, DART 공시, 일일 시장 데이터)
- **프론트엔드**: 번들러 없는 순수 JavaScript(ES 모듈) SPA, ApexCharts, Font Awesome
- **KQS 엔진**: `engine/kqs` 패키지, 별도 worker 이미지
- **인프라**: AWS EC2 + nginx + Cloudflare, GitHub Actions(OIDC → S3 → SSM) 배포, MongoDB 일일 백업·EBS 스냅샷
- **보안**: CSP·Permissions-Policy 헤더, nginx 요청 제한, Cloudflare 앞단 보호, 의존성 버전 고정(`requirements.lock.txt`)

## 로컬 실행

```bash
docker compose up --build -d     # 앱 + MongoDB → http://localhost:8000
docker compose logs -f backend
docker compose down              # 종료(데이터 유지)
```

Python으로 직접 실행할 때:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -c requirements.lock.txt && pip install --no-deps ./engine/kqs
cp app/backend/.env.example app/backend/.env   # 필요한 키만 채움. Git에 올리지 않음
uvicorn app.backend.main:app --reload
```

키(KIS, 네이버, DART, KRX, 천문연 등)는 모두 선택 사항입니다. 없으면 해당 기능만 "데이터 없음"으로 표시됩니다.

## 테스트

```bash
PYTHONPATH=engine/kqs/src python -m pytest -q tests                       # 백엔드
PYTHONPATH=engine/kqs/src:engine/kqs/tests python -m unittest discover -s engine/kqs/tests   # 엔진
python -m unittest discover -s scripts/tests                              # 운영 스크립트
python engine/kqs/tools/check_revision.py                                 # 엔진 개정 무결성
```

## 배포

`main`에 push하면 GitHub Actions(**Build and Deploy**)가 회귀 테스트를 통과한 커밋만 서버에 올리고
백엔드 컨테이너를 교체합니다(실패 시 이전 이미지로 롤백). 엔진 worker 이미지와 캘린더 변경은 서버에서
별도로 다시 빌드합니다. 자세한 절차는 [OPERATIONS](docs/engine/OPERATIONS.md)를 참고하세요.

## 개발 협업

Codex와 Claude Code가 함께 개발합니다. 작업 경계는 [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md),
[docs/CODEX_CHANGES.md](docs/CODEX_CHANGES.md)를 따릅니다.

## 참고 문서

- 시장 데이터 수집·품질: [MARKET_DATA.md](docs/MARKET_DATA.md)
- 실제 일봉 백테스트: [BACKTEST_ENGINE.md](docs/BACKTEST_ENGINE.md)
- 리스크 정책: [RISK_POLICY.md](docs/RISK_POLICY.md)
- KQS 추천 계약·판단 데이터셋: [KQS_RECOMMENDATION_CONTRACT.md](docs/KQS_RECOMMENDATION_CONTRACT.md), [KQS_HUMAN_DECISION_DATASET.md](docs/KQS_HUMAN_DECISION_DATASET.md)
- KRX 휴장일 캘린더: [KRX_CALENDAR.md](docs/engine/KRX_CALENDAR.md)
- 백업·복구: [BACKUP_RESTORE_RUNBOOK.md](docs/BACKUP_RESTORE_RUNBOOK.md)
