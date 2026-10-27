# Integrating Procurement BN with AFS SSO

Audience: the developers of the Procurement BN backend (`pWallet-hub/Procurrement-bn`, NestJS 10, PostgreSQL 16) and the administrator who registers the application.

Procurement BN is an API with no screens of its own. Users work in **Procurement FN**, the portal, which calls this API. This guide therefore covers two things: the BN backend signs employees in for FN, and the BN backend checks the `procurement-fn` assignment. The Procurement BN record in Command Center stays disabled, because it has no launch page. Its Keycloak client (`procurement-bn`) is the one the backend uses for the sign-in.

## Facts from the audit

- Local email and password sign-in with Argon2id, optional TOTP, lockout after 5 failures.
- Access token: HS256 JWT, 15 minutes. Refresh token: random, stored as SHA-256, rotated on each use, 7 days.
- `AuthGuard` reloads the user on every request, so deactivation takes effect at once.
- Roles are rows in `user_roles` (role codes such as `requesting_staff`, `accountant`, `pi`, `cfm`, `admin`). Permissions come from `role_permissions`.
- `users.password_hash` is already nullable, and there is no external identity column yet.

## Step 1: Command Center (administrator)

1. Confirm **Procurement BN** exists with its nine roles (seeded by migration 004). Leave it disabled: it has no launch URL.
2. Open **Procurement FN** and set its launch URL to the portal address. Enable it. Its nine roles are the same codes as BN.
3. Assign employees to **Procurement FN** with the role that matches their BN role. The access check uses the FN assignment.

## Step 2: AFS configuration (developer)

```bash
PROCUREMENT_BN_OIDC_CLIENT_ID=procurement-bn
PROCUREMENT_BN_OIDC_CLIENT_SECRET=<random>
PROCUREMENT_BN_REDIRECT_URI=https://api.procurement.example.org/api/v1/auth/sso/callback
PROCUREMENT_BN_ORIGIN=https://procurement.example.org
AFS_SERVICE_TOKEN_PROCUREMENT_FN=<random, shared with the BN backend only>
```

Rebuild or re-run `keycloak-init` so the `procurement-bn` client exists. `PROCUREMENT_BN_ORIGIN` is the portal's origin, because that is where the user returns after sign-in.

## Step 3: Database (BN repository)

Add one migration in `db/migrations/`:

```sql
ALTER TABLE users ADD COLUMN external_id text UNIQUE;
```

`external_id` holds the Keycloak `sub`. Existing local accounts keep it empty and keep their password sign-in.

## Step 4: Sign-in endpoints (BN repository)

Add a `sso` module with two routes under `/api/v1/auth/sso`:

- `GET /login?app=procurement-fn&next=<path>`: allow only `app=procurement-fn` (Procurement BN has no screens, so it is never a sign-in target), and only paths on the portal origin. Generate `state`, `nonce` and a PKCE verifier. Keep them in a signed, short-lived cookie (10 minutes, `HttpOnly`, `SameSite=Lax`, path `/api/v1/auth/sso`). Redirect to Keycloak.
- `GET /callback`: check `state`. Exchange the code at `KEYCLOAK_INTERNAL_URL/realms/afs/protocol/openid-connect/token` with the client secret and the verifier. Verify the ID token with `jose` against the realm keys (RS256, issuer `KEYCLOAK_PUBLIC_URL/realms/afs`, audience `procurement-bn`, nonce). Then:
  1. Call `GET {AFS_COMMAND_CENTER_URL}/api/internal/access?application=procurement-fn&subject=<sub>` with `X-Service-Token: AFS_SERVICE_TOKEN_PROCUREMENT_FN`. Deny unless `allowed` is true and `external_role` is set.
  2. Find the user by `external_id`. If none, link an existing account whose email matches, only when `email_verified` is true and `external_id` is empty. Do not create accounts here.
  3. Refuse inactive accounts.
  4. Set the user's roles to exactly the central role: replace `user_roles` for this user with `external_role`. Command Center is the source of truth for SSO users.
  5. Issue tokens with the existing token service, the same way the password login does.
  6. Create a one-time code (60 seconds, single use, stored hashed) and redirect the browser to `{portal}/sso/complete?code=<code>`.
- `POST /exchange` (called by the portal): accept the code once and return the access and refresh tokens, as the login endpoint does.

The browser never receives Keycloak tokens. The code exchange keeps the access token out of the address bar.

## Step 5: Portal (Procurement FN) changes

- The login page gets a **Sign in with AFS SSO** button that opens `…/api/v1/auth/sso/login?app=procurement-fn&next=/`.
- Add a `/sso/complete` route that reads `code` from the query, posts it to `/exchange`, and stores the tokens exactly as the existing login does. The portal's current token storage (localStorage) is an existing risk; see the caveat below.
- Keep the existing login form.

## Step 6: Protect the rules that matter

- Keep `AuthGuard` as it is. SSO tokens are ordinary tokens.
- Application-level permissions stay in the BN backend. The portal's route guards are presentation only.
- The audit found gaps to close before the pilot:
  - `TOTP_REQUIRED` does not force enrolment. Approver roles (`pi`, `cfm`, `director_dept`, `director_comms`, `accountant`, `superior`) should be required to use a second factor. Decide whether Keycloak's MFA replaces TOTP for SSO users. Until that is decided, SSO sessions should not skip TOTP.
  - Some read routes (lookups, case list) have no `@Perm`, so any signed-in user can call them. Add permissions.
  - `@Perm` on the budget-line routes replaces the class-level `admin.manage` guard. Reinstate the class guard.
  - The rate limiter is in memory per process. Move it to Redis before running more than one instance.

## Step 7: Tests to write

- Sign-in with an assigned employee gives the right role; an unassigned one is refused.
- A revoked assignment blocks the next sign-in.
- `next` and `app` values outside the allow-lists are refused.
- A code can be used once; an expired code is refused.
- Linking: a verified email links an unlinked account; an unverified email does not.
- The original password login still works, and SSO-only accounts cannot use it.

## Known caveats

- Access tokens issued through SSO last 15 minutes, and refresh tokens last 7 days. A revoked employee keeps access for at most 15 minutes after the next refresh is refused, but the refresh endpoint must also re-run the access check for that to hold. Add that check to `/auth/refresh`.
- The portal keeps tokens in localStorage. Any script injection could read them. Moving to HttpOnly cookies set by the BN backend is the recommended follow-up.

## Open questions for the team

1. Is Keycloak MFA acceptable in place of the BN TOTP for SSO users?
2. Should the central role replace all local roles, or should some local roles remain?
3. Who maintains the role-to-permission table, and should Command Center's roles be extended beyond the nine codes?
