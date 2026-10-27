"""Browser tests for AFS Command Center, run against the running stack.

They sign in through Keycloak like a real user would. A temporary employee is created in Keycloak and in
Command Center for each run, and removed at the end.

Environment (read from the repository's .env by run.sh):
  BASE_URL            portal address, default http://localhost:8080
  KEYCLOAK_URL        Keycloak address, default http://localhost:8081
  KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD      Keycloak master-realm administrator
  BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD  the super administrator created at first start
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest
from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8081").rstrip("/")
ADMIN_EMAIL = os.environ["BOOTSTRAP_ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["BOOTSTRAP_ADMIN_PASSWORD"]
KC_ADMIN = os.environ["KEYCLOAK_ADMIN"]
KC_ADMIN_PASSWORD = os.environ["KEYCLOAK_ADMIN_PASSWORD"]
REALM = os.environ.get("KEYCLOAK_REALM", "afs")


def _http(method, url, data=None, token=None, form=False):
    headers = {}
    body = None
    if form:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        return error.code, None


def _keycloak_token():
    status, body = _http(
        "POST",
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        {"grant_type": "password", "client_id": "admin-cli", "username": KC_ADMIN, "password": KC_ADMIN_PASSWORD},
        form=True,
    )
    assert status == 200, "could not get a Keycloak admin token"
    return body["access_token"]


@pytest.fixture(scope="session")
def employee():
    """A new employee in Keycloak with a verified email, removed again after the run."""
    email = f"e2e-{uuid.uuid4().hex[:10]}@example.test"
    password = "E2e-Password-2026!"
    token = _keycloak_token()
    status, _ = _http(
        "POST",
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
        {"username": email, "email": email, "emailVerified": True, "enabled": True, "firstName": "E2E", "lastName": "Employee"},
        token=token,
    )
    assert status == 201, "could not create the test employee in Keycloak"
    _, users = _http("GET", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users?username={urllib.parse.quote(email)}&exact=true", token=token)
    user_id = users[0]["id"]
    _http("PUT", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}/reset-password",
          {"type": "password", "value": password, "temporary": False}, token=token)
    yield {"email": email, "password": password}
    _http("DELETE", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}", token=token)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


def _sign_in(browser, email, password):
    context = browser.new_context()
    page = context.new_page()
    page.goto(BASE_URL + "/")
    page.click("text=Sign in with AFS SSO")
    page.wait_for_selector("#kc-login", timeout=20000)
    page.fill("#username", email)
    page.fill("#password", password)
    page.click("#kc-login")
    page.wait_for_selector("#signed-in:not(.hidden)", timeout=30000)
    return context, page


def test_signed_out_visitor_sees_sso_sign_in(browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(BASE_URL + "/")
    expect(page.locator("text=Sign in with AFS SSO")).to_be_visible()
    context.close()


def test_health_reports_database_and_identity_provider(browser):
    context = browser.new_context()
    response = context.request.get(BASE_URL + "/api/health")
    assert response.status == 200
    assert response.json() == {"status": "ok", "database": "up", "identity_provider": "up"}
    context.close()


def test_administrator_sees_the_console_and_integration_health(browser):
    context, page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    tabs = page.locator("#tabs button").all_inner_texts()
    for expected in ["Employees", "Applications & roles", "Assignments", "Integrations", "Audit log"]:
        assert expected in tabs
    page.click("#tabs button:has-text('Integrations')")
    expect(page.locator("#views")).to_contain_text("Integration health")
    context.close()


def test_new_employee_has_no_admin_tabs_and_no_applications(browser, employee):
    context, page = _sign_in(browser, employee["email"], employee["password"])
    tabs = page.locator("#tabs button").all_inner_texts()
    assert tabs == ["My applications"]
    expect(page.locator("#views")).to_contain_text("No applications are assigned to you yet")
    context.close()


def test_assigned_but_disabled_application_shows_as_unavailable(browser, employee):
    admin_context, admin_page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    # An application can only be assigned while enabled, so assign first, then disable it.
    slug = f"e2e-{uuid.uuid4().hex[:8]}"
    created = admin_context.request.post(
        BASE_URL + "/api/admin/applications",
        data={"slug": slug, "name": "E2E application", "description": "Created by the test run",
              "launch_url": "https://example.test/app", "enabled": True},
    )
    assert created.status == 201
    app_id = created.json()["id"]
    users = admin_context.request.get(BASE_URL + "/api/admin/users").json()
    user_id = next(u["id"] for u in users if u["email"] == employee["email"])
    grant = admin_context.request.post(BASE_URL + "/api/admin/assignments", data={"user_id": user_id, "application_id": app_id})
    assert grant.status == 201
    disabled = admin_context.request.patch(BASE_URL + f"/api/admin/applications/{app_id}", data={"enabled": False})
    assert disabled.status == 200

    context, page = _sign_in(browser, employee["email"], employee["password"])
    expect(page.locator("text=E2E application")).to_be_visible()
    expect(page.get_by_text("Unavailable", exact=True)).to_be_visible()
    expect(page.locator("text=Open application")).to_have_count(0)
    context.close()
    admin_context.close()


def test_sign_out_returns_to_the_sign_in_page(browser, employee):
    context, page = _sign_in(browser, employee["email"], employee["password"])
    page.click("text=Sign out")
    # Keycloak asks the user to confirm ending the session, which is its default for logout without an ID token hint.
    confirm = page.locator("input[name=confirmLogout]")
    if confirm.count():
        confirm.click()
    expect(page.locator("text=Sign in with AFS SSO")).to_be_visible(timeout=20000)
    context.close()
