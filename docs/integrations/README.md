# Integration guides for the AFS applications

Audience: the administrator who registers each application, and the developers who connect it to AFS SSO. Read [integration-audit.md](../integration-audit.md) for what each application's code looks like today, and [security.md](../security.md) for the rules the integrations must keep.

## Status

| Application | Guide | Status | What blocks it |
| --- | --- | --- | --- |
| ImageUnit | [imageunit.md](imageunit.md) | Pilot verified on the local stack (reference patch in `imageunit/`) | Production deployment and the decisions listed in its guide |
| Procurement BN (backend) and Procurement FN (portal) | [procurement-bn.md](procurement-bn.md), [procurement-fn.md](procurement-fn.md) | Ready to build. The BN backend runs the sign-in for FN | BN backend changes; FN token handling |
| Biocap Dashboard Backend | [biocap-dashboard.md](biocap-dashboard.md) | Ready to build, after the security fixes in its guide | Dashboard frontend URL is not known |
| Biocap Rwanda | [biocap-rwanda.md](biocap-rwanda.md) | Blocked | Its backend repository is not accessible to us |

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

Application slugs and their client settings:

| Slug | Keycloak client | Env prefix | Service token |
| --- | --- | --- | --- |
| `imageunit` | `imageunit` | `IMAGEUNIT_` | `AFS_SERVICE_TOKEN_IMAGEUNIT` |
| `biocap-rwanda` | `biocap-rwanda` | `BIOCAP_RWANDA_` | `AFS_SERVICE_TOKEN_BIOCAP_RWANDA` |
| `biocap-dashboard` | `biocap-dashboard` | `BIOCAP_DASHBOARD_` | `AFS_SERVICE_TOKEN_BIOCAP_DASHBOARD` |
| `procurement-bn` | `procurement-bn` | `PROCUREMENT_BN_` | `AFS_SERVICE_TOKEN_PROCUREMENT_BN` |
| `procurement-fn` | none (uses the BN sign-in) | none | `AFS_SERVICE_TOKEN_PROCUREMENT_FN` |

## The rules every integration follows

1. **Sign-in proves identity; Command Center grants access.** After Keycloak verifies the ID token, the application asks Command Center whether the employee has an active assignment, and which role applies. No assignment, no access. An assignment without a role is refused.
2. **Keycloak identities are linked explicitly.** Store the Keycloak `sub` on the application's user record. Link an existing account only when Keycloak reports the email as verified and the account is not yet linked. Do not create accounts at sign-in unless the team decides otherwise.
3. **Roles come from Command Center.** Set the application's role from the central role on every SSO sign-in, and enforce permissions in the application's backend.
4. **Use PKCE and validate the ID token** (signature, issuer, audience, expiry, nonce). Keep client secrets on the server.
5. **Redirects use allow-lists.** Accept only the application's own paths after sign-in.
6. **Know the revocation bound.** New sign-ins are refused as soon as access is removed. Sessions already issued stay valid until their lifetime ends, so keep application sessions short. See [security.md](../security.md).
7. **Keep the original login** unless the team decides to remove it, and test it as a regression.

## Order of work for any application

1. Command Center: register or confirm the application, set its launch URL, and create its roles (already seeded for the five applications).
2. AFS `.env`: set the application's client secret, redirect URI and origin, and its service token. Run `docker compose up -d` so Keycloak creates the client, or re-run `keycloak-init`.
3. Application backend: implement the sign-in flow, the access check and the identity link, as described in the application's guide.
4. Test: an assigned employee signs in; an unassigned one is refused; revoking access blocks the next sign-in; the original login still works.
5. Enable the application in Command Center only after step 4 passes.
