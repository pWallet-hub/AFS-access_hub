"""Identity resolution and provisioning for Command Center accounts."""

from .audit import record

ACCOUNT_LOCK_KEY = 727002


def sign_in(conn, claims, bootstrap_candidate):
    """Resolve a verified Keycloak identity to a Command Center user.

    Order of resolution:
      1. An existing user with the same Keycloak `sub`.
      2. A pre-provisioned user with the same email, only when Keycloak reports the email as
         verified. This link is recorded in the audit log as identity.link.
      3. Otherwise a new employee account is created. It can sign in but has no application access.

    `bootstrap_candidate` is True when the token carries the Keycloak realm role that marks the
    initial super administrator. It is honoured only while no active super administrator exists.

    Returns the user row, or None when the account is disabled.
    """
    sub = claims["sub"]
    email = (claims.get("email") or "").strip().lower()
    email_verified = claims.get("email_verified") is True
    display_name = claims.get("name") or claims.get("preferred_username") or email

    # Serialise sign-ins so concurrent first logins cannot both become the first super administrator.
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (ACCOUNT_LOCK_KEY,))

    user = conn.execute("SELECT * FROM users WHERE keycloak_sub = %s", (sub,)).fetchone()

    if user is None and email and email_verified:
        user = conn.execute(
            "UPDATE users SET keycloak_sub = %s, email_verified = true, updated_at = now()"
            " WHERE id = (SELECT id FROM users WHERE keycloak_sub IS NULL AND lower(email) = %s"
            " FOR UPDATE) RETURNING *",
            (sub, email),
        ).fetchone()
        if user is not None:
            record(
                conn,
                actor=user,
                action="identity.link",
                outcome="success",
                target_type="user",
                target_id=user["id"],
                details={"method": "pre-provisioned account, verified email"},
            )

    if user is None:
        user = conn.execute(
            "INSERT INTO users (keycloak_sub, email, display_name, email_verified)"
            " VALUES (%s, %s, %s, %s) RETURNING *",
            (sub, email, display_name, email_verified),
        ).fetchone()
        record(
            conn,
            actor=user,
            action="user.provision",
            outcome="success",
            target_type="user",
            target_id=user["id"],
            details={"method": "first sign-in"},
        )

    if user["status"] != "active":
        record(
            conn,
            actor=user,
            action="auth.login",
            outcome="failure",
            target_type="user",
            target_id=user["id"],
            details={"reason": "account disabled"},
        )
        return None

    if bootstrap_candidate and not _active_super_admin_exists(conn):
        user = conn.execute(
            "UPDATE users SET platform_role = 'super_admin', updated_at = now() WHERE id = %s RETURNING *",
            (user["id"],),
        ).fetchone()
        record(
            conn,
            actor=user,
            action="platform_role.bootstrap",
            outcome="success",
            target_type="user",
            target_id=user["id"],
            details={"platform_role": "super_admin"},
        )

    user = conn.execute(
        "UPDATE users SET display_name = %s, email = %s, email_verified = %s,"
        " last_login_at = now(), updated_at = now() WHERE id = %s RETURNING *",
        (display_name, email or user["email"], email_verified, user["id"]),
    ).fetchone()
    record(
        conn,
        actor=user,
        action="auth.login",
        outcome="success",
        target_type="user",
        target_id=user["id"],
    )
    return user


def _active_super_admin_exists(conn):
    row = conn.execute(
        "SELECT 1 AS found FROM users WHERE platform_role = 'super_admin' AND status = 'active' LIMIT 1"
    ).fetchone()
    return row is not None
