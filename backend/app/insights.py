"""Detail views behind the dashboard: the role catalog, one role, one platform role, one employee, and the signed-in
person's own account. Each returns figures the overview does not: holders and their history, tenure, who granted
what, and the audited changes for that one subject.

Scope follows the administrative API. Access administrators see application roles and assignments only for the
applications they manage; people and platform roles are visible to them as on the Employees page. Every employee can
read their own account through /api/me/overview.
"""

from datetime import timedelta
from statistics import median
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from .admin import ACCESS_ADMIN, _get_user, _require_manages, managed_application_ids
from .auth import current_user
from .dashboard import _series, period_days
from .db import connect

router = APIRouter(prefix="/api", tags=["insights"])

PLATFORM_ROLES = ("super_admin", "access_admin", "employee")
WEEKS = 12


def _scope_clause(managed, column="a.id"):
    return ("", ()) if managed is None else (f" AND {column} = ANY(%s)", (managed,))


def _weekly(dates, today):
    """Counts per week for the last WEEKS weeks, oldest first. Each week ends on `today`'s weekday."""
    starts = [today - timedelta(days=7 * (WEEKS - i)) + timedelta(days=1) for i in range(WEEKS)]
    counts = [0] * WEEKS
    for value in dates:
        day = value.date() if hasattr(value, "date") else value
        for i in range(WEEKS - 1, -1, -1):
            if day >= starts[i]:
                if day <= today:
                    counts[i] += 1
                break
    return [s.isoformat() for s in starts], counts


def _sign_ins(conn, user_id, day_list):
    rows = conn.execute(
        "SELECT occurred_at::date AS day, count(*) FILTER (WHERE outcome = 'success') AS ok,"
        " count(*) FILTER (WHERE outcome = 'failure') AS failed"
        " FROM audit_log WHERE action = 'auth.login' AND actor_user_id = %s AND occurred_at >= %s GROUP BY 1",
        (user_id, day_list[0]),
    ).fetchall()
    recent = conn.execute(
        "SELECT occurred_at, outcome FROM audit_log WHERE action = 'auth.login' AND actor_user_id = %s"
        " ORDER BY id DESC LIMIT 6",
        (user_id,),
    ).fetchall()
    return {
        "days": [d.isoformat() for d in day_list],
        "success": _series(rows, day_list, "ok"),
        "failure": _series(rows, day_list, "failed"),
        "recent": recent,
    }


def _access(conn, user_id, managed=None):
    scope, params = _scope_clause(managed)
    rows = conn.execute(
        "SELECT s.id, s.application_id, a.slug AS application_slug, a.name AS application_name, a.enabled,"
        " s.app_role_id, r.name AS role_name, r.description AS role_description, s.granted_at, s.revoked_at,"
        " coalesce(g.display_name, g.email) AS granted_by, coalesce(v.display_name, v.email) AS revoked_by"
        " FROM assignments s JOIN applications a ON a.id = s.application_id"
        " LEFT JOIN app_roles r ON r.id = s.app_role_id"
        " LEFT JOIN users g ON g.id = s.granted_by LEFT JOIN users v ON v.id = s.revoked_by"
        f" WHERE s.user_id = %s{scope} ORDER BY s.revoked_at DESC NULLS FIRST, a.name",
        (user_id, *params),
    ).fetchall()
    return [r for r in rows if r["revoked_at"] is None], [r for r in rows if r["revoked_at"] is not None]


def _changes_about(conn, user, limit=15):
    """Audited changes made to this person's account or access (not their sign-ins)."""
    return conn.execute(
        "SELECT l.id, l.occurred_at, l.action, l.outcome, l.application_slug, a.name AS application_name, l.details,"
        " coalesce(u.display_name, u.email) AS actor_name"
        " FROM audit_log l LEFT JOIN users u ON u.id = l.actor_user_id"
        " LEFT JOIN applications a ON a.slug = l.application_slug"
        " WHERE l.action <> 'auth.login' AND (l.target_id = %s OR lower(l.details->>'user_email') = lower(%s)"
        "   OR lower(l.details->>'email') = lower(%s))"
        " ORDER BY l.id DESC LIMIT %s",
        (str(user["id"]), user["email"], user["email"], limit),
    ).fetchall()


# ---------- Role catalog ----------


