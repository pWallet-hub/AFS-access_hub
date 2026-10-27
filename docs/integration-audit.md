# Phase 1: integration audit of the five AFS applications

Audience: the AFS engineering team, to choose the pilot and plan the integrations. Based on read-only shallow clones at the time of the audit. Nothing was run, modified or pushed in those repositories. Where a point could not be verified from the repository it is marked as unverified.

## Summary

**Correction (product pairing):** the five repositories form three products. Biocap Rwanda (frontend) and Biocap Dashboard Backend are one product: the frontend calls the backend's routes and uses the same six roles. Procurement FN (frontend) and Procurement BN (backend) are one product: the frontend calls the backend's `/api/v1` prefix. ImageUnit is one repository with its web app and API. Integrations are therefore planned per product, with the backend running the sign-in. The earlier note that the Biocap backend was out of reach does not apply: it is the Biocap Dashboard repository, which was audited. Per-product guides are in `docs/integrations/`.


| Application | Repository(ies) | Stack | Login today | Authorization enforced | SSO fit |
| --- | --- | --- | --- | --- | --- |
| Biocap Rwanda | `felixaveiro/Biocap-rwanda` | Next.js 15 (frontend only) | Bearer JWT from external API | Client-side only here; backend not visible | Depends on backend. Put SSO in the API, or in a Next.js server layer |
| Biocap Dashboard | `pWallet-hub/Biocap-dashboard-backend` | Express 5, MongoDB, Redis | Local password (bcrypt) + JWT | Route-level `protect`/`authorize`, single `role` string | Good: `protect` is one choke point, but needs JWKS validation |
| Procurement BN | `pWallet-hub/Procurrement-bn` | NestJS 10, PostgreSQL 16, Redis/BullMQ | Local password (Argon2id) + JWT | `@Perm` permission codes, DB-backed roles | Good: permission model is rich and in the backend |
| Procurement FN | `pWallet-hub/Procurrement-fn` | React 18 + Vite SPA (frontend only) | Bearer JWT from `/auth/login` (backend repo is BN) | Client-side only here | Depends on BN backend |
| ImageUnit | `IshKevin/ImageUnit` | Fastify 5, Drizzle, PostgreSQL, Next.js 16 frontend | Local password (argon2id) + server session cookie | Backend guards and ownership checks | Strongest fit for a pilot (see below) |

## Per-application findings

### Biocap Rwanda (`felixaveiro/Biocap-rwanda`)
- Next.js 15 App Router frontend, Dockerfile on `node:22-alpine`. The README's "Next 14" is stale.
- Login posts to an external API at `biocapapi.afs-rwanda.org`. The backend is **not in this repository**.
- Tokens (`biocap_token`, `biocap_refresh_token`, `biocap_role`) are stored in **JavaScript-readable cookies** (`src/lib/auth/cookies.ts`). Any XSS can read them.
- Role checks exist only in the browser (`useSession` in `src/hooks/useSession.ts`). There is no `middleware.ts`.
- Login redirects by role to fixed paths; there is no `next`/return-to handling, so no open-redirect surface here.
- `src/lib/data.ts` contains seed users with localStorage, but nothing imports it. It is dead code and should be removed.

### Biocap Dashboard Backend (`pWallet-hub/Biocap-dashboard-backend`)
- Express 5, Mongoose 9 on MongoDB, Redis for rate limits. CommonJS, Node 22, runs as non-root.
- Local login with bcryptjs. Self-registration exists, and admin self-assignment is blocked. Passwords expire after 90 days.
- Access token: HS256 JWT, 7-day expiry, secret `JWT_SECRET`. **The algorithm is not pinned and there are no `iss`/`aud` checks.**
- Refresh token is stored bcrypt-hashed, has no expiry and no rotation. Logout clears only the refresh hash, so issued access tokens stay valid for up to 7 days.
- `protect` re-reads the user from the database on each request, so deactivation takes effect at once.
- **Security issues to fix now:**
  - `config/db.js` has a **hardcoded fallback MongoDB Atlas URI including credentials**. Rotate the credentials and remove the fallback.
  - `scripts/seedAdmin.js` falls back to a development JWT secret.
  - `routes/auth.js` `/api/auth/refresh` takes `userId` from the request body.
- `PATCH /api/blogs/:id/consent` and `POST /api/blogs/:id/comments` use `protect` with no role check at route level. Whether the controllers check roles or ownership is unverified.
- `controllers/authController.js` is dead code duplicating the routes.

