# Integrating Biocap Rwanda with AFS SSO

Audience: the developers of `felixaveiro/Biocap-rwanda` (Next.js 15) and the team that owns its backend API.

**Status: blocked.** The repository contains only the Next.js frontend. Its API is an external service at `biocapapi.afs-rwanda.org`, and that backend repository is not accessible to us. Nothing in the frontend can enforce access, so the integration cannot be completed until the backend is available. This guide describes what that backend and the frontend must do once it is.

## Facts from the audit

- Next.js 15 App Router with React 19, Tailwind. Docker build on `node:22-alpine`. The README's "Next 14" is stale.
- Sign-in posts email or username and password to `/api/auth/login` on the external API. Register, forgot and reset password, and change password also live there.
- Access and refresh tokens are kept in **JavaScript-readable cookies** (`biocap_token`, `biocap_refresh_token`, `biocap_role`), 7 days, `SameSite=Lax`, no `HttpOnly`. Any script injection can read them.
- Role checks exist only in the browser (`useSession` and the layout guards). There is no `middleware.ts`, so server-rendered routes perform no checks.
- Logout clears the cookies and calls `/api/auth/logout`. Server-side invalidation cannot be verified from this repository.
- Six roles: `admin`, `communication_officer`, `editor`, `social_media_content_creator`, `media_correspondent`, `principal_investigator`. These are seeded by migration 004.
- `src/lib/data.ts` contains seed users kept in localStorage. Nothing imports it. Remove it.

## Step 1: Command Center (administrator)

1. Open **Biocap Rwanda** (slug `biocap-rwanda`) and set its launch URL to the frontend address. Keep it disabled until the backend work is done.
2. Confirm its six roles are listed, and assign employees with their current role.

## Step 2: AFS configuration (once the backend is available)

```bash
BIOCAP_RWANDA_OIDC_CLIENT_ID=biocap-rwanda
BIOCAP_RWANDA_OIDC_CLIENT_SECRET=<random>
BIOCAP_RWANDA_REDIRECT_URI=https://api.biocap.example.org/api/auth/sso/callback
BIOCAP_RWANDA_ORIGIN=https://biocap.example.org
AFS_SERVICE_TOKEN_BIOCAP_RWANDA=<random, shared with the Biocap backend only>
```

The redirect URI is the backend's callback address. Re-run `keycloak-init` (or `docker compose up -d`) to create the client.

## Step 3: Backend changes (owned by the Biocap backend team)

Implement the same pattern as [biocap-dashboard.md](biocap-dashboard.md), Steps 3–7:

- An SSO login and callback with PKCE, state and nonce checks, and ID-token verification (RS256, issuer, audience `biocap-rwanda`).
- The access check: `GET {AFS_COMMAND_CENTER_URL}/api/internal/access?application=biocap-rwanda&subject=<sub>` with `X-Service-Token: AFS_SERVICE_TOKEN_BIOCAP_RWANDA`. Deny unless `allowed` is true and `external_role` is set.
- Identity linking by a new `external_id` (Keycloak `sub`) column, with email linking only when the email is verified and the account is not yet linked.
- Roles set from Command Center on every sign-in.
- The backend must enforce roles on every protected route. The frontend guards are presentation only.
- Shorter session lifetime for SSO sessions, and a refresh path that repeats the access check.

## Step 4: Frontend changes (this repository)

- **Login page**: add **Sign in with AFS SSO**, linking to the backend's SSO login, with the frontend's return path as `next`.
- **Token storage**: move the tokens out of JavaScript-readable cookies. The backend should set them as `HttpOnly`, `Secure`, `SameSite=Lax` cookies, and the frontend should stop reading them. This is the most important frontend change, because any script injection can otherwise steal the tokens.
- **Remove dead code**: `src/lib/data.ts` and its seed users.
- **Logout**: also end the Keycloak session through the backend, so a shared computer does not stay signed in.
- Keep the password form for accounts that have a password.

## Step 5: Tests

- An assigned employee signs in with the correct role; an unassigned one is refused.
- A revoked assignment blocks the next sign-in.
- Tokens are not readable from JavaScript after the change (check in the browser console).
- Server-rendered routes refuse requests without a valid session.

## Blockers and unknowns

1. Who owns the backend repository, and can the team give access to it?
2. Does the backend verify token signatures and expiry, and how are its passwords hashed? Neither can be verified from this repository.
3. Is there an existing identity provider for the other AFS applications that Biocap should use? This guide assumes the AFS Keycloak realm.
4. Which roles should map from AFS identity groups, and who maintains that mapping?
