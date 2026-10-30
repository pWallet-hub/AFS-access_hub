# Production readiness

Status of each application for production, as of this review. **Verified** means it was run or tested here. **Needs
you** means it depends on infrastructure or a decision that only the team can make. **Open** means a known gap that is
not fixed yet.

## Shared

| Item | Status |
| --- | --- |
| Public addresses are HTTPS (DNS + TLS at a reverse proxy in front of each frontend and Keycloak) | Needs you |
| Every secret is random and at least 24 characters; no `change-me` values | Verified in each app's startup checks (AFS, ImageUnit, Procurement, Biocap) |
| Single sign-on for every application goes through AFS Command Center; access is granted only there | Verified (see each application) |
| Services are reachable only through the proxy: databases, caches, storage, and the identity provider's admin port are not published | Verified for AFS production overlay; needs checking on each host |
| Backups and restore tests | Scripts exist (AFS `scripts/backup.sh`, ImageUnit `ops/backup.sh`); schedule needs you |
| Email (Resend) and file storage keys | Needs you |

## AFS Command Center (identity and access)

- **Verified:** startup refuses placeholder or short secrets, plain-HTTP public addresses, and session cookies that are
  not HTTPS-only when `ENVIRONMENT=production` (`backend/app/production.py`, tests in `backend/tests/test_production.py`).
  The full backend suite passes (112 tests). The production Keycloak image builds (`keycloak/Dockerfile`) and the
  production overlay renders (`docker-compose.prod.yml`: optimized start, HTTP off, proxy headers trusted, no admin
  port published).
- **Needs you:** set `ENVIRONMENT=production`, `SESSION_HTTPS_ONLY=true`, and https addresses in `.env`; run
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`; name a real super administrator
  (the bootstrap account is a test account); set the Resend key so Keycloak can send verification mail.
- **Not verified here:** the production overlay has not been run end to end, because it needs a TLS proxy in front.

## ImageUnit

- **Verified:** `ops/gen-env.sh` produces a production file with strong secrets, HTTPS addresses, secure cookies, and
  loopback-only infrastructure ports. Adding the AFS SSO settings and running `npm run check-env` says the API would
  start. The console is gated on the server (`apps/web/src/proxy.ts`): anonymous visitors are redirected to sign-in.
- **Needs you:** the three DNS records and the Caddy profile (`make prod-up`); AFS SSO values for production (the
  redirect URI must be the public API callback); the data-protection decisions in `docs/DATA-PROTECTION.md`.
- **Open:** the password login stays on as a break-glass path (decide whether to keep it). Three collection tests were
  already failing on `main` before the SSO work (see `docs/integrations/imageunit.md`).

## Procurement

- **Verified:** production runs AFS-only by default. Password sign-in, invitations and password changes are refused
  (`PASSWORD_LOGIN`, default off in production; 43 backend tests pass). First sign-in through AFS creates the account
  from the AFS identity, with the AFS role. The sign-in page shows the AFS button only.
- **Needs you:** `NODE_ENV=production`, `APP_URL` and the portal address on https, `RESEND_API_KEY` and `MAIL_FROM`,
  real S3 storage keys, and the Postgres password. Production does not load demo accounts.
- **Open:** the backend review in `docs/integrations/procurement.md` (Step 0): reads without a permission check, TOTP not
  enforced for approvers, and an in-memory rate limiter. Each needs a decision before finance roles use it. Tokens are
  still kept in the browser's `localStorage`.

## Biocap

- **Verified:** the backend refuses to start in production unless the Mongo, JWT, Resend, Cloudinary and https settings
  are present, and with SSO on, all AFS settings are checked at startup. The hardcoded database credential was removed
  from the code. 56 backend tests pass. The frontend has a production Dockerfile with standalone output.
- **Needs you:** **rotate the MongoDB Atlas password.** The old one is in the repository history. Then create a
  read-only database user for the usage dashboard and put its connection string in the AFS `.env` (not in the repo).
  Set `NEXT_PUBLIC_API_URL` at build time to the public API.
- **Not verified here:** the frontend production build. In this sandbox Next.js cannot download Google Fonts, so the
  build stops there. It should build on a host with internet access; check it there before release.
- **Open:** refresh tokens never expire and are not rotated; the refresh route still takes `userId` from the request
  body; the consent and comment routes are not checked for ownership; tokens are not checked for `iss`/`aud`; tokens
  are still kept in JavaScript-readable cookies.
