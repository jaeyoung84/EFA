#!/usr/bin/env bash
set -euo pipefail

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_path="${workspace_root}/.venv-lean"

python3 -m venv "${venv_path}"
"${venv_path}/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"${venv_path}/bin/python" -m pip install --disable-pip-version-check -r "${workspace_root}/requirements-lean.txt"
"${venv_path}/bin/lean" --version

printf '%s\n' "LEAN CLI 설치 완료: ${venv_path}/bin/lean"
printf '%s\n' "클라우드 로그인이나 live 명령은 실행하지 않았습니다."
