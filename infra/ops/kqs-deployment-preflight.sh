#!/usr/bin/env bash
# Read-only preflight for an EXISTING deployment. stdout is the validated Compose project.
# Never starts containers, creates volumes, publishes recommendations or changes data.
set -euo pipefail

deploy_root="${1:-/opt/investing-app}"
state_dir="${KQS_STATE_DIR:-/opt/investing-app/state/kqs}"
fail() { echo "KQS deployment preflight: $*" >&2; exit 1; }

[[ "$deploy_root" == /opt/investing-app ]] || fail "unexpected deployment root; review migration separately"
[[ "$state_dir" == /opt/investing-app/state/kqs ]] || fail "unexpected KQS state directory"
[[ -r "$deploy_root/app/backend/.env" ]] || fail "existing backend environment file is missing"
[[ -s "$state_dir/recommendations.sqlite3" ]] || fail "existing recommendation database is missing"
[[ ! -L "$state_dir" ]] || fail "state directory must not be a symlink"

inspect() { docker inspect --format "$1" "$2"; }
project="$(inspect '{{index .Config.Labels "com.docker.compose.project"}}' investment_backend)" ||
  fail "existing backend container cannot be inspected"
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail "existing Compose project label is missing or invalid"

mounted_state="$(inspect '{{range .Mounts}}{{if eq .Destination "/app/state/kqs"}}{{.Source}}{{end}}{{end}}' investment_backend)"
mounted_rw="$(inspect '{{range .Mounts}}{{if eq .Destination "/app/state/kqs"}}{{.RW}}{{end}}{{end}}' investment_backend)"
[[ "$mounted_state" == "$state_dir" && "$mounted_rw" == false ]] ||
  fail "backend does not mount the expected KQS state read-only"

for spec in investment_mongo:investment_mongo_data investment_qdrant:investment_qdrant_data investment_meilisearch:investment_meilisearch_data; do
  container="${spec%%:*}"
  suffix="${spec#*:}"
  case "$container" in
    investment_mongo) destination=/data/db ;;
    investment_qdrant) destination=/qdrant/storage ;;
    investment_meilisearch) destination=/meili_data ;;
  esac
  container_project="$(inspect '{{index .Config.Labels "com.docker.compose.project"}}' "$container")"
  [[ "$container_project" == "$project" ]] || fail "$container belongs to a different Compose project"
  actual_volume="$(inspect "{{range .Mounts}}{{if eq .Destination \"$destination\"}}{{.Name}}{{end}}{{end}}" "$container")"
  [[ "$actual_volume" == "${project}_${suffix}" ]] ||
    fail "$container uses a nonstandard volume; configure and approve migration separately"
done
printf '%s\n' "$project"
