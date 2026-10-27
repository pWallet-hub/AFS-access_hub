# Integrating Biocap Dashboard Backend with AFS SSO

Audience: the developers of `pWallet-hub/Biocap-dashboard-backend` (Express 5, MongoDB, Redis) and the administrator who registers the dashboard.

This repository is an API. Its launch page, the dashboard frontend, was not part of the audit, so the launch URL is not yet known. Command Center can register the application now and enable it once the frontend address is set.

## Facts from the audit

- Local username or email and bcryptjs password, with self-registration (admin self-assignment is blocked) and a 90-day password expiry.
- Access token: HS256 JWT with a 7-day expiry, signed with `JWT_SECRET`. The algorithm is not pinned, and there are no `iss` or `aud` checks.
- Refresh token: random hex, stored bcrypt-hashed on the user, with no expiry and no rotation. Logout clears the refresh hash, but issued access tokens stay valid for up to 7 days.
- `protect` middleware reloads the user from the database on each request, so deactivation takes effect at once.
- One `role` string per user, with six values (see below). No permissions table.
- Some routes have only `protect` and no role check: `PATCH /api/blogs/:id/consent` and `POST /api/blogs/:id/comments`. Whether their controllers check roles or ownership is unverified.
- `/api/auth/refresh` takes `userId` from the request body.
- `config/db.js` has a **hardcoded MongoDB Atlas URI with credentials**. `scripts/seedAdmin.js` falls back to a development JWT secret.

## Step 0: Security fixes first (in the dashboard repository)

Do these before any SSO work, because SSO adds a second way in:

1. Rotate the MongoDB credentials that are in `config/db.js`. Remove the fallback URI, and read `MONGO_URI` from the environment only.
2. Remove the development JWT secret fallback in `scripts/seedAdmin.js`.
3. Pin the JWT algorithm (`algorithms: ['HS256']`) when verifying, and add `issuer` and `audience` claims to the tokens it issues.
4. Make refresh tokens expire, and rotate them on use.
5. Decide on `/api/auth/refresh` taking `userId` from the body. It should take the identity from the refresh token only.
6. Check the controllers behind the two unguarded routes above, and add a role or ownership check where it is missing.

## Step 1: Command Center (administrator)

1. Open **Biocap Dashboard Backend** (slug `biocap-dashboard`). Enter the dashboard frontend's URL as the launch URL. It stays disabled until that URL is known.
2. Confirm its six roles (seeded by migration 004): `admin`, `communication_officer`, `editor`, `social_media_content_creator`, `media_correspondent`, `principal_investigator`.
3. Assign employees with the role that matches their current dashboard role.

## Step 2: AFS configuration

```bash
BIOCAP_DASHBOARD_OIDC_CLIENT_ID=biocap-dashboard
BIOCAP_DASHBOARD_OIDC_CLIENT_SECRET=<random>
BIOCAP_DASHBOARD_REDIRECT_URI=https://api.dashboard.example.org/api/auth/sso/callback
BIOCAP_DASHBOARD_ORIGIN=https://dashboard.example.org
AFS_SERVICE_TOKEN_BIOCAP_DASHBOARD=<random, shared with the dashboard backend only>
```

Re-run `keycloak-init` (or `docker compose up -d`) so the `biocap-dashboard` client is created with PKCE enabled.

## Step 3: Database

MongoDB needs one new field on the `User` model, `keycloakSub`, with a unique sparse index, so that existing users without it are not affected:

```js
keycloakSub: { type: String, unique: true, sparse: true, index: true }
```

Make `password` optional for accounts linked to SSO (`required` only when `keycloakSub` is empty). Password sign-in must refuse accounts that have no password.

## Step 4: Sign-in routes (dashboard repository)

Add these to the Express app under `/api/auth/sso`:

- `GET /login`: generate `state`, `nonce` and a PKCE verifier, store them in a signed cookie (10 minutes, `HttpOnly`, `SameSite=Lax`, path `/api/auth/sso`), and redirect to Keycloak's authorization endpoint with `code_challenge_method=S256`.
- `GET /callback`:
  1. Check `state` against the cookie. Refuse if it does not match.
  2. Exchange the code at `KEYCLOAK_INTERNAL_URL/realms/afs/protocol/openid-connect/token`, sending the client secret and the verifier.
  3. Verify the ID token with `jose` against the realm keys: RS256 only, issuer `KEYCLOAK_PUBLIC_URL/realms/afs`, audience `biocap-dashboard`, and the nonce.
  4. Call `GET {AFS_COMMAND_CENTER_URL}/api/internal/access?application=biocap-dashboard&subject=<sub>` with `X-Service-Token: AFS_SERVICE_TOKEN_BIOCAP_DASHBOARD`. Refuse unless `allowed` is true and `external_role` is set.
  5. Find the user by `keycloakSub`. If none, link the account with the same verified email, only if it has no `keycloakSub`. Do not create accounts here.
  6. Refuse inactive accounts.
  7. Set `role` to the central `external_role`. Command Center is the source of truth.
  8. Issue the dashboard's own JWT (the same `generateToken` the password login uses), with the pinned algorithm and the new claims from Step 0.
  9. Redirect to the dashboard origin, to an allow-listed path only.

Protect the callback from open redirects: the `next` value must be a path on the dashboard origin.

## Step 5: Session lifetime

The dashboard issues stateless tokens, so a revoked employee would keep access until the token expires. Two changes limit that:

- Give SSO-issued access tokens a short lifetime (for example 8 hours), and make the refresh path for them re-run the access check against Command Center before issuing a new token.
- Keep the existing `protect` reload from the database, so disabled accounts are refused at once.

Without the refresh-time check, the 7-day token lifetime means revocation can take up to 7 days.

## Step 6: Protect the routes

- Keep the `authorize` role checks. Set roles only from Command Center for SSO users.
- Close the two unguarded routes from the audit (`consent`, `comments`) before the pilot, or confirm they are intended to be open to any signed-in user.
- Check that every `admin` route still requires `admin`.

## Step 7: Tests

- An assigned employee signs in and receives the right role; an unassigned one is refused.
- A revoked assignment blocks the next sign-in and the next token refresh.
- A tampered state cookie, a replayed code and a wrong audience are refused.
- A token signed with another algorithm is refused.
- The existing password login still works for password accounts, and SSO-only accounts cannot use it.

## Open questions

1. What is the dashboard frontend's address, and who owns it? It needs the same SSO button and callback handling.
2. Should SSO users keep a local password as a fallback?
3. Are the consent and comment routes meant to be open to every signed-in user?
