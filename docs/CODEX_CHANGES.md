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
