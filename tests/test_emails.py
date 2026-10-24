"""Email finder service: pattern building, derivation, verification order, storing, chunking.

No test touches the network, DNS or a socket: DNS is a fake resolver, HTTP is respx, SMTP is a
recorded stub, and verifiers/Hunter are in-memory fakes unless a test says otherwise.
"""

from __future__ import annotations

import itertools
import smtplib
import types
from datetime import UTC, datetime, timedelta

import dns.resolver
import httpx
import pytest
import respx

from leadgen import db
from leadgen.core import secrets
from leadgen.providers import base, registry
from leadgen.providers.base import ProviderError, RateLimiter, Verdict
from leadgen.services import budget, emails, projects
from leadgen.services import campaign as camp
from leadgen.services.emails import EmailError

MX = {
    "northwind.example": "aspmx.l.google.com",
    "contoso.example": "contoso-example.mail.protection.outlook.com",
    "fabrikam.example": "mx1.pphosted.com",
    "small.example": "mail.small.example",
}


class FakeAnswer:
    def __init__(self, host, preference=10):
        self.exchange = host + "."
        self.preference = preference


_REAL_SCRAPE = emails.scrape_site_emails  # the fixture below replaces it in every test


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Fake DNS, no SMTP, no website scraping, fast limiters, empty caches."""
    emails._mx_cache.clear()
    emails._site_cache.clear()
    monkeypatch.setattr(emails, "_site_limiter", RateLimiter(10_000))
    resolved = []

    def resolve(domain, rdtype="A", **kwargs):
        resolved.append(domain)
        if domain in MX:
            return [FakeAnswer("zz.example", 20), FakeAnswer(MX[domain], 10)]
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(emails.dns.resolver, "resolve", resolve)
    smtp_calls = []

    def no_smtp(host, email):
        smtp_calls.append((host, email))
        return None

    monkeypatch.setattr(emails, "_smtp_rcpt", no_smtp)

    class NoSocketSMTP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a real SMTP connection was attempted")

    monkeypatch.setattr(emails.smtplib, "SMTP", NoSocketSMTP)
    monkeypatch.setattr(emails, "scrape_site_emails", lambda domain: [])
    return types.SimpleNamespace(resolved=resolved, smtp=smtp_calls)


@pytest.fixture
def cid(ready_db):
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Northwind Training", source_type="text")
    return camp.create_campaign(
        name="SG logistics",
        offer_id=offer_id,
        project_id=projects.ensure_project_for_offer(offer_id),
        countries=["SG"],
        sectors=["logistics"],
        company_size="200-1000",
        channels=["email", "linkedin"],
        designations=["ld", "hr"],
    )["id"]


_counter = itertools.count(1)


def add_company(cid, name="Northwind Health", domain="northwind.example", status="approved"):
    with db.session() as conn:
        return db.insert(
            conn, "companies", campaign_id=cid, name=name, domain=domain, status=status
        )


def add_person(cid, company_id, first="Aria", last="Tan", status="confirmed"):
    n = next(_counter)
    with db.session() as conn:
        return db.insert(
            conn, "people", campaign_id=cid, company_id=company_id, first_name=first,
            last_name=last, raw_name=f"{first} {last}", status=status,
            confirmed=int(status == "confirmed"), linkedin_url=f"https://www.linkedin.com/in/p{n}",
        )  # fmt: skip


def email_rows(person_id=None):
    with db.session() as conn:
        sql = "SELECT * FROM emails" + (" WHERE person_id = ?" if person_id else "")
        return db.query(conn, sql + " ORDER BY id", (person_id,) if person_id else ())


class FakeVerifier:
    """A verifier that answers from a callable/dict; `answers` values: Verdict or exception."""

    def __init__(self, key, answers=None, default="verified"):
        self.info = types.SimpleNamespace(key=key)
        self.answers = answers if answers is not None else {}
        self.default = default
        self.calls = []

    def configured(self):
        return True

    def verify(self, email):
        self.calls.append(email)
        answer = self.answers(email) if callable(self.answers) else self.answers.get(email)
        if answer is None:
            answer = self.default
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, str):
            answer = Verdict(answer, answer, self.info.key)
        return answer


class FakeHunter:
    info = types.SimpleNamespace(key="hunter")

    def __init__(self, pattern="{first}.{last}", error=None):
        self.pattern = pattern
        self.error = error
        self.calls = []

    def configured(self):
        return True

    def domain_pattern(self, domain):
        self.calls.append(domain)
        if self.error:
            raise self.error
        return (self.pattern, 0.85) if self.pattern else None


# --- building addresses ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "first", "last", "expected"),
    [
        ("{first}.{last}", "Aria", "Tan", "aria.tan@example.com"),
        ("{first}{last}", "Aria", "Tan", "ariatan@example.com"),
        ("{f}{last}", "Aria", "Tan", "atan@example.com"),
        ("{first}", "Aria", "Tan", "aria@example.com"),
        ("{first}_{last}", "Aria", "Tan", "aria_tan@example.com"),
        ("{last}.{first}", "Aria", "Tan", "tan.aria@example.com"),
        ("{f}.{last}", "Aria", "Tan", "a.tan@example.com"),
        ("{first}{l}", "Aria", "Tan", "ariat@example.com"),
        ("{last}", "Aria", "Tan", "tan@example.com"),
        ("{first}-{last}", "Aria", "Tan", "aria-tan@example.com"),
        ("{last}{f}", "Aria", "Tan", "tana@example.com"),
        # accents (NFKD) and letters NFKD does not split
        ("{first}.{last}", "José", "Núñez", "jose.nunez@example.com"),
        ("{first}.{last}", "Zoë", "Müller", "zoe.muller@example.com"),
        ("{first}.{last}", "Søren", "Straße", "soren.strasse@example.com"),
        ("{first}.{last}", "Łukasz", "Đorđević", "lukasz.dordevic@example.com"),
        # hyphens and apostrophes are dropped
        ("{first}.{last}", "Anne-Marie", "O'Neil", "annemarie.oneil@example.com"),
        ("{first}.{last}", "Jean", "Smith-Jones", "jean.smithjones@example.com"),
        ("{first}.{last}", "Aria", "O’Brien", "aria.obrien@example.com"),
        # multi-part surnames: the last word
        ("{first}.{last}", "Aria", "van der Berg", "aria.berg@example.com"),
        ("{first}.{last}", "Maria", "de la Cruz", "maria.cruz@example.com"),
        ("{f}{last}", "Maria", "de la Cruz", "mcruz@example.com"),
        # single-letter first names and initials
        ("{first}.{last}", "J", "Tan", "j.tan@example.com"),
        ("{f}.{last}", "J.", "Tan", "j.tan@example.com"),
        ("{first}{l}", "Aria", "T", "ariat@example.com"),
        # upper case, digits and spaces around
        ("{first}.{last}", "  ARIA ", " TAN2 ", "aria.tan@example.com"),
    ],
)
def test_build_email_table(pattern, first, last, expected):
    assert emails.build_email(pattern, first, last, "example.com") == expected


@pytest.mark.parametrize(
    ("pattern", "first", "last", "domain"),
    [
        ("{first}.{last}", "", "Tan", "example.com"),
        ("{first}.{last}", "Aria", "", "example.com"),
        ("{first}.{last}", "王", "伟", "example.com"),  # no latin letters at all
        ("{first}.{last}", "Aria", "Tan", ""),
        ("{first}..{last}x", "Aria", "Tan", "example.com"),
        ("first.last", "Aria", "Tan", "example.com"),
        ("{middle}", "Aria", "Tan", "example.com"),
    ],
)
def test_build_email_refuses_unbuildable(pattern, first, last, domain):
    assert emails.build_email(pattern, first, last, domain) == ""


def test_build_email_cleans_domain():
    assert emails.build_email("{first}", "Aria", "Tan", "https://www.Example.com/about") == (
        "aria@example.com"
    )


def test_joined_surname_only_for_real_examples():
    args = ("{first}.{last}", "Aria", "van der Berg", "example.com")
    assert emails.candidate_emails(*args) == ["aria.berg@example.com"]
    assert emails.candidate_emails(*args, from_example=True) == [
        "aria.berg@example.com",
        "aria.vanderberg@example.com",
    ]
    # a one-word surname has no second form
    assert emails.candidate_emails("{first}", "Aria", "Tan", "example.com", True) == [
        "aria@example.com"
    ]
    assert emails.candidate_emails("{first}", "", "Tan", "example.com", True) == []


def test_infer_pattern_from_known_address():
    assert emails.infer_pattern("aria.tan@example.com", "Aria", "Tan") == "{first}.{last}"
    assert emails.infer_pattern("atan@example.com", "Aria", "Tan") == "{f}{last}"
    assert emails.infer_pattern("ariat@example.com", "Aria", "Tan") == "{first}{l}"
    assert emails.infer_pattern("tan.aria@example.com", "Aria", "Tan") == "{last}.{first}"
    assert emails.infer_pattern("Aria@Example.com", "Aria", "Tan") == "{first}"
    assert emails.infer_pattern("jose.nunez@example.com", "José", "Núñez") == "{first}.{last}"
    # multi-word surname: matches the last word or the joined form
    assert emails.infer_pattern("aria.berg@example.com", "Aria", "van der Berg") == (
        "{first}.{last}"
    )
    assert emails.infer_pattern("ariavanderberg@example.com", "Aria", "van der Berg") == (
        "{first}{last}"
    )
    assert emails.infer_pattern("zed@example.com", "Aria", "Tan") is None
    assert emails.infer_pattern("not-an-email", "Aria", "Tan") is None
    assert emails.infer_pattern("aria.tan@example.com", "", "Tan") is None


def test_infer_pattern_from_local_part_without_names():
    assert emails.infer_pattern_from_local("aria.tan") == "{first}.{last}"
    assert emails.infer_pattern_from_local("aria_tan") == "{first}_{last}"
    assert emails.infer_pattern_from_local("aria-tan") == "{first}-{last}"
    assert emails.infer_pattern_from_local("a.tan") == "{f}.{last}"
    assert emails.infer_pattern_from_local("aria") is None  # could be {first} or {last}
    assert emails.infer_pattern_from_local("a.b.c") is None
    assert emails.infer_pattern_from_local("a.t") is None


@pytest.mark.parametrize(
    "address",
    ["info@northwind.example", "HR@northwind.example", "careers@northwind.example",
     "admin@northwind.example", "contact@northwind.example", "hello@northwind.example",
     "sales@northwind.example", "no-reply@northwind.example", "noreply@northwind.example",
     "customer.service@northwind.example", "info", "h.r@northwind.example"],
)  # fmt: skip
def test_role_addresses_detected(address):
    assert emails.is_role_address(address)


@pytest.mark.parametrize(
    "address",
    ["aria.tan@northwind.example", "tan@northwind.example", "ariahr@northwind.example"],
)
def test_person_addresses_are_not_roles(address):
    assert not emails.is_role_address(address)


# --- mail servers ---------------------------------------------------------------------------


def test_mx_type_classification_and_cache(isolated):
    assert emails.mx_type("northwind.example") == "google"
    assert emails.mx_type("contoso.example") == "microsoft"
    assert emails.mx_type("fabrikam.example") == "gateway"
    assert emails.mx_type("small.example") == "other"
    assert emails.mx_type("WWW.Northwind.example") == "google"  # cleaned, cached
    assert isolated.resolved.count("northwind.example") == 1
    assert emails.mx_type("nowhere.example") is None
    assert emails.mx_type("") is None


def test_mx_type_transient_dns_failure_is_not_cached(monkeypatch):
    calls = []

    def flaky(domain, rdtype="A", **kw):
        calls.append(domain)
        if len(calls) == 1:
            raise dns.resolver.LifetimeTimeout()
        return [FakeAnswer("mx.small.example")]

    monkeypatch.setattr(emails.dns.resolver, "resolve", flaky)
    assert emails.mx_type("small.example") is None
    assert emails.mx_type("small.example") == "other"
    assert len(calls) == 2


def test_mx_type_no_mx_answer_and_null_mx(monkeypatch):
    def no_answer(domain, rdtype="A", **kw):
        raise dns.resolver.NoAnswer()

    monkeypatch.setattr(emails.dns.resolver, "resolve", no_answer)
    assert emails.mx_type("bare.example") is None
    monkeypatch.setattr(emails.dns.resolver, "resolve", lambda *a, **k: [FakeAnswer("")])
    assert emails.mx_type("null-mx.example") is None


# --- pattern derivation ---------------------------------------------------------------------


def test_derive_default_is_not_cached(ready_db):
    assert emails.derive_pattern("acme.example", hunter=FakeHunter(None)) == (
        "{first}.{last}",
        "default",
    )
    assert emails._cached_pattern("acme.example") is None


def test_derive_uses_hunter_then_caches_it(ready_db):
    hunter = FakeHunter("{f}{last}")
    assert emails.derive_pattern("acme.example", hunter=hunter) == ("{f}{last}", "hunter")
    assert emails.derive_pattern("acme.example", hunter=hunter) == ("{f}{last}", "hunter")
    assert hunter.calls == ["acme.example"]  # the second answer came from the cache
    with db.session() as conn:
        value = db.query(conn, "SELECT value FROM settings WHERE key = 'pattern:acme.example'")
    assert '"source": "hunter"' in value[0]["value"]


def test_derive_known_examples_beat_hunter(ready_db):
    hunter = FakeHunter("{first}")
    known = [
        ("Info@acme.example", "", ""),  # role address: ignored
        ("zed.other@elsewhere.example", "Zed", "Other"),  # other domain: ignored
        ("atan@acme.example", "Aria", "Tan"),
    ]
    assert emails.derive_pattern("acme.example", known, hunter=hunter) == (
        "{f}{last}",
        "known_email",
    )
    assert hunter.calls == []


def test_derive_known_examples_may_be_bare_addresses(ready_db):
    known = ["aria.tan@acme.example", "bob.lee@acme.example", "carl_x@acme.example"]
    assert emails.derive_pattern("acme.example", known, hunter=FakeHunter(None)) == (
        "{first}.{last}",
        "known_email",
    )


def test_derive_hunter_failure_falls_through(ready_db, monkeypatch):
    monkeypatch.setattr(emails, "scrape_site_emails", lambda d: ["ab.cd@acme.example"])
    hunter = FakeHunter(error=ProviderError("quota"))
    assert emails.derive_pattern("acme.example", hunter=hunter) == ("{first}.{last}", "website")
    monkeypatch.setattr(emails, "scrape_site_emails", lambda d: ["a.tan@zed.example"])
    assert emails.derive_pattern("zed.example", hunter=hunter) == ("{f}.{last}", "website")


def test_derive_hunter_before_website(ready_db, monkeypatch):
    monkeypatch.setattr(emails, "scrape_site_emails", lambda d: ["a.tan@acme.example"])
    assert emails.derive_pattern("acme.example", hunter=FakeHunter("{first}")) == (
        "{first}",
        "hunter",
    )


def test_derive_without_scraping(ready_db, monkeypatch):
    monkeypatch.setattr(emails, "scrape_site_emails", lambda d: ["a.tan@acme.example"])
    assert emails.derive_pattern("acme.example", hunter=FakeHunter(None), scrape=False) == (
        "{first}.{last}",
        "default",
    )


def test_derive_uses_hunter_provider_only_when_configured(ready_db, sleeps):
    # No key set: no HTTP request is made (respx would fail an unmocked call).
    with respx.mock:
        assert emails.derive_pattern("acme.example") == ("{first}.{last}", "default")


# --- website scraping -----------------------------------------------------------------------

SITE = "https://acme.example"
ROBOTS = f"{SITE}/robots.txt"


@pytest.fixture
def real_scrape(monkeypatch, isolated):
    monkeypatch.undo()  # drop every patch, then re-apply the ones the scraper needs
    emails._site_cache.clear()
    monkeypatch.setattr(emails, "_site_limiter", RateLimiter(10_000))


@respx.mock
def test_scrape_reads_mailto_and_text_on_same_domain(real_scrape):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(f"{SITE}/contact").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="mailto:Aria.Tan@acme.example?subject=Hi">mail</a>'
            '<a href="mailto:press@other.example">other</a>'
            "<p>Write to info&#64;acme.example or bob%2Elee@acme.example</p>",
        )
    )
    respx.get(f"{SITE}/about").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<p>carl.tan@acme.example, aria.tan@acme.example</p>",
        )
    )
    respx.route().mock(return_value=httpx.Response(404))  # home page and the other paths
    found = emails.scrape_site_emails("www.acme.example")
    assert found == [
        "aria.tan@acme.example",
        "info@acme.example",
        "bob.lee@acme.example",
        "carl.tan@acme.example",
    ]
    assert all(a.endswith("@acme.example") for a in found)
    # cached: a second call makes no more requests
    count = len(respx.calls)
    assert emails.scrape_site_emails("acme.example") == found
    assert len(respx.calls) == count
    # roles are found but never used for the pattern
    assert emails._pattern_from_examples("acme.example", found) == "{first}.{last}"


@respx.mock
def test_scrape_honours_robots_and_failures(real_scrape):
    respx.get(ROBOTS).mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /contact\n")
    )
    contact = respx.get(f"{SITE}/contact").mock(return_value=httpx.Response(200, text="x"))
    respx.get(f"{SITE}/about").mock(return_value=httpx.Response(500))
    respx.route().mock(return_value=httpx.Response(404))
    assert emails.scrape_site_emails("acme.example") == []
    assert contact.call_count == 0


@respx.mock
def test_scrape_network_error_gives_empty(real_scrape):
    respx.get(ROBOTS).mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{SITE}/contact").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{SITE}/about").mock(side_effect=httpx.ConnectError("down"))
    respx.route().mock(side_effect=httpx.ConnectError("down"))
    assert emails.scrape_site_emails("acme.example") == []


@respx.mock
@pytest.mark.parametrize("domain", ["localhost", "192.168.1.5", "intranet.local", "127.1", ""])
def test_scrape_refuses_private_addresses(real_scrape, domain):
    route = respx.route().mock(return_value=httpx.Response(200, text="info@northwind.example"))
    assert emails.scrape_site_emails(domain) == []
    assert route.call_count == 0


@respx.mock
def test_scrape_stops_on_redirect_to_private_address(real_scrape):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    respx.get(f"{SITE}/contact").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    )
    about = respx.get(f"{SITE}/about").mock(return_value=httpx.Response(404))
    local = respx.get("http://127.0.0.1/secret").mock(return_value=httpx.Response(200))
    respx.route(host="acme.example").mock(return_value=httpx.Response(404))
    assert emails.scrape_site_emails("acme.example") == []
    assert local.call_count == 0 and about.call_count == 0


# --- verification order ---------------------------------------------------------------------

REOON = "https://emailverifier.reoon.com/api/v1/verify"
ZB = "https://api.zerobounce.net/v2/validate"
HU = "https://api.hunter.io/v2/email-verifier"


@pytest.fixture
def all_keys(ready_db, sleeps):
    secrets.set("REOON_API_KEY", "reoon-key-1234")
    secrets.set("ZEROBOUNCE_API_KEY", "zb-key-1234")
    secrets.set("HUNTER_API_KEY", "hunter-key-1234")


@respx.mock
def test_verify_order_reoon_first_and_stops(all_keys):
    reoon = respx.get(REOON).mock(return_value=httpx.Response(200, json={"status": "safe"}))
    zb = respx.get(ZB).mock(return_value=httpx.Response(200, json={"status": "valid"}))
    result = emails.verify("aria.tan@northwind.example", "google")
    assert (result.status, result.verifier) == ("verified", "reoon")
    assert reoon.call_count == 1 and zb.call_count == 0


@respx.mock
def test_verify_falls_back_reoon_zerobounce_hunter(all_keys):
    reoon = respx.get(REOON).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
    zb = respx.get(ZB).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
    hu = respx.get(HU).mock(
        return_value=httpx.Response(200, json={"data": {"status": "invalid"}})
    )
    result = emails.verify("aria.tan@northwind.example", "google")
    assert (result.status, result.verifier) == ("invalid", "hunter")
    assert (reoon.call_count, zb.call_count, hu.call_count) == (1, 1, 1)


@respx.mock
def test_verify_skips_failing_and_out_of_credit_providers(all_keys):
    respx.get(REOON).mock(
        return_value=httpx.Response(200, json={"status": "error", "reason": "credits"})
    )
    respx.get(ZB).mock(return_value=httpx.Response(500))
    respx.get(HU).mock(return_value=httpx.Response(200, json={"data": {"status": "valid"}}))
    run = emails._Run()
    result = emails.verify("aria.tan@northwind.example", "google", run=run)
    assert (result.status, result.verifier) == ("verified", "hunter")
    assert run.dead == {"reoon"}  # out of credits: not asked again in this run
    assert "quota_exhausted" in run.issues["reoon"]
    assert run.issues["zerobounce"]["error"]["code"] == "http"


@respx.mock
def test_verify_all_inconclusive_is_guess(all_keys):
    respx.get(REOON).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
    respx.get(ZB).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
    respx.get(HU).mock(return_value=httpx.Response(200, json={"data": {"status": "unknown"}}))
    assert emails.verify("a@northwind.example", "google").status == "guess"


@respx.mock
def test_verify_without_any_key_is_guess(ready_db):
    result = emails.verify("a@northwind.example", "google")
    assert (result.status, result.verifier) == ("guess", "")


@respx.mock
@pytest.mark.parametrize(
    ("route_url", "body"),
    [
        (REOON, {"status": "catch_all", "is_catch_all": True}),
        (ZB, {"status": "catch-all", "sub_status": "accept_all"}),
        (HU, {"data": {"status": "accept_all"}}),
    ],
)
def test_catch_all_is_risky_and_remembered(all_keys, route_url, body):
    if route_url == ZB:
        respx.get(REOON).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
    if route_url == HU:
        respx.get(REOON).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
        respx.get(ZB).mock(return_value=httpx.Response(200, json={"status": "unknown"}))
    respx.get(route_url).mock(return_value=httpx.Response(200, json=body))
    first = emails.verify("a.b@northwind.example", "google")
    assert first.status == "risky" and first.catch_all
    before = len(respx.calls)
    again = emails.verify("c.d@northwind.example", "google")
    assert (again.status, again.verifier) == ("risky", "catch_all_cache")
    assert len(respx.calls) == before  # no credits spent on a known catch-all domain


@pytest.mark.parametrize("mx", ["google", "microsoft", "gateway", None])
def test_smtp_is_never_used_for_big_providers(ready_db, isolated, mx, monkeypatch):
    result = emails.verify("aria.tan@northwind.example", mx, verifiers=[])
    assert result.status == "guess"
    assert isolated.smtp == []  # _smtp_rcpt was never called, and smtplib.SMTP would raise


def test_smtp_probe_for_other_mx(ready_db, isolated, monkeypatch):
    answers = {}

    def rcpt(host, email):
        isolated.smtp.append((host, email))
        local = email.split("@")[0]
        return answers.get(local, answers.get("*"))

    monkeypatch.setattr(emails, "_smtp_rcpt", rcpt)
    answers["aria.tan"] = True
    answers["*"] = False  # the random address is rejected: not catch-all
    result = emails.verify("aria.tan@small.example", "other", verifiers=[])
    assert (result.status, result.verifier) == ("verified", "smtp")
    assert isolated.smtp[0] == ("mail.small.example", "aria.tan@small.example")
    assert isolated.smtp[1][1].startswith("zz-no-such-user-")
    assert emails._catch_all_cached("small.example") is False
    # rejected address
    answers.clear()
    answers["aria.tan"] = False
    assert emails.verify("aria.tan@small.example", "other", verifiers=[]).status == "invalid"


def test_smtp_catch_all_probe_makes_it_risky(ready_db, isolated, monkeypatch):
    monkeypatch.setattr(emails, "_smtp_rcpt", lambda host, email: True)
    result = emails.verify("aria.tan@small.example", "other", verifiers=[])
    assert (result.status, result.catch_all) == ("risky", True)
    assert emails._catch_all_cached("small.example") is True


def test_smtp_inconclusive_stays_guess(ready_db, isolated, monkeypatch):
    # tempfail on the real address
    monkeypatch.setattr(emails, "_smtp_rcpt", lambda host, email: None)
    assert emails.verify("a.b@small.example", "other", verifiers=[]).status == "guess"
    # accepted, but the catch-all probe was inconclusive: not verified
    seen = []

    def rcpt(host, email):
        seen.append(email)
        return True if len(seen) == 1 else None

    monkeypatch.setattr(emails, "_smtp_rcpt", rcpt)
    assert emails.verify("c.d@small.example", "other", verifiers=[]).status == "guess"


def test_api_answer_wins_over_smtp(ready_db, isolated):
    fake = FakeVerifier("reoon", default="invalid")
    result = emails.verify("a.b@small.example", "other", verifiers=[fake])
    assert (result.status, result.verifier) == ("invalid", "reoon")
    assert isolated.smtp == []
    unknown = FakeVerifier("reoon", default="unknown")
    emails.verify("a.b@small.example", "other", verifiers=[unknown])
    assert [h for h, _ in isolated.smtp] == ["mail.small.example"]  # inconclusive: SMTP tried


class ScriptedSMTP:
    """A stand-in for `smtplib.SMTP` with scripted replies; records the commands it receives."""

    mail_reply = (250, b"2.1.0 ok")
    replies: dict = {}  # recipient -> (code, text); anything else is accepted
    log: list = []
    connect_error: Exception | None = None

    def __init__(self, *a, **k):
        pass

    def connect(self, host, port):
        if ScriptedSMTP.connect_error:
            raise ScriptedSMTP.connect_error
        ScriptedSMTP.log.append(("connect", host, port))

    def ehlo_or_helo_if_needed(self):
        pass

    def mail(self, sender):
        ScriptedSMTP.log.append(("mail", sender))
        return ScriptedSMTP.mail_reply

    def rcpt(self, recipient):
        ScriptedSMTP.log.append(("rcpt", recipient))
        return ScriptedSMTP.replies.get(recipient, (250, b"2.1.5 ok"))

    def close(self):
        pass


@pytest.fixture
def scripted_smtp(monkeypatch):
    monkeypatch.undo()  # use the real `_smtp_rcpt`, with the scripted class instead of sockets
    ScriptedSMTP.mail_reply = (250, b"2.1.0 ok")
    ScriptedSMTP.replies = {}
    ScriptedSMTP.log = []
    ScriptedSMTP.connect_error = None
    monkeypatch.setattr(emails.smtplib, "SMTP", ScriptedSMTP)
    return ScriptedSMTP


ADDR = "a@small.example"
POSTMASTER = "postmaster@small.example"


def test_smtp_rcpt_accepts_and_uses_the_null_sender(scripted_smtp):
    assert emails._smtp_rcpt("mx.small.example", ADDR) is True
    assert ("mail", "") in scripted_smtp.log  # smtplib turns "" into MAIL FROM:<>
    assert ("connect", "mx.small.example", 25) in scripted_smtp.log
    assert smtplib.quoteaddr("") == "<>"


def test_smtp_rcpt_plain_rejection_with_postmaster_accepted_is_invalid(scripted_smtp):
    for code in (550, 551, 553):
        scripted_smtp.log.clear()
        scripted_smtp.replies = {ADDR: (code, b"5.1.1 no such user")}
        assert emails._smtp_rcpt("mx.small.example", ADDR) is False
        assert scripted_smtp.log[-1] == ("rcpt", POSTMASTER)  # asked in the same session
    scripted_smtp.replies = {ADDR: (550, b"user unknown")}  # no enhanced code at all
    assert emails._smtp_rcpt("mx.small.example", ADDR) is False


def test_smtp_rcpt_rejected_postmaster_means_unknown(scripted_smtp):
    scripted_smtp.replies = {
        ADDR: (550, b"5.1.1 no such user"),
        POSTMASTER: (550, b"5.1.1 we dislike you"),
    }
    assert emails._smtp_rcpt("mx.small.example", ADDR) is None


@pytest.mark.parametrize(
    "text", [b"5.7.1 SPF check failed", b"5.7.25 sender not allowed", b" 5.7.0 blocked by RBL"]
)
def test_smtp_rcpt_policy_rejection_means_unknown(scripted_smtp, text):
    scripted_smtp.replies = {ADDR: (550, text)}
    assert emails._smtp_rcpt("mx.small.example", ADDR) is None
    assert ("rcpt", POSTMASTER) not in scripted_smtp.log  # no need to ask


def test_smtp_rcpt_other_5_codes_are_not_policy(scripted_smtp):
    scripted_smtp.replies = {ADDR: (550, b"5.1.1 mailbox 5.7.1 unavailable")}  # not at the start
    assert emails._smtp_rcpt("mx.small.example", ADDR) is False


def test_smtp_rcpt_refused_sender_means_unknown(scripted_smtp):
    for reply in ((550, b"5.7.1 sender rejected"), (553, b"bad sender"), (451, b"later")):
        scripted_smtp.log.clear()
        scripted_smtp.mail_reply = reply
        assert emails._smtp_rcpt("mx.small.example", ADDR) is None
        assert not [c for c in scripted_smtp.log if c[0] == "rcpt"]  # never got as far as RCPT


@pytest.mark.parametrize("code", [451, 452, 421, 554, 250])
def test_smtp_rcpt_temporary_and_other_codes(scripted_smtp, code):
    scripted_smtp.replies = {ADDR: (code, b"try later")}
    assert emails._smtp_rcpt("mx.small.example", ADDR) is (True if code == 250 else None)


def test_smtp_rcpt_connection_errors_give_unknown(scripted_smtp):
    scripted_smtp.connect_error = OSError("blocked")
    assert emails._smtp_rcpt("mx.small.example", ADDR) is None
    scripted_smtp.connect_error = smtplib.SMTPServerDisconnected("bye")
    assert emails._smtp_rcpt("mx.small.example", ADDR) is None


def test_smtp_verify_does_not_call_a_policy_refusal_invalid(scripted_smtp, ready_db, monkeypatch):
    """End to end through `verify`: an SPF-style 5.7.1 refusal leaves the address a guess."""
    emails._mx_cache.clear()
    monkeypatch.setattr(
        emails.dns.resolver, "resolve", lambda *a, **k: [FakeAnswer("mail.small.example")]
    )
    scripted_smtp.replies = {"aria.tan@small.example": (550, b"5.7.1 SPF fail")}
    result = emails.verify("aria.tan@small.example", "other", verifiers=[])
    assert result.status == "guess"
    scripted_smtp.replies = {"aria.tan@small.example": (550, b"5.1.1 no such mailbox")}
    assert emails.verify("aria.tan@small.example", "other", verifiers=[]).status == "invalid"


# --- storing --------------------------------------------------------------------------------


def test_store_email_refuses_roles_and_suppressed(ready_db, cid):
    person = add_person(cid, add_company(cid))
    assert emails.store_email(person, "info@northwind.example", "guess") == "role_address"
    with db.session() as conn:
        db.insert(conn, "suppression", value="blocked.person@northwind.example", kind="email")
        db.insert(conn, "suppression", value="nope.example", kind="domain")
    assert emails.store_email(person, "Blocked.Person@northwind.example", "guess") == "suppressed"
    assert emails.store_email(person, "a.b@nope.example", "guess") == "suppressed"
    assert emails.store_email(person, "a.b@mail.nope.example", "guess") == "suppressed"
    assert emails.store_email(person, "a.b@notnope.example", "guess") == "stored"
    assert [r["email"] for r in email_rows()] == ["a.b@notnope.example"]
    with pytest.raises(ValueError):
        emails.store_email(person, "x@y.example", "maybe")


def test_store_email_replaces_guess_but_keeps_verified(ready_db, cid):
    person = add_person(cid, add_company(cid))
    emails.store_email(person, "a.b@northwind.example", "guess", method="m", pattern="{f}")
    emails.store_email(person, "ab@northwind.example", "verified", verifier="reoon")
    rows = [(r["email"], r["status"]) for r in email_rows()]
    assert rows == [("ab@northwind.example", "verified")]
    emails.store_email(person, "abc@northwind.example", "guess")
    assert len(email_rows()) == 2  # a verified row is never deleted by a later guess
    row = email_rows()[0]
    assert row["verifier"] == "reoon" and row["checked_at"]


# --- find_emails ----------------------------------------------------------------------------


def run_find(cid, **kw):
    kw.setdefault("hunter", FakeHunter(None))
    kw.setdefault("verifiers", [FakeVerifier("reoon")])
    return emails.find_emails(cid, **kw)


def test_find_emails_basic_flow(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    bob = add_person(cid, company, "Bob", "Lee")
    verifier = FakeVerifier("reoon", {"bob.lee@northwind.example": "invalid"})
    out = run_find(cid, verifiers=[verifier], hunter=FakeHunter("{first}.{last}"))
    assert out["ok"] and out["done"] and out["next_cursor"] is None
    assert out["this_call"] == {"verified": 1, "risky": 0, "guess": 0, "invalid": 1, "unknown": 0}
    assert out["campaign_emails"]["verified"] == 1
    assert out["patterns_used"] == {
        "northwind.example": {"pattern": "{first}.{last}", "source": "hunter"}
    }
    rows = {r["person_id"]: r for r in email_rows()}
    assert rows[aria]["email"] == "aria.tan@northwind.example"
    assert (rows[aria]["status"], rows[aria]["verifier"]) == ("verified", "reoon")
    assert rows[aria]["method"] == "pattern:hunter" and rows[aria]["pattern"] == "{first}.{last}"
    assert rows[bob]["status"] == "invalid"
    assert out["credits_used"] == {"hunter": 1.0, "verify": 2.0}
    assert budget.used(cid, "hunter") == 1 and budget.used(cid, "verify") == 2


def test_find_emails_rerun_skips_people_with_email(cid):
    company = add_company(cid)
    add_person(cid, company)
    run_find(cid)
    again = run_find(cid)
    assert again["people_done"] == 0 and again["skipped"] == {"already_has_email": 1}
    assert len(email_rows()) == 1


def test_find_emails_needs_email_channel(ready_db, cid):
    camp.update_campaign(cid, channels=["linkedin"])
    with pytest.raises(EmailError) as info:
        run_find(cid)
    assert info.value.code == "no_email_channel"
    assert info.value.as_response()["error"]["code"] == "no_email_channel"


def test_find_emails_unknown_campaign(ready_db):
    with pytest.raises(camp.CampaignError):
        run_find(999)


def test_find_emails_only_confirmed_people_with_names_and_domains(cid, isolated):
    company = add_company(cid)
    no_domain = add_company(cid, "No Domain Inc", "")
    ok = add_person(cid, company)
    add_person(cid, company, status="review")
    add_person(cid, no_domain)
    add_person(cid, company, "", "Tan")
    add_person(cid, company, "王", "伟")
    out = run_find(cid)
    assert out["people_done"] == 1
    assert out["skipped"] == {"no_company_domain": 1, "no_usable_name": 2}
    assert [r["person_id"] for r in email_rows()] == [ok]
    # explicit ids: unconfirmed and unknown ones are reported
    out = run_find(cid, person_ids=[ok, 12345])
    assert out["skipped_person_ids"] == [12345]


def test_find_emails_no_mx_is_invalid_and_dns_trouble_is_unknown(cid, monkeypatch):
    dead = add_company(cid, "Dead Co", "dead.example")
    p1 = add_person(cid, dead)
    verifier = FakeVerifier("reoon")
    out = run_find(cid, verifiers=[verifier])
    assert out["this_call"]["invalid"] == 1 and verifier.calls == []
    assert email_rows(p1)[0]["verifier"] == "mx"
    # transient DNS failure
    flaky = add_company(cid, "Flaky Co", "flaky.example")
    p2 = add_person(cid, flaky)

    def boom(domain, rdtype="A", **kw):
        raise dns.resolver.NoNameservers()

    monkeypatch.setattr(emails.dns.resolver, "resolve", boom)
    out = run_find(cid, verifiers=[verifier])
    assert out["this_call"]["unknown"] == 1
    assert email_rows(p2)[0]["status"] == "unknown"


def test_find_emails_without_verifier_gives_guess_and_needs_key(cid, isolated):
    company = add_company(cid, "Small Co", "small.example")
    add_person(cid, company)
    out = run_find(cid, verifiers=[])
    assert out["this_call"]["guess"] == 1
    assert out["verifier_needs_key"]["needs_key"]["provider"] == "reoon"
    assert any("guess" in n for n in out["notes"])
    assert [h for h, _ in isolated.smtp] == ["mail.small.example"]  # SMTP is tried for `other`


def test_find_emails_never_probes_smtp_for_google(cid, isolated):
    add_person(cid, add_company(cid))  # northwind.example = google
    run_find(cid, verifiers=[])
    assert isolated.smtp == []


def test_find_emails_catch_all_domain_is_risky_for_everyone(cid):
    company = add_company(cid)
    add_person(cid, company, "Aria", "Tan")
    add_person(cid, company, "Bob", "Lee")
    verifier = FakeVerifier("reoon", default=Verdict("risky", "catch_all", "reoon", True))
    out = run_find(cid, verifiers=[verifier])
    assert out["this_call"]["risky"] == 2
    assert len(verifier.calls) == 1  # the second person used the remembered catch-all


def test_find_emails_quota_mid_run_keeps_going_as_guess(cid):
    company = add_company(cid)
    for name in ("Aria", "Bob", "Cara"):
        add_person(cid, company, name, "Tan")
    verifier = FakeVerifier(
        "reoon",
        lambda email: "verified" if email.startswith("aria") else ProviderError("quota", "out"),
    )
    out = run_find(cid, verifiers=[verifier])
    assert out["this_call"]["verified"] == 1 and out["this_call"]["guess"] == 2
    assert len(verifier.calls) == 2  # not asked again after the quota error
    assert out["provider_issues"][0]["quota_exhausted"]["provider"] == "reoon"
    assert out["ok"] is True and out["done"] is True


def test_find_emails_second_verifier_takes_over(cid):
    add_person(cid, add_company(cid))
    first = FakeVerifier("reoon", default=ProviderError("rate_limited", "slow"))
    second = FakeVerifier("zerobounce", default="verified")
    out = run_find(cid, verifiers=[first, second])
    assert out["this_call"]["verified"] == 1
    assert email_rows()[0]["verifier"] == "zerobounce"
    assert out["provider_issues"][0]["error"]["code"] == "rate_limited"


def test_find_emails_multi_part_surname_tries_joined_form_for_real_patterns(cid):
    company = add_company(cid)
    person = add_person(cid, company, "Aria", "van der Berg")
    verifier = FakeVerifier("reoon", {"aria.berg@northwind.example": "invalid"})
    out = run_find(cid, verifiers=[verifier], hunter=FakeHunter("{first}.{last}"))
    assert verifier.calls == ["aria.berg@northwind.example", "aria.vanderberg@northwind.example"]
    assert email_rows(person)[0]["email"] == "aria.vanderberg@northwind.example"
    assert out["this_call"]["verified"] == 1


def test_find_emails_multi_part_surname_default_pattern_is_not_retried(cid):
    person = add_person(cid, add_company(cid), "Aria", "van der Berg")
    verifier = FakeVerifier("reoon", default="invalid")
    run_find(cid, verifiers=[verifier])
    assert verifier.calls == ["aria.berg@northwind.example"]
    assert email_rows(person)[0]["status"] == "invalid"


def test_find_emails_skips_role_and_suppressed_candidates(cid):
    company = add_company(cid)
    info = add_person(cid, company, "Info", "Desk")
    blocked = add_person(cid, company, "Bob", "Lee")
    with db.session() as conn:
        db.insert(conn, "suppression", value="bob@northwind.example", kind="email")
    verifier = FakeVerifier("reoon")
    out = run_find(cid, verifiers=[verifier], hunter=FakeHunter("{first}"))
    assert out["skipped"] == {"role_address": 1, "suppressed": 1}
    assert verifier.calls == [] and email_rows() == []
    assert info and blocked


def test_find_emails_uses_known_pattern_cache_and_hunter_once_per_domain(cid):
    company = add_company(cid)
    for name in ("Aria", "Bob", "Cara"):
        add_person(cid, company, name, "Tan")
    hunter = FakeHunter("{f}{last}")
    run_find(cid, hunter=hunter)
    assert hunter.calls == ["northwind.example"]
    assert sorted(r["email"] for r in email_rows()) == [
        "atan@northwind.example", "btan@northwind.example", "ctan@northwind.example",
    ]  # fmt: skip


def test_find_emails_explicit_ids_redo_guess_rows(cid):
    person = add_person(cid, add_company(cid))
    run_find(cid, verifiers=[])
    assert email_rows()[0]["status"] == "guess"
    assert run_find(cid)["skipped"] == {"already_has_email": 1}  # a plain re-run leaves it
    out = run_find(cid, person_ids=[person])
    assert out["this_call"]["verified"] == 1
    assert [(r["email"], r["status"]) for r in email_rows()] == [
        ("aria.tan@northwind.example", "verified")
    ]


# --- chunking, confirmation and budgets -----------------------------------------------------


def test_find_emails_chunks_with_cursor(cid):
    company = add_company(cid)
    ids = [add_person(cid, company, f"Name{n}", "Tan") for n in "abcde"]
    seen = []
    cursor = None
    pages = 0
    while True:
        out = run_find(cid, cursor=cursor, max_people_per_call=2)
        pages += 1
        seen.append(out["people_done"])
        if out["done"]:
            break
        assert out["next_cursor"] is not None
        cursor = out["next_cursor"]
    assert seen == [2, 2, 1] and pages == 3
    assert {r["person_id"] for r in email_rows()} == set(ids)
    assert out["campaign_emails"]["verified"] == 5


def test_find_emails_chunk_size_is_capped(cid):
    company = add_company(cid)
    for n in "abcdefghijklmnopqrstuvwxyz0":  # 27 people
        add_person(cid, company, f"Name{n}", "Tan")
    out = run_find(cid, max_people_per_call=500, confirm=True)
    assert out["people_done"] == 25 and out["done"] is False


def test_find_emails_invalid_cursor_and_ids(cid):
    with pytest.raises(EmailError) as info:
        run_find(cid, cursor="abc")
    assert info.value.code == "invalid_cursor"
    with pytest.raises(EmailError):
        run_find(cid, cursor=-1)
    with pytest.raises(EmailError):
        run_find(cid, person_ids="1,2")
    with pytest.raises(EmailError):
        run_find(cid, person_ids=[True])


def test_find_emails_time_limit_returns_cursor(cid, monkeypatch):
    company = add_company(cid)
    for n in "abc":
        add_person(cid, company, f"Name{n}", "Tan")
    ticks = itertools.count(0, 60)
    monkeypatch.setattr(emails.time, "monotonic", lambda: next(ticks))
    out = run_find(cid)
    assert out["people_done"] == 1 and out["done"] is False


def test_find_emails_asks_for_confirmation_when_big(cid):
    company = add_company(cid)
    for n in range(12):
        add_person(cid, company, f"Name{n}x".replace("0", "o").replace("1", "i"), "Tan")
    # threshold 20: 12 people + 1 pattern lookup = 13 credits -> fine
    small = run_find(cid, hunter=FakeHunter("{first}.{last}"))
    assert small["people_done"] == 12
    for n in range(25):
        add_person(cid, company, f"Extra{'abcdefghijklmnopqrstuvwxy'[n]}", "Lee")
    big = run_find(cid, hunter=FakeHunter("{first}.{last}"))
    assert big["needs_confirmation"] is True and big["people"] == 25
    assert big["verify_credits"] == 25 and "confirm=true" in big["message"]
    assert len(email_rows()) == 12  # nothing was searched
    go = run_find(cid, hunter=FakeHunter("{first}.{last}"), confirm=True)
    assert go["people_done"] == 25


def test_find_emails_verify_budget_stops_with_budget_reply_and_resumes(cid):
    camp.update_campaign(cid, budget={"verify": 2})
    company = add_company(cid)
    for name in ("Aria", "Bob", "Cara"):
        add_person(cid, company, name, "Tan")
    out = run_find(cid)
    assert out["ok"] is False and out["budget_reached"]["kind"] == "verify"
    assert out["people_done"] == 2 and out["done"] is False
    assert len(email_rows()) == 2
    # raising the budget with confirm continues exactly where it stopped
    out = run_find(cid, cursor=out["next_cursor"], confirm=True)
    assert out["ok"] and out["done"] and out["people_done"] == 1
    assert len(email_rows()) == 3


def test_find_emails_hunter_budget_skips_hunter_but_still_finds_emails(cid):
    camp.update_campaign(cid, budget={"hunter": 0})
    add_person(cid, add_company(cid))
    hunter = FakeHunter("{first}")
    out = run_find(cid, hunter=hunter)
    assert out["ok"] and hunter.calls == []
    assert out["hunter_budget"]["budget_reached"]["kind"] == "hunter"
    assert email_rows()[0]["email"] == "aria.tan@northwind.example"  # default pattern
    assert email_rows()[0]["method"] == "pattern:default"


def test_find_emails_hunter_quota_is_reported_not_fatal(cid):
    add_person(cid, add_company(cid))
    out = run_find(cid, hunter=FakeHunter(error=ProviderError("quota", "no credits")))
    assert out["ok"] and out["this_call"]["verified"] == 1
    assert out["provider_issues"][0]["quota_exhausted"]["provider"] == "hunter"


def test_hunter_verifier_is_counted_in_both_budgets(cid):
    add_person(cid, add_company(cid))
    out = run_find(cid, verifiers=[FakeVerifier("hunter")], hunter=FakeHunter(None))
    assert out["credits_used"] == {"hunter": 1.5, "verify": 1.0}
    assert budget.used(cid, "hunter") == 1.5


# --- estimate -------------------------------------------------------------------------------


def test_estimate_email_search(cid, monkeypatch, sleeps):
    company = add_company(cid)
    other = add_company(cid, "Contoso", "contoso.example")
    for name in ("Aria", "Bob"):
        add_person(cid, company, name, "Tan")
    add_person(cid, other, "Cara", "Lee")
    add_person(cid, other, "Dan", "Lee", status="review")
    est = emails.estimate_email_search(cid)
    assert (est["people"], est["domains"]) == (3, 2)
    assert est["hunter_credits"] == 0 and est["verify_credits"] == 0  # no keys yet
    assert est["budget_left"] == {"hunter": 25, "verify": 200}
    assert est["verifier_configured"] is False
    secrets.set("HUNTER_API_KEY", "hunter-key-1234")
    secrets.set("REOON_API_KEY", "reoon-key-1234")
    emails._cache_pattern("northwind.example", "{first}", "hunter", 0.8)
    est = emails.estimate_email_search(cid)
    assert (est["hunter_credits"], est["verify_credits"]) == (1, 3)  # one domain is cached
    assert est["needs_confirmation"] is False
    budget.charge(cid, "verify", 50)
    assert emails.estimate_email_search(cid)["budget_left"]["verify"] == 150
    only = emails.estimate_email_search(cid, [list(range(1, 100))[0]])
    assert only["people"] == 1


def test_estimate_counts_only_people_still_to_do(cid):
    company = add_company(cid)
    done = add_person(cid, company)
    add_person(cid, company, "Bob", "Lee")
    emails.store_email(done, "aria.tan@northwind.example", "verified")
    assert emails.estimate_email_search(cid)["people"] == 1


# --- registry integration -------------------------------------------------------------------


def test_registry_replies_for_email_providers():
    assert registry.needs_key_response("hunter", "x")["needs_key"]["signup_url"] == (
        "https://hunter.io/users/sign_up"
    )
    assert registry.quota_response("reoon")["quota_exhausted"]["name"].startswith("Reoon")
    assert base.ProviderError("quota").code == "quota"


# --- one Hunter lookup and one site scrape per domain ---------------------------------------


def test_hunter_without_a_pattern_is_asked_once_per_domain(cid):
    company = add_company(cid)
    for first in ("Aria", "Bob", "Carl"):
        add_person(cid, company, first, "Tan")
    hunter = FakeHunter(None)
    out = run_find(cid, hunter=hunter)
    assert hunter.calls == ["northwind.example"]
    assert budget.used(cid, "hunter") == 1
    assert out["credits_used"]["hunter"] == 1.0
    assert out["this_call"]["verified"] == 3
    assert emails._setting("hunter_none:northwind.example")


def test_hunter_without_a_pattern_is_not_asked_again_in_later_chunks(cid):
    company = add_company(cid)
    for first in ("Aria", "Bob", "Carl"):
        add_person(cid, company, first, "Tan")
    hunter = FakeHunter(None)
    cursor = None
    while True:
        out = run_find(cid, hunter=hunter, cursor=cursor, max_people_per_call=1)
        if out["done"]:
            break
        cursor = out["next_cursor"]
    assert hunter.calls == ["northwind.example"]
    assert budget.used(cid, "hunter") == 1
    # the estimate no longer counts the domain either
    with db.session() as conn:
        db.insert(conn, "companies", campaign_id=cid, name="Other", domain="other.example",
                  status="approved")  # fmt: skip
    emails._set_setting("pattern:other.example", '{"pattern": "{first}", "source": "hunter"}')
    secrets.set("HUNTER_API_KEY", "hunter-key-1234")
    assert emails.estimate_email_search(cid)["hunter_credits"] == 0


def test_hunter_none_marker_expires(cid):
    company = add_company(cid)
    add_person(cid, company)
    stale = (datetime.now(UTC) - timedelta(days=emails.HUNTER_NONE_DAYS + 1)).isoformat()
    emails._set_setting("hunter_none:northwind.example", stale)
    hunter = FakeHunter(None)
    run_find(cid, hunter=hunter)
    assert hunter.calls == ["northwind.example"]  # a stale marker is ignored...
    run_find(cid, hunter=hunter, person_ids=None)
    assert hunter.calls == ["northwind.example"]  # ...and replaced by a fresh one
    emails._set_setting("hunter_none:northwind.example", "garbage")
    assert emails._hunter_none_fresh("northwind.example") is False


def test_hunter_pattern_found_is_cached_not_marked_none(cid):
    company = add_company(cid)
    add_person(cid, company)
    run_find(cid, hunter=FakeHunter("{first}"))
    assert emails._setting("hunter_none:northwind.example") is None
    assert emails._cached_pattern("northwind.example") == ("{first}", "hunter")


def test_hunter_error_does_not_set_the_none_marker(cid):
    company = add_company(cid)
    add_person(cid, company)
    run_find(cid, hunter=FakeHunter(error=ProviderError("http", "boom")))
    assert emails._setting("hunter_none:northwind.example") is None


@respx.mock
def test_website_is_scraped_once_for_many_people_of_a_domain(cid, monkeypatch):
    monkeypatch.setattr(emails, "scrape_site_emails", _REAL_SCRAPE)
    site = "https://northwind.example"
    respx.get(f"{site}/robots.txt").mock(return_value=httpx.Response(404))
    contact = respx.get(f"{site}/contact").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>bob.lee@northwind.example</p>"
        )
    )
    about = respx.get(f"{site}/about").mock(return_value=httpx.Response(404))
    respx.route(host="northwind.example").mock(return_value=httpx.Response(404))
    company = add_company(cid)
    for first in ("Aria", "Carl", "Dana"):
        add_person(cid, company, first, "Tan")
    out = run_find(cid, hunter=FakeHunter(None))
    assert contact.call_count == 1 and about.call_count == 1
    assert out["patterns_used"]["northwind.example"]["source"] == "website"


def test_slow_provider_is_skipped_after_repeated_failures(cid):
    company = add_company(cid)
    for first in ("Aria", "Bob", "Carl", "Dana"):
        add_person(cid, company, first, "Tan")
    slow = FakeVerifier("reoon", default=ProviderError("http", "no reply in time"))
    backup = FakeVerifier("zerobounce")
    out = run_find(cid, verifiers=[slow, backup])
    assert len(slow.calls) == emails.HTTP_FAILURES_BEFORE_SKIP  # then it is left alone
    assert len(backup.calls) == 4 and out["this_call"]["verified"] == 4
    assert out["provider_issues"]


def test_slow_checks_stop_new_people_at_the_time_limit(cid, monkeypatch):
    company = add_company(cid)
    ids = [add_person(cid, company, first, "Tan") for first in ("Aria", "Bob", "Carl")]
    clock = {"now": 0.0}
    monkeypatch.setattr(emails.time, "monotonic", lambda: clock["now"])

    class Slow(FakeVerifier):
        def verify(self, email):
            clock["now"] += 20.0  # each check takes 20 s
            return super().verify(email)

    out = run_find(cid, verifiers=[Slow("reoon")], max_people_per_call=3)
    # person 2 starts at t=20 (< 25) and ends at 40; person 3 is not started
    assert out["people_done"] == 2 and out["done"] is False
    assert {r["person_id"] for r in email_rows()} == set(ids[:2])
    assert out["next_cursor"] == str(ids[1])


def test_person_is_deferred_when_the_deadline_passes_between_checks(cid, monkeypatch):
    company = add_company(cid)
    ids = [add_person(cid, company, first, "Tan") for first in ("Aria", "Bob")]
    clock = {"now": 0.0}
    monkeypatch.setattr(emails.time, "monotonic", lambda: clock["now"])

    class First(FakeVerifier):
        def verify(self, email):
            if email.startswith("bob."):
                clock["now"] += emails.VERIFY_DEADLINE_S + 1  # a slow, inconclusive check
                return Verdict("unknown", "unknown", "reoon")
            return super().verify(email)

    second = FakeVerifier("zerobounce")
    out = run_find(cid, verifiers=[First("reoon"), second])
    assert out["people_done"] == 1 and out["done"] is False
    assert second.calls == []  # not started: too late in this call
    assert [r["person_id"] for r in email_rows()] == [ids[0]]  # no guess stored for Bob
    assert out["next_cursor"] == str(ids[1] - 1)
    # the next call starts a fresh clock and finishes Bob
    clock["now"] = 0.0
    again = run_find(cid, verifiers=[FakeVerifier("reoon")], cursor=out["next_cursor"])
    assert again["done"] and again["this_call"]["verified"] == 1
    assert {r["person_id"] for r in email_rows()} == set(ids)


def test_first_person_is_verified_even_if_the_pattern_step_passes_the_deadline(cid, monkeypatch):
    company = add_company(cid)
    person = add_person(cid, company)
    clock = {"now": 0.0}
    monkeypatch.setattr(emails.time, "monotonic", lambda: clock["now"])

    class SlowHunter(FakeHunter):
        def domain_pattern(self, domain):
            clock["now"] += emails.VERIFY_DEADLINE_S + 1  # e.g. Hunter plus a slow site scrape
            return super().domain_pattern(domain)

    reoon = FakeVerifier("reoon")
    out = run_find(cid, hunter=SlowHunter("{first}.{last}"), verifiers=[reoon])
    assert reoon.calls == ["aria.tan@northwind.example"]
    assert out["this_call"]["verified"] == 1 and out["done"] is True
    row = email_rows()[0]
    assert row["person_id"] == person and row["status"] == "verified"
    assert row["verifier"] == "reoon"


def test_later_person_without_time_is_deferred_not_stored_as_guess(cid, monkeypatch):
    first_co = add_company(cid)
    second_co = add_company(cid, "Contoso Retail", "contoso.example")
    ids = [add_person(cid, first_co, "Aria", "Tan"), add_person(cid, second_co, "Bob", "Lee")]
    clock = {"now": 0.0}
    monkeypatch.setattr(emails.time, "monotonic", lambda: clock["now"])

    class SlowHunter(FakeHunter):
        def domain_pattern(self, domain):
            if domain == "contoso.example":  # Bob's pattern step eats the whole time budget
                clock["now"] += emails.VERIFY_DEADLINE_S + 1
            return super().domain_pattern(domain)

    reoon = FakeVerifier("reoon")
    out = run_find(cid, hunter=SlowHunter("{first}.{last}"), verifiers=[reoon])
    assert reoon.calls == ["aria.tan@northwind.example"]  # Bob was never checked
    assert out["people_done"] == 1 and out["done"] is False
    assert [r["person_id"] for r in email_rows()] == [ids[0]]  # no unchecked guess for Bob
    assert out["next_cursor"] == str(ids[1] - 1)
    clock["now"] = 0.0  # the next call starts a fresh clock; Bob is now its first person
    again = run_find(cid, hunter=FakeHunter("{first}.{last}"), verifiers=[reoon],
                     cursor=out["next_cursor"])  # fmt: skip
    assert again["done"] and again["this_call"]["verified"] == 1
    assert reoon.calls[-1] == "bob.lee@contoso.example"
    assert {r["person_id"] for r in email_rows()} == set(ids)
    assert all(r["status"] == "verified" for r in email_rows())


def test_verify_without_a_run_deadline_never_times_out(cid):
    result = emails.verify("a.b@northwind.example", "google", verifiers=[FakeVerifier("reoon")])
    assert result.status == "verified" and not result.timed_out


# --- AI search mode: examples, site sources, no keys (T21) --------------------------------------


def test_learn_pattern_from_examples_saves_a_pattern_found_by_the_ai(ready_db):
    out = emails.learn_pattern_from_examples(
        "www.Northwind.example",
        [
            "Bob.Lee@northwind.example",
            ("carl.ong@northwind.example", "Carl", "Ong"),
            "info@northwind.example",
            "someone@other.example",
            "not an address",
        ],
        ["https://northwind.example/team", "https://northwind.example/team"],
    )
    assert out["pattern"] == "{first}.{last}" and out["saved"] and out["examples_used"] == 2
    assert out["role_addresses"] == 1 and out["source_urls"] == ["https://northwind.example/team"]
    assert emails.known_pattern("northwind.example") == ("{first}.{last}", "ai_search")
    assert "ai_search" in emails.REAL_SOURCES


def test_learned_pattern_is_not_replaced_by_a_weaker_source_but_ai_can_update_ai(ready_db):
    emails._cache_pattern("northwind.example", "{f}{last}", "hunter", 0.9)
    out = emails.learn_pattern_from_examples("northwind.example", ["bob.lee@northwind.example"])
    assert out["pattern"] == "{f}{last}" and out["saved"] is False  # Hunter's stays
    emails._cache_pattern("lakeside.example", "{first}", "ai_search", 0.8)
    out = emails.learn_pattern_from_examples("lakeside.example", ["bob.lee@lakeside.example"])
    assert out["saved"] is True and emails.known_pattern("lakeside.example")[0] == "{first}.{last}"
    empty = emails.learn_pattern_from_examples("nowhere.example", ["info@nowhere.example"])
    assert empty["pattern"] is None and empty["saved"] is False and empty["role_addresses"] == 1


def test_find_emails_without_keys_uses_the_ai_examples_and_labels_the_result_guess(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    emails.learn_pattern_from_examples(
        "northwind.example", [("bob.lee@northwind.example", "Bob", "Lee")]
    )
    out = emails.find_emails(cid, hunter=None, verifiers=[])
    assert out["this_call"]["guess"] == 1 and out["verifier_needs_key"]
    assert out["patterns_used"]["northwind.example"]["source"] == "ai_search"
    row = email_rows(aria)[0]
    assert row["email"] == "aria.tan@northwind.example" and row["status"] == "guess"
    assert row["method"] == "pattern:ai_search"


@respx.mock
def test_scrape_site_sources_returns_page_urls_and_counts_only_good_pages(real_scrape):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    html = {"content-type": "text/html"}
    respx.get(f"{SITE}/").mock(
        return_value=httpx.Response(200, headers=html, text="<p>home: a.tan@acme.example</p>")
    )
    respx.get(f"{SITE}/contact").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE}/contact-us").mock(
        return_value=httpx.Response(
            200, headers=html, text="a.tan@acme.example and bob.lee@acme.example"
        )
    )
    respx.get(f"{SITE}/about").mock(return_value=httpx.Response(404))
    about_us = respx.get(f"{SITE}/about-us").mock(
        return_value=httpx.Response(200, headers=html, text="nothing here")
    )
    team = respx.get(f"{SITE}/team").mock(
        return_value=httpx.Response(200, headers=html, text="late@acme.example")
    )
    people = respx.get(f"{SITE}/people").mock(return_value=httpx.Response(404))
    found = emails.scrape_site_sources("acme.example")
    # the 404s do not count: 3 good pages so far (/, /contact-us, /about-us) + /team = 4 of 5
    assert found == [
        ("a.tan@acme.example", f"{SITE}/"),
        ("bob.lee@acme.example", f"{SITE}/contact-us"),
        ("late@acme.example", f"{SITE}/team"),
    ]
    assert about_us.call_count == 1 and team.call_count == 1 and people.call_count == 1
    assert emails.scrape_site_emails("acme.example") == [a for a, _u in found]
    # a smaller max_pages stops once that many pages answered
    emails._site_cache.clear()
    team.reset()
    narrow = emails.scrape_site_sources("acme.example", max_pages=2)
    assert narrow == found[:2] and team.call_count == 0


@respx.mock
def test_scrape_finds_the_team_page_when_contact_and_about_are_404(real_scrape):
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    html = {"content-type": "text/html"}
    for path in ("/", "/contact", "/contact-us", "/about", "/about-us"):
        respx.get(f"{SITE}{path}").mock(return_value=httpx.Response(404))
    respx.get(f"{SITE}/team").mock(
        return_value=httpx.Response(200, headers=html, text="Reach aria.tan@acme.example")
    )
    respx.get(f"{SITE}/people").mock(return_value=httpx.Response(404))
    assert emails.scrape_site_emails("acme.example") == ["aria.tan@acme.example"]


@respx.mock
def test_scrape_never_asks_for_more_than_the_request_cap(real_scrape, monkeypatch):
    monkeypatch.setattr(emails, "SITE_PATHS", tuple(f"/p{i}" for i in range(20)))
    respx.get(ROBOTS).mock(return_value=httpx.Response(404))
    pages = respx.get(url__regex=rf"{SITE}/p\d+").mock(return_value=httpx.Response(404))
    assert emails.scrape_site_sources("acme.example") == []
    assert pages.call_count == emails.SITE_MAX_REQUESTS


def test_addresses_on_domain_reads_text():
    text = "Write to Bob.Lee@NORTHWIND.example or info&#64;northwind.example, not x@other.example"
    assert emails.addresses_on_domain(text, "www.northwind.example") == [
        "bob.lee@northwind.example",
        "info@northwind.example",
    ]
