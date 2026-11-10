"""API tests for authentication, authorization, assignments and audit behaviour.

These run inside the backend container against the real database. Sessions are signed exactly
as Starlette's SessionMiddleware signs them, so the production authorization path is exercised.
"""

import base64
import json
import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

os.environ.setdefault("SESSION_SECRET", "test-session-secret")

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402

SESSION_SECRET = os.environ["SESSION_SECRET"]


@pytest.fixture(scope="module", autouse=True)
def migrated_app():
    # Entering the TestClient context runs the startup migrations, as the real server does.
    with TestClient(app):
        yield


def _unique_email():
    return f"test-{uuid.uuid4().hex[:10]}@example.test"


def make_user(platform_role="employee", status="active"):
    sub = f"test-{uuid.uuid4()}"
    with connect() as conn:
        return conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, platform_role, status)"
            " VALUES (%s, %s, %s, true, %s, %s) RETURNING *",
            (sub, _unique_email(), "Test User", platform_role, status),
        ).fetchone()


def client_for(user):
    client = TestClient(app)
    data = base64.b64encode(json.dumps({"sub": user["keycloak_sub"]}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner(SESSION_SECRET).sign(data).decode("utf-8"))
    return client


def make_application(slug_prefix="test"):
    slug = f"{slug_prefix}-{uuid.uuid4().hex[:8]}"
    return slug


@pytest.fixture
def super_admin():
    return make_user(platform_role="super_admin")


@pytest.fixture
def access_admin():
    return make_user(platform_role="access_admin")


@pytest.fixture
def employee():
    return make_user()


# ---------- Authentication and session handling ----------


def test_anonymous_requests_are_rejected():
    client = TestClient(app)
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/apps").status_code == 401
    assert client.get("/api/admin/users").status_code == 401


def test_callback_rejects_missing_state():
    client = TestClient(app)
    response = client.get("/auth/callback", params={"code": "abc"}, follow_redirects=False)
    assert response.status_code == 400


def test_callback_rejects_mismatched_state():
    client = TestClient(app)
    client.get("/auth/login", follow_redirects=False)
    response = client.get("/auth/callback", params={"code": "abc", "state": "forged"}, follow_redirects=False)
    assert response.status_code == 400


def test_disabled_account_loses_access_immediately(employee):
    client = client_for(employee)
    assert client.get("/api/me").status_code == 200
    with connect() as conn:
        conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (employee["id"],))
    assert client.get("/api/me").status_code == 401


# ---------- Authorization ----------


def test_employee_cannot_use_admin_api(employee):
    client = client_for(employee)
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/admin/audit").status_code == 403
    assert client.post("/api/admin/users", json={"email": _unique_email()}).status_code == 403


def test_access_admin_cannot_change_platform_roles(access_admin, employee):
    client = client_for(access_admin)
    response = client.patch(f"/api/admin/users/{employee['id']}", json={"platform_role": "super_admin"})
    assert response.status_code == 403
    with connect() as conn:
        row = conn.execute("SELECT platform_role FROM users WHERE id = %s", (employee["id"],)).fetchone()
        assert row["platform_role"] == "employee"
        denial = conn.execute(
            "SELECT outcome FROM audit_log WHERE target_id = %s AND action = 'user.update' ORDER BY id DESC LIMIT 1",
            (str(employee["id"]),),
        ).fetchone()
        assert denial["outcome"] == "failure"


def test_access_admin_can_disable_and_reactivate_employee(access_admin, employee):
    client = client_for(access_admin)
    assert client.patch(f"/api/admin/users/{employee['id']}", json={"status": "disabled"}).json()["status"] == "disabled"
    assert client.patch(f"/api/admin/users/{employee['id']}", json={"status": "active"}).json()["status"] == "active"


# ---------- Employee management ----------


def test_duplicate_employee_email_is_rejected(super_admin):
    client = client_for(super_admin)
    email = _unique_email()
    assert client.post("/api/admin/users", json={"email": email}).status_code == 201
    assert client.post("/api/admin/users", json={"email": email}).status_code == 409


def test_pre_provisioned_employee_is_linked_on_verified_sign_in(super_admin):
    from app.accounts import sign_in

    client = client_for(super_admin)
    email = _unique_email()
    client.post("/api/admin/users", json={"email": email})
    sub = f"test-{uuid.uuid4()}"
    with connect() as conn:
        user = sign_in(conn, {"sub": sub, "email": email.upper(), "email_verified": True, "name": "Linked"}, False)
    assert user is not None
    assert user["keycloak_sub"] == sub


def test_unverified_email_does_not_link_pre_provisioned_account(super_admin):
    from app.accounts import sign_in

    client = client_for(super_admin)
    email = _unique_email()
    client.post("/api/admin/users", json={"email": email})
    with connect() as conn:
        user = sign_in(conn, {"sub": f"test-{uuid.uuid4()}", "email": email, "email_verified": False}, False)
        pending = conn.execute(
            "SELECT keycloak_sub FROM users WHERE lower(email) = %s AND keycloak_sub IS NULL", (email,)
        ).fetchone()
    assert user["email"] == email
    assert pending is not None


