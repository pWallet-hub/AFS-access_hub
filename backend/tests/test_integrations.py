"""Integration monitoring: health timestamps, failure reports, token rejections and the launcher's unavailable state."""

import base64
import json
import os
import uuid

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.db import connect
from app.internal import service_token_env
from app.main import app

SESSION_SECRET = os.environ["SESSION_SECRET"]


def _user(platform_role="employee"):
    sub = f"test-{uuid.uuid4()}"
    with connect() as conn:
        return conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, platform_role)"
            " VALUES (%s, %s, 'Test', true, %s) RETURNING *",
            (sub, f"test-{uuid.uuid4().hex[:10]}@example.test", platform_role),
        ).fetchone()


def _client_for(user):
    client = TestClient(app)
    data = base64.b64encode(json.dumps({"sub": user["keycloak_sub"]}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner(SESSION_SECRET).sign(data).decode("utf-8"))
    return client


def _application(enabled=True):
    slug = f"test-{uuid.uuid4().hex[:8]}"
    os.environ.setdefault(service_token_env(slug), f"token-{slug}-{'x' * 20}")
    with connect() as conn:
        return conn.execute(
            "INSERT INTO applications (slug, name, launch_url, enabled) VALUES (%s, %s, %s, %s) RETURNING *",
            (slug, slug, "https://example.test/app" if enabled else None, enabled),
        ).fetchone()


def _token(application):
    return os.environ[service_token_env(application["slug"])]


def _access(application, token):
    return TestClient(app).get(
        "/api/internal/access",
        params={"application": application["slug"], "subject": "nobody"},
        headers={"X-Service-Token": token},
    )


def _row(application):
    with connect() as conn:
        return conn.execute("SELECT * FROM applications WHERE id = %s", (application["id"],)).fetchone()


def test_successful_access_check_updates_health():
    application = _application()
    assert _access(application, _token(application)).status_code == 200
    row = _row(application)
    assert row["last_access_check_at"] is not None
    assert row["last_access_ok_at"] is not None


def test_business_denial_is_not_an_integration_error():
    application = _application()
    _access(application, _token(application))
    assert _row(application)["last_error_at"] is None


def test_rejected_token_is_recorded_against_the_application():
    application = _application()
    response = _access(application, "not-the-token")
    assert response.status_code == 401
    row = _row(application)
    assert row["last_error"] == "service token rejected"
    with connect() as conn:
        event = conn.execute(
            "SELECT kind, outcome FROM integration_events WHERE application_id = %s ORDER BY id DESC LIMIT 1",
            (application["id"],),
        ).fetchone()
    assert event == {"kind": "token_rejected", "outcome": "failure"}


def test_application_can_report_a_sync_failure():
    application = _application()
    client = TestClient(app)
    response = client.post(
        "/api/internal/events",
        params={"application": application["slug"]},
        headers={"X-Service-Token": _token(application)},
        json={"kind": "sync_failure", "outcome": "failure", "detail": "could not reach the directory"},
    )
    assert response.status_code == 201
    row = _row(application)
    assert row["last_error"] == "could not reach the directory"
    assert row["last_error_at"] is not None


def test_report_without_the_right_token_is_refused():
    application = _application()
    response = TestClient(app).post(
        "/api/internal/events",
        params={"application": application["slug"]},
        headers={"X-Service-Token": "wrong"},
        json={"kind": "info", "outcome": "success"},
    )
    assert response.status_code == 401


def test_integration_status_reflects_the_latest_event():
    application = _application()
    _access(application, _token(application))
    admin = _client_for(_user("super_admin"))
    entry = next(a for a in admin.get("/api/admin/integrations").json() if a["id"] == str(application["id"]))
    assert entry["status"] == "ok"

    TestClient(app).post(
        "/api/internal/events",
        params={"application": application["slug"]},
        headers={"X-Service-Token": _token(application)},
        json={"kind": "revocation_failure", "outcome": "failure", "detail": "revocation call timed out"},
    )
    entry = next(a for a in admin.get("/api/admin/integrations").json() if a["id"] == str(application["id"]))
    assert entry["status"] == "error"
    events = admin.get(f"/api/admin/integrations/{application['id']}/events").json()
    assert events[0]["kind"] == "revocation_failure"


def test_status_is_not_connected_before_any_check():
    application = _application()
    admin = _client_for(_user("access_admin"))
    entry = next(a for a in admin.get("/api/admin/integrations").json() if a["id"] == str(application["id"]))
    assert entry["status"] == "not_connected"


def test_employee_cannot_read_integration_health():
    employee = _user()
    assert _client_for(employee).get("/api/admin/integrations").status_code == 403


def test_launcher_lists_disabled_assignments_as_unavailable():
    employee = _user()
    application = _application(enabled=False)
    with connect() as conn:
        conn.execute("INSERT INTO assignments (user_id, application_id) VALUES (%s, %s)", (employee["id"], application["id"]))
    entry = next(a for a in _client_for(employee).get("/api/apps").json() if a["slug"] == application["slug"])
    assert entry["available"] is False
    assert entry["launch_url"] is None
