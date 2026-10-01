# JY investing — 개인 투자 판단 시스템

KQS 엔진의 추천과 근거를 보고, 매수·매도 판단을 직접 기록하는 개인 투자 홈페이지입니다.
기록한 판단과 이후 성과는 나중에 AI 트레이닝 매니저의 학습 데이터가 됩니다.
**자동 주문·자동 모의체결·실거래는 하지 않습니다.** (레버리지 DCA의 Alpaca Paper 모의 계좌만 예외이며, 사용자 승인 없이 바꿀 수 없습니다.)

## 화면 구성

| 메뉴 | 화면 |
| --- | --- |
| 대시보드 | KQS 추천 요약(기준일·만료), 판단하지 않은 추천, 추천이 만료된 보유분, 시장 흐름 |
| 추천·판단 | KQS 추천·매매 기록(판단, FIFO lot, 매도·부분매도, 학습 데이터셋), 실제 가격 백테스트, 레버리지 DCA(모의) |
| 내 자산 | 내 보유자산, 보유자산 리스크, 자산 조합(상관관계) |
| 시장·종목 | 시장 현황, 자산군 차트, 오늘의 상승 종목, 소부장 종목, 거시경제, 기업 재무, DART 기업 검색·재무 분석, 산업 비교 |
| 운영 | 시스템 상태 |

시세를 가져오지 못하면 가짜(난수) 데이터를 만들지 않고, **데이터 없음 · 원인 · 조회 시각**을 표시합니다.

## 기술 스택

- **백엔드**: FastAPI + Uvicorn, MongoDB(motor/pymongo), numpy·pandas·matplotlib, yfinance, pandas_market_calendars, APScheduler
- **KQS 엔진**: `engine/kqs` 패키지. 별도 worker가 추천 스냅샷을 게시하고 웹은 읽기 전용으로 검증해 표시합니다 ([docs/engine](docs/engine/README.md)).
- **프론트엔드**: 번들러 없는 순수 JavaScript(ES 모듈) SPA, ApexCharts(CDN), Font Awesome
- **외부 연동(선택)**: OpenDART(`DART_API_KEY`), Alpaca Paper Trading(레버리지 DCA 전용)

## 로컬 실행 (Docker)

```bash
docker compose up --build -d     # 앱 + MongoDB
docker compose logs -f backend   # 로그
docker compose down              # 종료 (데이터 유지)
```

브라우저에서 <http://localhost:8000>을 열고, <http://localhost:8000/api/health>로 상태를 확인합니다.
포트를 바꾸려면 저장소 루트 `.env`에 `APP_PORT`, `MONGO_PORT`를 지정합니다.

## 로컬 실행 (Python)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install --no-deps ./engine/kqs
cp app/backend/.env.example app/backend/.env   # 필요한 값만 수정, Git에 올리지 않음
uvicorn app.backend.main:app --reload
```

## 테스트

```bash
PYTHONPATH=engine/kqs/src python -m pytest -q tests          # 백엔드 (테스트 전용 MongoDB 사용)
PYTHONPATH=engine/kqs/src python -m unittest discover -s scripts/tests
python3 scripts/check_kqs_release.py                          # KQS 배포 무결성
```

## 배포

`main`에 push하면 GitHub Actions가 KQS·레버리지·화면 범위 회귀 테스트를 통과한 커밋만
OIDC → S3 → SSM으로 EC2에 올리고, 서버에서 백엔드만 교체합니다(실패 시 이전 이미지로 롤백).
엔진 worker 이미지·캘린더 변경은 별도 수동 재빌드가 필요합니다. 자세한 내용은
[OPERATIONS](docs/engine/OPERATIONS.md)와 [infra/ops/deploy-backend-release.sh](infra/ops/deploy-backend-release.sh)를 참고하세요.

## 참고 문서

- 시장 데이터 수집·품질: [MARKET_DATA.md](docs/MARKET_DATA.md)
- 실제 일봉 백테스트 규칙: [BACKTEST_ENGINE.md](docs/BACKTEST_ENGINE.md)
- 리스크 정책: [RISK_POLICY.md](docs/RISK_POLICY.md)
- KQS 추천 계약·판단 데이터셋: [KQS_RECOMMENDATION_CONTRACT.md](docs/KQS_RECOMMENDATION_CONTRACT.md), [KQS_HUMAN_DECISION_DATASET.md](docs/KQS_HUMAN_DECISION_DATASET.md)
- 백업·복구: [BACKUP_RESTORE_RUNBOOK.md](docs/BACKUP_RESTORE_RUNBOOK.md)
