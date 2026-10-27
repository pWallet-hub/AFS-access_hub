# AFS Command Center

Centralized application access and SSO platform for the AFS ecosystem. The full specification is in [Task.md](Task.md).

This repository currently contains the **Phase 2 foundation**: a Docker Compose environment with PostgreSQL, Keycloak (AFS realm and Command Center OIDC client), a FastAPI backend that completes the OpenID Connect login, and an nginx-served portal.

## Prerequisites

- Docker Engine with the Docker Compose plugin. Nothing else needs to be installed on the host.

## Quick start

```bash
cp .env.example .env
# Replace every change-me-* value in .env first.
docker compose up --build -d
docker compose ps
```

Open the portal at **http://localhost:8080** and choose **Sign in with AFS SSO**.

The first start creates the AFS realm, the `afs-command-center` OIDC client and a test account:

- Email / username: `carterk279@gmail.com` (set by `BOOTSTRAP_ADMIN_EMAIL`)
- Password: the value of `BOOTSTRAP_ADMIN_PASSWORD` in `.env`

Keycloak's administration console is at **http://localhost:8081** (user `KEYCLOAK_ADMIN`, password `KEYCLOAK_ADMIN_PASSWORD`).

## Services

| Service | Purpose | Exposed at |
| --- | --- | --- |
| `frontend` | nginx serving the portal; proxies `/api/` and `/auth/` to the backend | http://localhost:8080 |
| `backend` | FastAPI API and OIDC login/callback/logout | via `frontend` |
| `keycloak` | Identity provider (Keycloak 26.0, `start-dev` mode) | http://localhost:8081 |
| `keycloak-init` | One-shot job that configures the realm, client and bootstrap user (idempotent) | none |
| `db` | PostgreSQL 16; hosts the `afs_command_center` and `keycloak` databases | none |

Startup order is enforced with health checks: the database must be healthy before Keycloak starts, Keycloak must be healthy before the bootstrap job runs, the bootstrap job must succeed before the backend starts, and the backend must be healthy before the frontend starts.

## Common commands

```bash
docker compose up --build -d        # start or rebuild
docker compose ps                   # service status and health
docker compose logs -f backend      # follow logs (also: keycloak, keycloak-init, db, frontend)
docker compose stop                 # stop, keep data
docker compose down                 # remove containers, keep data (named volume pgdata survives)
docker compose run --rm --no-deps backend python -m pytest tests -q   # run backend tests
curl -s http://localhost:8080/api/health                              # dependency health
```

### Destructive command

`docker compose down -v` deletes the `pgdata` volume, which holds **all** database data, including Keycloak's users and sessions. Only use it when you intend to reset the environment. Back up first (see below).

## Login flow

1. The browser opens `/auth/login`. The backend stores a random `state` and `nonce` in the session and redirects to Keycloak.
2. The user signs in at Keycloak.
3. Keycloak redirects to `/auth/callback`. The backend checks `state`, exchanges the code server-to-server, and verifies the ID token signature (JWKS), issuer, audience, expiry and nonce.
4. The backend stores the verified identity (`sub`, `email`, `name`) in a signed session cookie. `/api/me` returns it.

Accounts are identified by Keycloak's stable `sub` claim. The backend does not link identities by email.

## Configuration

All settings come from `.env` (see [.env.example](.env.example)). Notable values:

- `PUBLIC_BASE_URL` / `KEYCLOAK_PUBLIC_URL`: browser-facing URLs. Keycloak writes `KEYCLOAK_PUBLIC_URL` into token issuers, so changing it requires re-running the stack from a clean volume.
- `SESSION_SECRET`: signs session cookies. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.

Production must not use the `.env.example` values, `start-dev`, or plain HTTP. Those items are part of the hardening phase.

## Backup and restore

Back up all databases in the `db` container:

```bash
docker compose exec -T db pg_dumpall -U "$POSTGRES_USER" > backup-$(date +%F).sql
```

Restore into an empty stack:

```bash
docker compose up -d db
docker compose exec -T db psql -U "$POSTGRES_USER" -d postgres < backup-YYYY-MM-DD.sql
docker compose up -d
```

## Troubleshooting

- **`keycloak-init` failed**: `docker compose logs keycloak-init`. Usually a wrong `KEYCLOAK_ADMIN_PASSWORD` after a volume was created with a different one. Use `docker compose down -v` to reset (this deletes data).
- **Login loops or "Cookie not found" from Keycloak**: make sure you open the portal at `PUBLIC_BASE_URL` (http://localhost:8080) and not another host name.
- **Frontend stays "starting"**: the backend is unhealthy. Check `curl -s http://localhost:8080/api/health`; a 503 names the failing dependency.
- **Postgres image fails with `exec format error`**: a local `postgres:16-alpine` image was corrupt on the development machine. Re-pull the image, or use the Debian-based `postgres:16` tag as this repository does.

## Not yet implemented

Per [Task.md](Task.md), these are later phases: employee management, the application registry, access assignments and role mappings, the application launcher, audit logging, and the pilot integration with the five AFS applications. The backend currently has no application tables; migrations will be added with the first schema.