# ---------- Applications, role mappings and assignments ----------


def _create_enabled_application(client):
    slug = make_application()
    response = client.post(
        "/api/admin/applications",
        json={"slug": slug, "name": slug, "launch_url": "https://example.test/app", "enabled": True},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_enabling_application_requires_launch_url(super_admin):
    client = client_for(super_admin)
    response = client.post(
        "/api/admin/applications",
        json={"slug": make_application(), "name": "No URL", "enabled": True},
    )
    assert response.status_code == 422


def test_launch_url_must_be_http_or_https(super_admin):
    client = client_for(super_admin)
    response = client.post(
        "/api/admin/applications",
        json={"slug": make_application(), "name": "Bad", "launch_url": "javascript:alert(1)"},
    )
    assert response.status_code == 422


def test_role_must_belong_to_the_assigned_application(super_admin, employee):
    client = client_for(super_admin)
    app_a = _create_enabled_application(client)
    app_b = _create_enabled_application(client)
    role_a = client.post(
        f"/api/admin/applications/{app_a['id']}/roles",
        json={"name": "viewer", "external_role": "VIEWER"},
    ).json()

    response = client.post(
        "/api/admin/assignments",
        json={"user_id": str(employee["id"]), "application_id": app_b["id"], "app_role_id": role_a["id"]},
    )
    assert response.status_code == 422


def test_assignment_drives_launcher_and_revocation_removes_it(super_admin, employee):
    admin = client_for(super_admin)
    app = _create_enabled_application(admin)
    role = admin.post(
        f"/api/admin/applications/{app['id']}/roles",
        json={"name": "approver", "external_role": "APPROVER"},
    ).json()

    grant = admin.post(
        "/api/admin/assignments",
        json={"user_id": str(employee["id"]), "application_id": app["id"], "app_role_id": role["id"]},
    )
    assert grant.status_code == 201

    employee_client = client_for(employee)
    slugs = [item["slug"] for item in employee_client.get("/api/apps").json()]
    assert app["slug"] in slugs
    launched = next(item for item in employee_client.get("/api/apps").json() if item["slug"] == app["slug"])
    assert launched["role_name"] == "approver"

    assignment_id = grant.json()["id"]
    assert admin.delete(f"/api/admin/assignments/{assignment_id}").status_code == 204
    slugs_after = [item["slug"] for item in employee_client.get("/api/apps").json()]
    assert app["slug"] not in slugs_after


def test_role_changes_in_place_without_revoking_access(super_admin, employee):
    admin = client_for(super_admin)
    app = _create_enabled_application(admin)
    editor = admin.post(f"/api/admin/applications/{app['id']}/roles", json={"name": "editor", "external_role": "EDITOR"}).json()
    approver = admin.post(f"/api/admin/applications/{app['id']}/roles", json={"name": "approver", "external_role": "APPROVER"}).json()
    body = {"user_id": str(employee["id"]), "application_id": app["id"]}

    first = admin.post("/api/admin/assignments", json={**body, "app_role_id": editor["id"]})
    assert first.status_code == 201
    changed = admin.post("/api/admin/assignments", json={**body, "app_role_id": approver["id"]})
    assert changed.status_code == 201
    # Same assignment row, so the employee never loses access in between.
    assert changed.json()["id"] == first.json()["id"]

    employee_client = client_for(employee)
    launched = next(item for item in employee_client.get("/api/apps").json() if item["slug"] == app["slug"])
    assert launched["role_name"] == "approver"


def test_disabled_application_cannot_be_assigned(super_admin, employee):
    admin = client_for(super_admin)
    app = _create_enabled_application(admin)
    assert admin.patch(f"/api/admin/applications/{app['id']}", json={"enabled": False}).status_code == 200
    response = admin.post(
        "/api/admin/assignments",
        json={"user_id": str(employee["id"]), "application_id": app["id"]},
    )
    assert response.status_code == 409


def test_disabled_employee_cannot_be_assigned(super_admin, employee):
    admin = client_for(super_admin)
    app = _create_enabled_application(admin)
    admin.patch(f"/api/admin/users/{employee['id']}", json={"status": "disabled"})
    response = admin.post(
        "/api/admin/assignments",
        json={"user_id": str(employee["id"]), "application_id": app["id"]},
    )
    assert response.status_code == 409


def test_role_in_use_cannot_be_deleted(super_admin, employee):
    admin = client_for(super_admin)
    app = _create_enabled_application(admin)
    role = admin.post(
        f"/api/admin/applications/{app['id']}/roles",
        json={"name": "editor", "external_role": "EDITOR"},
    ).json()
    admin.post(
        "/api/admin/assignments",
        json={"user_id": str(employee["id"]), "application_id": app["id"], "app_role_id": role["id"]},
    )
    assert admin.delete(f"/api/admin/roles/{role['id']}").status_code == 409


# ---------- Audit ----------


def test_admin_changes_are_audited(super_admin):
    client = client_for(super_admin)
    email = _unique_email()
    created = client.post("/api/admin/users", json={"email": email}).json()
    entries = client.get("/api/admin/audit", params={"limit": 50}).json()["items"]
    matching = [e for e in entries if e["action"] == "user.create" and e["target_id"] == str(created["id"])]
    assert matching and matching[0]["outcome"] == "success"
    assert matching[0]["actor_identity"] == super_admin["keycloak_sub"]


def test_audit_log_cannot_be_modified(super_admin):
    client_for(super_admin).get("/api/admin/audit")  # ensure at least one row exists
    with pytest.raises(psycopg.Error):
        with connect() as conn:
            conn.execute("UPDATE audit_log SET outcome = 'success'")


def test_health_reports_dependency_status():
    response = TestClient(app).get("/api/health")
    body = response.json()
    assert body["database"] in ("up", "down")
    assert body["identity_provider"] in ("up", "down")
    assert response.status_code == (200 if body["status"] == "ok" else 503)


# ---------- Removing employees who have not signed in ----------


def test_pending_employee_can_be_removed_and_added_again(super_admin):
    client = client_for(super_admin)
    email = _unique_email()
    created = client.post("/api/admin/users", json={"email": email}).json()
    assert client.delete(f"/api/admin/users/{created['id']}").status_code == 204
    assert all(u["email"] != email for u in client.get("/api/admin/users").json())
    assert client.post("/api/admin/users", json={"email": email}).status_code == 201
    entries = client.get("/api/admin/audit", params={"limit": 50}).json()["items"]
    assert any(e["action"] == "user.delete" and e["target_id"] == str(created["id"]) and e["outcome"] == "success" for e in entries)


def test_employee_who_has_signed_in_cannot_be_removed(super_admin, employee):
    response = client_for(super_admin).delete(f"/api/admin/users/{employee['id']}")
    assert response.status_code == 409
    with connect() as conn:
        assert conn.execute("SELECT 1 FROM users WHERE id = %s", (employee["id"],)).fetchone()


def test_access_admin_can_remove_pending_employee_but_not_pending_admin(super_admin, access_admin):
    admin = client_for(access_admin)
    pending = admin.post("/api/admin/users", json={"email": _unique_email()}).json()
    assert admin.delete(f"/api/admin/users/{pending['id']}").status_code == 204
    promoted = client_for(super_admin).post("/api/admin/users", json={"email": _unique_email()}).json()
    client_for(super_admin).patch(f"/api/admin/users/{promoted['id']}", json={"platform_role": "super_admin"})
    assert admin.delete(f"/api/admin/users/{promoted['id']}").status_code == 403


def test_employee_cannot_remove_users(employee, super_admin):
    pending = client_for(super_admin).post("/api/admin/users", json={"email": _unique_email()}).json()
    assert client_for(employee).delete(f"/api/admin/users/{pending['id']}").status_code == 403


def test_removing_unknown_user_returns_404(super_admin):
    assert client_for(super_admin).delete(f"/api/admin/users/{uuid.uuid4()}").status_code == 404


# ---------- Invitations (set-password email through Keycloak) ----------


def test_failed_invitation_keeps_the_employee_and_reports_why(super_admin, fake_keycloak):
    fake_keycloak.error = "the identity provider is unreachable (ConnectError)"
    client = client_for(super_admin)
    email = _unique_email()
    response = client.post("/api/admin/users", json={"email": email})
    assert response.status_code == 201
    assert response.json()["invitation"]["status"] == "failed"
    assert "unreachable" in response.json()["invitation"]["detail"]
    assert any(u["email"] == email for u in client.get("/api/admin/users").json())
    entries = client.get("/api/admin/audit", params={"limit": 50}).json()["items"]
    assert any(e["action"] == "user.invite" and e["outcome"] == "failure" for e in entries)


def test_invitation_can_be_sent_again_to_a_pending_employee(super_admin, fake_keycloak):
    client = client_for(super_admin)
    email = _unique_email()
    created = client.post("/api/admin/users", json={"email": email}).json()
    response = client.post(f"/api/admin/users/{created['id']}/invite")
    assert response.status_code == 200 and response.json() == {"status": "sent"}
    assert fake_keycloak.invited == [email, email]


def test_no_invitation_for_employee_who_has_signed_in(super_admin, employee, fake_keycloak):
    assert client_for(super_admin).post(f"/api/admin/users/{employee['id']}/invite").status_code == 409
    assert fake_keycloak.invited == []


def test_employee_cannot_send_invitations(employee, super_admin):
    pending = client_for(super_admin).post("/api/admin/users", json={"email": _unique_email()}).json()
    assert client_for(employee).post(f"/api/admin/users/{pending['id']}/invite").status_code == 403


def test_removing_pending_employee_removes_their_unused_identity(super_admin, fake_keycloak):
    client = client_for(super_admin)
    email = _unique_email()
    created = client.post("/api/admin/users", json={"email": email}).json()
    assert client.delete(f"/api/admin/users/{created['id']}").status_code == 204
    assert fake_keycloak.removed == [email]
