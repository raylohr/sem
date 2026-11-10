#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
application_image=${1:?Application image required}
browser_image=${2:?Browser image required}
check_id="sem-crawl-check-$$"
network="$check_id-net"
database="$check_id-db"
redis="$check_id-redis"
proxy="$check_id-proxy"
browser="$check_id-browser"
worker="$check_id-worker"
controller="$check_id-controller"
cleanup() {
  docker rm -f "$controller" "$worker" "$browser" "$proxy" "$redis" "$database" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker network create --internal "$network" >/dev/null
docker run -d --rm --name "$database" --network "$network" --network-alias database \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,size=256m --memory=384m \
  -e POSTGRES_USER=sem_checks -e POSTGRES_DB=sem_checks -e POSTGRES_PASSWORD=isolated-checks-only postgres:16-alpine >/dev/null
docker run -d --rm --name "$redis" --network "$network" --network-alias redis --memory=128m redis:7.4-alpine >/dev/null
for attempt in {1..30}; do
  if docker exec "$database" pg_isready -h 127.0.0.1 -U sem_checks -d sem_checks >/dev/null 2>&1; then break; fi
  sleep 1
done
docker run -d --rm --name "$proxy" --network "$network" --network-alias proxy --memory=128m \
  "$application_image" python -m integrations.crawl_test_server >/dev/null
docker run -d --rm --name "$browser" --network "$network" --network-alias browser \
  --read-only --tmpfs /tmp:rw,nosuid,mode=1777,size=256m \
  --tmpfs /home/sem:rw,uid=1000,gid=1000,mode=700,size=64m --shm-size=256m --memory=2g \
  -e CRAWLER_RENDER_TOKEN=isolated-browser-smoke -e CRAWLER_EGRESS_PROXY=http://proxy:8765 \
  "$browser_image" >/dev/null
common=(-e DJANGO_SETTINGS_MODULE=config.test_settings -e DJANGO_SECRET_KEY=isolated-checks-only -e DB_PASSWORD=unused \
  -e SEM_CHECK_DB_PASSWORD=isolated-checks-only -e CELERY_BROKER_URL=redis://redis:6379/0 \
  -e CRAWLER_RENDER_URL=http://browser:8081/render/ -e CRAWLER_EGRESS_PROXY=http://proxy:8765 \
  -e CRAWLER_RENDER_TOKEN=isolated-browser-smoke)
docker run -d --rm --name "$worker" --network "$network" --memory=512m "${common[@]}" \
  "$application_image" python -c 'import os; from config.celery import app; app.conf.broker_url=os.environ["CELERY_BROKER_URL"]; app.worker_main(["worker","--loglevel=WARNING","--concurrency=2","--queues=crawler","--prefetch-multiplier=1"])' >/dev/null
if ! docker run --rm --name "$controller" --network "$network" --memory=512m "${common[@]}" \
    "$application_image" python -m integrations.crawl_queue_smoke; then
  docker logs --tail 60 "$worker"
  docker logs --tail 20 "$browser"
  exit 1
fi
