#!/usr/bin/env bash
# MongoDB 논리 백업. 아카이브에는 개인정보가 포함될 수 있으므로 backups/는 Git에서 제외된다.
set -euo pipefail

env_file="${1:-.env.desktop}"
backup_dir="${BACKUP_DIR:-backups}"

if [[ ! -f "$env_file" ]]; then
  echo "환경 파일을 찾을 수 없습니다: $env_file" >&2
  exit 2
fi
db_name="$(awk -F= '/^MONGODB_DB=/{print $2; exit}' "$env_file")"
if [[ ! "$db_name" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "MONGODB_DB 값이 없거나 형식이 올바르지 않습니다." >&2
  exit 2
fi

mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$backup_dir/${db_name}-${stamp}.archive.gz"
compose=(docker compose --env-file "$env_file")

"${compose[@]}" exec -T mongo mongodump --db "$db_name" --archive --gzip > "$archive"
sha256sum "$archive" > "${archive}.sha256"
echo "백업 완료: $archive"
echo "무결성 파일: ${archive}.sha256"
