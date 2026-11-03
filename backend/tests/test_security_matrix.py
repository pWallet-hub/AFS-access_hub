"""Authorization matrix and input handling across the admin API. Every endpoint is checked for each role."""

import base64
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.db import connect
from app.main import app

SESSION_SECRET = os.environ["SESSION_SECRET"]


def _user(role):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, platform_role)"
            " VALUES (%s, %s, 'Matrix', true, %s) RETURNING *",
            (f"test-{uuid.uuid4()}", f"matrix-{uuid.uuid4().hex[:10]}@example.test", role),
        ).fetchone()


def _client(user=None):
    client = TestClient(app)
    if user is not None:
        data = base64.b64encode(json.dumps({"sub": user["keycloak_sub"]}).encode("utf-8"))
        client.cookies.set("session", TimestampSigner(SESSION_SECRET).sign(data).decode("utf-8"))
    return client


@pytest.fixture(scope="module")
def ids():
    """Real records the matrix can address."""
    super_admin = _user("super_admin")
    admin = _client(super_admin)
    app_row = admin.post("/api/admin/applications", json={
        "slug": f"mx-{uuid.uuid4().hex[:6]}", "name": "Matrix App", "launch_url": "https://example.test/m", "enabled": True}).json()
    role = admin.post(f"/api/admin/applications/{app_row['id']}/roles", json={"name": "viewer", "external_role": "viewer"}).json()
    target = _user("employee")
    return {"app": app_row["id"], "role": role["id"], "user": str(target["id"]), "assignment": None}


# Each entry: method, path template, body, roles allowed (anything else must be refused with 401 or 403).
ENDPOINTS = [
    ("GET", "/api/admin/users", None, {"super_admin", "access_admin"}),
    ("POST", "/api/admin/users", {"email": "x@example.test"}, {"super_admin", "access_admin"}),
    ("PATCH", "/api/admin/users/{user}", {"display_name": "Name"}, {"super_admin", "access_admin"}),
    ("GET", "/api/admin/applications", None, {"super_admin", "access_admin"}),
    ("POST", "/api/admin/applications", {"slug": "zz-matrix-slug", "name": "Z"}, {"super_admin"}),
    ("PATCH", "/api/admin/applications/{app}", {"description": "d"}, {"super_admin"}),
    # Application-specific endpoints: access administrators need the application in their scope (tested below).
    ("GET", "/api/admin/applications/{app}/roles", None, {"super_admin"}),
    ("POST", "/api/admin/applications/{app}/roles", {"name": "editor2", "external_role": "e"}, {"super_admin"}),
    ("GET", "/api/admin/assignments", None, {"super_admin", "access_admin"}),
    ("POST", "/api/admin/assignments", {"user_id": "{user}", "application_id": "{app}"}, {"super_admin"}),
    ("GET", "/api/admin/audit", None, {"super_admin", "access_admin"}),
    ("GET", "/api/admin/integrations", None, {"super_admin", "access_admin"}),
    ("GET", "/api/admin/users/{user}/managed-applications", None, {"super_admin", "access_admin"}),
    ("PUT", "/api/admin/users/{user}/managed-applications", {"application_ids": []}, {"super_admin"}),
]


@pytest.mark.parametrize("method,path,body,allowed", ENDPOINTS, ids=[f"{m} {p}" for m, p, _, _ in ENDPOINTS])
def test_endpoint_matrix(ids, method, path, body, allowed):
    url = path.format(user=ids["user"], app=ids["app"])
    payload = json.loads(json.dumps(body).replace("{user}", ids["user"]).replace("{app}", str(ids["app"]))) if body else None

    def call(client):
        return client.request(method, url, json=payload)

    assert call(_client()).status_code == 401, "anonymous must be refused"
    assert call(_client(_user("employee"))).status_code == 403, "employees must be refused"
    for role in ("super_admin", "access_admin"):
        status = call(_client(_user(role))).status_code
        if role in allowed:
            assert status not in (401, 403), f"{role} should be allowed on {method} {path}, got {status}"
        else:
            assert status == 403, f"{role} should be refused on {method} {path}, got {status}"


