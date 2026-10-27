# Security model and revocation policy

Audience: developers and security reviewers. This describes the behaviour implemented in `backend/app` and the decisions behind it.

## Identity

- Employees authenticate with Keycloak (OpenID Connect authorization code flow). Command Center never sees passwords.
- Accounts are keyed by Keycloak's stable `sub` claim. Email addresses are never used to decide that two identities are the same.
- A pre-provisioned account (created by an administrator before first sign-in) is linked only when Keycloak reports the email as **verified**. The link is written to the audit log as `identity.link`.
- The first super administrator is identified by the Keycloak realm role `afs-super-admin` (assigned by `keycloak/bootstrap.py`). The role is honoured only while no active super administrator exists, and that grant is audited as `platform_role.bootstrap`.

## Tokens and sessions

- The ID token is verified for signature (JWKS), issuer, audience, expiry, subject and nonce. The access token is verified for signature, issuer and `azp`.
- The browser holds only a signed session cookie (`HttpOnly`, `SameSite=lax`) containing the Keycloak subject. It holds no access token, refresh token or client secret.
- The session is cleared and re-created on every sign-in (session fixation protection).
- `SESSION_HTTPS_ONLY=true` must be set in production so the cookie is sent only over HTTPS.

## Authorization layers

1. **Platform roles** (`super_admin`, `access_admin`, `employee`) are stored on the user and checked on every admin endpoint. Only `super_admin` may change platform roles or register applications.
2. **Application assignments** decide which applications appear in the launcher. The launcher reads assignments on every request.
3. **Application-specific roles** are scoped to one application. A role row can only be attached to an assignment for the same application, enforced by a composite foreign key in the database.

The backend enforces all three. The frontend hides controls the user cannot use, but that is presentation only.

## Revocation policy

| Event | Effect in Command Center | Effect in a destination application |
| --- | --- | --- |
| Account disabled | The next request from any existing session fails with 401. New sign-ins are refused and audited as `auth.login` failures. | Must be enforced by the application's integration. Until an application is integrated, its own login is unaffected. |
| Assignment revoked | The application disappears from the launcher on the next request. | The application must re-check assignments on each request (integration contract, see [integration-guide.md](integration-guide.md)). Until then, existing application sessions remain valid until they expire. |
| Application disabled | The launcher stops showing it. It cannot be newly assigned. | The application's own sessions are unaffected, so the application should be taken offline if an immediate cutoff is needed. |
| Platform role reduced | The next request uses the new role. | Not applicable. |

Existing application sessions are bounded by each application's own session lifetime. Integrations must set that lifetime and document it. Forced logout across applications is not implemented in this release.

## Safeguards

- The last active super administrator cannot be disabled or demoted (`409`).
- Launch URLs must be absolute `http` or `https` URLs. An application cannot be enabled without one.
- Role mappings are validated against the destination application's role names, which the application itself defines.
- Refused administrative actions (including permission denials) are audited with outcome `failure`.
- The `audit_log` table is append-only: database triggers reject `UPDATE`, `DELETE` and `TRUNCATE`. The application's database role is still the database owner in the Compose setup, so production must use a separate, least-privilege application role and restrict ownership.
- Secrets never appear in audit details, logs or the browser. Audit details contain identifiers and before/after values of ordinary fields only.

## Email

Command Center notices go through Resend, and Keycloak's verification and reset mail goes through Resend's SMTP service. Notices carry no secrets, only plain text and a link to the portal. Dynamic text is escaped. The audit log records the recipient's account, not their address.

## Integration monitoring

Each application's access checks update its `last_access_*` timestamps. Failures it reports (`POST /api/internal/events`), and rejected service tokens, are stored in `integration_events` and shown in the console's **Integrations** tab. Status is `not_connected` before any check, `error` after a failure reported later than the last successful check, and `ok` otherwise. A business denial such as "not assigned" is an audited decision, not an integration error.

## Known limits in this release

- CSRF protection relies on `SameSite=lax` cookies and JSON request bodies. Add a CSRF token before exposing state-changing endpoints to untrusted origins.
- Keycloak runs in `start-dev` mode and the stack uses HTTP on localhost. Production requires `start --optimized`, HTTPS, and separate secrets management.
- Keycloak brute-force protection is on (5 failures, then a lock that grows up to 15 minutes), applied by `keycloak/bootstrap.py` on every run. Command Center itself has no separate sign-in rate limit.
- The identity provider's port is bound to 127.0.0.1 in this stack, so its admin console is not reachable from the network. Production must put it behind HTTPS with the same restriction.
- Keycloak does not send email in this stack, so email verification is not enforced at sign-in. Linking an identity to a pre-provisioned account still requires `email_verified` in the token.
- Pre-provisioned email linking depends on Keycloak's email-verification setting. Keep `verifyEmail` required for the AFS realm in production.