@router.get("/admin/roles")
def role_catalog(actor=Depends(ACCESS_ADMIN)):
    """Every application role in scope, with holders and recent movement, plus platform role head counts."""
    with connect() as conn:
        managed = managed_application_ids(conn, actor)
        scope, params = _scope_clause(managed)
        roles = conn.execute(
            "SELECT r.id, r.name, r.external_role, r.description, r.created_at,"
            " a.id AS application_id, a.slug AS application_slug, a.name AS application_name, a.enabled AS application_enabled,"
            " (SELECT count(*) FROM assignments s WHERE s.app_role_id = r.id AND s.revoked_at IS NULL) AS holders,"
            " (SELECT count(*) FROM assignments s JOIN users u ON u.id = s.user_id WHERE s.app_role_id = r.id"
            "   AND s.revoked_at IS NULL AND u.last_login_at >= now() - interval '30 days') AS holders_signed_in_30d,"
            " (SELECT max(l.occurred_at) FROM audit_log l WHERE l.outcome = 'success'"
            "   AND (l.details->>'app_role_id' = r.id::text OR (l.target_type = 'app_role' AND l.target_id = r.id::text)))"
            "   AS last_change_at,"
            " (SELECT count(*) FROM audit_log l WHERE l.outcome = 'success' AND l.details->>'app_role_id' = r.id::text"
            "   AND l.occurred_at >= now() - interval '30 days') AS joined_30d"
            " FROM app_roles r JOIN applications a ON a.id = r.application_id"
            f" WHERE true{scope} ORDER BY a.name, r.name",
            params,
        ).fetchall()
        platform = {row["platform_role"]: row for row in conn.execute(
            "SELECT platform_role, count(*) AS people, count(*) FILTER (WHERE status = 'active') AS active"
            " FROM users GROUP BY platform_role"
        ).fetchall()}
    return {
        "roles": roles,
        "platform_roles": [
            {"name": name, "people": platform.get(name, {}).get("people", 0), "active": platform.get(name, {}).get("active", 0)}
            for name in PLATFORM_ROLES
        ],
    }


# ---------- One application role ----------


