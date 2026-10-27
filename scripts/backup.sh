#!/usr/bin/env bash
# Dump the stack's databases (Command Center and Keycloak) to a timestamped SQL file in ./backups.
# Roles are not included: they are created by the database container itself, so restores never touch them.
# Usage: scripts/backup.sh [output-file]
set -euo pipefail

cd "$(dirname "$0")/.."
out="${1:-backups/afs-$(date +%Y%m%d-%H%M%S).sql}"
mkdir -p "$(dirname "$out")"

{
  for database in afs_command_center keycloak; do
    docker compose exec -T db sh -c "pg_dump --create --clean --if-exists -U \"\$POSTGRES_USER\" $database"
  done
} > "$out"

if [ ! -s "$out" ]; then
  echo "backup failed: $out is empty" >&2
  exit 1
fi
echo "backup written: $out ($(wc -c < "$out") bytes)"
