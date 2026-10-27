import logging
import os
import pathlib
from contextlib import contextmanager

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

log = logging.getLogger("afs.db")

DATABASE_URL = os.environ["DATABASE_URL"]
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_LOCK_KEY = 727001


@contextmanager
def connect(timeout=5):
    """One transaction per request.

    Successful requests and requests rejected with an HTTPException commit, so refused
    actions are still written to the audit log. Any other exception rolls back.
    """
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=timeout)
    try:
        yield conn
    except HTTPException:
        conn.commit()
        raise
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def run_migrations():
    """Apply pending SQL migrations in filename order. Each file runs in one transaction."""
    conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
    try:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {row[0] for row in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            log.info("applied migration %s", path.name)
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("database migration failed")
        raise
    finally:
        conn.close()
