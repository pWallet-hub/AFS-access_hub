# Integrating Biocap with AFS SSO

Audience: the developers of Biocap, which is one product made of two repositories, and the administrator who registers it.

- **Frontend:** `felixaveiro/Biocap-rwanda` (Next.js 15, React 19). Live at biocap-rwanda.afs-rwanda.org.
- **Backend:** `pWallet-hub/Biocap-dashboard-backend` (Express 5, MongoDB, Redis). Its routes (`/api/auth/*`, `/api/blogs`, `/api/admin/users`, `/api/contributor`) are the ones the frontend calls.

Command Center registers them as **one application**, `biocap`. The backend runs the sign-in and checks access. The frontend only starts the sign-in and reads the result. The Keycloak client `biocap` belongs to the backend.

The pairing was inferred from the code: the same routes and the same six roles appear on both sides. The team should confirm it once, against the deployed API address that the frontend uses.

## Facts from the audit

Backend:
- Local username or email and bcryptjs password. Self-registration exists, but admin self-assignment is blocked. Passwords expire after 90 days.
- Access token: HS256 JWT, 7 days, signed with `JWT_SECRET`. The algorithm is not pinned, and there are no `iss` or `aud` checks.
- Refresh token: random, stored bcrypt-hashed, with no expiry and no rotation. Logout clears only the refresh hash.
- `protect` reloads the user from the database on each request, so deactivation takes effect at once.
- One `role` string per user. Six roles: `admin`, `communication_officer`, `editor`, `social_media_content_creator`, `media_correspondent`, `principal_investigator`.
- Two routes have only `protect`, with no role check: `PATCH /api/blogs/:id/consent` and `POST /api/blogs/:id/comments`. Whether the controllers check roles or ownership is unverified.
- `/api/auth/refresh` takes `userId` from the request body.
- `config/db.js` has a **hardcoded MongoDB Atlas URI with credentials**. `scripts/seedAdmin.js` falls back to a development JWT secret.

Frontend:
- Sign-in posts to `/api/auth/login`. Tokens are kept in **JavaScript-readable cookies** (`biocap_token`, `biocap_refresh_token`, `biocap_role`), with no `HttpOnly`.
- Role checks are only in the browser (`useSession` and layout guards). There is no `middleware.ts`.
- `src/lib/data.ts` holds seed users in localStorage. Nothing imports it.

## Step 0: Security fixes first

Do these in the backend before adding SSO, because SSO adds another way in:

1. Rotate the MongoDB credentials in `config/db.js`. Remove the fallback URI, and read `MONGO_URI` from the environment only.
2. Remove the development JWT secret fallback from `scripts/seedAdmin.js`.
3. Pin the JWT algorithm (`algorithms: ['HS256']`) on verification, and add `iss` and `aud` claims to the tokens it issues.
4. Make refresh tokens expire, and rotate them on use.
5. Take `userId` for `/api/auth/refresh` from the refresh token, never from the body.
6. Check the controllers behind the two unguarded routes, and add a role or ownership check where it is missing.
7. Move the tokens out of JavaScript-readable cookies (Step 4 below).

## Step 1: Command Center (administrator)

1. Open **Biocap** (slug `biocap`). Set its launch URL to the frontend address. Keep it disabled until the SSO steps pass.
2. Confirm its six roles are listed (seeded by migration 004). They match the backend's `ROLES`.
3. Assign employees with the role that matches their current Biocap role.

## Step 2: AFS configuration

```bash
BIOCAP_OIDC_CLIENT_ID=biocap
BIOCAP_OIDC_CLIENT_SECRET=<random>
BIOCAP_REDIRECT_URI=https://api.biocap.example.org/api/auth/sso/callback
BIOCAP_ORIGIN=https://biocap.example.org
AFS_SERVICE_TOKEN_BIOCAP=<random, shared with the Biocap backend only>
```

`BIOCAP_REDIRECT_URI` is the backend's callback. `BIOCAP_ORIGIN` is the frontend's address, where the user returns after sign-in. Run `docker compose up -d` or re-run `keycloak-init` to create the `biocap` client, which uses PKCE.

## Step 3: Backend (Biocap Dashboard repository)

**Database.** Add `keycloakSub` to the `User` model, with a unique sparse index. Make `password` required only when `keycloakSub` is empty. Password sign-in must refuse accounts without a password.

```js
keycloakSub: { type: String, unique: true, sparse: true, index: true }
```

**Routes** under `/api/auth/sso`:

- `GET /login?next=<path>`: generate `state`, `nonce` and a PKCE verifier. Keep them in a signed, 10-minute, `HttpOnly`, `SameSite=Lax` cookie at path `/api/auth/sso`. Redirect to Keycloak with `code_challenge_method=S256`. Accept `next` only as a path on the frontend origin.
- `GET /callback`:
  1. Check `state` against the cookie.
  2. Exchange the code at `KEYCLOAK_INTERNAL_URL/realms/afs/protocol/openid-connect/token`, with the client secret and the verifier.
  3. Verify the ID token with `jose` against the realm keys: RS256 only, issuer `KEYCLOAK_PUBLIC_URL/realms/afs`, audience `biocap`, and the nonce.
  4. Call `GET {AFS_COMMAND_CENTER_URL}/api/internal/access?application=biocap&subject=<sub>` with `X-Service-Token: AFS_SERVICE_TOKEN_BIOCAP`. Deny unless `allowed` is true and `external_role` is set.
  5. Find the user by `keycloakSub`. If none, link the account with the same verified email, only when it has no `keycloakSub`. Do not create accounts here.
  6. Refuse inactive accounts.
  7. Set `role` to the central `external_role`. Command Center is the source of truth.
  8. Issue the backend's own JWT with the pinned algorithm, through the same function the password login uses.
  9. Set the tokens as cookies (Step 4), and redirect to the frontend origin.

**Session lifetime.** The backend issues stateless tokens, so revocation waits for the token to expire. For SSO-issued tokens, use a short lifetime (for example 8 hours), and have `/api/auth/refresh` repeat the access check before issuing a new token. Without that check, a revoked employee could keep access for up to 7 days.

## Step 4: Frontend (Biocap Rwanda repository)

- **Login**: add **Sign in with AFS SSO**, linking to the backend's `/api/auth/sso/login?next=/…`. Keep the password form for accounts that have a password.
- **Tokens**: the backend sets the access and refresh tokens as `HttpOnly`, `Secure`, `SameSite=Lax` cookies. The frontend stops reading them. This is the most important frontend change, because script injection can currently steal the tokens.
- **Roles**: the frontend may still read the role for display, but every guard must depend on the backend's answer, not on the cookie.
- **Logout**: call the backend's logout, which also ends the Keycloak session.
- **Dead code**: delete `src/lib/data.ts`.

## Step 5: Tests

- An assigned employee signs in with the correct role; an unassigned one is refused with a message.
- A revoked assignment blocks the next sign-in and the next token refresh.
- A tampered state cookie, a replayed code, a wrong audience and a token signed with another algorithm are all refused.
- Tokens are not readable from JavaScript after the change (check in the browser console).
- The password login still works for password accounts, and SSO-only accounts cannot use it.

## Open questions

1. Confirm the frontend and backend pairing against the deployed addresses.
2. Are the consent and comment routes meant to be open to any signed-in user?
3. Should SSO users keep a local password as a fallback?
