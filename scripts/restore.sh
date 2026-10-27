#!/usr/bin/env bash
# Restore a dump made by scripts/backup.sh. Replaces all data in the stack's databases.
# Usage: scripts/restore.sh backups/<file>.sql
set -euo pipefail

cd "$(dirname "$0")/.."
dump="${1:?usage: scripts/restore.sh <dump.sql>}"
[ -s "$dump" ] || { echo "restore failed: $dump is missing or empty" >&2; exit 1; }

# Stop everything that holds connections to the databases being replaced, keeping the database container up.
docker compose stop backend frontend keycloak keycloak-init >/dev/null 2>&1 || true

docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d postgres' < "$dump"

docker compose up -d
echo "restore complete from $dump; waiting for services"
for _ in $(seq 1 60); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/api/health)" = "200" ]; then
    echo "stack healthy"
    exit 0
  fi
  sleep 3
done
echo "restore finished but the stack is not healthy yet; check: docker compose ps" >&2
exit 1
