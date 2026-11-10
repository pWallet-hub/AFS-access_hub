"""Transactional email through Resend: what is sent, how it is escaped, the audit trail, and failure isolation."""

import base64
import json
import os
import uuid

import httpx
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app import notifications
from app.db import connect
from app.main import app

SESSION_SECRET = os.environ["SESSION_SECRET"]


class FakeResend:
    """Stands in for https://api.resend.com/emails. Records each request and returns the configured status."""

    def __init__(self, status=200, raise_error=False):
        self.calls = []
        self.status = status
        self.raise_error = raise_error

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json)
        if self.raise_error:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(self.status, request=httpx.Request("POST", url))


def _enable(monkeypatch, fake):
    monkeypatch.setattr(notifications, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(notifications, "RESEND_FROM", "AFS <noreply@example.test>")
    monkeypatch.setattr(notifications.httpx, "post", fake)


def _admin():
    sub = f"test-{uuid.uuid4()}"
    with connect() as conn:
        user = conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified, platform_role)"
            " VALUES (%s, %s, 'Admin', true, 'super_admin') RETURNING *",
            (sub, f"test-{uuid.uuid4().hex[:10]}@example.test"),
        ).fetchone()
    client = TestClient(app)
    data = base64.b64encode(json.dumps({"sub": user["keycloak_sub"]}).encode("utf-8"))
    client.cookies.set("session", TimestampSigner(SESSION_SECRET).sign(data).decode("utf-8"))
    return client


def test_render_escapes_dynamic_text():
    html_body, text_body = notifications.render(
        "<b>Title</b>", ["Hello <script>alert(1)</script>"], "Open", "https://example.test/?a=1&b=2"
    )
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body
    assert "&lt;b&gt;Title&lt;/b&gt;" in html_body
    assert "https://example.test/?a=1&amp;b=2" in html_body
    assert "Hello <script>alert(1)</script>" in text_body


def test_nothing_is_sent_without_configuration(monkeypatch):
    fake = FakeResend()
    monkeypatch.setattr(notifications, "RESEND_API_KEY", "")
    monkeypatch.setattr(notifications.httpx, "post", fake)
    assert notifications.notify("someone@example.test", None, "test", "Subject", ["Body"]) is False
    assert fake.calls == []


def test_successful_send_is_audited(monkeypatch):
    fake = FakeResend(status=200)
    _enable(monkeypatch, fake)
    target = uuid.uuid4()
    assert notifications.notify("someone@example.test", target, "test.kind", "Subject", ["Body"]) is True
    sent = fake.calls[0]
    assert sent["to"] == ["someone@example.test"]
    assert sent["subject"] == "Subject"
    assert sent["from"] == "AFS <noreply@example.test>"
    with connect() as conn:
        row = conn.execute(
            "SELECT action, outcome, details FROM audit_log WHERE target_id = %s ORDER BY id DESC LIMIT 1", (str(target),)
        ).fetchone()
    assert row == {"action": "notification.sent", "outcome": "success", "details": {"kind": "test.kind", "http_status": 200}}


def test_rejected_and_unreachable_sends_are_audited_as_failures(monkeypatch):
    _enable(monkeypatch, FakeResend(status=422))
    target = uuid.uuid4()
    assert notifications.notify("x@example.test", target, "rejected", "S", ["B"]) is False
    _enable(monkeypatch, FakeResend(raise_error=True))
    assert notifications.notify("x@example.test", target, "unreachable", "S", ["B"]) is False
    with connect() as conn:
        rows = conn.execute(
            "SELECT action, outcome FROM audit_log WHERE target_id = %s ORDER BY id", (str(target),)
        ).fetchall()
    assert [r["action"] for r in rows] == ["notification.failed", "notification.failed"]
    assert {r["outcome"] for r in rows} == {"failure"}


