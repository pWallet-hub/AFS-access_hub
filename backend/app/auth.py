import os
import secrets
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from .accounts import sign_in
from .db import connect

KEYCLOAK_PUBLIC_URL = os.environ["KEYCLOAK_PUBLIC_URL"].rstrip("/")
KEYCLOAK_INTERNAL_URL = os.environ["KEYCLOAK_INTERNAL_URL"].rstrip("/")
KEYCLOAK_REALM = os.environ["KEYCLOAK_REALM"]
OIDC_CLIENT_ID = os.environ["OIDC_CLIENT_ID"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
PUBLIC_BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")

# Realm role that marks the initial super administrator (created by keycloak/bootstrap.py).
BOOTSTRAP_SUPER_ADMIN_ROLE = "afs-super-admin"

# The browser-facing issuer is what Keycloak writes into tokens (KC_HOSTNAME).
# Server-to-server calls use the internal Docker network address instead.
ISSUER = f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}"
INTERNAL_REALM_URL = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}"
REDIRECT_URI = f"{PUBLIC_BASE_URL}/auth/callback"

JWKS_CLIENT = jwt.PyJWKClient(f"{INTERNAL_REALM_URL}/protocol/openid-connect/certs")

router = APIRouter()


@router.get("/auth/login")
def login(request: Request):
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    request.session["oidc_state"] = state
    request.session["oidc_nonce"] = nonce
    query = urlencode(
        {
            "client_id": OIDC_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
        }
    )
    return RedirectResponse(f"{ISSUER}/protocol/openid-connect/auth?{query}")


def _decode_token(token, audience):
    signing_key = JWKS_CLIENT.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=ISSUER,
        audience=audience,
        options={"require": ["exp", "iss", "sub"], "verify_aud": audience is not None},
    )


@router.get("/auth/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    expected_state = request.session.pop("oidc_state", None)
    expected_nonce = request.session.pop("oidc_nonce", None)
    if error or not code or not expected_state or not secrets.compare_digest(state or "", expected_state):
        raise HTTPException(status_code=400, detail="Sign-in was cancelled or could not be verified. Please try again.")

    token_response = httpx.post(
        f"{INTERNAL_REALM_URL}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": OIDC_CLIENT_ID,
            "client_secret": OIDC_CLIENT_SECRET,
        },
        timeout=10,
    )
    if token_response.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not complete sign-in with the identity provider.")
    tokens = token_response.json()

    id_claims = _decode_token(tokens["id_token"], audience=OIDC_CLIENT_ID)
    if not expected_nonce or id_claims.get("nonce") != expected_nonce:
        raise HTTPException(status_code=400, detail="Sign-in could not be verified. Please try again.")

    access_claims = _decode_token(tokens["access_token"], audience=None)
    if access_claims.get("azp") != OIDC_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Sign-in could not be verified. Please try again.")
    realm_roles = access_claims.get("realm_access", {}).get("roles", [])

    with connect() as conn:
        user = sign_in(conn, id_claims, bootstrap_candidate=BOOTSTRAP_SUPER_ADMIN_ROLE in realm_roles)
    if user is None:
        raise HTTPException(status_code=403, detail="This account is disabled. Contact an access administrator.")

    # Fresh session on every sign-in prevents session fixation.
    request.session.clear()
    request.session["sub"] = user["keycloak_sub"]
    return RedirectResponse(f"{PUBLIC_BASE_URL}/")


@router.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    query = urlencode({"client_id": OIDC_CLIENT_ID, "post_logout_redirect_uri": f"{PUBLIC_BASE_URL}/"})
    return RedirectResponse(f"{ISSUER}/protocol/openid-connect/logout?{query}")


def current_user(request: Request):
    """Load the signed-in user from the database on every request.

    The session holds only the Keycloak subject. Disabling an account therefore takes effect on
    the next request, even for a session that was valid a moment earlier.
    """
    sub = request.session.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Not signed in")
    with connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE keycloak_sub = %s", (sub,)).fetchone()
    if user is None or user["status"] != "active":
        request.session.clear()
        raise HTTPException(status_code=401, detail="Your session is no longer valid. Please sign in again.")
    return user


def require_platform_role(*allowed_roles):
    def dependency(user=Depends(current_user)):
        if user["platform_role"] not in allowed_roles:
            raise HTTPException(status_code=403, detail="You do not have permission for this action.")
        return user

    return dependency
