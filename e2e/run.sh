#!/usr/bin/env bash
# Run the browser tests against the running stack, inside the Playwright container (no host installs).
# Usage: e2e/run.sh   (requires `docker compose up -d` and a healthy stack)
set -euo pipefail

cd "$(dirname "$0")/.."
docker run --rm --network host \
  --env-file <(grep -E '^(BOOTSTRAP_ADMIN_EMAIL|BOOTSTRAP_ADMIN_PASSWORD|KEYCLOAK_ADMIN|KEYCLOAK_ADMIN_PASSWORD|KEYCLOAK_REALM)=' .env) \
  -v "$PWD/e2e:/e2e:ro" -w /tmp \
  mcr.microsoft.com/playwright/python:v1.47.0-jammy \
  sh -c 'pip install -q pytest==8.3.4 playwright==1.47.0 >/tmp/pip.log 2>&1 && python3 -m pytest /e2e -q -p no:cacheprovider'
