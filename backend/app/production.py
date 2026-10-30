"""Startup checks for production. When ENVIRONMENT=production, the service refuses to start with placeholder or short
secrets, or with a public address that is not HTTPS. Development keeps working unchanged."""

import os

MIN_SECRET_LENGTH = 24
PLACEHOLDER_MARKERS = ("change-me", "example", "password", "secret-value", "test-")

# Secrets that must be random and long in production (name -> reason in the message).
SECRET_VARIABLES = (
    "SESSION_SECRET",
    "OIDC_CLIENT_SECRET",
    "KEYCLOAK_ADMIN_PASSWORD",
    "AFS_SERVICE_TOKEN_IMAGEUNIT",
    "AFS_SERVICE_TOKEN_BIOCAP",
    "AFS_SERVICE_TOKEN_PROCUREMENT",
    "IMAGEUNIT_OIDC_CLIENT_SECRET",
    "BIOCAP_OIDC_CLIENT_SECRET",
    "PROCUREMENT_OIDC_CLIENT_SECRET",
)
# Public addresses browsers use. They must be HTTPS in production.
HTTPS_VARIABLES = ("PUBLIC_BASE_URL", "KEYCLOAK_PUBLIC_URL", "IMAGEUNIT_ORIGIN", "BIOCAP_ORIGIN", "PROCUREMENT_ORIGIN")


def production_problems(env=os.environ):
    """Return a list of problems. Empty when the configuration is fit for production (or not in production)."""
    if env.get("ENVIRONMENT", "development") != "production":
        return []
    problems = []
    for name in SECRET_VARIABLES:
        value = env.get(name, "")
        if not value:
            continue  # optional applications that are not configured are not a problem
        if len(value) < MIN_SECRET_LENGTH or any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
            problems.append(f"{name} must be a random value of at least {MIN_SECRET_LENGTH} characters, not a placeholder")
    for name in HTTPS_VARIABLES:
        value = env.get(name, "")
        if value and not value.startswith("https://"):
            problems.append(f"{name} must be an https:// address in production")
    if env.get("SESSION_HTTPS_ONLY", "false").lower() != "true":
        problems.append("SESSION_HTTPS_ONLY must be true in production (session cookies are sent over HTTPS only)")
    if "KC_HOSTNAME" in env and not env["KC_HOSTNAME"].startswith("https://"):
        problems.append("KC_HOSTNAME must be an https:// address in production")
    return problems


def assert_production_ready(env=os.environ):
    problems = production_problems(env)
    if problems:
        raise RuntimeError("Refusing to start in production:\n- " + "\n- ".join(problems))
