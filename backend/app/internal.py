"""Service-to-service access check for integrated applications.

An application's backend calls this with its own service token to decide whether a signed-in employee
may use it, and which application role to apply. Each application has a separate token,
AFS_SERVICE_TOKEN_<SLUG> (for example AFS_SERVICE_TOKEN_PROCUREMENT), so a token can only ask about
its own application. Browsers never call it: nginx does not expose /api/internal.
"""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Query

from .audit import record
from .db import connect
from .schemas import SLUG_PATTERN

router = APIRouter(prefix="/api/internal", tags=["internal"])


def service_token_env(application):
    return "AFS_SERVICE_TOKEN_" + application.upper().replace("-", "_")


def _verify_service_token(application, presented):
    expected = os.environ.get(service_token_env(application), "")
    # An unset token means no caller can authenticate, rather than an empty one matching.
    if not expected or not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="Invalid service token")


@router.get("/access")
def check_access(
    application: str = Query(pattern=SLUG_PATTERN),
    subject: str = Query(min_length=1, max_length=255),
    x_service_token: str | None = Header(default=None),
):
    """Return whether `subject` (a Keycloak sub) may use `application`, and the role to apply."""
    _verify_service_token(application, x_service_token)
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
        return {"allowed": True, "external_role": external_role, "reason": "assigned"}
