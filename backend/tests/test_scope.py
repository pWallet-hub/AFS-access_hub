"""Scoped delegation: access administrators manage access only for the applications they are given."""

import base64
import json
import os
import uuid

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.db import connect
from app.main import app

SESSION_SECRET = os.environ["SESSION_SECRET"]


def _user(platform_role):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, platform_role)"
            " VALUES (%s, %s, 'Test', true, %s) RETURNING *",
            (f"test-{uuid.uuid4()}", f"test-{uuid.uuid4().hex[:10]}@example.test", platform_role),
        ).fetchone()


def _client(user):
    client = TestClient(app)
    data = base64.b64encode(json.dumps({"sub": user["keycloak_sub"]}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner(SESSION_SECRET).sign(data).decode("utf-8"))
    return client


def _app(name):
    slug = f"scope-{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        return conn.execute(
            "INSERT INTO applications (slug, name, launch_url, enabled) VALUES (%s, %s, 'https://example.test/a', true) RETURNING *",
            (slug, name),
        ).fetchone()


def _role(application):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO app_roles (application_id, name, external_role) VALUES (%s, 'viewer', 'viewer') RETURNING *",
            (application["id"],),
        ).fetchone()


def _set_scope(super_admin, access_admin, applications):
    return _client(super_admin).put(
        f"/api/admin/users/{access_admin['id']}/managed-applications",
        json={"application_ids": [str(a["id"]) for a in applications]},
    )


def test_access_admin_without_scope_cannot_grant_anything():
    admin = _user("access_admin")
    employee = _user("employee")
    application = _app("Unscoped App")
    response = _client(admin).post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": str(application["id"])})
    assert response.status_code == 403


def test_access_admin_can_grant_within_scope_only():
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    employee = _user("employee")
    allowed = _app("Allowed App")
    other = _app("Other App")
    assert _set_scope(super_admin, access_admin, [allowed]).status_code == 200

    admin = _client(access_admin)
    assert admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": str(allowed["id"])}).status_code == 201
    assert admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": str(other["id"])}).status_code == 403


def test_refused_grant_is_audited_with_the_reason():
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    employee = _user("employee")
    other = _app("Audited App")
    _client(access_admin).post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": str(other["id"])})
    with connect() as conn:
        row = conn.execute(
            "SELECT outcome, details FROM audit_log WHERE action = 'assignment.grant' AND application_slug = %s"
            " ORDER BY id DESC LIMIT 1",
            (other["slug"],),
        ).fetchone()
    assert row["outcome"] == "failure"
    assert row["details"]["reason"] == "not a manager of this application"


def test_revoke_outside_scope_is_refused():
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    employee = _user("employee")
    other = _app("Revoke Scope App")
    grant = _client(super_admin).post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": str(other["id"])})
    assignment_id = grant.json()["id"]
    _set_scope(super_admin, access_admin, [_app("Something else")])
    assert _client(access_admin).delete(f"/api/admin/assignments/{assignment_id}").status_code == 403
    assert _client(super_admin).delete(f"/api/admin/assignments/{assignment_id}").status_code == 204


def test_assignment_list_shows_only_managed_applications():
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    employee = _user("employee")
    mine = _app("Listed Mine")
    theirs = _app("Listed Theirs")
    for application in (mine, theirs):
        _client(super_admin).post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": str(application["id"])})
    _set_scope(super_admin, access_admin, [mine])
    ids = {row["application_id"] for row in _client(access_admin).get("/api/admin/assignments").json()}
    assert str(mine["id"]) in ids
    assert str(theirs["id"]) not in ids


def test_roles_of_unmanaged_applications_are_refused():
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    application = _app("Roles App")
    _role(application)
    assert _client(access_admin).get(f"/api/admin/applications/{application['id']}/roles").status_code == 403
    _set_scope(super_admin, access_admin, [application])
    assert _client(access_admin).get(f"/api/admin/applications/{application['id']}/roles").status_code == 200


def test_only_super_admins_can_set_scope():
    access_admin = _user("access_admin")
    other_admin = _user("access_admin")
    application = _app("Scope Setter App")
    response = _client(access_admin).put(f"/api/admin/users/{other_admin['id']}/managed-applications",
                                         json={"application_ids": [str(application["id"])]})
    assert response.status_code == 403


def test_scope_applies_only_to_access_admins():
    super_admin = _user("super_admin")
    employee = _user("employee")
    application = _app("Employee Scope App")
    response = _set_scope(super_admin, employee, [application])
    assert response.status_code == 409


def test_me_reports_the_managed_applications():
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    application = _app("Me Reports App")
    assert _client(super_admin).get("/api/me").json()["managed_application_ids"] is None
    _set_scope(super_admin, access_admin, [application])
    assert _client(access_admin).get("/api/me").json()["managed_application_ids"] == [str(application["id"])]


def test_demotion_clears_the_scope():
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    application = _app("Demotion App")
    _set_scope(super_admin, access_admin, [application])
    _client(super_admin).patch(f"/api/admin/users/{access_admin['id']}", json={"platform_role": "employee"})
    _client(super_admin).patch(f"/api/admin/users/{access_admin['id']}", json={"platform_role": "access_admin"})
    assert _client(super_admin).get(f"/api/admin/users/{access_admin['id']}/managed-applications").json() == []
