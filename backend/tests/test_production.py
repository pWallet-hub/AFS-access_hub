"""Production startup checks: the service refuses unsafe configuration when ENVIRONMENT=production."""

from app.production import assert_production_ready, production_problems

GOOD = {
    "ENVIRONMENT": "production",
    "SESSION_SECRET": "q9Zr4t8LmW2xVb6nP0sKd3HfJ7yGc1Ua",
    "OIDC_CLIENT_SECRET": "Lw8aTq2NpE6vXc0RsD4mKb9HyJ3uFg7z",
    "AFS_SERVICE_TOKEN_BIOCAP": "T4hR9mQw2ZxKc8LpB6vNd1SgY3eJf7Ua",
    "PUBLIC_BASE_URL": "https://auth.afs-rwanda.example",
    "KEYCLOAK_PUBLIC_URL": "https://auth.afs-rwanda.example",
    "BIOCAP_ORIGIN": "https://biocap.afs-rwanda.example",
    "SESSION_HTTPS_ONLY": "true",
}


def test_development_is_never_blocked():
    assert production_problems({"ENVIRONMENT": "development", "SESSION_SECRET": "change-me"}) == []


def test_a_complete_production_configuration_starts():
    assert production_problems(GOOD) == []
    assert_production_ready(GOOD)


def test_placeholder_and_short_secrets_are_refused():
    problems = production_problems({**GOOD, "SESSION_SECRET": "change-me-to-64-random-hex-chars"})
    assert any("SESSION_SECRET" in p for p in problems)
    assert any("SESSION_SECRET" in p for p in production_problems({**GOOD, "SESSION_SECRET": "short"}))


def test_plain_http_public_addresses_are_refused():
    problems = production_problems({**GOOD, "PUBLIC_BASE_URL": "http://auth.example", "BIOCAP_ORIGIN": "http://biocap.example"})
    assert any("PUBLIC_BASE_URL" in p for p in problems)
    assert any("BIOCAP_ORIGIN" in p for p in problems)


def test_session_cookies_must_be_https_only():
    env = {k: v for k, v in GOOD.items() if k != "SESSION_HTTPS_ONLY"}
    assert any("SESSION_HTTPS_ONLY" in p for p in production_problems(env))


def test_startup_stops_with_every_problem_listed():
    try:
        assert_production_ready({**GOOD, "SESSION_SECRET": "change-me", "PUBLIC_BASE_URL": "http://x.example"})
    except RuntimeError as err:
        assert "SESSION_SECRET" in str(err) and "PUBLIC_BASE_URL" in str(err)
    else:
        raise AssertionError("startup should be refused")
