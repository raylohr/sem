#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
image=${1:-sem-app:checks}
if [[ $# -gt 0 ]]; then shift; else docker build -t "$image" .; fi
check_id="sem-check-$$"
network="$check_id-net"
database="$check_id-db"
application="$check_id-app"
cleanup() {
  docker rm -f "$application" "$database" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker network create --internal "$network" >/dev/null
docker run -d --rm --name "$database" --network "$network" --network-alias database \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,size=256m --memory=384m \
  -e POSTGRES_USER=sem_checks -e POSTGRES_DB=sem_checks \
  -e POSTGRES_PASSWORD=isolated-checks-only postgres:16-alpine >/dev/null
ready=0
for attempt in {1..30}; do
  if docker exec "$database" pg_isready -h 127.0.0.1 -U sem_checks -d sem_checks >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo 'Temporary test database did not become ready.' >&2; exit 1; }
docker run --rm --name "$application" --network "$network" \
  --read-only --tmpfs /tmp:rw,nosuid,mode=1777,size=256m \
  --tmpfs /app/media:rw,uid=1000,gid=1000 \
  -e DJANGO_SECRET_KEY=isolated-checks-only -e DB_PASSWORD=unused-test-placeholder \
  -e SEM_CHECK_DB_PASSWORD=isolated-checks-only \
  "$image" python manage.py test --settings=config.test_settings --noinput "$@"
