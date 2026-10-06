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
