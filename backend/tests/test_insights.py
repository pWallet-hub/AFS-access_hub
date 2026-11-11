"""Detail views: the role catalog, one role, one platform role, one employee, and the signed-in person's own account."""

import uuid

from app.db import connect

from test_api import access_admin, client_for, employee, make_user, super_admin  # noqa: F401 (fixtures)
from test_dashboard import _app, _assign, _role


def _grant(admin, user, application, role=None):
    body = {"user_id": str(user["id"]), "application_id": str(application["id"])}
    if role:
        body["app_role_id"] = str(role["id"])
    response = client_for(admin).post("/api/admin/assignments", json=body)
    assert response.status_code == 201
    return response.json()["id"]


def _scope(super_admin, access_admin, applications):
    response = client_for(super_admin).put(f"/api/admin/users/{access_admin['id']}/managed-applications",
                                           json={"application_ids": [str(a["id"]) for a in applications]})
    assert response.status_code == 200


def test_employees_cannot_read_detail_views(employee):
    client = client_for(employee)
    assert client.get("/api/admin/roles").status_code == 403
    assert client.get(f"/api/admin/roles/{uuid.uuid4()}").status_code == 403
    assert client.get("/api/admin/platform-roles/employee").status_code == 403
    assert client.get(f"/api/admin/users/{employee['id']}/profile").status_code == 403


def test_role_detail_lists_holders_with_who_granted_them_and_the_history(super_admin):
    application = _app()
    role = _role(application, "approver")
    holder, other = make_user(), make_user()
    _grant(super_admin, holder, application, role)
    assignment_id = _grant(super_admin, other, application, role)
    assert client_for(super_admin).delete(f"/api/admin/assignments/{assignment_id}").status_code == 204
    _assign(make_user(), application)  # standard access, not this role

    body = client_for(super_admin).get(f"/api/admin/roles/{role['id']}").json()
    assert body["role"]["name"] == "approver" and body["role"]["application_slug"] == application["slug"]
    assert [h["email"] for h in body["holders"]] == [holder["email"]]
    assert body["holders"][0]["granted_by"] == "Test User"
    stats = body["stats"]
    assert stats["holders"] == 1 and stats["application_people"] == 2 and stats["share_of_application"] == 0.5
    assert stats["joined_total"] == 2 and stats["left_total"] == 1
    assert len(body["weeks"]) == 12 and body["joined_weekly"][-1] == 2 and body["left_weekly"][-1] == 1
    assert {e["action"] for e in body["events"]} >= {"assignment.grant", "assignment.revoke"}
    assert [s["name"] for s in body["siblings"]] == ["approver"]


def test_access_admin_cannot_open_roles_outside_their_scope(super_admin, access_admin):
    mine, other = _app(), _app()
    my_role, other_role = _role(mine), _role(other)
    _scope(super_admin, access_admin, [mine])
    client = client_for(access_admin)
    assert client.get(f"/api/admin/roles/{my_role['id']}").status_code == 200
    assert client.get(f"/api/admin/roles/{other_role['id']}").status_code == 403
    ids = {r["id"] for r in client.get("/api/admin/roles").json()["roles"]}
    assert str(my_role["id"]) in ids and str(other_role["id"]) not in ids


def test_role_catalog_counts_holders_and_platform_roles(super_admin):
    application = _app()
    role = _role(application)
    _grant(super_admin, make_user(), application, role)
    body = client_for(super_admin).get("/api/admin/roles").json()
    entry = next(r for r in body["roles"] if r["id"] == str(role["id"]))
    assert entry["holders"] == 1 and entry["joined_30d"] == 1 and entry["last_change_at"] is not None
    assert [p["name"] for p in body["platform_roles"]] == ["super_admin", "access_admin", "employee"]


def test_platform_role_detail_shows_people_scope_and_changes(super_admin):
    application = _app()
    person = make_user()
    client = client_for(super_admin)
    assert client.patch(f"/api/admin/users/{person['id']}", json={"platform_role": "access_admin"}).status_code == 200
    _scope(super_admin, person, [application])
    body = client.get("/api/admin/platform-roles/access_admin").json()
    row = next(p for p in body["people"] if p["id"] == str(person["id"]))
    assert row["managed_applications"] == [application["name"]]
    assert any(c["subject"] == "Test User" and c["to_role"] == "access_admin" for c in body["changes"])
    assert client.get("/api/admin/platform-roles/nobody").status_code == 404


def test_profile_shows_current_and_past_access_and_changes(super_admin):
    application, other = _app(), _app()
    person = make_user()
    _grant(super_admin, person, application)
    revoked = _grant(super_admin, person, other)
    client_for(super_admin).delete(f"/api/admin/assignments/{revoked}")
    body = client_for(super_admin).get(f"/api/admin/users/{person['id']}/profile").json()
    assert [a["application_id"] for a in body["access"]] == [str(application["id"])]
    assert [a["application_id"] for a in body["past_access"]] == [str(other["id"])]
    assert body["past_access"][0]["revoked_by"] == "Test User"
    assert {c["action"] for c in body["changes"]} >= {"assignment.grant", "assignment.revoke"}
    assert len(body["sign_ins"]["days"]) == 30


def test_profile_for_access_admin_shows_only_managed_applications(super_admin, access_admin):
    mine, other = _app(), _app()
    person = make_user()
    _assign(person, mine)
    _assign(person, other)
    _scope(super_admin, access_admin, [mine])
    body = client_for(access_admin).get(f"/api/admin/users/{person['id']}/profile").json()
    assert body["scope"] == "managed"
    assert [a["application_id"] for a in body["access"]] == [str(mine["id"])]


def test_everyone_can_read_their_own_account(super_admin, employee):
    application = _app()
    role = _role(application, "viewer")
    with connect() as conn:
        conn.execute("UPDATE app_roles SET description = 'Reads reports' WHERE id = %s", (role["id"],))
    _grant(super_admin, employee, application, role)
    body = client_for(employee).get("/api/me/overview").json()
    assert body["account"]["email"] == employee["email"]
    assert body["access"][0]["role_name"] == "viewer" and body["access"][0]["role_description"] == "Reads reports"
    assert body["changes"][0]["action"] == "assignment.grant" and "details" not in body["changes"][0]
    assert len(body["sign_ins"]["success"]) == 30


def test_audit_entries_carry_the_actor_name(super_admin):
    application = _app()
    _grant(super_admin, make_user(), application)
    items = client_for(super_admin).get(f"/api/admin/audit?application={application['slug']}").json()["items"]
    assert items[0]["actor_name"] == "Test User" and items[0]["actor_user_id"] == str(super_admin["id"])
