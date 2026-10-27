# Integration guides for the AFS applications

Audience: the administrator who registers each application, and the developers who connect it to AFS SSO. Read [integration-audit.md](../integration-audit.md) for what each application's code looks like today, and [security.md](../security.md) for the rules the integrations must keep.

## Applications

Each product is one application in Command Center. A product with a frontend and a backend is registered once, and its backend runs the sign-in.

| Application (slug) | Made of | Guide | Status |
| --- | --- | --- | --- |
| ImageUnit (`imageunit`) | One repository: web app and API | [imageunit.md](imageunit.md) | Pilot verified on the local stack; reference patch in `imageunit/` |
| Biocap (`biocap`) | Biocap Rwanda frontend (Next.js) and Biocap Dashboard backend (Express, MongoDB) | [biocap.md](biocap.md) | Ready to build, after the security fixes in its guide |
| Procurement (`procurement`) | Procurement FN portal (React) and Procurement BN backend (NestJS, PostgreSQL) | [procurement.md](procurement.md) | Ready to build, after the backend gaps in its guide |

The Biocap and Procurement pairings were inferred from the code: the frontend calls the routes the backend serves, with the same role names. The team should confirm each pairing against the deployed addresses.

## What AFS provides for every application

These are already in this repository and are set up per application:

| Item | Where |
| --- | --- |
| Application record, launch URL and enable flag | Command Center admin console (Applications) |
| Application roles (seeded from each application's own role values) | `backend/migrations/003_imageunit_roles.sql`, `004_app_roles.sql` |
| Keycloak client, PKCE required, created only when its secret is set | `keycloak/bootstrap.py`, `APPLICATIONS` |
| Access check: `GET /api/internal/access?application=<slug>&subject=<keycloak sub>` with `X-Service-Token` | `backend/app/internal.py` |
| One service token per application (`AFS_SERVICE_TOKEN_<APP>`) | `.env.example` |
| `/api/internal/` blocked from browsers | `frontend/nginx.conf` |

Application slugs and their client settings. Each client belongs to the product's backend, which runs the sign-in:

| Slug | Keycloak client | Env prefix | Service token |
| --- | --- | --- | --- |
| `imageunit` | `imageunit` | `IMAGEUNIT_` | `AFS_SERVICE_TOKEN_IMAGEUNIT` |
| `biocap` | `biocap` | `BIOCAP_` | `AFS_SERVICE_TOKEN_BIOCAP` |
| `procurement` | `procurement` | `PROCUREMENT_` | `AFS_SERVICE_TOKEN_PROCUREMENT` |

## Reporting failures

An application can report a sync or revocation failure it hits, so administrators see it in the **Integrations** tab:

```
POST /api/internal/events?application=<slug>      with X-Service-Token
{"kind": "sync_failure" | "revocation_failure" | "info", "outcome": "failure" | "success", "detail": "..."}
```

Report a revocation failure when removing a user from the application's own records fails. Administrators then see that access may still exist there.

## The rules every integration follows

1. **Sign-in proves identity; Command Center grants access.** After Keycloak verifies the ID token, the application asks Command Center whether the employee has an active assignment, and which role applies. No assignment, no access. An assignment without a role is refused.
2. **Keycloak identities are linked explicitly.** Store the Keycloak `sub` on the application's user record. Link an existing account only when Keycloak reports the email as verified and the account is not yet linked. Do not create accounts at sign-in unless the team decides otherwise.
3. **Roles come from Command Center.** Set the application's role from the central role on every SSO sign-in, and enforce permissions in the application's backend.
4. **Use PKCE and validate the ID token** (signature, issuer, audience, expiry, nonce). Keep client secrets on the server.
5. **Redirects use allow-lists.** Accept only the application's own paths after sign-in.
6. **Know the revocation bound.** New sign-ins are refused as soon as access is removed. Sessions already issued stay valid until their lifetime ends, so keep application sessions short. See [security.md](../security.md).
7. **Keep the original login** unless the team decides to remove it, and test it as a regression.

## Order of work for any application

1. Command Center: confirm the application, set its launch URL to the frontend, and check its roles (already seeded).
2. AFS `.env`: set the product's client secret, redirect URI (its backend callback), origin (its frontend), and service token. Run `docker compose up -d` so Keycloak creates the client, or re-run `keycloak-init`.
3. Backend: implement the sign-in flow, the access check and the identity link, as described in the product's guide. The frontend only starts the sign-in and receives the result.
4. Test: an assigned employee signs in; an unassigned one is refused; revoking access blocks the next sign-in; the original login still works.
5. Enable the application in Command Center only after step 4 passes.
