"""Transactional email for Command Center, sent through Resend's HTTP API.

Sending runs after the request's changes are committed (FastAPI background task), so an email problem can
never undo an administrative change. Without RESEND_API_KEY, notices are skipped and logged, and the feature
is off. Each attempt is recorded in the audit log as notification.sent or notification.failed.
"""

import html
import logging
import os

import httpx

from .audit import record
from .db import connect

log = logging.getLogger("afs.notifications")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
RESEND_URL = "https://api.resend.com/emails"


def enabled():
    return bool(RESEND_API_KEY and RESEND_FROM)


def render(title, paragraphs, action_label=None, action_url=None):
    """Return (subject-less) HTML and text bodies. All dynamic text is escaped."""
    body = "".join(f"<p style=\"margin:0 0 12px\">{html.escape(p)}</p>" for p in paragraphs)
    button = ""
    if action_label and action_url:
        button = (
            f"<p style=\"margin:20px 0\"><a href=\"{html.escape(action_url)}\" "
            "style=\"background:#0b5cad;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none\">"
            f"{html.escape(action_label)}</a></p>"
        )
    html_body = (
        "<div style=\"font-family:system-ui,sans-serif;color:#1d1d1f;max-width:560px\">"
        f"<h2 style=\"font-size:18px;margin:0 0 16px\">{html.escape(title)}</h2>{body}{button}"
        "<p style=\"color:#6e6e73;font-size:12px;margin-top:24px\">AFS Command Center</p></div>"
    )
    text = "\n\n".join([title, *paragraphs] + ([f"{action_label}: {action_url}"] if action_url else []))
    return html_body, text


def send(recipient, subject, html_body, text_body, audit_target_id=None, kind="notice"):
    """Send one email. Never raises; the outcome goes to the audit log."""
    if not enabled():
        log.info("email skipped (RESEND_API_KEY or RESEND_FROM not set): %s", kind)
        return False
    try:
        response = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": RESEND_FROM, "to": [recipient], "subject": subject, "html": html_body, "text": text_body},
            timeout=10,
        )
        ok = response.status_code in (200, 201)
        detail = {"kind": kind, "http_status": response.status_code}
        if not ok:
            log.warning("resend rejected email (%s): HTTP %s", kind, response.status_code)
    except httpx.HTTPError as error:
        ok = False
        detail = {"kind": kind, "error": type(error).__name__}
        log.warning("resend unreachable (%s): %s", kind, type(error).__name__)
    with connect() as conn:
        record(
            conn,
            actor=None,
            action="notification.sent" if ok else "notification.failed",
            outcome="success" if ok else "failure",
            target_type="user",
            target_id=audit_target_id,
            details=detail,
        )
    return ok


def notify(recipient, target_user_id, kind, subject, paragraphs, action_label=None, action_path=None):
    """Build and send a notice. The subject doubles as the heading. `action_path` is appended to the portal address.

    Never raises: a notice is a side effect of an administrative change, and must not fail the request.
    """
    try:
        action_url = f"{PUBLIC_BASE_URL}{action_path}" if action_path else None
        html_body, text_body = render(subject, paragraphs, action_label, action_url)
        return send(recipient, subject, html_body, text_body, audit_target_id=target_user_id, kind=kind)
    except Exception:  # noqa: BLE001 - the change is already committed; log and move on
        log.exception("notification failed to build (%s)", kind)
        return False
