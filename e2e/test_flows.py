"""End-to-end flows, clicked through the console. Success paths, cancellations and error messages.
Any uncaught page error or unexpected console error fails the test."""

import os
import uuid

import pytest
from playwright.sync_api import expect

from test_portal import ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, KEYCLOAK_URL, REALM, _http, _new_keycloak_user, _sign_in

# The browser logs every failed HTTP response as "Failed to load resource". The flows trigger 401, 404, 409 and 422
# on purpose and check the message the app shows, so only that generic network line is ignored.
IGNORED_CONSOLE = ("Failed to load resource",)


@pytest.fixture
def watched(browser):
    """A signed-in admin context whose page errors are collected and checked after the test."""
    context, page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    problems = []
    page.on("pageerror", lambda e: problems.append(f"uncaught: {e}"))
    page.on("console", lambda m: problems.append(f"console: {m.text}") if m.type == "error" and not any(i in m.text for i in IGNORED_CONSOLE) else None)
    yield context, page
    context.close()
    assert not problems, "\n".join(problems)


def _dialog(page):
    return page.locator("dialog[open]")


def _toast(page, text):
    expect(page.locator("#toasts").get_by_text(text, exact=False).first).to_be_visible(timeout=10000)


def _go(page, route):
    page.click(f"#nav a:has-text('{route}')")
    page.wait_for_selector("main h1", timeout=10000)


def test_employee_lifecycle(watched):
    _, page = watched
    _go(page, "Employees")
    email = f"flow-{uuid.uuid4().hex[:8]}@example.test"

    # Add: the dialog validates, then creates the account and shows it in the table.
    page.click("button:has-text('Add employee')")
    dialog = _dialog(page)
    dialog.get_by_label("Email *").fill("not-an-email")
    dialog.get_by_role("button", name="Add employee").click()
    expect(dialog).to_be_visible()  # browser validation keeps the dialog open
    dialog.get_by_label("Email *").fill(email)
    dialog.get_by_label("Display name").fill("Flow Person")
    dialog.get_by_role("button", name="Add employee").click()
    _toast(page, "Employee added")
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    expect(row).to_be_visible()
    expect(row).to_contain_text("Waiting for first sign-in")

    # Duplicate email shows the server's message in the dialog, not a crash.
    page.click("button:has-text('Add employee')")
    dialog = _dialog(page)
    dialog.get_by_label("Email *").fill(email)
    dialog.get_by_role("button", name="Add employee").click()
    expect(dialog.get_by_role("alert")).to_contain_text("already exists")
    dialog.get_by_role("button", name="Cancel").click()

    # Edit the display name.
    row.get_by_role("button", name=f"Edit {email}").click()
    dialog = _dialog(page)
    dialog.get_by_label("Display name").fill("Renamed Person")
    dialog.get_by_role("button", name="Save changes").click()
    _toast(page, "Employee updated")
    expect(page.get_by_role("table", name="Employees").locator("tr", has_text=email)).to_contain_text("Renamed Person")

    # Disable needs confirmation; cancelling changes nothing.
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    row.get_by_role("button", name=f"Disable {email}").click()
    _dialog(page).get_by_role("button", name="Cancel").click()
    expect(row).to_contain_text("Active")
    row.get_by_role("button", name=f"Disable {email}").click()
    _dialog(page).get_by_role("button", name="Disable account").click()
    _toast(page, "Account disabled")
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    expect(row).to_contain_text("Disabled")
    row.get_by_role("button", name=f"Reactivate {email}").click()
    _dialog(page).get_by_role("button", name="Reactivate account").click()
    _toast(page, "Account reactivated")


def test_employee_search_and_filters(watched):
    _, page = watched
    _go(page, "Employees")
    page.get_by_label("Filter by status").select_option("disabled")
    expect(page.locator("main")).to_contain_text("Disabled")
    page.get_by_label("Filter by status").select_option("")
    page.get_by_label("Filter by role").select_option("super_admin")
    expect(page.get_by_role("table", name="Employees").locator("tr", has_text=ADMIN_EMAIL)).to_be_visible()
    page.get_by_label("Filter by role").select_option("")


def test_platform_role_change_and_scope_editor(watched):
    _, page = watched
    _go(page, "Employees")
    email = f"role-{uuid.uuid4().hex[:8]}@example.test"
    page.click("button:has-text('Add employee')")
    _dialog(page).get_by_label("Email *").fill(email)
    _dialog(page).get_by_role("button", name="Add employee").click()
    _toast(page, "Employee added")
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    row.get_by_label(f"Platform role for {email}").select_option("access_admin")
    _toast(page, "Role updated")
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    row.get_by_role("button", name=f"Edit {email}").click()
    dialog = _dialog(page)
    expect(dialog.get_by_text("Applications this administrator manages")).to_be_visible()
    dialog.get_by_role("button", name="Cancel").click()
    # Put the role back so the account is an ordinary employee again.
    row.get_by_label(f"Platform role for {email}").select_option("employee")
    _toast(page, "Role updated")


