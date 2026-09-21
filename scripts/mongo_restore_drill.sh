#!/usr/bin/env bash
# 운영 DB를 덮어쓰지 않는 복구 훈련. 복원 대상은 임시 DB이며 종료 시 자동 삭제한다.
set -euo pipefail

archive="${1:?사용법: scripts/mongo_restore_drill.sh backups/<archive>.gz [.env.desktop]}"
env_file="${2:-.env.desktop}"
if [[ ! -f "$archive" || ! -f "$env_file" ]]; then
  echo "백업 아카이브 또는 환경 파일을 찾을 수 없습니다." >&2
  exit 2
fi
db_name="$(awk -F= '/^MONGODB_DB=/{print $2; exit}' "$env_file")"
if [[ ! "$db_name" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "MONGODB_DB 값이 없거나 형식이 올바르지 않습니다." >&2
  exit 2
fi

drill_db="${db_name}_restore_drill_$(date -u +%Y%m%d%H%M%S)"
compose=(docker compose --env-file "$env_file")
cleanup() {
  "${compose[@]}" exec -T mongo mongosh --quiet --eval "db.getSiblingDB('${drill_db}').dropDatabase()" >/dev/null || true
}
trap cleanup EXIT

"${compose[@]}" exec -T mongo mongorestore --gzip --archive --nsFrom "${db_name}.*" --nsTo "${drill_db}.*" --drop < "$archive"
collection_count="$("${compose[@]}" exec -T mongo mongosh --quiet --eval "db.getSiblingDB('${drill_db}').getCollectionNames().length")"
if [[ ! "$collection_count" =~ ^[0-9]+$ || "$collection_count" -eq 0 ]]; then
  echo "복구 훈련 실패: 임시 DB에 복원된 컬렉션이 없습니다." >&2
  exit 1
fi
echo "복구 훈련 통과: 임시 DB ${drill_db}에 컬렉션 ${collection_count}개를 확인했습니다."
echo "임시 DB는 종료 시 자동 삭제됩니다."
