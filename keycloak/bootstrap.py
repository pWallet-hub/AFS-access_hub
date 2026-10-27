"""Idempotently configure the AFS realm, the Command Center OIDC client and the bootstrap account.

Runs as a one-shot container after Keycloak reports healthy. Safe to re-run:
existing realm, client and user are reconciled rather than duplicated. The
bootstrap user's password is only set when the user is first created, so a
password changed later in the Keycloak admin console is not overwritten.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KC = os.environ["KEYCLOAK_INTERNAL_URL"].rstrip("/")
REALM = os.environ["KEYCLOAK_REALM"]
ADMIN_USER = os.environ["KEYCLOAK_ADMIN"]
ADMIN_PASSWORD = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
CLIENT_ID = os.environ["OIDC_CLIENT_ID"]
CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
PUBLIC_BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")
BOOTSTRAP_EMAIL = os.environ["BOOTSTRAP_ADMIN_EMAIL"]
BOOTSTRAP_PASSWORD = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
BOOTSTRAP_ROLE = "afs-super-admin"


def call(method, path, token=None, json_body=None, form=None):
    """Return (status, parsed_json_or_None). Never raises on HTTP error statuses."""
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{KC}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body) if body else None
        except json.JSONDecodeError:
            return error.code, None


def log(message):
    print(f"[keycloak-bootstrap] {message}", flush=True)


def wait_for_keycloak(attempts=60):
    for _ in range(attempts):
        try:
            status, _ = call("GET", "/realms/master/.well-known/openid-configuration")
            if status == 200:
                return
        except urllib.error.URLError:
            pass
        time.sleep(2)
    sys.exit("Keycloak did not become reachable in time")


def admin_token():
    status, body = call(
        "POST",
        "/realms/master/protocol/openid-connect/token",
        form={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASSWORD,
        },
    )
    if status != 200:
        sys.exit(f"Could not obtain Keycloak admin token (HTTP {status}). Check KEYCLOAK_ADMIN credentials.")
    return body["access_token"]


def ensure_realm(token):
    status, _ = call("GET", f"/admin/realms/{REALM}", token=token)
    if status == 200:
        log(f"realm '{REALM}' exists")
        return
    status, body = call("POST", "/admin/realms", token=token, json_body={"realm": REALM, "enabled": True})
    if status != 201:
        sys.exit(f"Failed to create realm '{REALM}' (HTTP {status}): {body}")
    log(f"created realm '{REALM}'")


def ensure_client(token):
    representation = {
        "clientId": CLIENT_ID,
        "name": "AFS Command Center",
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "secret": CLIENT_SECRET,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": [f"{PUBLIC_BASE_URL}/auth/callback"],
        "webOrigins": [PUBLIC_BASE_URL],
        "attributes": {"post.logout.redirect.uris": f"{PUBLIC_BASE_URL}/"},
    }
    _, existing = call("GET", f"/admin/realms/{REALM}/clients?clientId={CLIENT_ID}", token=token)
    if existing:
        status, body = call("PUT", f"/admin/realms/{REALM}/clients/{existing[0]['id']}", token=token, json_body=representation)
        if status != 204:
            sys.exit(f"Failed to update client '{CLIENT_ID}' (HTTP {status}): {body}")
        log(f"updated client '{CLIENT_ID}'")
        return
    status, body = call("POST", f"/admin/realms/{REALM}/clients", token=token, json_body=representation)
    if status != 201:
        sys.exit(f"Failed to create client '{CLIENT_ID}' (HTTP {status}): {body}")
    log(f"created client '{CLIENT_ID}'")


def ensure_realm_role(token):
    status, _ = call("GET", f"/admin/realms/{REALM}/roles/{BOOTSTRAP_ROLE}", token=token)
    if status == 200:
        return
    status, body = call(
        "POST",
        f"/admin/realms/{REALM}/roles",
        token=token,
        json_body={"name": BOOTSTRAP_ROLE, "description": "Marks the initial AFS Command Center super administrator."},
    )
    if status != 201:
        sys.exit(f"Failed to create realm role '{BOOTSTRAP_ROLE}' (HTTP {status}): {body}")
    log(f"created realm role '{BOOTSTRAP_ROLE}'")


def grant_bootstrap_role(token, user_id):
    _, role = call("GET", f"/admin/realms/{REALM}/roles/{BOOTSTRAP_ROLE}", token=token)
    status, body = call(
        "POST",
        f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
        token=token,
        json_body=[role],
    )
    if status != 204:
        sys.exit(f"Failed to grant realm role to bootstrap user (HTTP {status}): {body}")
    log(f"granted realm role '{BOOTSTRAP_ROLE}' to bootstrap user")


def ensure_bootstrap_user(token):
    """Create the bootstrap account once. Later runs leave its password and roles untouched."""
    _, matches = call(
        "GET",
        f"/admin/realms/{REALM}/users?username={urllib.parse.quote(BOOTSTRAP_EMAIL)}&exact=true",
        token=token,
    )
    if matches:
        log(f"bootstrap user {BOOTSTRAP_EMAIL} exists; password and roles left unchanged")
        return
    user = {
        "username": BOOTSTRAP_EMAIL,
        "email": BOOTSTRAP_EMAIL,
        "emailVerified": True,
        "enabled": True,
        # Keycloak 26's user profile requires names, otherwise login forces a profile update.
        "firstName": "Test",
        "lastName": "Admin",
    }
    status, body = call("POST", f"/admin/realms/{REALM}/users", token=token, json_body=user)
    if status != 201:
        sys.exit(f"Failed to create bootstrap user (HTTP {status}): {body}")
    _, created = call(
        "GET",
        f"/admin/realms/{REALM}/users?username={urllib.parse.quote(BOOTSTRAP_EMAIL)}&exact=true",
        token=token,
    )
    status, body = call(
        "PUT",
        f"/admin/realms/{REALM}/users/{created[0]['id']}/reset-password",
        token=token,
        json_body={"type": "password", "value": BOOTSTRAP_PASSWORD, "temporary": False},
    )
    if status != 204:
        sys.exit(f"Failed to set bootstrap user password (HTTP {status}): {body}")
    grant_bootstrap_role(token, created[0]["id"])
    log(f"created bootstrap user {BOOTSTRAP_EMAIL}")


def main():
    wait_for_keycloak()
    token = admin_token()
    ensure_realm(token)
    ensure_client(token)
    ensure_realm_role(token)
    ensure_bootstrap_user(token)
    log("configuration complete")


if __name__ == "__main__":
    main()
