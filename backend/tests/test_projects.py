"""Projects group applications; employees see their assigned applications grouped by project."""

import base64
import json
import os
import uuid

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.db import connect
from app.main import app

SESSION_SECRET = os.environ["SESSION_SECRET"]


def _user(role="employee"):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, platform_role)"
            " VALUES (%s, %s, 'Test', true, %s) RETURNING *",
            (f"test-{uuid.uuid4()}", f"test-{uuid.uuid4().hex[:10]}@example.test", role),
        ).fetchone()


def _client(user):
    client = TestClient(app)
    data = base64.b64encode(json.dumps({"sub": user["keycloak_sub"]}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner(SESSION_SECRET).sign(data).decode("utf-8"))
    return client


def test_applications_are_grouped_under_their_project():
    admin = _client(_user("super_admin"))
    project = admin.post("/api/admin/projects", json={"slug": f"proj-{uuid.uuid4().hex[:6]}", "name": "Test Project"}).json()
    app_row = admin.post("/api/admin/applications", json={
        "slug": f"grp-{uuid.uuid4().hex[:6]}", "name": "Grouped App", "launch_url": "https://example.test/g",
        "enabled": True, "project_id": project["id"]}).json()
    employee = _user()
    admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": app_row["id"]})

    groups = _client(employee).get("/api/projects").json()
    project_group = next(g for g in groups if g["slug"] == project["slug"])
    assert project_group["name"] == "Test Project"
    assert [a["name"] for a in project_group["applications"]] == ["Grouped App"]


def test_applications_without_a_project_fall_under_other():
    admin = _client(_user("super_admin"))
    app_row = admin.post("/api/admin/applications", json={
        "slug": f"loose-{uuid.uuid4().hex[:6]}", "name": "Loose App", "launch_url": "https://example.test/l", "enabled": True}).json()
    employee = _user()
    admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": app_row["id"]})
    other = next(g for g in _client(employee).get("/api/projects").json() if g["slug"] == "other")
    assert other["name"] == "Other applications"
    assert "Loose App" in [a["name"] for a in other["applications"]]


def test_unknown_project_is_refused():
    admin = _client(_user("super_admin"))
    response = admin.post("/api/admin/applications", json={
        "slug": f"bad-{uuid.uuid4().hex[:6]}", "name": "Bad", "project_id": str(uuid.uuid4())})
    assert response.status_code == 422


def test_employee_with_no_assignments_sees_no_projects():
    assert _client(_user()).get("/api/projects").json() == []


def test_project_changes_are_audited_without_failing():
    admin = _client(_user("super_admin"))
    project = admin.post("/api/admin/projects", json={"slug": f"aud-{uuid.uuid4().hex[:6]}", "name": "Audited"}).json()
    app_row = admin.post("/api/admin/applications", json={"slug": f"aud-{uuid.uuid4().hex[:6]}", "name": "Audit App"}).json()
    response = admin.patch(f"/api/admin/applications/{app_row['id']}", json={"project_id": project["id"]})
    assert response.status_code == 200
    assert response.json()["project_id"] == project["id"]


def test_duplicate_project_slug_conflicts():
    admin = _client(_user("super_admin"))
    slug = f"dupp-{uuid.uuid4().hex[:6]}"
    assert admin.post("/api/admin/projects", json={"slug": slug, "name": "First"}).status_code == 201
    assert admin.post("/api/admin/projects", json={"slug": slug, "name": "Second"}).status_code == 409


def test_access_admin_cannot_create_projects():
    with connect() as conn:
        access_admin = conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, platform_role)"
            " VALUES (%s, %s, 'AA', true, 'access_admin') RETURNING *",
            (f"test-{uuid.uuid4()}", f"aa-{uuid.uuid4().hex[:8]}@example.test"),
        ).fetchone()
    response = _client(access_admin).post("/api/admin/projects", json={"slug": f"nope-{uuid.uuid4().hex[:6]}", "name": "No"})
    assert response.status_code == 403
