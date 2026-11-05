# Deployment: how each application connects to AFS Command Center

Audience: the administrator who deploys the AFS stack and registers each application, and the developers who deploy an application's backend and frontend.

This document covers deployment only. Sign-in and access rules are in [README.md](README.md), and each application's own flow is in its guide: [imageunit.md](imageunit.md), [biocap.md](biocap.md), [procurement.md](procurement.md). Production hardening is in [../production-readiness.md](../production-readiness.md).

## 1. How the pieces fit

```
Browser ──► Application frontend ──► Application backend ──► Keycloak (sso.<domain>)  sign-in, token check
                                            │
                                            └──► Command Center backend (internal)   /api/internal/access
                                                 X-Service-Token: AFS_SERVICE_TOKEN_<APP>
```

- **Keycloak** (`sso.<domain>`) is public. It verifies who the employee is.
- **Command Center** decides whether the employee may use the application, and with which role. It is reached only from the application's backend, over the internal network. Its `/api/internal/` path is blocked from browsers by nginx, and it must not be routed through the public site.
- **Each application backend** holds its own Keycloak client secret and its own service token. A service token works only for its own application.
- Browsers never receive a service token or a Keycloak client secret.

## 2. What is deployed today

The production stack is `docker-compose.prod.yml`, deployed through Coolify as one Docker Compose project. It contains `db`, `db-init`, `keycloak`, `keycloak-init`, `backend` (Command Center) and `frontend` (Command Center's web app). Coolify routes:

| Service | Public address | Port |
| --- | --- | --- |
| Command Center frontend | `accesshub.afs-rwanda.org` | 80 |
| Keycloak | `sso.afs-rwanda.org` | 8080 |

The stack's `.env.production` lists which variables are set. Values come from Coolify's environment settings, not from a file in the repository.

Application deployments are separate. Each application keeps its own repository and compose file, and is listed below with its public address and how it reaches Command Center.

## 3. Per-application deployment

Addresses marked `example.org` are placeholders. Replace them with the real addresses before deploying. Biocap's frontend address is taken from its guide (`biocap-rwanda.afs-rwanda.org`).

### 3.1 Summary

| Application (slug) | Public frontend | Public backend (callback) | Keycloak client | Service token | In `keycloak-init` (prod compose)? | In Command Center backend env (prod compose)? | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ImageUnit (`imageunit`) | `imageunit.example.org` | `api.imageunit.example.org` | `imageunit` | `AFS_SERVICE_TOKEN_IMAGEUNIT` | Yes | Yes | Pilot verified locally; production rollout open |
| Biocap (`biocap`) | `biocap-rwanda.afs-rwanda.org` | `api.biocap.example.org` | `biocap` | `AFS_SERVICE_TOKEN_BIOCAP` | Yes | Yes | Ready to build, after its security fixes |
| Procurement (`procurement`) | `procurement.example.org` | `api.procurement.example.org` | `procurement` | `AFS_SERVICE_TOKEN_PROCUREMENT` | Yes | Yes | Ready to build, after its backend gaps |
| Marketing Mailer (`marketing`) | `marketing.example.org` | `marketing-api.example.org` | `marketing` | `AFS_SERVICE_TOKEN_MARKETING` | **No, must add** | **No, must add** | Backend skeleton built; not registered |

### 3.2 ImageUnit

- **Coolify:** deploy its web app and API as two services in its own project. Give the API the callback domain and the web app its own domain.
- **Command Center:** open **Applications → ImageUnit**, set the launch URL to the web app's public address, and enable it. An application cannot be enabled without a launch URL.
- **Keycloak client:** created by `keycloak-init` when `IMAGEUNIT_OIDC_CLIENT_SECRET` is set. Its redirect URI is `IMAGEUNIT_REDIRECT_URI`, which must be the API's callback and must match exactly what ImageUnit sends.
- **Environment, Command Center:** `IMAGEUNIT_OIDC_CLIENT_ID`, `IMAGEUNIT_OIDC_CLIENT_SECRET`, `IMAGEUNIT_REDIRECT_URI`, `IMAGEUNIT_ORIGIN`, `AFS_SERVICE_TOKEN_IMAGEUNIT`.
- **Environment, ImageUnit API:** `AFS_OIDC_ISSUER` (the public realm URL), `AFS_COMMAND_CENTER_URL` (internal, for example `http://backend:8000`), `AFS_SERVICE_TOKEN` (the same value as `AFS_SERVICE_TOKEN_IMAGEUNIT`), `PUBLIC_WEB_URL`.
- **Network:** the ImageUnit API must reach Command Center's backend on the internal network. See section 4.

### 3.3 Biocap

- **Coolify:** deploy the Biocap frontend (Next.js) and the Biocap Dashboard backend (Express, MongoDB, Redis) as separate services.
- **Command Center:** open **Applications → Biocap**, set the launch URL to the frontend, and keep it disabled until the SSO steps pass.
- **Keycloak client:** `biocap`, created when `BIOCAP_OIDC_CLIENT_SECRET` is set. `BIOCAP_REDIRECT_URI` is the Biocap backend's callback (`/api/auth/sso/callback`). `BIOCAP_ORIGIN` is the frontend address.
- **Environment, Command Center:** `BIOCAP_OIDC_CLIENT_ID`, `BIOCAP_OIDC_CLIENT_SECRET`, `BIOCAP_REDIRECT_URI`, `BIOCAP_ORIGIN`, `AFS_SERVICE_TOKEN_BIOCAP`. Also `BIOCAP_DB_URI`, a read-only connection used for the usage dashboard.
- **Environment, Biocap backend:** `AFS_COMMAND_CENTER_URL`, `AFS_SERVICE_TOKEN_BIOCAP`, `KEYCLOAK_PUBLIC_URL`, `KEYCLOAK_INTERNAL_URL`, and the client settings for `biocap`.
- **Blocker:** the security fixes in [biocap.md](biocap.md) (Step 0) must be done before this goes to production. They include a hardcoded database URI in the repository and unpinned JWT verification.

### 3.4 Procurement

- **Coolify:** deploy the BN backend (NestJS, PostgreSQL) and the FN portal (React) as separate services.
- **Command Center:** open **Applications → Procurement**, set the launch URL to the portal, and enable it.
- **Keycloak client:** `procurement`, created when `PROCUREMENT_OIDC_CLIENT_SECRET` is set. `PROCUREMENT_REDIRECT_URI` is the BN callback (`/api/v1/auth/sso/callback`). `PROCUREMENT_ORIGIN` is the portal address.
- **Environment, Command Center:** `PROCUREMENT_OIDC_CLIENT_ID`, `PROCUREMENT_OIDC_CLIENT_SECRET`, `PROCUREMENT_REDIRECT_URI`, `PROCUREMENT_ORIGIN`, `AFS_SERVICE_TOKEN_PROCUREMENT`.
- **Environment, BN backend:** `AFS_COMMAND_CENTER_URL`, `AFS_SERVICE_TOKEN_PROCUREMENT`, and the client settings for `procurement`.
- **Blocker:** the backend gaps listed in [procurement.md](procurement.md) must be closed first.

### 3.5 Marketing Mailer

Built in `/mnt/d/cc/Marketing-mailer`, and not yet registered anywhere.

- **Coolify:** a separate project for the marketing backend and frontend. It has its own PostgreSQL database. It is not part of the AFS compose stack, and it is not in `keycloak-init`.
- **Command Center:** add an application with slug `marketing`, launch URL set to the marketing frontend. Seed its roles, `admin` and `member`, in a migration in this repository, as the other applications do.
- **Keycloak client:** `marketing`. It needs `MARKETING_OIDC_CLIENT_SECRET`, `MARKETING_REDIRECT_URI` (`https://marketing-api.example.org/api/auth/callback`) and `MARKETING_ORIGIN`. These are read by `keycloak-init`, so they must be added to `keycloak-init`'s environment in `docker-compose.prod.yml`, and `keycloak/bootstrap.py` needs the entry `("marketing", "Marketing Mailer", "MARKETING")` in `APPLICATIONS`.
- **Environment, Command Center:** `MARKETING_OIDC_CLIENT_ID`, `MARKETING_OIDC_CLIENT_SECRET`, `MARKETING_REDIRECT_URI`, `MARKETING_ORIGIN`, `AFS_SERVICE_TOKEN_MARKETING`. Also add `AFS_SERVICE_TOKEN_MARKETING` to the Command Center backend's environment in `docker-compose.prod.yml`. Without it, the hub rejects every marketing access check.
- **Environment, marketing backend:** `DATABASE_URL`, `SESSION_SECRET` (at least 32 characters), `KEYCLOAK_PUBLIC_URL`, `KEYCLOAK_INTERNAL_URL`, `KEYCLOAK_REALM`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `PUBLIC_BASE_URL`, `FRONTEND_ORIGIN`, `COMMAND_CENTER_URL`, `AFS_SERVICE_TOKEN_MARKETING`. The template is `backend/.env.example`.
- **Email:** Resend. Marketing mail uses a separate sending subdomain from the hub's transactional mail, so that bulk sends do not affect password resets. The subdomain is an open item in `DESIGN.md`.
- **Network:** section 4 applies. This is the decision that is still open for this app.

## 4. The internal network: the open decision

The hub's `/api/internal/access` is reachable only from services that can reach `backend:8000` on the Docker network. Coolify runs each project on its own network. An application in a separate project cannot reach `http://backend:8000` by default.

Options for each application backend:

| Option | How | Verdict |
| --- | --- | --- |
| A. Shared Coolify network | Attach the application's backend to the network that the Command Center backend is on, and set `AFS_COMMAND_CENTER_URL` to `http://backend:8000` | **Recommended.** Keeps the internal endpoint private |
| B. Put the application in the AFS compose stack | Add the service to `docker-compose.prod.yml` | Works, but couples every application's release to the hub's |
| C. Call Command Center's public address | Set `AFS_COMMAND_CENTER_URL` to a public URL | **Not allowed.** nginx blocks `/api/internal/` from browsers, and the endpoint must not be public |

Decide the option once, and apply it to all four applications. Confirm that the hub's backend container is attached to the shared network before the first deployment.

## 5. Order of deployment

Follow this order for each application. Do not enable an application in Command Center until its sign-in has been tested.

1. **Secrets.** Generate a client secret and a service token for the application (at least 32 characters; `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`). Add them to the Coolify environment for Command Center and the application. Do not commit them.
2. **Keycloak client.** Add the application's variables to `.env` and the `keycloak-init` environment, then re-run `keycloak-init` (or redeploy the stack). Check the client exists in the `afs` realm, with PKCE required.
3. **Command Center application.** In the admin console, add or check the application, set its launch URL, and check its roles. Leave it disabled.
4. **Application backend.** Deploy with its environment. Check `/api/health` (or the equivalent) responds, and that the backend can reach `AFS_COMMAND_CENTER_URL`.
5. **Application frontend.** Deploy with its origin, and check it loads over HTTPS.
6. **Test the sign-in.** An assigned employee signs in and gets the right role. An unassigned employee is refused. Revoking access blocks the next sign-in and the next request. The application's own login still works, if it keeps one.
7. **Enable.** In Command Center, enable the application.

## 6. Rotating secrets

- **Service token:** it is shared by two services. Change it in both `.env` files (Command Center and the application) at the same time, then restart both. A mismatch makes every access check fail, and Command Center records a `token_rejected` event for the application.
- **Client secret:** change it in Keycloak and in the application's environment together, then restart `keycloak-init` and the application backend.
- **Session secret:** changing it signs every user out of that application.

## 7. Checks before go-live

- Each application's callback address matches its Keycloak client's redirect URI exactly, including scheme, host and path.
- Each application's `*_ORIGIN` matches the address users see in the browser.
- Each application's service token exists in Command Center and in the application, and no two applications share a token.
- The internal endpoint is not reachable from the public address (`curl https://accesshub.afs-rwanda.org/api/internal/access` returns the frontend's response or an error, not an access decision).
- Cookies are `Secure` and `HttpOnly` (for Biocap and Procurement, see their guides).
- Command Center's **Integrations** tab shows no unexpected failures after the first sign-ins.

## 8. Gaps found while writing this

These are the differences between this document and the current production configuration:

1. `docker-compose.prod.yml` has no `MARKETING_*` entries in `keycloak-init`, and no `AFS_SERVICE_TOKEN_MARKETING` in the Command Center backend. Add them before the marketing application is registered.
2. `keycloak/bootstrap.py` `APPLICATIONS` has no `marketing` entry.
3. There is no migration that seeds the `marketing` application and its roles.
4. The `.env.production` file has no `MARKETING_*` names.
5. The network option (section 4) is not chosen. Each application's backend needs it to reach Command Center.