def test_application_lifecycle(watched):
    _, page = watched
    _go(page, "Applications")
    name = f"Flow App {uuid.uuid4().hex[:5]}"
    slug = f"flow-{uuid.uuid4().hex[:8]}"

    page.click("button:has-text('Register application')")
    dialog = _dialog(page)
    dialog.get_by_label("Name *").fill(name)
    dialog.get_by_label("Slug *").fill(slug)
    dialog.get_by_role("button", name="Register").click()
    _toast(page, "Application registered")
    row = page.get_by_role("table", name="Applications").locator("tr", has_text=name)
    expect(row).to_contain_text("Disabled")

    # Enabling without a launch URL is refused with the reason shown.
    row.get_by_role("button", name=f"Enable {name}").click()
    _toast(page, "launch URL")
    # Give it a URL, then enable.
    row.get_by_role("button", name=f"Edit {name}").click()
    dialog = _dialog(page)
    dialog.get_by_label("Launch URL").fill("https://example.test/flow")
    dialog.get_by_role("button", name="Save changes").click()
    _toast(page, "Application updated")
    row = page.get_by_role("table", name="Applications").locator("tr", has_text=name)
    row.get_by_role("button", name=f"Enable {name}").click()
    _toast(page, "Application enabled")
    row = page.get_by_role("table", name="Applications").locator("tr", has_text=name)
    expect(row).to_contain_text("Enabled")

    # Roles: add one, then delete it (with confirmation).
    row.get_by_role("button", name=f"Roles for {name}").click()
    dialog = _dialog(page)
    dialog.get_by_label("Central role name").fill("reviewer")
    dialog.get_by_label("Value in the application").fill("REVIEWER")
    dialog.get_by_role("button", name="Add role").click()
    _toast(page, "Role added")
    dialog = _dialog(page)
    expect(dialog.get_by_role("cell", name="reviewer", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Delete reviewer").click()
    _dialog(page).get_by_role("button", name="Delete role").click()
    expect(_dialog(page).get_by_text("No roles yet")).to_be_visible(timeout=10000)
    _dialog(page).get_by_role("button", name="Done").click()

    # Disable again.
    row = page.get_by_role("table", name="Applications").locator("tr", has_text=name)
    row.get_by_role("button", name=f"Disable {name}").click()
    _dialog(page).get_by_role("button", name="Disable").click()
    _toast(page, "Application disabled")


def test_assignment_grant_and_revoke(watched):
    _, page = watched
    # An enabled application with a role, and an active employee.
    slug = f"asg-{uuid.uuid4().hex[:8]}"
    app_name = f"Assign App {uuid.uuid4().hex[:5]}"
    email = f"asg-{uuid.uuid4().hex[:8]}@example.test"
    _go(page, "Applications")
    page.click("button:has-text('Register application')")
    _dialog(page).get_by_label("Name *").fill(app_name)
    _dialog(page).get_by_label("Slug *").fill(slug)
    _dialog(page).get_by_label("Launch URL").fill("https://example.test/asg")
    _dialog(page).get_by_role("button", name="Register").click()
    _toast(page, "Application registered")
    row = page.get_by_role("table", name="Applications").locator("tr", has_text=app_name)
    row.get_by_role("button", name=f"Enable {app_name}").click()
    _toast(page, "Application enabled")
    page.get_by_role("table", name="Applications").locator("tr", has_text=app_name).get_by_role("button", name=f"Roles for {app_name}").click()
    _dialog(page).get_by_label("Central role name").fill("member")
    _dialog(page).get_by_label("Value in the application").fill("MEMBER")
    _dialog(page).get_by_role("button", name="Add role").click()
    _toast(page, "Role added")
    _dialog(page).get_by_role("button", name="Done").click()

    _go(page, "Employees")
    page.click("button:has-text('Add employee')")
    _dialog(page).get_by_label("Email *").fill(email)
    _dialog(page).get_by_role("button", name="Add employee").click()
    _toast(page, "Employee added")

    _go(page, "Assignments")
    page.click("button:has-text('Grant access')")
    dialog = _dialog(page)
    dialog.get_by_label("Employee").select_option(label=email)
    dialog.get_by_label("Application").select_option(label=app_name)
    # The role list loads after the application is chosen, so wait for it as a user would.
    expect(dialog.get_by_label("Role").locator("option", has_text="member (MEMBER)")).to_have_count(1)
    dialog.get_by_label("Role").select_option(label="member (MEMBER)")
    dialog.get_by_role("button", name="Grant access").click()
    _toast(page, "Access granted")
    row = page.get_by_role("table", name="Assignments").locator("tr", has_text=email)
    expect(row).to_contain_text("member")

    row.get_by_role("button", name=f"Revoke {app_name} from {email}").click()
    _dialog(page).get_by_role("button", name="Cancel").click()
    expect(page.get_by_role("table", name="Assignments").locator("tr", has_text=email)).to_be_visible()
    page.get_by_role("table", name="Assignments").locator("tr", has_text=email).get_by_role("button", name=f"Revoke {app_name} from {email}").click()
    _dialog(page).get_by_role("button", name="Revoke access").click()
    _toast(page, "Access revoked")
    expect(page.get_by_role("table", name="Assignments").locator("tr", has_text=email)).to_have_count(0)


def test_integrations_and_audit(watched):
    _, page = watched
    _go(page, "Integrations")
    page.get_by_role("button", name="View events for").first.click()
    expect(_dialog(page).get_by_role("heading", name="Events:")).to_be_visible()
    page.keyboard.press("Escape")
    _go(page, "Audit log")
    expect(page.get_by_role("table", name="Audit entries")).to_be_visible()
    page.get_by_role("button", name="Details").first.click()
    expect(page.locator("pre.details").first).to_be_visible()
    page.get_by_label("Filter by action").fill("user.create")
    page.get_by_label("Filter by action").press("Enter")
    page.wait_for_timeout(500)
    expect(page.locator("main")).to_contain_text("user.create")


def test_employee_flow_and_sign_out(browser):
    email, password, keycloak_id, token = _new_keycloak_user("flowemp")
    admin_context, admin_page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    try:
        admin_context.request.post(BASE_URL + "/api/admin/users", data={"email": email})
        context, page = _sign_in(browser, email, password)
        expect(page.locator("main")).to_contain_text("Nothing to open yet")
        # Unknown and old addresses fall back to the home page.
        page.goto(BASE_URL + "/#/app/does-not-exist")
        expect(page.locator("main h1")).to_contain_text("Welcome")
        page.goto(BASE_URL + "/#/project")
        expect(page.locator("main h1")).to_contain_text("Welcome")
        # Admin screens are not reachable by typing their address.
        page.goto(BASE_URL + "/#/employees")
        expect(page.locator("main h1")).to_contain_text("Welcome")
        expect(page.locator("#nav a")).to_have_text(["Home"])
        context.close()
    finally:
        _http("DELETE", f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{keycloak_id}", token=token)
        admin_context.close()


def test_sso_error_codes_are_shown_on_the_sign_in_page(browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(BASE_URL + "/?sso_error=not_assigned")
    expect(page.locator("#signin-message")).to_be_visible()
    context.close()


def test_super_admin_sets_an_access_administrators_scope(watched):
    _, page = watched
    _go(page, "Applications")
    scoped = f"Scope App {uuid.uuid4().hex[:5]}"
    page.click("button:has-text('Register application')")
    _dialog(page).get_by_label("Name *").fill(scoped)
    _dialog(page).get_by_label("Slug *").fill(f"sc-{uuid.uuid4().hex[:8]}")
    _dialog(page).get_by_role("button", name="Register").click()
    _toast(page, "Application registered")

    _go(page, "Employees")
    email = f"scope-{uuid.uuid4().hex[:8]}@example.test"
    page.click("button:has-text('Add employee')")
    _dialog(page).get_by_label("Email *").fill(email)
    _dialog(page).get_by_role("button", name="Add employee").click()
    _toast(page, "Employee added")
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    row.get_by_label(f"Platform role for {email}").select_option("access_admin")
    _toast(page, "Role updated")
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    row.get_by_role("button", name=f"Edit {email}").click()
    dialog = _dialog(page)
    dialog.get_by_label(scoped).check()
    dialog.get_by_role("button", name="Save changes").click()
    _toast(page, "Employee updated")
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    row.get_by_role("button", name=f"Edit {email}").click()
    expect(_dialog(page).get_by_label(scoped)).to_be_checked()
    _dialog(page).get_by_role("button", name="Cancel").click()
    row = page.get_by_role("table", name="Employees").locator("tr", has_text=email)
    row.get_by_label(f"Platform role for {email}").select_option("employee")
    _toast(page, "Role updated")


def test_mobile_layout_has_no_sideways_scrolling(browser):
    context, page = _sign_in(browser, ADMIN_EMAIL, ADMIN_PASSWORD)
    page.set_viewport_size({"width": 390, "height": 844})
    problems = []
    for route in ["home", "employees", "applications", "assignments", "integrations", "audit"]:
        page.goto(BASE_URL + "/#/" + route)
        page.wait_for_selector("main h1", timeout=10000)
        page.wait_for_timeout(300)
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        if overflow > 1:
            problems.append(f"{route}: {overflow}px sideways")
    context.close()
    assert not problems, problems
