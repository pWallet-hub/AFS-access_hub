# AFS Command Center

Centralized application access and SSO platform for the AFS ecosystem. The full specification is in [Task.md](Task.md).

This repository contains the Command Center platform:

- A Docker Compose environment with PostgreSQL, Keycloak (AFS realm and OIDC client), a FastAPI backend and an nginx-served portal.
- Central sign-in through Keycloak (OpenID Connect), with account resolution and a disabled-account check on every request.
- Employee management (pre-provisioned accounts, enable/disable), platform roles (`super_admin`, `access_admin`, `employee`).
- Application registry, application-specific role mappings, and assignments with revocation.
- The employee launcher, showing only assigned, enabled applications.
- An append-only audit log for privileged changes and sign-ins.

Documentation: [security model and revocation policy](docs/security.md) and [integrating an application](docs/integration-guide.md).

Integration guides for all five applications are in [docs/integrations/](docs/integrations/README.md). The ImageUnit pilot has a reference patch in `docs/integrations/imageunit/`. The full specification is in [Task.md](Task.md).

Not yet included: integration of the five AFS applications themselves (see "Status" below).

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

## Email (Resend)

Set `RESEND_API_KEY` and `RESEND_FROM` (an address on a domain verified in Resend) in `.env`, then `docker compose up -d`.

- **Keycloak** sends verification and password-reset mail through Resend's SMTP service (`smtp.resend.com`). Email verification and password reset are switched on only when Resend is configured.
- **Command Center** sends notices through Resend's HTTP API when an employee is added, when their account is disabled or reactivated, when their platform role changes, and when access is granted or removed. Sending happens after the change is saved, so a mail failure never undoes it. Each attempt is in the audit log as `notification.sent` or `notification.failed`.

Without `RESEND_API_KEY`, mail is off and notices are logged and skipped.

## Troubleshooting

- **`keycloak-init` failed**: `docker compose logs keycloak-init`. Usually a wrong `KEYCLOAK_ADMIN_PASSWORD` after a volume was created with a different one. Use `docker compose down -v` to reset (this deletes data).
- **Login loops or "Cookie not found" from Keycloak**: make sure you open the portal at `PUBLIC_BASE_URL` (http://localhost:8080) and not another host name.
- **Frontend stays "starting"**: the backend is unhealthy. Check `curl -s http://localhost:8080/api/health`; a 503 names the failing dependency.
- **Postgres image fails with `exec format error`**: a local `postgres:16-alpine` image was corrupt on the development machine. Re-pull the image, or use the Debian-based `postgres:16` tag as this repository does.

## Using the administrative console

The first person to sign in with the realm role `afs-super-admin` (the bootstrap account) becomes the super administrator. After that:

- **Employees**: add an employee by email before they sign in. Their account links to the central identity on first sign-in with a verified email. Disable or reactivate accounts here. Only super administrators change platform roles.
- **Applications & roles**: register an application (disabled by default), set its verified `https` launch URL, enable it, and define its application-specific roles.
- **Assignments**: grant or update an employee's access to an enabled application, optionally with one of its roles. Revoke access here.
- **Audit log**: every privileged change, denied attempt, and sign-in is recorded.

The five AFS applications are registered (disabled, without launch URLs) by `backend/migrations/002_seed_applications.sql`. Set their URLs in the console before enabling them.

## Testing

Backend tests (API, authorization, assignments, audit, access checks, integration monitoring, seeded roles):

```bash
docker compose run --rm --no-deps backend python -m pytest tests -q -p no:cacheprovider
```

The tests run against a separate `<POSTGRES_DB>_test` database, created automatically, so they never touch working data.

Browser tests (sign-in through Keycloak, the console, the employee launcher, unavailable applications, sign-out), run in a Playwright container against the running stack:

```bash
docker compose up -d
e2e/run.sh
```

The browser tests create a temporary employee in Keycloak and remove it afterwards.

## Backup and restore

```bash
scripts/backup.sh                      # writes backups/afs-<timestamp>.sql
scripts/restore.sh backups/<file>.sql  # replaces all data, then waits for the stack to be healthy
```

Both databases (Command Center and Keycloak) are included. Roles are not, so restores never change database users. Tested by wiping all volumes, restoring, and comparing record counts and the Keycloak accounts.

## Status

| Phase (Task.md section 12) | Status |
| --- | --- |
| 1. Repository audit and design | Done for all five repositories: see [integration-audit.md](docs/integration-audit.md). |
| 2. Docker foundation and identity | Done. |
| 3. Core Command Center | Done: employees, application registry, role mappings, assignments, launcher, audit log. |
| 4. Pilot integration | ImageUnit verified end to end on the local stack. Its production rollout is open. |
| 5. Remaining integrations | Guides written for Biocap and Procurement, each one product (frontend and backend). Code changes are owned by each product's team. |
| 6. Hardening and release | Partly done. See [security.md](docs/security.md) "Known limits". Production deployment, backup validation and cross-application logout remain. |
