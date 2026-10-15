# Codex → Claude Code 인계 (방향 결정 · 구현 분리)

사용자 지시(2026-10-16): **Codex가 방향을 정하고, Claude Code가 그 문서를 읽고 코드를 짠다.**

- 방향 문서 위치: `docs/handoff/codex/<YYYY-MM-DD>-<이름>.md`.
- 만드는 법 두 가지:
  1. Claude Code가 `scripts/codex_direction.sh <이름> "<요청>"`으로 Codex를 읽기 전용으로 불러 받는다.
     기본은 데스크톱 Codex 앱의 `codex.exe`(앱과 같은 계정·설정). Windows 샌드박스가 WSL 경로를 못 읽어서
     저장소를 `C:\Users\gram\.codex-mirror\IV-TEST`로 동기화한 사본에서 실행한다. `CODEX_ENGINE=wsl`이면 WSL CLI.
  2. 사용자가 Codex 앱에서 직접 방향을 정하면, Codex가 같은 형식으로 이 폴더에 문서를 쓴다.
- 형식: 목표 / 결정 사항 / 구현 범위 / 수용 기준 / 하지 말 것 / 미해결 질문.
- Claude Code는 구현 뒤 문서 끝에 `## 구현 기록`(커밋, 테스트 결과, 문서와 달라진 점과 이유)을 덧붙인다.
  방향 문서와 다르게 구현해야 하면 먼저 사용자에게 알린다.
- 협업 변경 잠금(COLLAB-001)은 그대로다: Codex 표시 구역은 사용자 승인 뒤에만 수정.
