"""Seeded application roles must match the roles each application actually enforces."""

import pytest

from app.db import connect

EXPECTED = {
    "biocap": {"admin", "communication_officer", "editor", "social_media_content_creator",
               "media_correspondent", "principal_investigator"},
    "procurement": {"requesting_staff", "accountant", "director_comms", "director_dept", "pi", "cfm",
                    "market_verifier", "superior", "admin"},
    "imageunit": {"admin", "editor", "photographer"},
}

@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_seeded_roles_match_the_application(slug):
    with connect() as conn:
        rows = conn.execute(
            "SELECT r.name, r.external_role FROM app_roles r JOIN applications a ON a.id = r.application_id"
            " WHERE a.slug = %s",
            (slug,),
        ).fetchall()
    names = {row["name"] for row in rows}
    assert names == EXPECTED[slug]
    # The value the application stores must equal the central role name.
    assert all(row["name"] == row["external_role"] for row in rows)

