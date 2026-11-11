"""Dashboard overview: one read-only call that feeds the home page statistics and the per-application pages.

Scope follows the rest of the administrative API:
- Super administrators see every application, session counts and live figures.
- Access administrators see access figures (people, roles, health) only for the applications they manage, and no
  session counts, which stay super-administrator only as on /api/admin/usage.
Every registered application appears automatically: nothing here names an application.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from .admin import ACCESS_ADMIN, _integration_status, managed_application_ids
from .db import connect
from .usage import biocap_live_stats

router = APIRouter(prefix="/api/admin", tags=["dashboard"])

DAYS = 30


def _series(rows, days, key="n"):
    by_day = {row["day"]: int(row[key]) for row in rows}
    return [by_day.get(day, 0) for day in days]


@router.get("/overview")
def overview(actor=Depends(ACCESS_ADMIN)):
    is_super = actor["platform_role"] == "super_admin"
    with connect() as conn:
        # The database's date, so days line up with application_usage, which is keyed on current_date.
        today = conn.execute("SELECT current_date AS d").fetchone()["d"]
        days = [today - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]
        start = days[0]

        managed = managed_application_ids(conn, actor)
        managed_set = None if managed is None else set(managed)

        apps = conn.execute(
            "SELECT id, slug, name, description, enabled, launch_url, integration_type, created_at,"
            " last_access_check_at, last_access_ok_at, last_error_at, last_error"
            " FROM applications ORDER BY created_at, slug"
        ).fetchall()

        people = {row["application_id"]: row["n"] for row in conn.execute(
            "SELECT application_id, count(*) AS n FROM assignments WHERE revoked_at IS NULL GROUP BY application_id"
        ).fetchall()}

        role_rows = conn.execute(
            "SELECT r.application_id, r.id, r.name, r.external_role,"
            " (SELECT count(*) FROM assignments s WHERE s.app_role_id = r.id AND s.revoked_at IS NULL) AS people"
            " FROM app_roles r ORDER BY r.name"
        ).fetchall()
        roles_by_app = {}
        for row in role_rows:
            roles_by_app.setdefault(row["application_id"], []).append(
                {"id": row["id"], "name": row["name"], "external_role": row["external_role"], "people": row["people"]})

        granted_30d = {row["application_id"]: row["n"] for row in conn.execute(
            "SELECT application_id, count(*) AS n FROM assignments WHERE granted_at >= %s GROUP BY application_id",
            (start,),
        ).fetchall()}
        revoked_30d = {row["application_id"]: row["n"] for row in conn.execute(
            "SELECT application_id, count(*) AS n FROM assignments WHERE revoked_at >= %s GROUP BY application_id",
            (start,),
        ).fetchall()}

        failures_7d = {row["application_id"]: row["n"] for row in conn.execute(
            "SELECT application_id, count(*) AS n FROM integration_events"
            " WHERE outcome = 'failure' AND occurred_at >= now() - interval '7 days' GROUP BY application_id"
        ).fetchall()}

        sessions = {}
        sessions_prev = {}
        if is_super:
            for row in conn.execute(
                "SELECT application_id, day, sessions AS n FROM application_usage WHERE day >= %s", (start,)
            ).fetchall():
                sessions.setdefault(row["application_id"], []).append(row)
            sessions_prev = {row["application_id"]: int(row["n"]) for row in conn.execute(
                "SELECT application_id, sum(sessions) AS n FROM application_usage"
                " WHERE day >= %s AND day < %s GROUP BY application_id",
                (start - timedelta(days=DAYS), start),
            ).fetchall()}

        users = conn.execute(
            "SELECT platform_role, status, keycloak_sub IS NOT NULL AS linked,"
            " last_login_at >= now() - interval '7 days' AS recent_7d,"
            " last_login_at >= now() - interval '30 days' AS recent_30d"
            " FROM users"
        ).fetchall()

        sign_ins = conn.execute(
            "SELECT occurred_at::date AS day,"
            " count(*) FILTER (WHERE outcome = 'success') AS ok,"
            " count(*) FILTER (WHERE outcome = 'failure') AS failed"
            " FROM audit_log WHERE action = 'auth.login' AND occurred_at >= %s GROUP BY 1",
            (start,),
        ).fetchall()

        recent = conn.execute(
            "SELECT l.id, l.occurred_at, l.action, l.target_type, l.application_slug, l.outcome,"
            " coalesce(u.display_name, u.email) AS actor_name"
            " FROM audit_log l LEFT JOIN users u ON u.id = l.actor_user_id"
            " WHERE l.action <> 'auth.login' ORDER BY l.id DESC LIMIT 12"
        ).fetchall()

        users_with_access = conn.execute(
            "SELECT count(DISTINCT user_id) AS n FROM assignments WHERE revoked_at IS NULL"
            + ("" if managed is None else " AND application_id = ANY(%s)"),
            () if managed is None else (managed,),
        ).fetchone()["n"]

    applications = []
    for index, app in enumerate(apps):
        in_scope = managed_set is None or app["id"] in managed_set
        entry = {
            "id": app["id"],
            "slug": app["slug"],
            "name": app["name"],
            "description": app["description"],
            "enabled": app["enabled"],
            "launch_url": app["launch_url"],
            "integration_type": app["integration_type"],
            "color_index": index,  # stable per application, so its color never changes when others are added
            "managed": in_scope,
            "health": _integration_status(app),
            "last_access_ok_at": app["last_access_ok_at"],
            "last_error": app["last_error"],
        }
        if in_scope:
            entry.update({
                "people": people.get(app["id"], 0),
                "roles": roles_by_app.get(app["id"], []),
                "granted_30d": granted_30d.get(app["id"], 0),
                "revoked_30d": revoked_30d.get(app["id"], 0),
                "failures_7d": failures_7d.get(app["id"], 0),
            })
            role_people = sum(r["people"] for r in entry["roles"])
            entry["standard_access"] = entry["people"] - role_people
        if is_super:
            daily = _series(sessions.get(app["id"], []), days)
            entry.update({
                "sessions_daily": daily,
                "sessions_7d": sum(daily[-7:]),
                "sessions_30d": sum(daily),
                "sessions_prev_30d": sessions_prev.get(app["id"], 0),
            })
        applications.append(entry)

    scoped = [a for a in applications if a["managed"]]
    totals = {
        "applications": len(applications),
        "applications_enabled": sum(1 for a in applications if a["enabled"]),
        "applications_failing": sum(1 for a in applications if a["health"] == "error"),
        "applications_connected": sum(1 for a in applications if a["health"] == "ok"),
        "employees": len(users),
        "employees_active": sum(1 for u in users if u["status"] == "active"),
        "employees_disabled": sum(1 for u in users if u["status"] == "disabled"),
        "employees_pending": sum(1 for u in users if not u["linked"] and u["status"] == "active"),
        "signed_in_7d": sum(1 for u in users if u["recent_7d"]),
        "signed_in_30d": sum(1 for u in users if u["recent_30d"]),
        "assignments": sum(a["people"] for a in scoped),
        "users_with_access": users_with_access,
        "granted_30d": sum(a["granted_30d"] for a in scoped),
        "revoked_30d": sum(a["revoked_30d"] for a in scoped),
    }
    if is_super:
        totals["sessions_7d"] = sum(a["sessions_7d"] for a in applications)
        totals["sessions_30d"] = sum(a["sessions_30d"] for a in applications)
        totals["sessions_prev_30d"] = sum(a["sessions_prev_30d"] for a in applications)

    roles = {"super_admin": 0, "access_admin": 0, "employee": 0}
    for user in users:
        roles[user["platform_role"]] = roles.get(user["platform_role"], 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "all" if managed is None else "managed",
        "can_see_usage": is_super,
        "days": [d.isoformat() for d in days],
        "totals": totals,
        "platform_roles": roles,
        "sign_ins": {"success": _series(sign_ins, days, "ok"), "failure": _series(sign_ins, days, "failed")},
        "applications": applications,
        "recent_activity": recent,
        "live": {"biocap": biocap_live_stats()} if is_super else {},
    }