@router.get("/admin/roles/{role_id}")
def role_detail(role_id: UUID, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        role = conn.execute(
            "SELECT r.id, r.name, r.external_role, r.description, r.created_at,"
            " a.id AS application_id, a.slug AS application_slug, a.name AS application_name, a.enabled AS application_enabled"
            " FROM app_roles r JOIN applications a ON a.id = r.application_id WHERE r.id = %s",
            (role_id,),
        ).fetchone()
        if role is None:
            raise HTTPException(status_code=404, detail="Role not found")
        _require_manages(conn, actor, {"id": role["application_id"], "slug": role["application_slug"], "name": role["application_name"]},
                         "role.view", "app_role", role_id)

        holders = conn.execute(
            "SELECT s.id AS assignment_id, u.id AS user_id, u.email, u.display_name, u.status, u.last_login_at,"
            " s.granted_at, coalesce(g.display_name, g.email) AS granted_by"
            " FROM assignments s JOIN users u ON u.id = s.user_id LEFT JOIN users g ON g.id = s.granted_by"
            " WHERE s.app_role_id = %s AND s.revoked_at IS NULL ORDER BY s.granted_at DESC",
            (role_id,),
        ).fetchall()
        joined = [row["occurred_at"] for row in conn.execute(
            "SELECT occurred_at FROM audit_log WHERE outcome = 'success' AND action IN ('assignment.grant', 'assignment.update')"
            " AND details->>'app_role_id' = %s",
            (str(role_id),),
        ).fetchall()]
        left_rows = conn.execute(
            "SELECT id, revoked_at FROM assignments WHERE app_role_id = %s AND revoked_at IS NOT NULL", (role_id,)
        ).fetchall()
        events = conn.execute(
            "SELECT l.id, l.occurred_at, l.action, l.outcome, l.details->>'user_email' AS subject,"
            " coalesce(u.display_name, u.email) AS actor_name"
            " FROM audit_log l LEFT JOIN users u ON u.id = l.actor_user_id"
            " WHERE l.details->>'app_role_id' = %s OR (l.target_type = 'app_role' AND l.target_id = %s)"
            "   OR (l.action = 'assignment.revoke' AND l.target_id = ANY(%s))"
            " ORDER BY l.id DESC LIMIT 20",
            (str(role_id), str(role_id), [str(r["id"]) for r in left_rows]),
        ).fetchall()
        siblings = conn.execute(
            "SELECT r.id, r.name, r.external_role,"
            " (SELECT count(*) FROM assignments s WHERE s.app_role_id = r.id AND s.revoked_at IS NULL) AS holders"
            " FROM app_roles r WHERE r.application_id = %s ORDER BY r.name",
            (role["application_id"],),
        ).fetchall()
        app_people = conn.execute(
            "SELECT count(*) AS n FROM assignments WHERE application_id = %s AND revoked_at IS NULL", (role["application_id"],)
        ).fetchone()["n"]
        today = conn.execute("SELECT current_date AS d").fetchone()["d"]

    weeks, joined_weekly = _weekly(joined, today)
    _, left_weekly = _weekly([r["revoked_at"] for r in left_rows], today)
    tenure = [(today - h["granted_at"].date()).days for h in holders]
    month_ago = today - timedelta(days=30)
    return {
        "role": role,
        "holders": holders,
        "stats": {
            "holders": len(holders),
            "share_of_application": round(len(holders) / app_people, 3) if app_people else 0,
            "application_people": app_people,
            "signed_in_30d": sum(1 for h in holders if h["last_login_at"] and h["last_login_at"].date() >= month_ago),
            "never_signed_in": sum(1 for h in holders if h["last_login_at"] is None),
            "disabled_holders": sum(1 for h in holders if h["status"] != "active"),
            "median_tenure_days": int(median(tenure)) if tenure else None,
            "joined_total": len(joined),
            "left_total": len(left_rows),
        },
        "weeks": weeks,
        "joined_weekly": joined_weekly,
        "left_weekly": left_weekly,
        "events": events,
        "siblings": siblings,
    }


# ---------- One platform role ----------


@router.get("/admin/platform-roles/{name}")
def platform_role_detail(name: str, actor=Depends(ACCESS_ADMIN)):
    if name not in PLATFORM_ROLES:
        raise HTTPException(status_code=404, detail="Platform role not found")
    with connect() as conn:
        people = conn.execute(
            "SELECT u.id, u.email, u.display_name, u.status, u.keycloak_sub IS NOT NULL AS linked, u.created_at, u.last_login_at,"
            " (SELECT count(*) FROM assignments s WHERE s.user_id = u.id AND s.revoked_at IS NULL) AS applications"
            " FROM users u WHERE u.platform_role = %s ORDER BY lower(u.email)",
            (name,),
        ).fetchall()
        scope = {}
        if name == "access_admin":
            for row in conn.execute(
                "SELECT m.user_id, a.name FROM application_managers m JOIN applications a ON a.id = m.application_id ORDER BY a.name"
            ).fetchall():
                scope.setdefault(row["user_id"], []).append(row["name"])
        changes = conn.execute(
            "SELECT l.id, l.occurred_at, l.action, l.outcome, coalesce(u.display_name, u.email) AS actor_name,"
            " coalesce(t.display_name, t.email) AS subject,"
            " l.details->'changes'->'platform_role'->>'from' AS from_role, l.details->'changes'->'platform_role'->>'to' AS to_role"
            " FROM audit_log l LEFT JOIN users u ON u.id = l.actor_user_id LEFT JOIN users t ON t.id::text = l.target_id"
            " WHERE (l.action = 'user.update' AND (l.details->'changes'->'platform_role'->>'to' = %s"
            "        OR l.details->'changes'->'platform_role'->>'from' = %s))"
            "    OR (l.action = 'platform_role.bootstrap' AND %s = 'super_admin')"
            " ORDER BY l.id DESC LIMIT 20",
            (name, name, name),
        ).fetchall()
        today = conn.execute("SELECT current_date AS d").fetchone()["d"]
    month_ago = today - timedelta(days=30)
    for person in people:
        person["managed_applications"] = scope.get(person["id"], []) if name == "access_admin" else None
    return {
        "name": name,
        "people": people,
        "stats": {
            "people": len(people),
            "active": sum(1 for p in people if p["status"] == "active"),
            "disabled": sum(1 for p in people if p["status"] != "active"),
            "pending": sum(1 for p in people if not p["linked"] and p["status"] == "active"),
            "signed_in_30d": sum(1 for p in people if p["last_login_at"] and p["last_login_at"].date() >= month_ago),
            "applications_per_person": round(sum(p["applications"] for p in people) / len(people), 1) if people else 0,
        },
        "changes": changes,
    }


# ---------- One employee ----------


@router.get("/admin/users/{user_id}/profile")
def user_profile(user_id: UUID, actor=Depends(ACCESS_ADMIN)):
    with connect() as conn:
        user = _get_user(conn, user_id)
        managed = managed_application_ids(conn, actor)
        active, past = _access(conn, user_id, managed)
        managed_apps = conn.execute(
            "SELECT a.id, a.slug, a.name FROM application_managers m JOIN applications a ON a.id = m.application_id"
            " WHERE m.user_id = %s ORDER BY a.name",
            (user_id,),
        ).fetchall() if user["platform_role"] == "access_admin" else None
        sign_ins = _sign_ins(conn, user_id, period_days(conn, 30))
        changes = _changes_about(conn, user)
        performed = conn.execute(
            "SELECT l.id, l.occurred_at, l.action, l.outcome, l.application_slug, a.name AS application_name, l.details"
            " FROM audit_log l LEFT JOIN applications a ON a.slug = l.application_slug"
            " WHERE l.actor_user_id = %s AND l.action <> 'auth.login' ORDER BY l.id DESC LIMIT 15",
            (user_id,),
        ).fetchall() if user["platform_role"] != "employee" else []
    return {
        "user": user,
        "scope": "all" if managed is None else "managed",
        "access": active,
        "past_access": past,
        "managed_applications": managed_apps,
        "sign_ins": sign_ins,
        "changes": changes,
        "performed": performed,
    }


# ---------- The signed-in person ----------


@router.get("/me/overview")
def my_overview(user=Depends(current_user)):
    """The signed-in person's own account: their access with role descriptions, its history, and their sign-ins."""
    with connect() as conn:
        active, past = _access(conn, user["id"])
        sign_ins = _sign_ins(conn, user["id"], period_days(conn, 30))
        changes = _changes_about(conn, user, limit=10)
    for row in changes:
        row.pop("details", None)
    return {
        "account": {
            "email": user["email"],
            "name": user["display_name"],
            "platform_role": user["platform_role"],
            "status": user["status"],
            "created_at": user["created_at"],
            "last_login_at": user["last_login_at"],
        },
        "access": active,
        "past_access": past,
        "sign_ins": sign_ins,
        "changes": changes,
    }