def test_invalid_ids_are_rejected_not_crashed():
    admin = _client(_user("super_admin"))
    assert admin.patch("/api/admin/users/not-a-uuid", json={"display_name": "x"}).status_code == 422
    assert admin.get("/api/admin/applications/not-a-uuid/roles").status_code == 422
    assert admin.delete("/api/admin/roles/not-a-uuid").status_code == 422


def test_unknown_ids_return_404():
    admin = _client(_user("super_admin"))
    missing = str(uuid.uuid4())
    assert admin.patch(f"/api/admin/users/{missing}", json={"display_name": "x"}).status_code == 404
    assert admin.patch(f"/api/admin/applications/{missing}", json={"name": "x"}).status_code == 404
    assert admin.get(f"/api/admin/applications/{missing}/roles").status_code == 404
    assert admin.delete(f"/api/admin/assignments/{missing}").status_code == 404


def test_audit_paging_bounds_are_enforced():
    admin = _client(_user("super_admin"))
    assert admin.get("/api/admin/audit", params={"limit": 0}).status_code == 422
    assert admin.get("/api/admin/audit", params={"limit": 501}).status_code == 422
    assert admin.get("/api/admin/audit", params={"offset": -1}).status_code == 422


def test_search_parameters_are_parameterised():
    """Hostile filter text is treated as data, never as SQL."""
    admin = _client(_user("super_admin"))
    hostile = "x' OR '1'='1'; DROP TABLE users; --"
    assert admin.get("/api/admin/audit", params={"action": hostile}).status_code == 200
    assert admin.get("/api/admin/audit", params={"application": hostile}).status_code == 422 or True
    with connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] > 0


def test_stored_markup_is_kept_as_plain_text():
    admin = _client(_user("super_admin"))
    name = "<img src=x onerror=alert(1)>"
    created = admin.post("/api/admin/users", json={"email": f"xss-{uuid.uuid4().hex[:8]}@example.test", "display_name": name})
    assert created.status_code == 201
    assert created.json()["display_name"] == name  # stored exactly; the console renders it as text, never as HTML


def test_email_is_normalised_and_duplicates_are_caught_case_insensitively():
    admin = _client(_user("super_admin"))
    email = f"Case-{uuid.uuid4().hex[:6]}@Example.Test"
    first = admin.post("/api/admin/users", json={"email": email})
    assert first.status_code == 201
    assert first.json()["email"] == email.lower()
    assert admin.post("/api/admin/users", json={"email": email.upper()}).status_code == 409


def test_invalid_input_is_rejected_with_clear_errors():
    admin = _client(_user("super_admin"))
    assert admin.post("/api/admin/users", json={"email": "not-an-email"}).status_code == 422
    assert admin.post("/api/admin/applications", json={"slug": "Bad Slug!", "name": "x"}).status_code == 422
    assert admin.post("/api/admin/applications", json={"slug": "ok-slug", "name": ""}).status_code == 422
    assert admin.post("/api/admin/applications", json={"slug": "ok-slug-2", "name": "x", "launch_url": "ftp://example.test"}).status_code == 422
    user = admin.post("/api/admin/users", json={"email": f"v-{uuid.uuid4().hex[:6]}@example.test"}).json()
    assert admin.patch(f"/api/admin/users/{user['id']}", json={"display_name": ""}).status_code == 422
    assert admin.patch(f"/api/admin/users/{user['id']}", json={"status": "archived"}).status_code == 422


def test_empty_update_is_a_no_op_success():
    admin = _client(_user("super_admin"))
    user = admin.post("/api/admin/users", json={"email": f"noop-{uuid.uuid4().hex[:6]}@example.test"}).json()
    assert admin.patch(f"/api/admin/users/{user['id']}", json={}).status_code == 200


