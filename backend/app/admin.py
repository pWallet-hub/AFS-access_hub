"""Administrative API: employees, applications, role mappings, assignments and the audit log.

Every endpoint checks the platform role on the server. The frontend hides controls the user
cannot use, but that is never relied on for protection.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from psycopg import errors

from .usage import application_usage, biocap_live_stats
from .audit import record
from .auth import require_platform_role
from .db import connect
from .notifications import notify
from .schemas import ApplicationCreate, ApplicationUpdate, AssignmentCreate, ProjectCreate, RoleCreate, UserCreate, UserUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])

ACCESS_ADMIN = require_platform_role("super_admin", "access_admin")
SUPER_ADMIN = require_platform_role("super_admin")

USER_COLUMNS = (
    "id, email, display_name, platform_role, status, email_verified,"
    " keycloak_sub IS NOT NULL AS linked, created_at, last_login_at"
)


def _deny(conn, actor, action, target_type, target_id=None, application_slug=None):
    record(
        conn,
        actor=actor,
        action=action,
        outcome="failure",
        target_type=target_type,
        target_id=target_id,
        application_slug=application_slug,
        details={"reason": "insufficient platform role"},
    )
    raise HTTPException(status_code=403, detail="You do not have permission for this action.")


def _manages(conn, actor, application_id):
    """Super administrators manage every application. Access administrators manage only the applications listed for them."""
    if actor["platform_role"] == "super_admin":
        return True
    row = conn.execute(
        "SELECT 1 AS ok FROM application_managers WHERE user_id = %s AND application_id = %s",
        (actor["id"], application_id),
    ).fetchone()
    return row is not None


def _require_manages(conn, actor, application, action, target_type, target_id=None):
    if _manages(conn, actor, application["id"]):
        return
    record(
        conn,
        actor=actor,
        action=action,
        outcome="failure",
        target_type=target_type,
        target_id=target_id,
        application_slug=application["slug"],
        details={"reason": "not a manager of this application"},
    )
    raise HTTPException(status_code=403, detail=f"You do not manage access to {application['name']}.")


def managed_application_ids(conn, actor):
    """None for super administrators (no limit); otherwise the list of application ids this administrator manages."""
    if actor["platform_role"] == "super_admin":
        return None
    return [row["application_id"] for row in conn.execute(
        "SELECT application_id FROM application_managers WHERE user_id = %s", (actor["id"],)).fetchall()]


def _active_super_admin_count(conn):
    return conn.execute(
        "SELECT count(*) AS n FROM users WHERE platform_role = 'super_admin' AND status = 'active'"
    ).fetchone()["n"]


def _get_user(conn, user_id):
    row = conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return row


def _get_application(conn, application_id):
    row = conn.execute("SELECT * FROM applications WHERE id = %s", (application_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return row


# ---------- Employees ----------


@router.get("/users")
def list_users(actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        return conn.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY lower(email)").fetchall()


@router.post("/users", status_code=201)
def create_user(body: UserCreate, background: BackgroundTasks, actor=Depends(ACCESS_ADMIN)):
    email = body.email.lower()
    with connect() as conn:
        try:
            with conn.transaction():
                row = conn.execute(
                    "INSERT INTO users (email, display_name) VALUES (%s, %s) RETURNING id",
                    (email, body.display_name),
                ).fetchone()
        except errors.UniqueViolation:
            record(
                conn,
                actor=actor,
                action="user.create",
                outcome="failure",
                target_type="user",
                details={"reason": "email already registered"},
            )
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        record(
            conn,
            actor=actor,
            action="user.create",
            outcome="success",
            target_type="user",
            target_id=row["id"],
            details={"email": email},
        )
        created = _get_user(conn, row["id"])
    background.add_task(
        notify,
        email,
        created["id"],
        "account.created",
        "You have been added to AFS Command Center",
        [
            "An administrator has added you to AFS Command Center, the single place to reach the applications you are assigned.",
            "Sign in with your AFS account. Your applications appear once an administrator assigns them.",
        ],
        "Open AFS Command Center",
        "/",
    )
    return created


@router.patch("/users/{user_id}")
def update_user(user_id: UUID, body: UserUpdate, background: BackgroundTasks, actor=Depends(ACCESS_ADMIN)):
    changes = body.model_dump(exclude_none=True)
    with connect() as conn:
        if "platform_role" in changes and actor["platform_role"] != "super_admin":
            _deny(conn, actor, "user.update", "user", user_id)

        current = conn.execute("SELECT * FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="User not found")

        if current["platform_role"] == "super_admin" and current["status"] == "active":
            new_status = changes.get("status", current["status"])
            new_role = changes.get("platform_role", current["platform_role"])
            removing_super_admin = new_status != "active" or new_role != "super_admin"
            if removing_super_admin and _active_super_admin_count(conn) <= 1:
                record(
                    conn,
                    actor=actor,
                    action="user.update",
                    outcome="failure",
                    target_type="user",
                    target_id=user_id,
                    details={"reason": "last active super administrator"},
                )
                raise HTTPException(status_code=409, detail="The last active super administrator cannot be disabled or demoted")

        if "platform_role" in changes and changes["platform_role"] != "access_admin":
            # Application scope belongs to access administrators; it is cleared when the role changes away from that.
            conn.execute("DELETE FROM application_managers WHERE user_id = %s", (user_id,))

        if changes:
            allowed = {"status", "platform_role", "display_name"}
            assignments = ", ".join(f"{field} = %s" for field in changes if field in allowed)
            values = [changes[field] for field in changes if field in allowed]
            conn.execute(
                f"UPDATE users SET {assignments}, updated_at = now() WHERE id = %s",
                (*values, user_id),
            )
            record(
                conn,
                actor=actor,
                action="user.update",
                outcome="success",
                target_type="user",
                target_id=user_id,
                details={"changes": {f: {"from": current[f], "to": changes[f]} for f in changes if f in allowed}},
            )
        updated = _get_user(conn, user_id)
    if "status" in changes and changes["status"] != current["status"]:
        disabled = changes["status"] == "disabled"
        background.add_task(
            notify,
            current["email"],
            user_id,
            "account.disabled" if disabled else "account.reactivated",
            "Your AFS Command Center account has been disabled" if disabled else "Your AFS Command Center account is active again",
            [
                "Your access to AFS Command Center has been suspended. Contact an access administrator if you think this is a mistake."
                if disabled
                else "Your access to AFS Command Center has been restored. Sign in to see your applications.",
            ],
        )
    if "platform_role" in changes and changes["platform_role"] != current["platform_role"]:
        background.add_task(
            notify,
            current["email"],
            user_id,
            "account.role_changed",
            "Your AFS Command Center role has changed",
            [f"Your platform role is now {changes['platform_role'].replace('_', ' ')}."],
            "Open AFS Command Center",
            "/",
        )
    return updated


# ---------- Applications ----------


@router.get("/applications")
def list_applications(actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        return conn.execute(
            "SELECT a.id, a.slug, a.name, a.description, a.launch_url, a.enabled, a.integration_type, a.oidc_client_id,"
            " a.project_id, p.name AS project_name, a.created_at, a.updated_at"
            " FROM applications a LEFT JOIN projects p ON p.id = a.project_id ORDER BY a.name"
        ).fetchall()


@router.get("/projects")
def list_projects(actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        return conn.execute(
            "SELECT p.id, p.slug, p.name, p.description, count(a.id) AS application_count FROM projects p"
            " LEFT JOIN applications a ON a.project_id = p.id GROUP BY p.id ORDER BY p.name"
        ).fetchall()


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, actor=Depends(SUPER_ADMIN)):
    with connect() as conn:
        try:
            with conn.transaction():
                row = conn.execute(
                    "INSERT INTO projects (slug, name, description) VALUES (%s, %s, %s) RETURNING id, slug, name, description",
                    (body.slug, body.name, body.description),
                ).fetchone()
        except errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="A project with this slug already exists")
        record(conn, actor=actor, action="project.create", outcome="success", target_type="project", target_id=row["id"],
               details={"slug": body.slug, "name": body.name})
        return row


@router.post("/applications", status_code=201)
def create_application(body: ApplicationCreate, actor=Depends(SUPER_ADMIN)):
    if body.project_id is not None:
        with connect() as conn:
            if conn.execute("SELECT 1 AS ok FROM projects WHERE id = %s", (body.project_id,)).fetchone() is None:
                raise HTTPException(status_code=422, detail="The selected project does not exist")
    if body.enabled and not body.launch_url:
        raise HTTPException(status_code=422, detail="An application needs a launch URL before it can be enabled")
    with connect() as conn:
        try:
            with conn.transaction():
                row = conn.execute(
                    "INSERT INTO applications (slug, name, description, launch_url, enabled, integration_type,"
                    " oidc_client_id, project_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (
                        body.slug,
                        body.name,
                        body.description,
                        body.launch_url,
                        body.enabled,
                        body.integration_type,
                        body.oidc_client_id,
                        body.project_id,
                    ),
                ).fetchone()
        except errors.UniqueViolation:
            record(
                conn,
                actor=actor,
                action="application.create",
                outcome="failure",
                target_type="application",
                application_slug=body.slug,
                details={"reason": "slug already registered"},
            )
            raise HTTPException(status_code=409, detail="An application with this slug already exists")
        record(
            conn,
            actor=actor,
            action="application.create",
            outcome="success",
            target_type="application",
            target_id=row["id"],
            application_slug=body.slug,
            details={"enabled": body.enabled, "integration_type": body.integration_type},
        )
        return row


@router.patch("/applications/{application_id}")
def update_application(application_id: UUID, body: ApplicationUpdate, actor=Depends(SUPER_ADMIN)):
    changes = body.model_dump(exclude_none=True)
    with connect() as conn:
        current = _get_application(conn, application_id)
        if "project_id" in changes and conn.execute("SELECT 1 AS ok FROM projects WHERE id = %s", (changes["project_id"],)).fetchone() is None:
            raise HTTPException(status_code=422, detail="The selected project does not exist")
        final_url = changes["launch_url"] if "launch_url" in changes else current["launch_url"]
        final_enabled = changes["enabled"] if "enabled" in changes else current["enabled"]
        if final_enabled and not final_url:
            raise HTTPException(status_code=422, detail="An application needs a launch URL before it can be enabled")
        if changes:
            assignments = ", ".join(f"{field} = %s" for field in changes)
            conn.execute(
                f"UPDATE applications SET {assignments}, updated_at = now() WHERE id = %s",
                (*changes.values(), application_id),
            )
            record(
                conn,
                actor=actor,
                action="application.update",
                outcome="success",
                target_type="application",
                target_id=application_id,
                application_slug=current["slug"],
                details={"changes": {f: {"from": current[f], "to": changes[f]} for f in changes if f != "oidc_client_id"}},
            )
        return _get_application(conn, application_id)


# ---------- Application-specific roles (mappings) ----------


@router.get("/applications/{application_id}/roles")
def list_roles(application_id: UUID, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        application = _get_application(conn, application_id)
        _require_manages(conn, actor, application, "role_mapping.list", "application", application_id)
        return conn.execute(
            "SELECT id, name, external_role, description FROM app_roles WHERE application_id = %s ORDER BY name",
            (application_id,),
        ).fetchall()


@router.post("/applications/{application_id}/roles", status_code=201)
def create_role(application_id: UUID, body: RoleCreate, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        application = _get_application(conn, application_id)
        _require_manages(conn, actor, application, "role_mapping.create", "application", application_id)
        try:
            with conn.transaction():
                row = conn.execute(
                    "INSERT INTO app_roles (application_id, name, external_role, description)"
                    " VALUES (%s, %s, %s, %s) RETURNING id, name, external_role, description",
                    (application_id, body.name, body.external_role, body.description),
                ).fetchone()
        except errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="This role name already exists for the application")
        record(
            conn,
            actor=actor,
            action="role_mapping.create",
            outcome="success",
            target_type="app_role",
            target_id=row["id"],
            application_slug=application["slug"],
            details={"name": body.name, "external_role": body.external_role},
        )
        return row


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(role_id: UUID, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        role = conn.execute(
            "SELECT r.id, r.name, a.id AS application_id, a.name AS application_name, a.slug FROM app_roles r"
            " JOIN applications a ON a.id = r.application_id WHERE r.id = %s",
            (role_id,),
        ).fetchone()
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        _require_manages(conn, actor, {"id": role["application_id"], "slug": role["slug"], "name": role["application_name"]},
                         "role_mapping.delete", "app_role", role_id)
        try:
            with conn.transaction():
                conn.execute("DELETE FROM app_roles WHERE id = %s", (role_id,))
        except errors.ForeignKeyViolation:
            record(
                conn,
                actor=actor,
                action="role_mapping.delete",
                outcome="failure",
                target_type="app_role",
                target_id=role_id,
                application_slug=role["slug"],
                details={"reason": "role is referenced by assignments"},
            )
            raise HTTPException(status_code=409, detail="This role has been used in assignments and cannot be deleted")
        record(
            conn,
            actor=actor,
            action="role_mapping.delete",
            outcome="success",
            target_type="app_role",
            target_id=role_id,
            application_slug=role["slug"],
            details={"name": role["name"]},
        )


# ---------- Assignments ----------


@router.get("/usage")
def usage(actor=Depends(SUPER_ADMIN)):
    """Sessions per application, and live figures from applications that report them. Super administrators only."""
    with connect() as conn:
        rows = application_usage(conn)
    return {"applications": rows, "live": {"biocap": biocap_live_stats()}}


@router.get("/assignments")
def list_assignments(
    user_id: UUID | None = None,
    application_id: UUID | None = None,
    actor=Depends(ACCESS_ADMIN),
):
    filters = ["s.revoked_at IS NULL"]
    params = []
    if user_id:
        filters.append("s.user_id = %s")
        params.append(user_id)
    if application_id:
        filters.append("s.application_id = %s")
        params.append(application_id)
    with connect() as conn:
        managed = managed_application_ids(conn, actor)
        if managed is not None:
            filters.append("s.application_id = ANY(%s)")
            params.append(managed)
        return conn.execute(
            "SELECT s.id, s.user_id, u.email, s.application_id, a.slug AS application_slug, a.name AS application_name,"
            " s.app_role_id, r.name AS role_name, s.granted_at"
            " FROM assignments s"
            " JOIN users u ON u.id = s.user_id"
            " JOIN applications a ON a.id = s.application_id"
            " LEFT JOIN app_roles r ON r.id = s.app_role_id"
            f" WHERE {' AND '.join(filters)} ORDER BY a.name, lower(u.email)",
            params,
        ).fetchall()


@router.post("/assignments", status_code=201)
def grant_assignment(body: AssignmentCreate, background: BackgroundTasks, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        user = _get_user(conn, body.user_id)
        application = _get_application(conn, body.application_id)
        _require_manages(conn, actor, application, "assignment.grant", "assignment", body.user_id)
        if user["status"] != "active":
            raise HTTPException(status_code=409, detail="Disabled accounts cannot receive application access")
        if not application["enabled"]:
            raise HTTPException(status_code=409, detail="Disabled applications cannot be assigned")

        existing = conn.execute(
            "SELECT id, app_role_id FROM assignments WHERE user_id = %s AND application_id = %s AND revoked_at IS NULL"
            " FOR UPDATE",
            (body.user_id, body.application_id),
        ).fetchone()
        try:
            with conn.transaction():
                if existing:
                    row = conn.execute(
                        "UPDATE assignments SET app_role_id = %s WHERE id = %s RETURNING id",
                        (body.app_role_id, existing["id"]),
                    ).fetchone()
                    action = "assignment.update"
                else:
                    row = conn.execute(
                        "INSERT INTO assignments (user_id, application_id, app_role_id, granted_by)"
                        " VALUES (%s, %s, %s, %s) RETURNING id",
                        (body.user_id, body.application_id, body.app_role_id, actor["id"]),
                    ).fetchone()
                    action = "assignment.grant"
        except errors.ForeignKeyViolation:
            record(
                conn,
                actor=actor,
                action="assignment.grant",
                outcome="failure",
                target_type="assignment",
                target_id=body.user_id,
                application_slug=application["slug"],
                details={"reason": "role does not belong to this application"},
            )
            raise HTTPException(status_code=422, detail="The selected role does not belong to this application")
        record(
            conn,
            actor=actor,
            action=action,
            outcome="success",
            target_type="assignment",
            target_id=row["id"],
            application_slug=application["slug"],
            details={"user_email": user["email"], "app_role_id": str(body.app_role_id) if body.app_role_id else None},
        )
        role = None
        if body.app_role_id:
            role = conn.execute("SELECT name FROM app_roles WHERE id = %s", (body.app_role_id,)).fetchone()
        role_name = role["name"] if role else None
    role_text = f" with the role {role_name}" if role_name else ""
    if existing:
        # The access was already there: only the role changed, so the employee is told that instead.
        background.add_task(
            notify,
            user["email"],
            user["id"],
            "access.role_changed",
            f"Your role in {application['name']} changed",
            [f"Your role in {application['name']} is now {role_name or 'standard access'}. It applies at your next sign-in."],
            f"Open {application['name']}",
            "/",
        )
    else:
        background.add_task(
            notify,
            user["email"],
            user["id"],
            "access.granted",
            f"You now have access to {application['name']}",
            [f"You have been given access to {application['name']}{role_text}."],
            f"Open {application['name']}",
            "/",
        )
    return {"id": row["id"]}


@router.delete("/assignments/{assignment_id}", status_code=204)
def revoke_assignment(assignment_id: UUID, background: BackgroundTasks, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        row = conn.execute(
            "SELECT s.id, s.user_id, s.application_id, u.email, a.slug, a.name AS app_name FROM assignments s"
            " JOIN users u ON u.id = s.user_id JOIN applications a ON a.id = s.application_id"
            " WHERE s.id = %s AND s.revoked_at IS NULL FOR UPDATE OF s",
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Active assignment not found")
        _require_manages(conn, actor, {"id": row["application_id"], "slug": row["slug"], "name": row["app_name"]},
                         "assignment.revoke", "assignment", assignment_id)
        conn.execute(
            "UPDATE assignments SET revoked_at = now(), revoked_by = %s WHERE id = %s",
            (actor["id"], assignment_id),
        )
        record(
            conn,
            actor=actor,
            action="assignment.revoke",
            outcome="success",
            target_type="assignment",
            target_id=assignment_id,
            application_slug=row["slug"],
            details={"user_email": row["email"]},
        )
    background.add_task(
        notify,
        row["email"],
        row["user_id"],
        "access.revoked",
        f"Your access to {row['app_name']} has been removed",
        [f"Your access to {row['app_name']} has been removed. Contact an access administrator if you still need it."],
    )


# ---------- Audit ----------


@router.get("/audit")
def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, max_length=60),
    outcome: Literal["success", "failure"] | None = None,
    application: str | None = Query(default=None, max_length=41),
    actor=Depends(ACCESS_ADMIN),
):
    filters, params = [], []
    if action:
        filters.append("action ILIKE %s")
        params.append(f"%{action}%")
    if outcome:
        filters.append("outcome = %s")
        params.append(outcome)
    if application:
        filters.append("application_slug = %s")
        params.append(application)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, occurred_at, actor_identity, action, target_type, target_id, application_slug, outcome,"
            f" details FROM audit_log {where} ORDER BY id DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        ).fetchall()
        total = conn.execute(f"SELECT count(*) AS n FROM audit_log {where}", params).fetchone()["n"]
    return {"total": total, "items": rows}


# ---------- Integration health ----------


def _integration_status(row):
    """ok: checks succeed and no later failure; error: a failure was reported after the last good check;
    not_connected: no access check has reached Command Center yet."""
    if row["last_access_check_at"] is None:
        return "not_connected"
    if row["last_error_at"] is not None and (row["last_access_ok_at"] is None or row["last_error_at"] > row["last_access_ok_at"]):
        return "error"
    return "ok"


@router.get("/integrations")
def list_integrations(actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, slug, name, enabled, integration_type, launch_url, last_access_check_at, last_access_ok_at,"
            " last_error_at, last_error FROM applications ORDER BY name"
        ).fetchall()
    return [{**row, "status": _integration_status(row)} for row in rows]


@router.get("/integrations/{application_id}/events")
def list_integration_events(application_id: UUID, actor=Depends(ACCESS_ADMIN), limit: int = Query(default=50, ge=1, le=200)):
    with connect() as conn:
        _get_application(conn, application_id)
        return conn.execute(
            "SELECT id, occurred_at, kind, outcome, detail FROM integration_events WHERE application_id = %s"
            " ORDER BY id DESC LIMIT %s",
            (application_id, limit),
        ).fetchall()


# ---------- Scoped delegation ----------


class ManagedApplications(BaseModel):
    application_ids: list[UUID] = Field(default_factory=list, max_length=200)


@router.get("/users/{user_id}/managed-applications")
def get_managed_applications(user_id: UUID, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        _get_user(conn, user_id)
        return conn.execute(
            "SELECT a.id, a.slug, a.name FROM application_managers m JOIN applications a ON a.id = m.application_id"
            " WHERE m.user_id = %s ORDER BY a.name",
            (user_id,),
        ).fetchall()


@router.put("/users/{user_id}/managed-applications")
def set_managed_applications(user_id: UUID, body: ManagedApplications, actor=Depends(SUPER_ADMIN)):
    """Replace the applications an access administrator manages. Only super administrators can set this."""
    with connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        if user["platform_role"] != "access_admin":
            record(conn, actor=actor, action="app_scope.update", outcome="failure", target_type="user", target_id=user_id,
                   details={"reason": "scope applies only to access administrators"})
            raise HTTPException(status_code=409, detail="Application scope applies only to access administrators. Super administrators manage every application.")
        wanted = set(body.application_ids)
        known = {row["id"] for row in conn.execute("SELECT id FROM applications WHERE id = ANY(%s)", (list(wanted),)).fetchall()}
        if known != wanted:
            raise HTTPException(status_code=422, detail="One or more applications do not exist")
        current = {row["application_id"] for row in conn.execute(
            "SELECT application_id FROM application_managers WHERE user_id = %s", (user_id,)).fetchall()}
        added, removed = wanted - current, current - wanted
        conn.execute("DELETE FROM application_managers WHERE user_id = %s", (user_id,))
        for application_id in wanted:
            conn.execute(
                "INSERT INTO application_managers (user_id, application_id, granted_by) VALUES (%s, %s, %s)",
                (user_id, application_id, actor["id"]),
            )
        names = {row["id"]: row["name"] for row in conn.execute(
            "SELECT id, name FROM applications WHERE id = ANY(%s)", (list(added | removed) or [None],)).fetchall()}
        record(
            conn,
            actor=actor,
            action="app_scope.update",
            outcome="success",
            target_type="user",
            target_id=user_id,
            details={"added": sorted(names.get(a, str(a)) for a in added), "removed": sorted(names.get(r, str(r)) for r in removed)},
        )
        return conn.execute(
            "SELECT a.id, a.slug, a.name FROM application_managers m JOIN applications a ON a.id = m.application_id"
            " WHERE m.user_id = %s ORDER BY a.name",
            (user_id,),
        ).fetchall()
