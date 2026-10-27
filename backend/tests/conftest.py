"""Point the test run at its own database so it never touches the working data.

The bootstrap super administrator is granted only while no active super administrator exists, so
test fixtures writing into the working database would block that grant. This module runs before
the application is imported, so `app.db` picks up the test database URL.
"""

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql


def _test_database_url(base_url):
    parts = urlsplit(base_url)
    test_db = f"{parts.path.lstrip('/')}_test"
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_db,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db)))
    return urlunsplit(parts._replace(path=f"/{test_db}"))


os.environ["DATABASE_URL"] = _test_database_url(os.environ["DATABASE_URL"])
# Fixed service token for the test process, independent of the working .env.
os.environ["AFS_SERVICE_TOKEN_IMAGEUNIT"] = "test-service-token"
