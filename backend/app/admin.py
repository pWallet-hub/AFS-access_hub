"""Administrative API: employees, applications, role mappings, assignments and the audit log.

Every endpoint checks the platform role on the server. The frontend hides controls the user
cannot use, but that is never relied on for protection.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import errors

from .audit import record
from .auth import require_platform_role
from .db import connect
from .schemas import ApplicationCreate, ApplicationUpdate, AssignmentCreate, RoleCreate, UserCreate, UserUpdate

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
def create_user(body: UserCreate, actor=Depends(ACCESS_ADMIN)):
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
        return _get_user(conn, row["id"])


@router.patch("/users/{user_id}")
def update_user(user_id: UUID, body: UserUpdate, actor=Depends(ACCESS_ADMIN)):
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

        if changes:
            allowed = {"status", "platform_role"}
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
        return _get_user(conn, user_id)


# ---------- Applications ----------


@router.get("/applications")
def list_applications(actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, slug, name, description, launch_url, enabled, integration_type, oidc_client_id,"
            " created_at, updated_at FROM applications ORDER BY name"
        ).fetchall()


@router.post("/applications", status_code=201)
def create_application(body: ApplicationCreate, actor=Depends(SUPER_ADMIN)):
    if body.enabled and not body.launch_url:
        raise HTTPException(status_code=422, detail="An application needs a launch URL before it can be enabled")
    with connect() as conn:
        try:
            with conn.transaction():
                row = conn.execute(
                    "INSERT INTO applications (slug, name, description, launch_url, enabled, integration_type,"
                    " oidc_client_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (
                        body.slug,
                        body.name,
                        body.description,
                        body.launch_url,
                        body.enabled,
                        body.integration_type,
                        body.oidc_client_id,
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
        _get_application(conn, application_id)
        return conn.execute(
            "SELECT id, name, external_role, description FROM app_roles WHERE application_id = %s ORDER BY name",
            (application_id,),
        ).fetchall()


@router.post("/applications/{application_id}/roles", status_code=201)
def create_role(application_id: UUID, body: RoleCreate, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        application = _get_application(conn, application_id)
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
            "SELECT r.id, r.name, a.slug FROM app_roles r JOIN applications a ON a.id = r.application_id"
            " WHERE r.id = %s",
            (role_id,),
        ).fetchone()
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
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
def grant_assignment(body: AssignmentCreate, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        user = _get_user(conn, body.user_id)
        application = _get_application(conn, body.application_id)
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
        return {"id": row["id"]}


@router.delete("/assignments/{assignment_id}", status_code=204)
def revoke_assignment(assignment_id: UUID, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        row = conn.execute(
            "SELECT s.id, s.user_id, u.email, a.slug FROM assignments s"
            " JOIN users u ON u.id = s.user_id JOIN applications a ON a.id = s.application_id"
            " WHERE s.id = %s AND s.revoked_at IS NULL FOR UPDATE OF s",
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Active assignment not found")
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


# ---------- Audit ----------


@router.get("/audit")
def list_audit(limit: int = Query(default=100, ge=1, le=500), actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, occurred_at, actor_identity, action, target_type, target_id, application_slug, outcome,"
            " details FROM audit_log ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
