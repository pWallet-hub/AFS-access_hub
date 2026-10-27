# Integrating an application with AFS Command Center

Audience: developers adding an application to the AFS SSO platform. Read [security.md](security.md) first.

## Two kinds of integration

- **Registration only** (`integration_type = none`). The application appears in the registry and its launch link can be used. It keeps its own login and does not yet trust Command Center. Use this for the first step, or for applications that cannot accept SSO yet. Employees still need the application's own accounts.
- **OIDC SSO** (`integration_type = oidc`). The application delegates sign-in to Keycloak and maps the verified identity to its own user. This is the state the spec calls "SSO-integrated". An application must not be described this way until it has been verified end to end (Task.md section 13).

## Step 1: register the application

A super administrator creates the application in the Command Center admin area (**Applications & roles** → **Register application**), or the seeded record is updated. Provide:

- `slug`: lowercase, digits and hyphens, 2 to 41 characters.
- `launch_url`: the verified absolute `https` URL of the application.
- `integration_type`: `none` or `oidc`.
- `oidc_client_id`: the Keycloak client the application will use (client secrets are never stored here).

The application stays disabled until its launch URL is set and an administrator enables it.

## Step 2: define application roles

Under **Application-specific roles**, add one row per role the application actually enforces:

- **Central role** (`name`): the name shown to administrators, for example `viewer`, `approver`.
- **Role in application** (`external_role`): the exact role name the application recognises internally.

Only map roles that the application's own backend enforces. A role in Command Center grants nothing by itself.

## Step 3: configure the OIDC client (OIDC integrations)

Add the application's client to the Keycloak realm in `keycloak/bootstrap.py`, following the pattern used for `afs-command-center`:

- Confidential client, with the secret supplied from the environment (never written into the repository).
- Redirect URI: the application's own callback URL only.
- Standard authorization code flow only. Direct grants disabled.
- Web origins: the application's origin only.

## Step 4: validate tokens in the application backend

The application's backend must:

1. Validate the ID token signature (from the realm JWKS), issuer, audience (its own client ID), expiry, and nonce.
2. Resolve the Keycloak `sub` to a local account through an explicit identity link table. Never link by email alone.
3. Create or link the account according to the application's provisioning policy. Unknown identities are not granted access just because they signed in.
4. **Check Command Center assignments.** Access is granted only if the employee has an active assignment. Re-check on every request, not only at sign-in, so revocation takes effect. The check goes through an internal endpoint that the application's backend calls with a service credential, or through a signed assignment claim, whichever the application's architecture supports. Decide this during the pilot and record the decision in the audit document.
5. Map the assignment's role to its own permissions, using its own authorization rules.
6. Establish its own session. Do not reuse the Keycloak token as the application session.
7. Validate redirect targets after login. Accept only relative paths on the application's own origin.

## Step 5: test before claiming integration

Automated tests must cover (Task.md SEC-03):

- Valid sign-in and session creation.
- Invalid, expired, or wrongly audienced tokens.
- Missing assignment, and revoked assignment.
- Account linking, including the case where the email matches but the identity is not linked.
- Role enforcement on protected endpoints, not only in the frontend.
- Direct URL access and callback handling.
- The application's original login, as a regression test.

## Adding a new application later

For an ordinary application with the same stack, steps 1 and 2 are configuration only and need no code changes to the Command Center portal. A different framework or authentication model needs a new adapter that follows steps 3 to 7.
