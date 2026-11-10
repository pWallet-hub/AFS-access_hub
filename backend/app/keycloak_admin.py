"""Keycloak accounts for employees added by an administrator.

Command Center signs in to Keycloak's admin API as its own client (client-credentials grant). The client's
service account holds only realm-management's user roles (granted by keycloak/bootstrap.py), so this module
can create and delete users and send Keycloak's account emails, and nothing else.

An invitation is Keycloak's "execute actions" email with the UPDATE_PASSWORD action: the employee follows the
link, chooses a password, and is sent back to Command Center to sign in. Keycloak sends it through the realm's
mail settings (Resend SMTP). Following the link proves the employee controls the address, which is why the
account is created with a verified email; it has no password until they choose one.
"""

import os

import httpx

KEYCLOAK_INTERNAL_URL = os.environ["KEYCLOAK_INTERNAL_URL"].rstrip("/")
KEYCLOAK_REALM = os.environ["KEYCLOAK_REALM"]
OIDC_CLIENT_ID = os.environ["OIDC_CLIENT_ID"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
PUBLIC_BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")

ADMIN_URL = f"{KEYCLOAK_INTERNAL_URL}/admin/realms/{KEYCLOAK_REALM}"
TOKEN_URL = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
# Where Keycloak's "Back to application" link points once the password is set. Must be a redirect URI of the client.
RETURN_URL = f"{PUBLIC_BASE_URL}/"
INVITE_LIFESPAN_SECONDS = 3 * 24 * 3600

# Outcomes of invite().
SENT = "sent"  # set-password email sent
HAS_PASSWORD = "has_password"  # the employee already has an AFS account with a password; nothing to set


class KeycloakError(Exception):
    """Keycloak refused or could not be reached. The message is safe to show an administrator."""


def _token():
    try:
        response = httpx.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "client_id": OIDC_CLIENT_ID, "client_secret": OIDC_CLIENT_SECRET},
            timeout=10,
        )
    except httpx.HTTPError as error:
        raise KeycloakError(f"the identity provider is unreachable ({type(error).__name__})") from error
    if response.status_code != 200:
        raise KeycloakError(
            f"Command Center could not authenticate to the identity provider (HTTP {response.status_code});"
            " redeploy so keycloak-init enables its service account"
        )
    return response.json()["access_token"]


def _call(token, method, path, **kwargs):
    try:
        return httpx.request(method, f"{ADMIN_URL}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=20, **kwargs)
    except httpx.HTTPError as error:
        raise KeycloakError(f"the identity provider is unreachable ({type(error).__name__})") from error


def _find(token, email):
    response = _call(token, "GET", "/users", params={"email": email, "exact": "true"})
    if response.status_code != 200:
        raise KeycloakError(f"looking up the account failed (HTTP {response.status_code})")
    matches = [u for u in response.json() if (u.get("email") or "").lower() == email]
    return matches[0] if matches else None


def _has_password(token, user_id):
    response = _call(token, "GET", f"/users/{user_id}/credentials")
    if response.status_code != 200:
        raise KeycloakError(f"reading the account's credentials failed (HTTP {response.status_code})")
    return any(c.get("type") == "password" for c in response.json())


def _names(display_name):
    parts = (display_name or "").split(None, 1)
    # Keycloak asks for missing names at first sign-in, so an empty name is acceptable.
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")


def invite(email, display_name=None):
    """Make sure `email` has a Keycloak account and, unless it already has a password, email a set-password link.

    Returns SENT or HAS_PASSWORD. Raises KeycloakError.
    """
    email = email.strip().lower()
    token = _token()
    user = _find(token, email)
    if user is None:
        first, last = _names(display_name)
        response = _call(token, "POST", "/users", json={
            "username": email,
            "email": email,
            "emailVerified": True,
            "enabled": True,
            "firstName": first,
            "lastName": last,
        })
        if response.status_code not in (201, 409):
            raise KeycloakError(f"creating the account failed (HTTP {response.status_code})")
        user = _find(token, email)
        if user is None:
            raise KeycloakError("the account was created but could not be found")
    elif _has_password(token, user["id"]):
        return HAS_PASSWORD

    response = _call(
        token,
        "PUT",
        f"/users/{user['id']}/execute-actions-email",
        params={"client_id": OIDC_CLIENT_ID, "redirect_uri": RETURN_URL, "lifespan": INVITE_LIFESPAN_SECONDS},
        json=["UPDATE_PASSWORD"],
    )
    if response.status_code != 204:
        if response.status_code == 500:
            raise KeycloakError("the identity provider could not send the email; check RESEND_API_KEY and RESEND_FROM")
        raise KeycloakError(f"sending the set-password email failed (HTTP {response.status_code})")
    return SENT


def remove_unused(email):
    """Delete the Keycloak account for `email` if it never got a password (an unanswered invitation).

    Accounts with a password are real AFS identities and are left alone. Returns True when an account was deleted.
    """
    email = email.strip().lower()
    token = _token()
    user = _find(token, email)
    if user is None or _has_password(token, user["id"]):
        return False
    response = _call(token, "DELETE", f"/users/{user['id']}")
    if response.status_code not in (204, 404):
        raise KeycloakError(f"deleting the account failed (HTTP {response.status_code})")
    return True
