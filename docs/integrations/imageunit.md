# Integrating ImageUnit with AFS SSO

Audience: the AFS administrator who registers ImageUnit, and the developers who wire it to the AFS Command Center stack. Status: verified end to end on the local stack (see [Verification](#verification)). Not yet deployed.

## What each side provides

| Piece | Provided by | Where |
| --- | --- | --- |
| Keycloak client `imageunit` (confidential, PKCE S256, authorization code only) | AFS stack | `keycloak/bootstrap.py` |
| Application record and launch URL | AFS Command Center admin console | Applications |
| Application roles `admin`, `editor`, `photographer` | AFS stack (seeded) | `backend/migrations/003_imageunit_roles.sql` |
| Access check `GET /api/internal/access` (service token) | AFS Command Center backend | `backend/app/internal.py` |
| Login flow, identity linking, role sync, session | ImageUnit API | `apps/api/src/modules/sso.ts`, `apps/api/src/lib/sso.ts` |
| Reference code for ImageUnit | This repository | [`imageunit/sso-pilot.patch`](imageunit/sso-pilot.patch) |

The patch is against `IshKevin/ImageUnit` at commit `ae8fa74` (branch `main`). It does not include the two generated Drizzle snapshot files (`apps/api/drizzle/meta/0006_snapshot.json`, `0007_snapshot.json`). Regenerate them with `npx drizzle-kit generate` after applying the schema change, or take them from the pilot branch.

## Access rules

1. Keycloak verifies the employee's identity. The ID token is checked for signature, issuer, audience, expiry and nonce.
2. AFS Command Center must report an **active assignment** to ImageUnit **with an application role**. Sign-in alone grants nothing. An assignment without a role is refused (`unknown_role`).
3. The Keycloak identity is linked to an **existing** ImageUnit account. Linking needs `email_verified = true` and an account whose email matches and which is not yet linked. Accounts are not created on sign-in; an administrator creates them first.
4. The ImageUnit account must be `active`.
5. ImageUnit's role is set from the Command Center role (`admin`, `editor`, `photographer`) on every SSO sign-in.

## Step 1: AFS Command Center (administrator)

1. Sign in to the Command Center portal as a super administrator.
2. **Applications & roles**: open `ImageUnit`, set the launch URL to the public ImageUnit web address (for example `https://imageunit.example.org`), and enable it. ImageUnit cannot be enabled without a launch URL.
3. Confirm the roles `admin`, `editor` and `photographer` are listed under **Application-specific roles**. They were seeded by migration 003; do not rename them, because the value is what ImageUnit reads.
4. **Employees**: make sure each person who needs ImageUnit exists. Pre-provision them by email if they have not signed in yet.
5. **Assignments**: grant access to ImageUnit and choose the role. Revoking an assignment takes effect at the person's next SSO sign-in in ImageUnit, and no later than the SSO session lifetime (8 hours). See [Revocation](#revocation).

## Step 2: AFS stack configuration (developer)

Set these in the AFS `.env` (see `.env.example`):

```bash
IMAGEUNIT_OIDC_CLIENT_ID=imageunit
IMAGEUNIT_OIDC_CLIENT_SECRET=<random, at least 24 characters>
IMAGEUNIT_REDIRECT_URI=https://imageunit.example.org/api/auth/sso/callback
IMAGEUNIT_ORIGIN=https://imageunit.example.org
AFS_SERVICE_TOKEN_IMAGEUNIT=<random, at least 32 characters, shared only with ImageUnit>
```

- `IMAGEUNIT_REDIRECT_URI` must be the callback on the **ImageUnit web address** (the same host as the login page), not the API's own hostname. It must match `AFS_OIDC_REDIRECT_URI` exactly. The ImageUnit web app forwards `/api/*` to the API, and sign-in cookies (the SSO state cookie, then the session) belong to the web address. A callback on the API's own hostname cannot read the state cookie and fails with `expired`.
- Run `docker compose up -d` (or re-run `keycloak-init`) so the `imageunit` client is created or updated.
- Each application has its own token (`AFS_SERVICE_TOKEN_<APP>`). A token works only for its own application.
- ImageUnit's API must be able to reach the Command Center backend at `AFS_COMMAND_CENTER_URL` on the internal network. nginx blocks `/api/internal/` from browsers, so the call must not go through the public site.

Generate secrets with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Step 3: ImageUnit configuration (developer)

Apply the patch to a branch of the ImageUnit repository, regenerate the snapshots if needed, then install and migrate:

```bash
git checkout -b afs-sso
git apply path/to/sso-pilot.patch
npm install
npm run db:migrate --workspace @imageunit/api
```

Migrations added:

- `0006_sso_pilot.sql`: `users.password_hash` becomes nullable (SSO-only accounts have no password); `users.oidc_sub` is added with a unique index.
- `0007_sso_session_flag.sql`: `sessions.sso` marks SSO sessions.

Environment variables for the ImageUnit API:

| Variable | Example | Notes |
| --- | --- | --- |
| `AFS_SSO_ENABLED` | `true` | When `true`, all variables below are required; startup fails otherwise. |
| `AFS_OIDC_ISSUER` | `https://auth.afs.example.org/realms/afs` | The public issuer. It must equal Keycloak's `KC_HOSTNAME` realm URL. |
| `AFS_OIDC_INTERNAL_ISSUER` | `http://keycloak:8080/realms/afs` | Optional. Where the API reaches Keycloak. Defaults to `AFS_OIDC_ISSUER`. |
| `AFS_OIDC_CLIENT_ID` | `imageunit` | |
| `AFS_OIDC_CLIENT_SECRET` | same as `IMAGEUNIT_OIDC_CLIENT_SECRET` | Server-side only. |
| `AFS_OIDC_REDIRECT_URI` | `https://imageunit.example.org/api/auth/sso/callback` | Set it to the **web** address's callback, the same value as `IMAGEUNIT_REDIRECT_URI`. The default (`API_PUBLIC_URL` + `/api/auth/sso/callback`) is wrong when the API has its own hostname. |
| `AFS_COMMAND_CENTER_URL` | `http://backend:8000` | Internal address of the Command Center backend. |
| `AFS_SERVICE_TOKEN` | same as `AFS_SERVICE_TOKEN_IMAGEUNIT` in the AFS `.env` | Server-side only. Sent as `X-Service-Token`; valid only for `imageunit`. |
| `AFS_APP_SLUG` | `imageunit` | Optional. Matches the application slug in Command Center. |
| `COOKIE_SECURE` | `true` in production | Required on HTTPS. Config validation refuses to start otherwise. |

Also set `PUBLIC_WEB_URL` to the ImageUnit web address, because the callback redirects there after sign-in.

The web app shows **Sign in with AFS SSO** on `/login` when the API reports `enabled: true` from `GET /api/auth/sso/config`. The password form stays available for break-glass access.

## Step 4: Deploy and check

1. Start the ImageUnit stack with the variables above.
2. Open `https://imageunit.example.org/login` and choose **Sign in with AFS SSO**.
3. Expected results:
   - An assigned employee reaches `/console` with the role from Command Center.
   - An unassigned employee returns to `/login` with a message (`not_assigned`), and no session is created.
   - An employee without an ImageUnit account returns with `no_account`.
4. In Command Center, **Audit log** shows `auth.sso_login`, `auth.sso_link` (first time only), `user.role_synced` when the role changes, and `access.denied` for refused attempts.

Error codes shown to users (from the callback redirect `…/login?sso_error=<code>`): `cancelled`, `expired`, `token`, `invalid_token`, `unavailable`, `not_assigned`, `unknown_role`, `no_account`, `unverified_email`, `disabled`.

## Revocation

| Event | What happens in ImageUnit |
| --- | --- |
| Assignment revoked in Command Center | New SSO sign-ins are refused at once. An existing SSO session keeps working until its fixed 8-hour expiry. It is not renewed. |
| Account disabled in Command Center | Same as above: new sign-ins refused, existing SSO session ends at its expiry. |
| Account suspended in ImageUnit | The next request fails, because the session check reads the account status. |
| Password sign-in | Still works for accounts that have a password. SSO-only accounts cannot use it. |

Reducing the 8-hour bound needs a change to the session lifetime (`SSO_SESSION_TTL_MS` in `apps/api/src/lib/sso.ts`). A forced logout in real time needs back-channel logout from Keycloak, which is not built.

## Verification

Checked on the local stack (Keycloak 26, Command Center backend, ImageUnit API from the patch, SeaweedFS for object storage):

- **Assigned employee**: browser sign-in through Keycloak lands on `/console`. `GET /api/auth/me` returns the account with role `editor`, synced from Command Center. The session is set as `iu_session`.
- **Unassigned employee**: refused with `not_assigned`. No session is created.
- **SSO-only account**: password sign-in returns 401.
- **Logout**: the session is cleared; `/api/auth/me` returns 401.
- **Identity link**: the account's `oidc_sub` is set after the first sign-in. The unassigned account stays unlinked.
- **Revocation** (run during testing): with the assignment revoked, a fresh sign-in is refused.
- **ImageUnit tests**: the SSO unit tests (29) pass. Three collection tests in `tests/media.test.ts` fail on the untouched `main` commit as well, so they predate this change.
- **Command Center tests**: 29 backend tests pass, including the access-check tests in `backend/tests/test_internal.py`.

Not yet verified: running the ImageUnit web app itself against the callback (the browser test used a stub page for the final redirect), production HTTPS, and a load test.

## Known limits and decisions for the team

1. **Revocation delay** of up to 8 hours for existing sessions (see above).
2. **No account creation at sign-in.** An administrator must create each ImageUnit account. Decide whether ImageUnit should create accounts automatically for assigned employees.
3. **Role is set by Command Center** on each sign-in. A local role change in ImageUnit is overwritten at the next SSO sign-in.
4. **Password login stays on** as a break-glass path. Decide whether it should be turned off for production.
5. **Linking by email** uses the verified email in Keycloak. Keep email verification required in the AFS realm.
6. **Service token** is a shared secret between the two backends. Rotate it through both `.env` files at the same time.

## Using this for other applications

The same pattern applies to any application:

1. Register the application and its roles in Command Center, and enable it with a launch URL.
2. Add a Keycloak client with PKCE, a redirect URI on the application's own callback, and a secret from the environment.
3. In the application backend, implement the flow above: state and nonce checks, PKCE exchange, ID-token verification, `GET /api/internal/access`, identity linking, role mapping, and the application's own session.
4. Register the new client in `keycloak/bootstrap.py` (`ensure_clients`), then add its variables to `.env.example` and `docker-compose.yml`.

Applications whose stacks differ (the audit found Express with MongoDB, NestJS with PostgreSQL, and frontend-only repositories) follow the same steps. For frontend-only repositories, the flow has to be implemented in the backend that holds the authorization, as [integration-audit.md](../integration-audit.md) notes. See also [integration-guide.md](../integration-guide.md) and [security.md](../security.md).
