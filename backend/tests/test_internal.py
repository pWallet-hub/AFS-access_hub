"""Access checks used by integrated applications through /api/internal/access."""

import os
import uuid

from fastapi.testclient import TestClient

from app.db import connect
from app.internal import service_token_env
from app.main import app

SERVICE_TOKEN = "test-service-token"


def _user(status="active"):
    sub = f"test-{uuid.uuid4()}"
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, status)"
            " VALUES (%s, %s, 'Test', true, %s) RETURNING *",
            (sub, f"test-{uuid.uuid4().hex[:10]}@example.test", status),
        ).fetchone()
    return row


def _application(enabled=True):
    slug = f"test-{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        return conn.execute(
            "INSERT INTO applications (slug, name, launch_url, enabled) VALUES (%s, %s, %s, %s) RETURNING *",
            (slug, slug, "https://example.test/app", enabled),
        ).fetchone()


def _role(application, name="editor"):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO app_roles (application_id, name, external_role) VALUES (%s, %s, %s) RETURNING *",
            (application["id"], name, name),
        ).fetchone()


def _assign(user, application, role=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO assignments (user_id, application_id, app_role_id) VALUES (%s, %s, %s)",
            (user["id"], application["id"], role["id"] if role else None),
        )


def _check(application_slug, subject, token=None):
    """Call the access check. The application's token is provisioned for the test process unless given."""
    env = service_token_env(application_slug)
    os.environ.setdefault(env, f"token-{application_slug}-{'x' * 20}")
    if token is None:
        token = os.environ[env]
    headers = {"X-Service-Token": token} if token else {}
    return TestClient(app).get(
        "/api/internal/access", params={"application": application_slug, "subject": subject}, headers=headers
    )


def test_token_for_another_application_is_rejected():
    user = _user()
    application = _application()
    _assign(user, application)
    other_token = os.environ.setdefault("AFS_SERVICE_TOKEN_IMAGEUNIT", SERVICE_TOKEN)
    # A token issued for one application cannot ask about a different one.
    assert _check(application["slug"], user["keycloak_sub"], token=other_token).status_code == 401


def test_requires_service_token():
    user = _user()
    application = _application()
    assert _check(application["slug"], user["keycloak_sub"], token="").status_code == 401
    assert _check(application["slug"], user["keycloak_sub"], token="wrong").status_code == 401


def test_assigned_user_receives_the_application_role():
    user = _user()
    application = _application()
    role = _role(application, "editor")
    _assign(user, application, role)

    body = _check(application["slug"], user["keycloak_sub"]).json()
    assert body == {"allowed": True, "external_role": "editor", "reason": "assigned"}


def test_assignment_without_a_role_is_allowed_with_no_external_role():
    user = _user()
    application = _application()
    _assign(user, application)

    body = _check(application["slug"], user["keycloak_sub"]).json()
    assert body["allowed"] is True
    assert body["external_role"] is None


def test_unassigned_user_is_denied():
    user = _user()
    application = _application()
    body = _check(application["slug"], user["keycloak_sub"]).json()
    assert body == {"allowed": False, "external_role": None, "reason": "not assigned"}


def test_revoked_assignment_is_denied():
    user = _user()
    application = _application()
    _assign(user, application)
    with connect() as conn:
        conn.execute("UPDATE assignments SET revoked_at = now() WHERE user_id = %s", (user["id"],))
    assert _check(application["slug"], user["keycloak_sub"]).json()["reason"] == "not assigned"


def test_disabled_account_is_denied_even_when_assigned():
    user = _user(status="disabled")
    application = _application()
    _assign(user, application)
    assert _check(application["slug"], user["keycloak_sub"]).json()["reason"] == "account disabled"


def test_disabled_application_is_denied():
    user = _user()
    application = _application(enabled=False)
    _assign(user, application)
    assert _check(application["slug"], user["keycloak_sub"]).json()["reason"] == "application disabled"


def test_unknown_identity_and_application_are_denied():
    application = _application()
    assert _check(application["slug"], "no-such-subject").json()["reason"] == "unknown identity"
    assert _check("no-such-application", "no-such-subject").json()["reason"] == "unknown application"


def test_denials_are_audited():
    user = _user()
    application = _application()
    _check(application["slug"], user["keycloak_sub"])
    with connect() as conn:
        row = conn.execute(
            "SELECT outcome, details FROM audit_log WHERE action = 'access.denied' AND application_slug = %s"
            " ORDER BY id DESC LIMIT 1",
            (application["slug"],),
        ).fetchone()
    assert row["outcome"] == "failure"
    assert row["details"]["reason"] == "not assigned"
