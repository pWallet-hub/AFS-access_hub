import json
from functools import partial

from psycopg.types.json import Jsonb

# Audit details may hold UUIDs and dates; store them as plain text rather than failing the audit write.
JSON = partial(json.dumps, default=str)


def record(conn, *, action, outcome, target_type, actor=None, target_id=None, application_slug=None, details=None):
    """Append one audit entry in the caller's transaction.

    `details` must never contain passwords, tokens, cookies or client secrets.
    Callers pass only identifiers and before/after values of ordinary fields.
    """
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, actor_identity, action, target_type, target_id,"
        " application_slug, outcome, details) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            actor["id"] if actor else None,
            (actor["keycloak_sub"] or str(actor["id"])) if actor else "anonymous",
            action,
            target_type,
            str(target_id) if target_id is not None else None,
            application_slug,
            outcome,
            Jsonb(details or {}, dumps=JSON),
        ),
    )
