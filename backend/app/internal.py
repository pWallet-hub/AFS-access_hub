"""Service-to-service endpoints for integrated applications.

An application's backend calls these with its own service token. Each application has a separate token,
AFS_SERVICE_TOKEN_<APP> (for example AFS_SERVICE_TOKEN_PROCUREMENT), so a token can only act for its own
application. Browsers never call them: nginx does not expose /api/internal.

- GET  /api/internal/access: may this identity use the application, and with which role?
- POST /api/internal/events: the application reports a sync or revocation failure, or an informational event.
"""

import hmac
import os
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .audit import record
from .db import connect
from .schemas import SLUG_PATTERN

router = APIRouter(prefix="/api/internal", tags=["internal"])


def service_token_env(application):
    return "AFS_SERVICE_TOKEN_" + application.upper().replace("-", "_")


def _token_matches(application, presented):
    expected = os.environ.get(service_token_env(application), "")
    # An unset token means no caller can authenticate, rather than an empty one matching.
    return bool(expected and presented and hmac.compare_digest(presented, expected))


def _authorize(application, presented):
    if _token_matches(application, presented):
        return
    with connect() as conn:
        app = conn.execute("SELECT id FROM applications WHERE slug = %s", (application,)).fetchone()
        if app is not None:
            # Rejected tokens are visible to administrators against the application they were sent for.
            conn.execute(
                "INSERT INTO integration_events (application_id, kind, outcome, detail) VALUES (%s, 'token_rejected', 'failure', %s)",
                (app["id"], "service token missing" if not presented else "service token does not match"),
            )
            conn.execute(
                "UPDATE applications SET last_error_at = now(), last_error = 'service token rejected' WHERE id = %s",
                (app["id"],),
            )
    raise HTTPException(status_code=401, detail="Invalid service token")


@router.get("/access")
def check_access(
    application: str = Query(pattern=SLUG_PATTERN),
    subject: str = Query(min_length=1, max_length=255),
    x_service_token: str | None = Header(default=None),
):
    """Return whether `subject` (a Keycloak sub) may use `application`, and the role to apply."""
    _authorize(application, x_service_token)
    with connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE keycloak_sub = %s", (subject,)).fetchone()
        app = conn.execute("SELECT * FROM applications WHERE slug = %s", (application,)).fetchone()

        reason = None
        external_role = None
        if app is None:
            reason = "unknown application"
        elif not app["enabled"]:
            reason = "application disabled"
        elif user is None:
            reason = "unknown identity"
        elif user["status"] != "active":
            reason = "account disabled"
        else:
            assignment = conn.execute(
                "SELECT r.external_role FROM assignments s"
                " LEFT JOIN app_roles r ON r.id = s.app_role_id"
                " WHERE s.user_id = %s AND s.application_id = %s AND s.revoked_at IS NULL",
                (user["id"], app["id"]),
            ).fetchone()
            if assignment is None:
                reason = "not assigned"
            else:
                external_role = assignment["external_role"]

        if app is not None:
            # The check itself worked whatever the answer; "not assigned" is a decision, not an integration failure.
            conn.execute(
                "UPDATE applications SET last_access_check_at = now(), last_access_ok_at = now() WHERE id = %s",
                (app["id"],),
            )

        if reason is not None:
            record(
                conn,
                actor=user,
                action="access.denied",
                outcome="failure",
                target_type="application",
                target_id=app["id"] if app else None,
                application_slug=application,
                details={"reason": reason},
            )
            return {"allowed": False, "external_role": None, "reason": reason}
        conn.execute(
            "INSERT INTO application_usage (application_id, day, sessions) VALUES (%s, current_date, 1)"
            " ON CONFLICT (application_id, day) DO UPDATE SET sessions = application_usage.sessions + 1",
            (app["id"],),
        )
        return {"allowed": True, "external_role": external_role, "reason": "assigned"}


class IntegrationEvent(BaseModel):
    kind: Literal["sync_failure", "revocation_failure", "info"]
    outcome: Literal["success", "failure"]
    detail: str = Field(default="", max_length=1000)


@router.post("/events", status_code=201)
def report_event(
    body: IntegrationEvent,
    application: str = Query(pattern=SLUG_PATTERN),
    x_service_token: str | None = Header(default=None),
):
    """Record a failure an application hit while syncing or revoking access, so administrators can see it."""
    _authorize(application, x_service_token)
    with connect() as conn:
        app = conn.execute("SELECT id FROM applications WHERE slug = %s", (application,)).fetchone()
        if app is None:
            raise HTTPException(status_code=404, detail="Unknown application")
        conn.execute(
            "INSERT INTO integration_events (application_id, kind, outcome, detail) VALUES (%s, %s, %s, %s)",
            (app["id"], body.kind, body.outcome, body.detail),
        )
        if body.outcome == "failure":
            conn.execute(
                "UPDATE applications SET last_error_at = now(), last_error = %s WHERE id = %s",
                ((body.detail or body.kind)[:500], app["id"]),
            )
    return {"ok": True}
