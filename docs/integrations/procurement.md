# Integrating Procurement with AFS SSO

Audience: the developers of Procurement, which is one product made of two repositories, and the administrator who registers it.

- **Frontend (portal):** `pWallet-hub/Procurrement-fn` (React 18, TypeScript, Vite). Its API base is `/api/v1`.
- **Backend:** `pWallet-hub/Procurrement-bn` (NestJS 10, PostgreSQL 16, Redis). Its global prefix is `api/v1`, and it describes itself as the backend for the procurement and e-signature system.

Command Center registers them as **one application**, `procurement`. The BN backend runs the sign-in, checks access, and serves the API. The FN portal only starts the sign-in, receives a one-time code, and exchanges it for tokens. The Keycloak client `procurement` belongs to the BN backend.

## Facts from the audit

Backend (BN):
- Local email and password with Argon2id, optional TOTP, and lockout after 5 failures.
- Access token: HS256 JWT, 15 minutes. Refresh token: random, stored as SHA-256, rotated on use, 7 days.
- `AuthGuard` reloads the user on every request.
- Roles are rows in `user_roles`: `requesting_staff`, `accountant`, `director_comms`, `director_dept`, `pi`, `cfm`, `market_verifier`, `superior`, `admin`. Permissions come from `role_permissions`.
- `users.password_hash` is already nullable. There is no external identity column yet.
- Gaps: `TOTP_REQUIRED` does not force enrolment; some read routes have no `@Perm`, so any signed-in user can call them; a method-level `@Perm` replaces the class-level `admin.manage` guard on budget-line routes; the rate limiter is in memory.

Frontend (FN):
- Sign-in posts to `/auth/login` on the BN API. Bearer tokens are kept in localStorage (`afs.access_token`, `afs.refresh_token`).
- Route guards (`/audit`, `/reports`, `/admin`) and permission checks are client-side only.
- Public by design: `/sign/:token` and `/verify/:documentId`.
- The README documents shared development accounts and a development password.

## Step 0: Fix the backend gaps first

Before the pilot, in the BN backend:

1. Read every route that has no `@Perm` and decide who may call it. Add a permission to each one that should be restricted.
2. Restore the class-level `admin.manage` guard on the budget-line routes.
3. Make TOTP enrolment mandatory for approver roles (`pi`, `cfm`, `director_dept`, `director_comms`, `accountant`, `superior`), or confirm that Keycloak MFA replaces it for SSO users. Until that is decided, SSO sessions must not skip TOTP.
4. Move the rate limiter to Redis, because it is per process today.
5. Remove the development accounts and password from the README and from any environment that is reachable.

## Step 1: Command Center (administrator)

1. Open **Procurement** (slug `procurement`). Set its launch URL to the portal address (for example `https://procurement.example.org`). Enable it.
2. Assign each employee with the role that matches their current BN role. The nine role codes are seeded by migration 004.
3. Without an assignment, the sign-in is refused.

## Step 2: AFS configuration

```bash
PROCUREMENT_OIDC_CLIENT_ID=procurement
PROCUREMENT_OIDC_CLIENT_SECRET=<random>
PROCUREMENT_REDIRECT_URI=https://api.procurement.example.org/api/v1/auth/sso/callback
PROCUREMENT_ORIGIN=https://procurement.example.org
AFS_SERVICE_TOKEN_PROCUREMENT=<random, shared with the BN backend only>
```

`PROCUREMENT_REDIRECT_URI` is the BN callback. `PROCUREMENT_ORIGIN` is the portal's address, where the user returns after sign-in. Run `docker compose up -d` or re-run `keycloak-init` to create the `procurement` client, which uses PKCE.

## Step 3: Backend (BN repository)

**Database.** Add one migration in `db/migrations/`:

```sql
ALTER TABLE users ADD COLUMN external_id text UNIQUE;
```

`external_id` holds the Keycloak `sub`. Existing accounts keep their password sign-in.

**Routes** under `/api/v1/auth/sso`:

- `GET /login?next=<path>`: generate `state`, `nonce` and a PKCE verifier. Keep them in a signed, 10-minute, `HttpOnly`, `SameSite=Lax` cookie at path `/api/v1/auth/sso`. Redirect to Keycloak with `code_challenge_method=S256`. Accept `next` only as a path on the portal origin.
- `GET /callback`:
  1. Check `state` against the cookie.
  2. Exchange the code at `KEYCLOAK_INTERNAL_URL/realms/afs/protocol/openid-connect/token`, with the client secret and the verifier.
  3. Verify the ID token with `jose` against the realm keys: RS256 only, issuer `KEYCLOAK_PUBLIC_URL/realms/afs`, audience `procurement`, and the nonce.
  4. Call `GET {AFS_COMMAND_CENTER_URL}/api/internal/access?application=procurement&subject=<sub>` with `X-Service-Token: AFS_SERVICE_TOKEN_PROCUREMENT`. Deny unless `allowed` is true and `external_role` is set.
  5. Find the user by `external_id`. If none, link an existing account with the same verified email, only when `external_id` is empty. Do not create accounts here.
  6. Refuse inactive accounts.
  7. Replace the user's `user_roles` with exactly the central role. Command Center is the source of truth for SSO users.
  8. Issue tokens through the existing token service, the same way password login does.
  9. Create a one-time code (60 seconds, single use, stored hashed) and redirect the browser to `{PORTAL}/sso/complete?code=<code>`.
- `POST /exchange`: the portal sends the code once and receives the access and refresh tokens.

The browser never receives Keycloak tokens, and no token appears in a URL. Only the one-time code does.

**Refresh.** `/auth/refresh` must repeat the access check for SSO users. Without it, a revoked employee keeps access for as long as the refresh token lives (7 days).

## Step 4: Frontend (FN repository)

- **Login page**: add **Sign in with AFS SSO**. It links to `…/api/v1/auth/sso/login?next=/`. Keep the password form if local accounts remain.
- **New route `/sso/complete`**: read `code`, post it to `/api/v1/auth/sso/exchange`, store the tokens the same way the password login does, then go to the requested page. On failure, show the message and offer a link back to login.
- **Logout**: call the existing logout, and also end the Keycloak session through the BN backend.
- The existing refresh logic in `src/api/client.ts` handles SSO tokens unchanged.
- Tokens in localStorage are an existing risk: any injected script can read them. The recommended follow-up is HttpOnly cookies set by the BN backend, which also removes the need for the portal to hold tokens.

## Step 5: Protect the financial operations

Portal guards are presentation only. Before SSO is enabled for finance roles, check each of these in the BN backend:

- The advance payment endpoint requires `case.advance_arrangement` and the payment stage.
- Cancelling a case allows only admin, accountant, or the requester.
- Document signing verifies the signer is the assigned user or holds the slot's role.
- `/audit` and `/reports` routes require their roles on the server.
- Public routes (`/sign/:token`, `/verify/:documentId`) stay public and stay out of SSO.

## Step 6: Tests

- An assigned employee signs in and reaches the requested page. An unassigned employee is refused.
- A revoked assignment blocks the next sign-in and the next refresh.
- A one-time code works once and expires after 60 seconds.
- A `next` value or origin outside the allow-list is refused.
- A user without the right role gets 403 from the API directly. Test with a token, not through the UI.
- Public routes still work without signing in.
- The password login still works for password accounts.

## Open questions

1. Is Keycloak MFA acceptable in place of TOTP for SSO users?
2. Should local password login remain for portal users?
3. Which routes in Step 0 are meant to be open to every signed-in user?
