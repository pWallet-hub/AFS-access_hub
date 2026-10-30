"""Usage for the Command Center dashboard.

Two sources:
- Sessions per application, counted by Command Center itself each time it grants access (application_usage).
- Live figures from an application's own database, when the application is configured to report them. Today that is
  Biocap, through BIOCAP_DB_URI. Reads are counts only, with a short timeout, and never write. No personal details leave
  the database: the dashboard gets totals only.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("usage")

LIVE_TTL_SECONDS = 300
_lock = threading.Lock()
_live_cache = {"at": 0.0, "value": None}


def application_usage(conn):
    """One row per application: enabled, active employees, and sessions over the last 7 and 30 days."""
    return conn.execute(
        """
        SELECT a.slug, a.name, a.enabled, a.last_access_ok_at,
          (SELECT count(*) FROM assignments s WHERE s.application_id = a.id AND s.revoked_at IS NULL) AS active_employees,
          COALESCE((SELECT sum(u.sessions) FROM application_usage u
                    WHERE u.application_id = a.id AND u.day >= current_date - 6), 0) AS sessions_7d,
          COALESCE((SELECT sum(u.sessions) FROM application_usage u
                    WHERE u.application_id = a.id AND u.day >= current_date - 29), 0) AS sessions_30d
        FROM applications a
        ORDER BY a.name
        """
    ).fetchall()


def biocap_live_stats():
    """Counts from Biocap's live database. Cached for a few minutes so the dashboard does not query it on every view."""
    uri = os.environ.get("BIOCAP_DB_URI", "")
    if not uri:
        return {"status": "not_configured"}
    with _lock:
        if _live_cache["value"] is not None and time.time() - _live_cache["at"] < LIVE_TTL_SECONDS:
            return _live_cache["value"]

    try:
        from pymongo import MongoClient

        client = MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            readPreference="secondaryPreferred",
        )
        try:
            db = client.get_default_database()
            users, blogs = db["users"], db["blogs"]
            since = datetime.now(timezone.utc) - timedelta(days=30)
            value = {
                "status": "ok",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "users_total": users.estimated_document_count(),
                "users_active": users.count_documents({"isActive": True}),
                "users_logged_in_30d": users.count_documents({"lastLoginAt": {"$gte": since}}),
                "stories_total": blogs.estimated_document_count(),
                "stories_published": blogs.count_documents({"status": "published"}),
                "stories_pending_review": blogs.count_documents({"status": "pending_review"}),
            }
        finally:
            client.close()
    except Exception as exc:  # the URI is never included in the message: only the exception type
        log.warning("Biocap live stats unavailable: %s", type(exc).__name__)
        return {"status": "unavailable", "detail": type(exc).__name__}

    with _lock:
        _live_cache.update({"at": time.time(), "value": value})
    return value