### Procurement BN (`pWallet-hub/Procurrement-bn`)
- NestJS 10 on Node 22, PostgreSQL 16 (raw `pg`, no ORM), Redis + BullMQ worker, S3-compatible storage, Resend email. Migrations in `db/migrations/`.
- Local login with **Argon2id**. Lockout after 5 failures for 15 minutes. Optional TOTP.
- Access token: HS256 JWT, 15 minutes. Refresh token: random 256-bit, stored as SHA-256, rotated on use, 7 days. `AuthGuard` re-reads the user on each request. Logout revokes the refresh token.
- Roles in `users`, `roles`, `user_roles`, `role_permissions`. Role codes include `requesting_staff`, `accountant`, `director_comms`, `director_dept`, `pi`, `cfm`, `market_verifier`, `superior`, `admin`. `GLOBAL_ROLES` (pi, accountant, cfm, admin) see all cases.
- Endpoints use `@Perm(...)` (for example `case.create`, `payment.record`, `budget.manage`, `admin.manage`).
- **Gaps to check:**
  - `TOTP_REQUIRED` does not force enrolment. Only users who already have TOTP enabled are challenged.
  - A method-level `@Perm` replaces the class-level `admin.manage` guard on the budget-line routes (`admin.controller.ts`, lines 93–95). Only admin holds `budget.manage` today, so it is not exploitable now, but it is fragile.
  - Several reads (lookups, case list) have no `@Perm`, so any signed-in user can call them.
  - The rate limiter is in-memory, per process.
  - The team's own handoff notes say demo logins worked on the live API at one point; the current state is unverified.

### Procurement FN (`pWallet-hub/Procurrement-fn`)
- React 18, TypeScript, Vite SPA with React Router and TanStack Query. nginx serves it and proxies `/api` to the BN backend. **There is no backend in this repository.**
- Login via `/auth/login` with optional TOTP. Bearer access and refresh tokens are in **localStorage** (`afs.access_token`, `afs.refresh_token`). Any XSS can read them.
- Route guards (`/audit`, `/reports`, `/admin`) and the `can('case.advance_arrangement')` check are UI-only. The financial endpoints must be checked in the BN backend.
- Some routes look unprotected at the client (`/cases/:id/purchase-file`, cancel, sign). Only the backend can confirm they check roles.
- The README documents shared development seed accounts and a development password.
- Public by design: `/sign/:token` (external signing) and `/verify/:documentId`.

### ImageUnit (`IshKevin/ImageUnit`)
- npm-workspaces monorepo. API: Fastify 5, Drizzle ORM on PostgreSQL, Redis/BullMQ worker, S3-compatible storage with presigned URLs, sharp/ffmpeg. Web: Next.js 16 / React 19, with rewrites that keep `/api` same-origin.
- Local login only, argon2id, 12+ character password policy, lockout after 5 failures for 15 minutes, per-IP rate limit. The admin is bootstrapped from the environment. No OAuth/OIDC code exists; the README and runbook state OAuth/2FA is not built.
- **Session:** opaque 32-byte token in an HttpOnly, SameSite=Lax cookie `iu_session`. Only the SHA-256 hash is stored. 7-day sliding expiry. Suspension is checked on every request. Logout deletes the row. Changing the password revokes other sessions. Origin checks apply to unsafe methods. This is the most robust session model of the five.
- Authorization is enforced in the backend: `guard.perm`/`guard.admin`, `loadManagedEvent` and `loadPhoto` ownership and membership checks, and admin-only permanent delete.
- To check: `POST /library/bulk` and `PATCH /photos/:id` use only `guard.user` and rely on internal scoping (scoping read, not tested). `/metrics` is open unless `METRICS_TOKEN` is set.

## Recommendation for the Phase 4 pilot

**ImageUnit** is the best pilot:

- Its server-side session cookie means an OIDC callback can establish the existing session. Tokens never reach the browser, and the existing guards, suspension check and audit keep working.
- Authorization is already enforced in the backend, which is what the integration must rely on.
- The Next.js rewrite keeps the browser same-origin, which simplifies the redirect URIs.

Conditions before the pilot starts:

1. `passwordHash` is `NOT NULL`, so SSO-only users need a schema change (nullable hash or a separate identity table).
2. Linking by email must require the Keycloak `email_verified` claim. Store the OIDC `sub` in a new column and link on that.
3. Disabling a user in Keycloak does not end existing ImageUnit sessions. Either add back-channel logout or use a short session lifetime for SSO sessions.
4. Decide whether password login stays as break-glass. Decide whether Keycloak groups or the ImageUnit admin UI are the source of truth for roles.

**Procurement BN** is the second candidate for a pilot (the most rigorous permission model), but its JWT model needs JWKS validation and a token-exchange endpoint. It also needs the TOTP gaps closed first, because the approval workflow depends on it.

**Biocap Rwanda and Procurement FN** are frontends whose real enforcement is in their backends, so their integration depends on the Biocap backend and Procurement BN respectively. The Biocap backend repository is not in scope of this audit.

## Immediate security actions (in those repositories, not in this one)

1. Biocap Dashboard Backend: rotate the MongoDB credentials that are hardcoded in `config/db.js`, and remove the fallback.
2. Biocap Dashboard Backend: pin the JWT algorithm and add `iss`/`aud` checks; make refresh tokens expire and rotate.
3. Biocap Rwanda and Procurement FN: stop storing tokens in JavaScript-readable storage. Either HttpOnly cookies set by a server, or a server-side session.
4. Procurement BN: make TOTP enrolment mandatory for roles that approve payments, and review the unguarded read routes.

## Unknowns the team must answer

- Does an AFS Keycloak realm exist for other systems, and are user emails identical across them?
- Who owns the backend repositories for Biocap Rwanda, and where is it hosted?
- Source of truth for roles: Keycloak groups, or each application's own admin UI?
- Should password login remain as a break-glass path in each application?
- Are website API keys and public galleries (ImageUnit) staying as they are?
- Are Procurement's external signing and verification routes intended to stay public?
