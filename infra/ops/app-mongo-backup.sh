#!/bin/sh
# 사이트 MongoDB(회원·판단 기록·포트폴리오·관심종목·노트) 일일 백업.
# 1) mongodump 압축 아카이브 + SHA-256  2) 같은 Mongo 안의 임시 DB에 복원해 컬렉션·문서 수 확인 후 임시 DB 삭제
# 3) 비공개·암호화 S3에 업로드(설정된 경우)  4) 로컬은 최근 N개만 보관.
# 운영 DB를 복원하거나 덮어쓰지 않는다. 임시 DB 이름은 restore_check_ 접두어만 쓰고 그것만 삭제한다.
set -eu

container="${APP_MONGO_CONTAINER:-investment_mongo}"
db="${APP_MONGO_DB:-investment_db}"
backup_root="${APP_BACKUP_DIR:-/srv/app-backups}"
keep="${APP_BACKUP_KEEP:-14}"
[ -r /srv/kqs/secrets/ops.env ] && . /srv/kqs/secrets/ops.env
s3_uri="${APP_BACKUP_S3_URI:-}"

case "$db" in ""|*[!A-Za-z0-9_-]*) echo "APP_MONGO_DB 형식이 올바르지 않습니다." >&2; exit 2 ;; esac
case "$keep" in ""|*[!0-9]*) echo "APP_BACKUP_KEEP는 숫자여야 합니다." >&2; exit 2 ;; esac

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dir="$backup_root/$stamp"
tmp_db="restore_check_$(date -u +%Y%m%d%H%M%S)"
umask 077
mkdir -p "$dir"
archive="$dir/$db.archive.gz"

counts() {
  docker exec "$container" mongosh --quiet "$1" --eval \
    'db.getCollectionNames().sort().forEach(c => print(c + " " + db.getCollection(c).countDocuments()))'
}

drop_tmp() {
  case "$tmp_db" in restore_check_*) docker exec "$container" mongosh --quiet "$tmp_db" --eval 'db.dropDatabase()' >/dev/null 2>&1 || true ;; esac
}
trap drop_tmp EXIT

# 1) 백업
counts "$db" > "$dir/source-counts.txt"
docker exec "$container" mongodump --quiet --db "$db" --archive --gzip > "$archive"
[ -s "$archive" ] || { echo "백업 아카이브가 비어 있습니다." >&2; exit 1; }
(cd "$dir" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")

# 2) 복구 확인(임시 DB)
docker exec -i "$container" mongorestore --quiet --archive --gzip \
  --nsInclude "$db.*" --nsFrom "$db.*" --nsTo "$tmp_db.*" < "$archive"
counts "$tmp_db" > "$dir/restored-counts.txt"
drop_tmp
src_cols=$(wc -l < "$dir/source-counts.txt")
restored_cols=$(wc -l < "$dir/restored-counts.txt")
src_users=$(awk '$1=="users"{print $2}' "$dir/source-counts.txt")
restored_users=$(awk '$1=="users"{print $2}' "$dir/restored-counts.txt")
if [ "$src_cols" -eq 0 ] || [ "$src_cols" -ne "$restored_cols" ] || [ "${src_users:-x}" != "${restored_users:-y}" ]; then
  echo "복구 확인 실패: 컬렉션 $src_cols→$restored_cols, users ${src_users:-?}→${restored_users:-?}" >&2
  exit 1
fi
restored_docs=$(awk '{s+=$2} END {print s+0}' "$dir/restored-counts.txt")
cat > "$dir/backup-manifest.json" <<EOF
{"database": "$db", "created_at": "$stamp", "archive": "$(basename "$archive")",
 "sha256": "$(cut -d' ' -f1 "$archive.sha256")", "collections": $restored_cols, "documents": $restored_docs,
 "restore_check": "ok"}
EOF
echo "백업·복구 확인 완료: $stamp (컬렉션 $restored_cols, 문서 $restored_docs)"

# 3) 업로드
if [ -n "$s3_uri" ]; then
  aws s3 cp --recursive --sse AES256 --only-show-errors "$dir" "${s3_uri%/}/$stamp/"
  echo "업로드 완료: ${s3_uri%%://*}://…/$stamp/"
else
  echo "APP_BACKUP_S3_URI가 없어 로컬에만 보관합니다."
fi

# 4) 로컬 보관 개수 정리(이름이 시각 형식인 디렉터리만)
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '????????T??????Z' | sort | head -n "-$keep" | while read -r old; do
  rm -rf -- "$old"
done
