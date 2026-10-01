#!/usr/bin/env bash
# Server half of the GitHub Actions -> SSM backend deployment. Runs as root on the EC2 host.
#
# Builds the tested commit in its own release folder, swaps ONLY the backend container and
# restores the previous image if the new one is not healthy. It never touches the Mongo
# volume, app/backend/.env, KQS state, systemd timers or the engine worker image.
set -euo pipefail

sha="${1:?commit sha required}"
release_dir="${2:?release directory required}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid commit sha" >&2; exit 1; }
[[ "$release_dir" == /opt/investing-app-releases/"$sha" ]] || { echo "unexpected release directory" >&2; exit 1; }
deploy_root=/opt/investing-app
releases_root=/opt/investing-app-releases
image="investing-app:${sha:0:12}"

cd "$release_dir"
# Read-only identity check of the existing deployment (env file, KQS state, read-only mount, volumes).
project="$(bash infra/ops/kqs-deployment-preflight.sh "$deploy_root")"
echo "compose project: $project"

# The host has no buildx; drop BuildKit-only cache mounts, keep everything else identical.
sed 's/RUN --mount=type=cache,target=\/root\/.cache\/pip \\/RUN \\/' Dockerfile > Dockerfile.deploy
if ! docker build -t "$image" -f Dockerfile.deploy . > "$release_dir/build.log" 2>&1; then
  tail -40 "$release_dir/build.log" >&2
  echo "backend image build failed; running backend left untouched" >&2
  exit 1
fi
echo "built $image"

previous_image="$(docker inspect --format '{{.Config.Image}}' investment_backend)"
echo "previous image: $previous_image"
cp "$deploy_root/docker-compose.prod.yml" "$deploy_root/docker-compose.prod.yml.previous"
cp docker-compose.prod.yml "$deploy_root/docker-compose.prod.yml"
docker compose -f "$deploy_root/docker-compose.prod.yml" config --quiet

cd "$deploy_root"
export COMPOSE_PROJECT_NAME="$project"
BACKEND_IMAGE="$image" docker compose -f docker-compose.prod.yml up -d --no-deps backend

healthy=false
for _ in $(seq 1 24); do
  if curl -fsS http://127.0.0.1:8801/api/health >/dev/null 2>&1; then healthy=true; break; fi
  sleep 5
done
if [[ "$healthy" != true ]]; then
  echo "new backend is not healthy; restoring $previous_image" >&2
  docker logs --tail 40 investment_backend >&2 || true
  cp "$deploy_root/docker-compose.prod.yml.previous" "$deploy_root/docker-compose.prod.yml"
  BACKEND_IMAGE="$previous_image" docker compose -f docker-compose.prod.yml up -d --no-deps backend
  exit 1
fi
echo "health: $(curl -fsS http://127.0.0.1:8801/api/health)"
printf '%s\n' "$sha" > "$deploy_root/DEPLOYED_COMMIT"

# Housekeeping limited to this app: keep the running and previous backend images and the
# three newest release folders; remove only dangling layers left by the rebuild.
for old in $(docker images --format '{{.Repository}}:{{.Tag}}' investing-app); do
  [[ "$old" == "$image" || "$old" == "$previous_image" ]] || docker rmi "$old" >/dev/null 2>&1 || true
done
docker image prune -f >/dev/null
ls -1dt "$releases_root"/*/ 2>/dev/null | tail -n +4 | xargs -r rm -rf
echo "deployed $sha"
