"""The do-not-contact list: normalising, matching, the CLI. Everything is invented."""

from __future__ import annotations

import pytest

from leadgen import cli, db
from leadgen.services import suppression
from leadgen.services.suppression import SuppressionError

PROFILE = "https://www.linkedin.com/in/aria-tan-1a2b3c"


@pytest.fixture(autouse=True)
def _db(ready_db):
    return None


def test_emails_are_lower_cased_and_stored_once():
    out = suppression.add(
        [" Aria.Tan@Example.org ", "aria.tan@example.org", "bad address"], "email", "asked"
    )
    assert out["added"] == ["aria.tan@example.org"]
    assert out["invalid"] == [{"value": "bad address", "reason": "not a valid email"}]
    again = suppression.add(["ARIA.TAN@example.org"], "email")
    assert again["added"] == [] and again["already_listed"] == ["aria.tan@example.org"]
    assert again["total_on_list"] == 1


def test_domains_are_reduced_to_the_host():
    out = suppression.add(
        ["https://www.Northwind.example/team?x=1", "mail.example.org", "nodots", "a b.com"],
        "domain",
    )
    assert out["added"] == ["northwind.example", "mail.example.org"]
    assert [i["value"] for i in out["invalid"]] == ["nodots", "a b.com"]


def test_linkedin_links_are_canonical_and_only_personal_profiles_count():
    out = suppression.add(
        [
            PROFILE.upper() + "/?utm=1",
            "https://sg.linkedin.com/in/ben-lee-99",
            "https://www.linkedin.com/company/x",
        ],
        "linkedin",
    )
    assert out["added"] == [PROFILE, "https://sg.linkedin.com/in/ben-lee-99"]
    assert len(out["invalid"]) == 1


def test_bad_requests_are_refused_with_codes():
    for args, code in (
        ((["a@example.org"], "phone"), "invalid_value"),
        (([], "email"), "invalid_value"),
        (("a@example.org", "email"), "invalid_value"),
        (([f"u{i}@example.org" for i in range(501)], "email"), "too_many_values"),
    ):
        with pytest.raises(SuppressionError) as err:
            suppression.add(*args)
        assert err.value.code == code
        assert err.value.as_response()["error"]["code"] == code


def test_reason_is_kept_short_and_single_line():
    suppression.add(["a@example.org"], "email", "line one\nline two " + "x" * 400)
    [row] = suppression.list()
    assert "\n" not in row["reason"] and len(row["reason"]) == 300


def test_is_suppressed_by_email_domain_and_parent_domain():
    suppression.add(["aria.tan@example.org"], "email")
    suppression.add(["northwind.example"], "domain")
    assert suppression.is_suppressed(email="Aria.Tan@example.org")
    assert not suppression.is_suppressed(email="ben.carter@example.org")
    assert suppression.is_suppressed(email="ben@northwind.example")
    assert suppression.is_suppressed(email="ben@mail.northwind.example")
    assert suppression.is_suppressed(domain="https://www.northwind.example/about")
    assert suppression.is_suppressed(domain="eu.northwind.example")
    assert not suppression.is_suppressed(domain="notnorthwind.example")
    assert not suppression.is_suppressed()


def test_is_suppressed_by_linkedin_profile_whatever_the_country_subdomain():
    suppression.add([PROFILE], "linkedin")
    assert suppression.is_suppressed(linkedin_url="https://sg.linkedin.com/in/Aria-Tan-1a2b3c/")
    assert not suppression.is_suppressed(linkedin_url="https://www.linkedin.com/in/someone-else")
    assert not suppression.is_suppressed(linkedin_url="not a link")


def test_list_newest_first_and_by_kind():
    suppression.add(["a@example.org"], "email", "first")
    suppression.add(["example.net"], "domain", "second")
    rows = suppression.list()
    assert [r["value"] for r in rows] == ["example.net", "a@example.org"]
    assert set(rows[0]) == {"value", "kind", "reason", "created_at"}
    assert [r["kind"] for r in suppression.list("email")] == ["email"]
    with pytest.raises(SuppressionError):
        suppression.list("phone")


def test_remove_returns_whether_it_was_listed():
    suppression.add(["a@example.org"], "email")
    assert suppression.remove(" A@Example.org ", "email") is True
    assert suppression.remove("a@example.org", "email") is False
    assert not suppression.is_suppressed(email="a@example.org")
    with pytest.raises(SuppressionError):
        suppression.remove("x", "phone")


def test_add_in_joins_the_callers_transaction():
    with db.session() as conn:
        suppression.add_in(conn, ["a@example.org"], "email", "x")
        conn.rollback()
    assert suppression.list() == []


def test_the_cli_lists_adds_and_removes(capsys):
    assert (
        cli.main(["suppression", "add", "email", "a@example.org", "nope", "--reason", "asked"]) == 1
    )
    out = capsys.readouterr().out
    assert "Added 1" in out and "Not a valid email: nope" in out
    assert cli.main(["suppression", "list"]) == 0
    assert "a@example.org  (asked)" in capsys.readouterr().out
    assert cli.main(["suppression", "list", "--kind", "domain"]) == 0
    assert "0 entries" in capsys.readouterr().out
    assert cli.main(["suppression", "remove", "email", "a@example.org"]) == 0
    assert "can be contacted again" in capsys.readouterr().out
    assert cli.main(["suppression", "remove", "email", "a@example.org"]) == 1
    assert "not on the email list" in capsys.readouterr().out
