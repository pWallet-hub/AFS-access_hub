"""Usage counts on the Command Center dashboard, and the live Biocap figures.

The Biocap reader is tested with a fake client, so these tests never touch the live database."""

import pymongo
import pytest

from app import usage
from app.db import connect

from test_api import access_admin, client_for, employee, make_application, make_user, super_admin  # noqa: F401 (fixtures)
from test_internal import _check


def _sessions_today(slug):
    with connect() as conn:
        row = conn.execute(
            "SELECT coalesce(sum(u.sessions), 0) AS n FROM application_usage u JOIN applications a ON a.id = u.application_id"
            " WHERE a.slug = %s AND u.day = current_date",
            (slug,),
        ).fetchone()
    return row["n"]


def _assigned_application(user):
    slug = make_application("usage")
    with connect() as conn:
        app_row = conn.execute(
            "INSERT INTO applications (slug, name, launch_url, enabled) VALUES (%s, %s, %s, true) RETURNING *",
            (slug, slug, "https://example.test/app"),
        ).fetchone()
        conn.execute("INSERT INTO assignments (user_id, application_id) VALUES (%s, %s)", (user["id"], app_row["id"]))
    return slug


def test_an_allowed_access_check_counts_one_session():
    person = make_user()
    slug = _assigned_application(person)
    before = _sessions_today(slug)
    response = _check(slug, person["keycloak_sub"])
    assert response.status_code == 200 and response.json()["allowed"] is True
    assert _sessions_today(slug) == before + 1


def test_a_denied_access_check_is_not_counted():
    assigned = make_user()
    slug = _assigned_application(assigned)
    stranger = make_user()
    before = _sessions_today(slug)
    assert _check(slug, stranger["keycloak_sub"]).json()["allowed"] is False
    assert _sessions_today(slug) == before


def test_usage_is_for_super_administrators_only(super_admin, access_admin, employee):
    assert client_for(employee).get("/api/admin/usage").status_code == 403
    assert client_for(access_admin).get("/api/admin/usage").status_code == 403
    response = client_for(super_admin).get("/api/admin/usage")
    assert response.status_code == 200
    assert "biocap" in response.json()["live"]


@pytest.fixture
def fresh_cache(monkeypatch):
    monkeypatch.setattr(usage, "_live_cache", {"at": 0.0, "value": None})


def test_biocap_figures_say_when_they_are_not_configured(monkeypatch, fresh_cache):
    monkeypatch.delenv("BIOCAP_DB_URI", raising=False)
    assert usage.biocap_live_stats() == {"status": "not_configured"}


def test_an_unreachable_biocap_database_is_reported_without_its_address(monkeypatch, fresh_cache):
    monkeypatch.setenv("BIOCAP_DB_URI", "mongodb://reader:hunter2-secret@127.0.0.1:1/biocap_db?serverSelectionTimeoutMS=500")
    result = usage.biocap_live_stats()
    assert result["status"] == "unavailable"
    assert "hunter2-secret" not in str(result) and "127.0.0.1" not in str(result)


class _Collection:
    def __init__(self, total, counts):
        self.total, self.counts = total, counts

    def estimated_document_count(self):
        return self.total

    def count_documents(self, query):
        return self.counts[next(iter(query))]


class _FakeClient:
    def __init__(self, uri, **kwargs):
        self.read_preference = kwargs.get("readPreference")

    def get_default_database(self):
        return {
            "users": _Collection(12, {"isActive": 11, "lastLoginAt": 7}),
            "blogs": _Collection(17, {"status": 5}),
        }

    def close(self):
        pass


def test_biocap_figures_are_counts_read_from_secondaries(monkeypatch, fresh_cache):
    monkeypatch.setenv("BIOCAP_DB_URI", "mongodb://reader@example.test/biocap_db")
    monkeypatch.setattr(pymongo, "MongoClient", _FakeClient)
    result = usage.biocap_live_stats()
    assert result["status"] == "ok"
    assert (result["users_total"], result["users_active"], result["users_logged_in_30d"]) == (12, 11, 7)
    assert (result["stories_total"], result["stories_published"]) == (17, 5)
    assert "email" not in str(result).lower()
