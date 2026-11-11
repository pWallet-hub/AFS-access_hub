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


def _sign_in(browser, email, password):
    context = browser.new_context()
    page = context.new_page()
    page.goto(BASE_URL + "/")
    page.click("text=Sign in with AFS SSO")
    page.wait_for_selector("#kc-login", timeout=20000)
    page.fill("#username", email)
    page.fill("#password", password)
    page.click("#kc-login")
    page.wait_for_selector("#view-app:not([hidden])", timeout=30000)
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
    links = page.locator("#nav a").all_inner_texts()
    for expected in ["Employees", "Applications", "Assignments", "Integrations", "Audit log"]:
        assert expected in links
    page.click("#nav a:has-text('Integrations')")
    expect(page.get_by_role("heading", name="Integrations")).to_be_visible()
    expect(page.locator("main")).to_contain_text("Failing")
    context.close()


def test_new_employee_has_no_admin_tabs_and_no_applications(browser, employee):
    context, page = _sign_in(browser, employee["email"], employee["password"])
    links = page.locator("#nav a").all_inner_texts()
    assert links == ["Home"]
    expect(page.locator("main")).to_contain_text("Nothing to open yet")
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
    # The name is also in the side menu, so look on the page itself.
    expect(page.locator("main").get_by_text("E2E application")).to_be_visible()
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


def test_employee_search_filters_the_list(browser):
    context, page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    page.click("#nav a:has-text('Employees')")
    search = page.get_by_role("searchbox", name="Search employees")
    search.fill("no-such-person-zzz")
    expect(page.locator("main")).to_contain_text("No employees match")
    search.fill("carterk279")
    expect(page.get_by_role("table", name="Employees")).to_contain_text("carterk279@gmail.com")
    context.close()


def test_add_employee_dialog_validates_the_email(browser):
    context, page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    page.click("#nav a:has-text('Employees')")
    page.click("button:has-text('Add employee')")
    dialog = page.locator("dialog[open]")
    dialog.get_by_label("Email *").fill("not-an-email")
    dialog.get_by_role("button", name="Add employee").click()
    # The browser blocks submission for an invalid email, so no request is made and the dialog stays open.
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    expect(page.locator("dialog[open]")).to_have_count(0)
    context.close()


def test_audit_log_can_be_filtered_by_outcome(browser):
    context, page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    page.click("#nav a:has-text('Audit log')")
    page.get_by_label("Filter by outcome").select_option("failure")
    page.wait_for_timeout(800)
    rows = page.get_by_role("table", name="Audit entries").locator("tbody tr")
    if rows.count() == 0:
        expect(page.locator("main")).to_contain_text("No matching entries")
    for i in range(rows.count()):
        text = rows.nth(i).inner_text()
        assert "Failure" in text and "Success" not in text, text
    context.close()


def _new_keycloak_user(prefix):
    email = f"{prefix}-{uuid.uuid4().hex[:10]}@example.test"
    password = "E2e-Password-2026!"
    token = _keycloak_token()
    _http("POST", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
          {"username": email, "email": email, "emailVerified": True, "enabled": True, "firstName": "E2E", "lastName": prefix}, token=token)
    _, users = _http("GET", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users?username={urllib.parse.quote(email)}&exact=true", token=token)
    user_id = users[0]["id"]
    _http("PUT", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}/reset-password",
          {"type": "password", "value": password, "temporary": False}, token=token)
    return email, password, user_id, token


def test_access_admin_sees_only_the_applications_in_their_scope(browser):
    admin_context, _ = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    scoped_name = f"E2E Scoped {uuid.uuid4().hex[:6]}"
    other_name = f"E2E Other {uuid.uuid4().hex[:6]}"
    apps = {}
    for name in (scoped_name, other_name):
        created = admin_context.request.post(BASE_URL + "/api/admin/applications", data={
            "slug": f"e2e-{uuid.uuid4().hex[:8]}", "name": name, "description": "Created by the test run",
            "launch_url": "https://example.test/app", "enabled": True})
        assert created.status == 201
        apps[name] = created.json()["id"]

    email, password, keycloak_id, token = _new_keycloak_user("ops")
    try:
        created = admin_context.request.post(BASE_URL + "/api/admin/users", data={"email": email})
        assert created.status == 201
        user_id = created.json()["id"]
        assert admin_context.request.patch(BASE_URL + f"/api/admin/users/{user_id}", data={"platform_role": "access_admin"}).status == 200
        scope = admin_context.request.put(BASE_URL + f"/api/admin/users/{user_id}/managed-applications",
                                          data={"application_ids": [apps[scoped_name]]})
        assert scope.status == 200

        ops_context, ops_page = _sign_in(browser, email, password)
        ops_page.click("#nav a:has-text('Assignments')")
        filter_select = ops_page.get_by_label("Filter by application")
        filter_select.wait_for(timeout=10000)
        expect(filter_select.locator("option").nth(1)).to_have_count(1)
        filter_options = filter_select.locator("option").all_inner_texts()
        assert scoped_name in filter_options
        assert other_name not in filter_options

        ops_page.click("button:has-text('Grant access')")
        dialog = ops_page.locator("dialog[open]")
        app_options = dialog.get_by_label("Application").locator("option").all_inner_texts()
        assert app_options == [scoped_name]
        dialog.get_by_role("button", name="Cancel").click()
        ops_context.close()
    finally:
        _http("DELETE", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{keycloak_id}", token=token)
        admin_context.close()


def test_application_opens_inside_the_portal_and_in_a_new_tab(browser, employee):
    admin_context, _ = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    name = f"E2E Embedded {uuid.uuid4().hex[:6]}"
    created = admin_context.request.post(BASE_URL + "/api/admin/applications", data={
        "slug": f"e2e-{uuid.uuid4().hex[:8]}", "name": name, "description": "Opens inside the portal",
        "launch_url": BASE_URL + "/demo/", "enabled": True})
    assert created.status == 201
    app_id = created.json()["id"]
    users = admin_context.request.get(BASE_URL + "/api/admin/users").json()
    user_id = next(u["id"] for u in users if u["email"] == employee["email"])
    assert admin_context.request.post(BASE_URL + "/api/admin/assignments", data={"user_id": user_id, "application_id": app_id}).status == 201

    context, page = _sign_in(browser, employee["email"], employee["password"])
    open_link = page.locator("a.btn.primary", has_text="Open").first
    open_link.wait_for(timeout=10000)
    # Applications open only in a new tab; nothing is embedded in the portal.
    expect(page.get_by_role("link", name="Open here")).to_have_count(0)
    expect(page.locator("iframe")).to_have_count(0)
    expect(open_link).to_have_attribute("target", "_blank")
    assert "/demo/" in open_link.get_attribute("href")
    expect(open_link).to_have_attribute("rel", "noopener noreferrer")
    context.close()
    admin_context.close()
