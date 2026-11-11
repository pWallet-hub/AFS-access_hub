"""The dashboard overview: who may read it, what each role sees, and that new applications appear without code changes."""

import uuid

from app.db import connect

from test_api import access_admin, client_for, employee, make_user, super_admin  # noqa: F401 (fixtures)


def _app(name="Dash"):
    slug = f"dash-{uuid.uuid4().hex[:8]}"
    with connect() as conn:
        return conn.execute(
            "INSERT INTO applications (slug, name, launch_url, enabled) VALUES (%s, %s, %s, true) RETURNING *",
            (slug, f"{name} {slug}", "https://example.test/app"),
        ).fetchone()


def _role(application, name="viewer"):
    with connect() as conn:
        return conn.execute(
            "INSERT INTO app_roles (application_id, name, external_role) VALUES (%s, %s, %s) RETURNING *",
            (application["id"], name, name.upper()),
        ).fetchone()


def _assign(user, application, role=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO assignments (user_id, application_id, app_role_id) VALUES (%s, %s, %s)",
            (user["id"], application["id"], role["id"] if role else None),
        )


def _sessions(application, n, days_ago=0):
    with connect() as conn:
        conn.execute(
            "INSERT INTO application_usage (application_id, day, sessions) VALUES (%s, current_date - %s, %s)",
            (application["id"], days_ago, n),
        )


def _find(body, application):
    return next(a for a in body["applications"] if a["id"] == str(application["id"]))


def test_employees_cannot_read_the_overview(employee):
    assert client_for(employee).get("/api/admin/overview").status_code == 403


def test_a_newly_registered_application_appears_with_its_figures(super_admin):
    application = _app()
    role = _role(application)
    _assign(make_user(), application, role)
    _assign(make_user(), application)
    _sessions(application, 5)
    _sessions(application, 3, days_ago=10)
    _sessions(application, 7, days_ago=40)

    body = client_for(super_admin).get("/api/admin/overview").json()
    assert body["scope"] == "all" and body["can_see_usage"] is True
    assert len(body["days"]) == 30
    entry = _find(body, application)
    assert entry["people"] == 2
    assert entry["standard_access"] == 1
    assert entry["roles"] == [{"id": str(role["id"]), "name": "viewer", "external_role": "VIEWER", "people": 1}]
    assert entry["sessions_daily"][-1] == 5 and len(entry["sessions_daily"]) == 30
    assert entry["sessions_7d"] == 5 and entry["sessions_30d"] == 8 and entry["sessions_prev_30d"] == 7
    assert entry["health"] == "not_connected"


def test_colors_are_stable_when_applications_are_added(super_admin):
    first = _app()
    before = _find(client_for(super_admin).get("/api/admin/overview").json(), first)["color_index"]
    _app()
    after = _find(client_for(super_admin).get("/api/admin/overview").json(), first)["color_index"]
    assert before == after


def test_access_admin_sees_figures_only_for_managed_applications_and_no_sessions(super_admin, access_admin):
    mine, other = _app("Mine"), _app("Other")
    _assign(make_user(), mine)
    _assign(make_user(), other)
    _sessions(mine, 4)
    response = client_for(super_admin).put(
        f"/api/admin/users/{access_admin['id']}/managed-applications", json={"application_ids": [str(mine["id"])]})
    assert response.status_code == 200

    body = client_for(access_admin).get("/api/admin/overview").json()
    assert body["scope"] == "managed" and body["can_see_usage"] is False
    assert body["live"] == {}
    assert "sessions_30d" not in body["totals"]
    managed = _find(body, mine)
    assert managed["managed"] is True and managed["people"] == 1 and "sessions_daily" not in managed
    unmanaged = _find(body, other)
    assert unmanaged["managed"] is False and "people" not in unmanaged and "roles" not in unmanaged
    assert body["totals"]["assignments"] == 1


def test_sign_ins_are_counted_per_day(super_admin):
    before = client_for(super_admin).get("/api/admin/overview").json()["sign_ins"]["success"][-1]
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor_identity, action, target_type, outcome) VALUES ('t', 'auth.login', 'user', 'success')")
    after = client_for(super_admin).get("/api/admin/overview").json()["sign_ins"]["success"][-1]
    assert after == before + 1
