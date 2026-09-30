#!/bin/sh
# Upload the newest local backup directory to a private, encrypted S3 prefix if configured.
set -eu
[ -r /srv/kqs/secrets/ops.env ] && . /srv/kqs/secrets/ops.env
if [ -z "${KQS_BACKUP_S3_URI:-}" ]; then
  echo "KQS_BACKUP_S3_URI is not set; keeping local backups only."
  exit 0
fi
latest=$(find /srv/kqs/backups -mindepth 1 -maxdepth 1 -type d -name '????????T??????Z' | sort | tail -n 1)
if [ -z "$latest" ] || [ ! -f "$latest/backup-manifest.json" ]; then
  echo "No completed local backup to upload." >&2
  exit 1
fi
name=$(basename "$latest")
aws s3 cp --recursive --sse AES256 --only-show-errors "$latest" "${KQS_BACKUP_S3_URI%/}/$name/"
echo "Uploaded backup $name."
