#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n "$(git status --porcelain)" ]]; then
  echo 'Commit wijzigingen eerst; productie gebruikt uitsluitend gepushte commits.' >&2
  exit 1
fi
git fetch origin main
revision=$(git rev-parse --verify "${1:-origin/main}^{commit}")
git merge-base --is-ancestor "$revision" origin/main
# Use the deployment files belonging to this release as well.
if [[ "$revision" != "$(git rev-parse HEAD)" ]]; then
  echo 'Checkout eerst de gewenste releasecommit en voer dit script opnieuw uit.' >&2
  exit 1
fi
export SEM_PROD_IMAGE="sem-app:prod-$revision"
export SEM_PROD_BROWSER_IMAGE="sem-browser:prod-$revision"
export SEM_PROD_GATEWAY_IMAGE="sem-gateway:prod-$revision"
build_dir=$(mktemp -d /tmp/sem-release.XXXXXX)
trap 'rm -rf "$build_dir"' EXIT
git archive "$revision" | tar -x -C "$build_dir"
docker build -t "$SEM_PROD_IMAGE" "$build_dir"
docker build -t "$SEM_PROD_GATEWAY_IMAGE" -f "$build_dir/ops/proxy/Dockerfile" "$build_dir/ops/proxy"
docker build -t "$SEM_PROD_BROWSER_IMAGE" -f "$build_dir/ops/Dockerfile.browser" "$build_dir"
./ops/test.sh "$SEM_PROD_IMAGE"
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,mode=1777,size=256m --tmpfs /home/sem:rw,uid=1000,gid=1000,mode=700,size=64m --shm-size=256m --memory=1g "$SEM_PROD_BROWSER_IMAGE" python -m integrations.crawl_smoke
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,mode=1777,size=256m --tmpfs /home/sem:rw,uid=1000,gid=1000,mode=700,size=64m --shm-size=256m --memory=1g "$SEM_PROD_BROWSER_IMAGE" python -m integrations.crawl_rpc_smoke
./ops/test-crawl.sh "$SEM_PROD_IMAGE" "$SEM_PROD_BROWSER_IMAGE"
compose=(docker compose -f compose.yaml)
# Stop writers before database migrations.
"${compose[@]}" stop web worker beat crawl-worker crawl-browser crawl-egress
"${compose[@]}" --profile tools run --rm migrate
"${compose[@]}" up -d --no-build web gateway
"${compose[@]}" up -d --no-build worker beat crawl-egress crawl-browser crawl-worker
"${compose[@]}" exec -T web python manage.py remove_stale_contenttypes --noinput
"${compose[@]}" exec -T web python manage.py check
curl --fail --silent --retry 15 --retry-connrefused --retry-delay 2 http://127.0.0.1:8002/ready/
printf '\nProductiecommit: %s\nImage: %s\n' "$revision" "$SEM_PROD_IMAGE"
