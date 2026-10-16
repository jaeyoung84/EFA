# Codex 변경 기록

<!-- Codex 수정: COLLAB-001 (2026-10-06), 파일 전체. -->

Codex가 수정한 파일·구역을 Claude Code와 구분하기 위한 기록이다. 후속 작업마다
변경 ID, 날짜, 정확한 파일·범위, 목적을 추가한다. 표식이나 이 기록에 등록된 범위는
Claude Code가 수정하기 전에 사용자의 명시적 승인이 필요하다. 승인 후에도 이전
기록은 보존한다. 표기가 없는 과거 코드의 작성자를 추정하지 않는다.

| 변경 ID | 날짜 | Codex 수정 범위 | 목적 |
| --- | --- | --- | --- |
| COLLAB-001 | 2026-10-06 | AGENTS.md의 “Codex·Claude Code 협업 변경 잠금” 절; CLAUDE.md 전체; docs/CODEX_CHANGES.md 전체; docs/engine/CHANGES.md의 COLLAB-001 절 | Codex 변경 표기와 Claude Code의 해당 범위 수정 전 사용자 승인 규칙 추가 |
| DART-001 | 2026-10-06 | app/backend/dart_market_activity.py의 7일 집계·당일 재수집·20시 재확인; app/frontend/js/views/dartMarketActivity.js의 7일 안내·조회 실패 시 화면 유지; tests/test_dart_market_activity.py의 보존·실패·재수집 회귀 테스트 | 최근 7 KST 날짜의 공시를 새 수집이 비거나 실패해도 유지. 로컬 jy_investing DB에 2026-09-30~10-06 보충 수집; 12개 테스트 통과. 운영 배포는 미실시 |
| PORT-001 | 2026-10-06 | docker-compose.desktop.yml의 backend 호스트 포트 매핑 제거; AGENTS.md의 “AWS 홈페이지와 로컬 Docker 경계” 절 | AWS 홈페이지를 로컬 테스트 환경과 혼동하지 않도록 로컬 포트 공개와 무단 로컬 Docker 작업 차단 |
| AUTH-001 | 2026-10-06 | AGENTS.md의 “AWS 홈페이지와 로컬 Docker 경계” 절 내 Chrome IAM·GitHub Actions OIDC 규칙 | 사용자가 쓰는 Chrome IAM 콘솔을 대화형 확인의 우선 경로로 지정하고 WSL 원격 로그인을 기본 경로에서 제외 |
| DART-002 | 2026-10-06 | .github/workflows/dart-refresh.yml 전체 | GitHub OIDC와 고정 SSM 명령으로 운영 백엔드의 당일 DART 수집만 수동 실행; 로컬 Docker·AWS CLI 로그인 불필요 |
| CODX-SL-001 | 2026-10-08 | app/backend/short_lending_reference.py 전체; app/backend/scheduler.py의 import·08:45/16:10 수집 작업; app/backend/main.py의 저장자료 API; app/frontend/js/utils/pickContext.js의 수급 참고 칩; scripts/tests/test_short_lending_reference.py 전체; docs/engine/CHANGES.md의 CODX-SL-001 절; 이 행 | 선정 추천의 공매도 거래·대차잔고를 시점별로 불변 누적하고 중립 참고자료로만 표시. 추천·매매 규칙 미변경, AWS 배포 미실시 |
| CODX-EXIT-001 | 2026-10-12 | app/backend/kqs_strategy_board.py의 exit_plan 문구·v4 basis·holding_signals.signal_basis; app/frontend/js/utils/exitPlan.js의 헤더; app/frontend/js/views/strategyBoard.js의 참고/경고 배지·건수·안내; app/frontend/js/views/dashboard.js의 경고 배지·건수; tests/test_kqs_strategy_board.py의 회귀 검증; scripts/tests/strategy_board_render.mjs의 경고 렌더 테스트; docs/engine/CHANGES.md의 CODX-EXIT-001 절; docs/engine/CODX_EXIT_001_HANDOFF.md 전체; 이 행 | 현행 규칙 불변 상태에서 종가 경고와 청산 관측을 분리해 표시하고 후속 전향 검증·Claude 인계 범위를 고정 |
| CODX-EXIT-002 | 2026-10-12 | app/backend/kqs_strategy_board.py의 v4_holding_exit 보유일 경계; tests/test_kqs_strategy_board.py의 첫 게시일 합성 회귀; docs/engine/CHANGES.md의 CODX-EXIT-002 절; docs/engine/CODX_EXIT_002_FORWARD_STATUS.md 전체; 이 행 | 첫 게시일의 허위 15일 청산을 방지하고 새 청산 성과는 독립 전향 표본 전까지 판단 보류임을 명시 |
| CODX-USPORT-001 | 2026-10-12 | app/backend/us_portfolio_updates.py 전체; app/backend/holdings_news.py의 미국 뉴스 캐시 병합; app/backend/routers/portfolio.py의 국가 구분·시장별 평가 합계·SEC API; app/backend/scheduler.py의 collect_us_portfolio_updates 작업; app/backend/.env.example의 SEC_USER_AGENT; app/frontend/js/views/portfolioHoldings.js의 한국·미국 합산 및 뉴스·공시 화면; app/frontend/js/views/dashboard.js의 시장별 뉴스 표기; app/frontend/styles.css의 holdings-update 스타일; tests/test_us_portfolio_updates.py 전체; scripts/tests/portfolio_us_render.mjs 전체; docs/engine/CHANGES.md의 CODX-USPORT-001 절; docs/engine/CODX_USPORT_001_HANDOFF.md 전체; 이 행 | 미국 주식을 국내 보유자산과 현재 환율 기준 원화로 합산하고, 미국 뉴스·SEC 제출자료를 캐시 기반 참고자료로 표시. 추천·주문·레버리지 DCA 미변경 |
| CODX-MA-001 | 2026-10-16 | research/rsi_obv_sma60_exploratory/PREREGISTRATION.md·run.py·test_run.py 전체; research/rsi_obv_sma60_exploratory/RESULT.json 실행 결과; docs/engine/CHANGES.md의 CODX-MA-001 절; 이 행 | 현재 RSI-OBV와 신호일 종가>SMA60 변형을 동일한 과거 자료·적격·비용으로 한 번 탐색 비교. 소진 과거창이므로 운영 채택·추천 변경 없음 |
| CODX-MA-002 | 2026-10-16 | docs/engine/PREREGISTRATION_RSI_OBV_SMA60_SLOPE_v1.md 전체; engine/kqs/src/kqs_runtime/rsi_obv_sma60_slope_shadow.py 전체; engine/kqs/src/kqs_ops/paths.py의 rsi_obv_sma60_slope_shadow 경로; engine/kqs/src/kqs_runtime/pipeline.py의 CODX-MA-002 import·그림자 관측 단계; engine/kqs/tests/test_rsi_obv_sma60_slope_shadow.py 전체; engine/kqs/tests/test_pipeline.py의 CODX-MA-002 검증 구역; docs/engine/CHANGES.md의 CODX-MA-002 절; 이 행 | 현행 추천은 바꾸지 않고 RSI-OBV 신호일 SMA60 상승 여부 하나만 2026-10-16 이후 독립 전향 표본으로 누적 |
| COLLAB-002 | 2026-10-16 | AGENTS.md의 “Codex·Claude Code 협업 변경 잠금” 절 내 엔진 개정 등록·인계 규칙; 이 행 | Codex가 엔진 코드를 변경할 때 후속 개정 manifest·검증 기록 등록과 인계 시 개정 ID 명시를 의무화 |
| CODX-DIR-001 | 2026-10-16 | docs/handoff/codex/2026-10-16-rsi-obv-next-hypothesis.md 전체; 이 행 | EINT-043 이후 세 번째 SMA 필터는 중단하고, 다음 단일 후보를 시장 전체 평균회귀 신호 밀도 전향 그림자로 한정. 엔진·추천·기존 전향 관측은 변경하지 않음 |
