#!/usr/bin/env bash
# Codex에게 '방향 문서'를 받는다(읽기 전용 — Codex는 코드를 바꾸지 않음). Claude Code가 이 문서를 읽고 구현한다.
# usage: scripts/codex_direction.sh <짧은-이름> "<요청 내용>"   (요청 대신 파일 경로를 주면 그 내용을 쓴다)
# 결과: docs/handoff/codex/<YYYY-MM-DD>-<짧은-이름>.md
# 엔진: 기본은 Windows 데스크톱 Codex 앱의 codex.exe(앱과 같은 계정·설정·기록). CODEX_ENGINE=wsl이면 WSL CLI.
# 데스크톱 Codex의 Windows 샌드박스는 \\wsl.localhost 경로에 접근하지 못하므로, 추적·미무시 파일을
# C:\Users\gram\.codex-mirror\IV-TEST로 동기화한 사본에서 읽기 전용으로 실행한다(원본은 건드릴 수 없음).
set -euo pipefail
export PATH="$HOME/.local/node/bin:$PATH"
slug="${1:?짧은 이름}"; request="${2:?요청 내용}"
[ -f "$request" ] && request="$(cat "$request")"
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/docs/handoff/codex/$(TZ=Asia/Seoul date +%F)-$slug.md"
[ -e "$out" ] && { echo "이미 있음: $out" >&2; exit 1; }
tmp="$(mktemp)"
prompt="당신은 이 저장소(EFA 투자 홈페이지·KQS 퀀트 엔진)의 방향 결정자입니다. 구현은 Claude Code가 합니다.
코드·파일을 수정하지 말고, 필요한 파일만 읽어 조사한 뒤 아래 형식의 한국어 방향 문서만 답하세요.
AGENTS.md, docs/CODEX_CHANGES.md, docs/engine/README.md의 규칙(사전등록·변경 잠금·배포 경계)을 지키는 방향이어야 합니다.

# <제목>
## 목표 — 사용자에게 무엇이 달라지는지 1~3문장
## 결정 사항 — 선택한 방식과 이유, 버린 대안
## 구현 범위 — 만들거나 바꿀 파일·함수(정확한 경로), Codex 표시 구역을 건드리는지 여부
## 수용 기준 — 통과해야 할 테스트·확인 항목(구체적으로)
## 하지 말 것 — 범위 밖, 금지 사항
## 미해결 질문 — 사용자가 정해야 할 것(없으면 '없음')

요청:
$request"
if [ "${CODEX_ENGINE:-desktop}" = "wsl" ]; then
  codex exec -s read-only -C "$root" -c model_reasoning_effort='"high"' -o "$tmp" "$prompt" > "$tmp.log" 2>&1 || { tail -20 "$tmp.log" >&2; exit 1; }
else
  exe="$(ls -t /mnt/c/Users/*/AppData/Local/OpenAI/Codex/bin/*/codex.exe 2>/dev/null | head -1)"
  [ -n "$exe" ] || { echo "데스크톱 Codex(codex.exe)를 찾지 못함 — CODEX_ENGINE=wsl로 다시 실행" >&2; exit 1; }
  user_home="${exe%%/AppData/*}"                          # /mnt/c/Users/<이름>
  mirror="$user_home/.codex-mirror/IV-TEST"; rm -rf "$mirror"; mkdir -p "$mirror"   # 지운 파일이 남지 않게 매번 새로
  (cd "$root" && git ls-files -co --exclude-standard -z | rsync -a --from0 --files-from=- ./ "$mirror/")
  wout="$user_home/AppData/Local/Temp/codex_direction_$$.md"
  (cd "$user_home" && "$exe" exec -s read-only --skip-git-repo-check -C "$(wslpath -w "$mirror")" -c model_reasoning_effort='"high"' \
      -o "$(wslpath -w "$wout")" "$prompt") > "$tmp.log" 2>&1 || { tail -20 "$tmp.log" >&2; exit 1; }
  mv "$wout" "$tmp"
fi
{ echo "<!-- Codex 방향 문서 · 요청 $(TZ=Asia/Seoul date '+%F %T KST') · ${CODEX_ENGINE:-desktop} Codex 읽기 전용 · 구현: Claude Code -->"
  echo "<!-- 요청: $(printf '%s' "$request" | tr '\n' ' ' | cut -c1-300) -->"; echo; cat "$tmp"; } > "$out"
rm -f "$tmp" "$tmp.log"
echo "$out"