def test_creating_an_employee_sends_a_welcome_notice(monkeypatch):
    fake = FakeResend()
    _enable(monkeypatch, fake)
    admin = _admin()
    email = f"new-{uuid.uuid4().hex[:8]}@example.test"
    assert admin.post("/api/admin/users", json={"email": email}).status_code == 201
    assert fake.calls[-1]["to"] == [email]
    assert "added to AFS Command Center" in fake.calls[-1]["subject"]


def test_a_failed_email_does_not_undo_the_change(monkeypatch):
    _enable(monkeypatch, FakeResend(raise_error=True))
    admin = _admin()
    email = f"new-{uuid.uuid4().hex[:8]}@example.test"
    response = admin.post("/api/admin/users", json={"email": email})
    assert response.status_code == 201
    listed = admin.get("/api/admin/users").json()
    assert any(u["email"] == email for u in listed)


def test_disabling_an_account_sends_a_notice(monkeypatch):
    fake = FakeResend()
    _enable(monkeypatch, fake)
    admin = _admin()
    email = f"victim-{uuid.uuid4().hex[:8]}@example.test"
    user = admin.post("/api/admin/users", json={"email": email}).json()
    admin.patch(f"/api/admin/users/{user['id']}", json={"status": "disabled"})
    assert "disabled" in fake.calls[-1]["subject"]
    assert fake.calls[-1]["to"] == [email]


def test_granting_and_revoking_access_send_notices(monkeypatch):
    fake = FakeResend()
    _enable(monkeypatch, fake)
    admin = _admin()
    email = f"grant-{uuid.uuid4().hex[:8]}@example.test"
    user = admin.post("/api/admin/users", json={"email": email}).json()
    slug = f"app-{uuid.uuid4().hex[:8]}"
    app_row = admin.post(
        "/api/admin/applications",
        json={"slug": slug, "name": "Notice App", "launch_url": "https://example.test/app", "enabled": True},
    ).json()
    grant = admin.post("/api/admin/assignments", json={"user_id": user["id"], "application_id": app_row["id"]})
    assert grant.status_code == 201
    assert fake.calls[-1]["subject"] == "You now have access to Notice App"
    admin.delete(f"/api/admin/assignments/{grant.json()['id']}")
    assert fake.calls[-1]["subject"] == "Your access to Notice App has been removed"


def test_display_name_can_be_corrected():
    admin = _admin()
    user = admin.post("/api/admin/users", json={"email": f"n-{uuid.uuid4().hex[:8]}@example.test"}).json()
    response = admin.patch(f"/api/admin/users/{user['id']}", json={"display_name": "Corrected Name"})
    assert response.status_code == 200
    assert response.json()["display_name"] == "Corrected Name"


def test_audit_can_be_filtered_and_paged():
    admin = _admin()
    admin.post("/api/admin/users", json={"email": f"f-{uuid.uuid4().hex[:8]}@example.test"})
    page = admin.get("/api/admin/audit", params={"action": "user.create", "outcome": "success", "limit": 5}).json()
    assert page["total"] >= 1
    assert all(item["action"] == "user.create" and item["outcome"] == "success" for item in page["items"])
    assert len(page["items"]) <= 5
    second = admin.get("/api/admin/audit", params={"action": "user.create", "limit": 1, "offset": 1}).json()
    assert len(second["items"]) <= 1


def test_sender_is_normalised_to_a_format_resend_accepts():
    # A bare `<email>` is rejected by Resend with HTTP 422, which silently stopped every notice in production.
    assert notifications.format_sender("<noreply@example.test>") == "noreply@example.test"
    assert notifications.format_sender("<noreply@example.test>", "AFS  Command  Center") == "AFS Command Center <noreply@example.test>"
    assert notifications.format_sender("noreply@example.test", "AFS") == "AFS <noreply@example.test>"
    assert notifications.format_sender("noreply@example.test") == "noreply@example.test"
    assert notifications.format_sender('"Biocap" <noreply@example.test>', "AFS") == "Biocap <noreply@example.test>"
    assert notifications.format_sender("  ", "AFS") == ""