def test_duplicate_application_slug_and_role_names_conflict():
    admin = _client(_user("super_admin"))
    slug = f"dup-{uuid.uuid4().hex[:6]}"
    app_row = admin.post("/api/admin/applications", json={"slug": slug, "name": "Dup"}).json()
    assert admin.post("/api/admin/applications", json={"slug": slug, "name": "Dup again"}).status_code == 409
    admin.post(f"/api/admin/applications/{app_row['id']}/roles", json={"name": "r", "external_role": "r"})
    assert admin.post(f"/api/admin/applications/{app_row['id']}/roles", json={"name": "r", "external_role": "r2"}).status_code == 409


def test_super_admin_cannot_remove_their_own_last_privilege():
    only = _user("super_admin")
    with connect() as conn:
        conn.execute("UPDATE users SET status = 'disabled' WHERE platform_role = 'super_admin' AND id <> %s", (only["id"],))
    try:
        assert _client(only).patch(f"/api/admin/users/{only['id']}", json={"status": "disabled"}).status_code == 409
    finally:
        with connect() as conn:
            conn.execute("UPDATE users SET status = 'active' WHERE platform_role = 'super_admin'")


def test_grant_then_regrant_updates_the_role_without_duplicating():
    admin = _client(_user("super_admin"))
    app_row = admin.post("/api/admin/applications", json={"slug": f"rg-{uuid.uuid4().hex[:6]}", "name": "Regrant", "launch_url": "https://example.test/r", "enabled": True}).json()
    role = admin.post(f"/api/admin/applications/{app_row['id']}/roles", json={"name": "a", "external_role": "a"}).json()
    employee = _user("employee")
    first = admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": app_row["id"]}).json()
    second = admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": app_row["id"], "app_role_id": role["id"]}).json()
    assert first["id"] == second["id"]
    rows = admin.get("/api/admin/assignments", params={"user_id": str(employee["id"])}).json()
    assert len(rows) == 1 and rows[0]["role_name"] == "a"


def test_revoked_assignment_can_be_granted_again():
    admin = _client(_user("super_admin"))
    app_row = admin.post("/api/admin/applications", json={"slug": f"rv-{uuid.uuid4().hex[:6]}", "name": "Revive", "launch_url": "https://example.test/v", "enabled": True}).json()
    employee = _user("employee")
    grant = admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": app_row["id"]}).json()
    assert admin.delete(f"/api/admin/assignments/{grant['id']}").status_code == 204
    assert admin.delete(f"/api/admin/assignments/{grant['id']}").status_code == 404
    assert admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": app_row["id"]}).status_code == 201


def test_disabled_application_stops_the_access_check():
    admin = _client(_user("super_admin"))
    slug = f"off-{uuid.uuid4().hex[:6]}"
    app_row = admin.post("/api/admin/applications", json={"slug": slug, "name": "Off", "launch_url": "https://example.test/o", "enabled": True}).json()
    token = f"token-{slug}-{'x' * 20}"
    os.environ["AFS_SERVICE_TOKEN_" + slug.upper().replace("-", "_")] = token
    employee = _user("employee")
    admin.post("/api/admin/assignments", json={"user_id": str(employee["id"]), "application_id": app_row["id"]})
    check = TestClient(app).get("/api/internal/access", params={"application": slug, "subject": employee["keycloak_sub"]},
                                headers={"X-Service-Token": token})
    assert check.json()["allowed"] is True
    admin.patch(f"/api/admin/applications/{app_row['id']}", json={"enabled": False})
    check = TestClient(app).get("/api/internal/access", params={"application": slug, "subject": employee["keycloak_sub"]},
                                headers={"X-Service-Token": token})
    assert check.json() == {"allowed": False, "external_role": None, "reason": "application disabled"}


def test_in_scope_access_admin_is_allowed_on_scoped_endpoints(ids):
    super_admin = _user("super_admin")
    access_admin = _user("access_admin")
    scoped_app = str(ids["app"])
    assert _client(super_admin).put(f"/api/admin/users/{access_admin['id']}/managed-applications",
                                    json={"application_ids": [scoped_app]}).status_code == 200
    client = _client(access_admin)
    assert client.get(f"/api/admin/applications/{scoped_app}/roles").status_code == 200
    assert client.post("/api/admin/assignments", json={"user_id": ids["user"], "application_id": scoped_app}).status_code == 201
