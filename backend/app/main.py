import logging
import os
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from . import admin, auth, internal
from .db import DATABASE_URL, connect, run_migrations
from .auth import INTERNAL_REALM_URL, current_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SESSION_SECRET = os.environ["SESSION_SECRET"]
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "false").lower() == "true"


@asynccontextmanager
async def lifespan(_app):
    # A failed migration stops the container, so it never reports healthy with a broken schema.
    run_migrations()
    yield


app = FastAPI(title="AFS Command Center API", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(internal.router)


def check_database():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.OperationalError:
        return False


def check_identity_provider():
    try:
        response = httpx.get(f"{INTERNAL_REALM_URL}/.well-known/openid-configuration", timeout=3)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


@app.get("/api/health")
def health():
    database_ok = check_database()
    identity_ok = check_identity_provider()
    healthy = database_ok and identity_ok
    body = {
        "status": "ok" if healthy else "unavailable",
        "database": "up" if database_ok else "down",
        "identity_provider": "up" if identity_ok else "down",
    }
    return JSONResponse(body, status_code=200 if healthy else 503)


@app.get("/api/me")
def me(user=Depends(current_user)):
    with connect() as conn:
        managed = admin.managed_application_ids(conn, user)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["display_name"],
        "platform_role": user["platform_role"],
        # null means no limit (super administrator); otherwise the applications this administrator manages.
        "managed_application_ids": None if managed is None else [str(a) for a in managed],
    }


@app.get("/api/apps")
def my_applications(user=Depends(current_user)):
    """Applications the signed-in employee is assigned to. Disabled ones are listed as unavailable, without a link."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT a.slug, a.name, a.description, a.enabled, a.launch_url, r.name AS role_name"
            " FROM assignments s"
            " JOIN applications a ON a.id = s.application_id"
            " LEFT JOIN app_roles r ON r.id = s.app_role_id"
            " WHERE s.user_id = %s AND s.revoked_at IS NULL"
            " ORDER BY a.name",
            (user["id"],),
        ).fetchall()
    return [
        {
            "slug": row["slug"],
            "name": row["name"],
            "description": row["description"],
            "role_name": row["role_name"],
            "available": row["enabled"],
            "launch_url": row["launch_url"] if row["enabled"] else None,
        }
        for row in rows
    ]
