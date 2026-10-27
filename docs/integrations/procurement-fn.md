# Integrating Procurement FN with AFS SSO

Audience: the developers of the Procurement FN portal (`pWallet-hub/Procurrement-fn`, React 18, Vite) and of the Procurement BN backend that serves it.

Procurement FN has no backend of its own. Its API is Procurement BN, so the sign-in belongs in the BN backend, and FN only needs a button and a code exchange. The full BN-side design is in [procurement-bn.md](procurement-bn.md), Step 4; this guide covers the portal and the handover between the two.

## Facts from the audit

- React 18 + TypeScript + Vite single-page app, served by nginx, which proxies `/api` to the BN backend.
- Sign-in posts to `/auth/login` on the BN API. Bearer access and refresh tokens are kept in localStorage (`afs.access_token`, `afs.refresh_token`).
- Route guards (`/audit`, `/reports`, `/admin`) and the permission checks are client-side only. Server enforcement lives in BN.
- Some routes look unprotected from the client (`/cases/:id/purchase-file`, cancel, sign). Confirm each on the BN side.
- Public by design: `/sign/:token` and `/verify/:documentId`. Keep them public, and keep them out of SSO.
- The README documents shared development accounts and a development password. Remove them from any environment that is reachable.

## Step 1: Command Center (administrator)

1. Open **Procurement FN** in the admin console. Set the launch URL to the portal address (for example `https://procurement.example.org`). Enable it.
2. Assign each employee to **Procurement FN** with the matching role (`requesting_staff`, `accountant`, `director_comms`, `director_dept`, `pi`, `cfm`, `market_verifier`, `superior` or `admin`). Without an assignment, the BN backend refuses the sign-in.
3. Procurement FN has no Keycloak client. Do not create one. Its service token is enough.

## Step 2: AFS configuration

```bash
AFS_SERVICE_TOKEN_PROCUREMENT_FN=<random, shared with the BN backend only>
```

The BN backend calls the access check with this token for `application=procurement-fn`. The portal never sees it.

## Step 3: Handover from the BN backend to the portal

The portal signs in through `GET /api/v1/auth/sso/login?app=procurement-fn&next=/`, implemented in the BN backend. The flow then ends like this:

1. The BN callback validates Keycloak's tokens and the access check, then creates a one-time code.
2. The browser is redirected to `{PORTAL_ORIGIN}/sso/complete?code=<code>`.
3. The portal `POST`s the code to `/api/v1/auth/sso/exchange` and receives the same access and refresh tokens the password login returns.

Rules for the handover:

- `PORTAL_ORIGIN` is an allow-list value. Redirects to any other origin are refused.
- The code is valid for 60 seconds and works once.
- Tokens never appear in a URL. Only the one-time code does.

## Step 4: Portal changes (this repository)

- **Login page**: add **Sign in with AFS SSO**. It links to the BN login endpoint with `app=procurement-fn`. Keep the existing login form for users without AFS access, if the team keeps local accounts.
- **New route `/sso/complete`**: read `code`, post it to the exchange endpoint, store the tokens exactly as the password login does, then navigate to the requested page. On failure, show the message and link back to login.
- **Logout**: call the existing `/auth/logout`. Also end the Keycloak session with the BN logout endpoint, so a shared computer does not stay signed in.
- Read `src/auth/AuthProvider.tsx` and `src/api/client.ts`; the refresh logic already handles 401 responses, and SSO tokens use the same path.

## Step 5: Protect the financial operations

Client-side checks are not a control. Before the pilot, check each of these in the BN backend:

- `POST` to `/cases/:id/advance` (advance payment) requires `case.advance_arrangement` and the payment stage.
- Cancelling a case allows only admin, accountant, or the requester.
- Document signing verifies the signer is the assigned user or holds the slot's role.
- `/audit` and `/reports` routes require their roles on the server, not only in the portal.

If any of these fail the check, fix the BN backend before enabling SSO for finance roles.

## Step 6: Tests

- An assigned employee signs in and reaches the requested page; an unassigned one sees the refusal.
- The one-time code cannot be reused, and it expires after 60 seconds.
- A `next` or origin outside the allow-list is refused.
- A user without a role cannot open `/admin`, `/audit` or `/reports`, and the API refuses the same calls directly (test with a token, not the UI).
- Public routes (`/sign/:token`, `/verify/:documentId`) still work without signing in.

## Known caveats

- The portal keeps tokens in localStorage, which any injected script can read. Switching to HttpOnly cookies set by the BN backend is the recommended follow-up, and it removes the need for the portal to hold tokens at all.
- A revoked employee keeps access until their current access token expires (15 minutes). The BN refresh endpoint must re-run the access check, or revocation will not reach the portal in time.

## Open questions

1. Should the local password login remain for portal users, or should SSO become the only login?
2. Which of the BN routes listed in Step 5 are intended to be open to any signed-in user?
