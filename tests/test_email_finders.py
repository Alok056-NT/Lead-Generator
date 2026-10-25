"""LinkedIn finders in the email pipeline (V13): Apollo, Prospeo and GetProspect run before the
pattern path, phone numbers (found, not checked; Apollo's later), the finder budget, the
estimate and the phones on the studio results.

No test touches the network: finders and verifiers are fakes, Apollo's poll is respx, DNS is a
fake resolver. People and domains are invented (example.com style).
"""

from __future__ import annotations

import types

import dns.resolver
import httpx
import pytest
import respx

from leadgen import db
from leadgen.core import config, secrets
from leadgen.providers import apollo, prospeo
from leadgen.providers.base import Found, ProviderError, RateLimiter, Verdict
from leadgen.services import browse, budget, emails, preferences, projects
from leadgen.services import campaign as camp

MX = {"northwind.example": "aspmx.l.google.com"}


class FakeAnswer:
    def __init__(self, host, preference=10):
        self.exchange = host + "."
        self.preference = preference


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Fake DNS, no website scraping, fast limiters, empty caches."""
    emails._mx_cache.clear()
    emails._site_cache.clear()
    monkeypatch.setattr(emails, "_site_limiter", RateLimiter(10_000))
    monkeypatch.setattr(apollo, "_limiter", RateLimiter(10_000))
    monkeypatch.setattr(emails, "scrape_site_emails", lambda domain: [])

    def resolve(domain, rdtype="A", **kwargs):
        if domain in MX:
            return [FakeAnswer(MX[domain])]
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(emails.dns.resolver, "resolve", resolve)


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


_counter = iter(range(1, 10_000))


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


def email_rows(person_id):
    with db.session() as conn:
        return db.query(conn, "SELECT * FROM emails WHERE person_id = ? ORDER BY id", (person_id,))


def phone_rows(person_id):
    with db.session() as conn:
        return db.query(
            conn, "SELECT number, kind, source, status, request_id FROM phones WHERE person_id = ?",
            (person_id,),
        )  # fmt: skip


class FakeFinder:
    """Stands in for a finder; uses Prospeo's INFO so error replies have a known provider."""

    info = prospeo.INFO

    def __init__(self, found=None, error=None):
        self.found, self.error, self.calls = found, error, []

    def configured(self):
        return True

    def find(self, linkedin_url, want_phone=False):
        self.calls.append((linkedin_url, want_phone))
        if self.error:
            raise self.error
        return self.found or Found("prospeo")


class FakeVerifier:
    """A verifier that answers from a dict of address -> status; other addresses are verified."""

    def __init__(self, key, answers=None):
        self.info = types.SimpleNamespace(key=key)
        self.answers = answers or {}

    def configured(self):
        return True

    def verify(self, email):
        return Verdict(self.answers.get(email, "verified"), "x", self.info.key)


class FakeHunter:
    info = types.SimpleNamespace(key="hunter")

    def __init__(self, pattern="{first}.{last}"):
        self.pattern = pattern
        self.calls = []

    def configured(self):
        return True

    def domain_pattern(self, domain):
        self.calls.append(domain)
        return (self.pattern, 0.85) if self.pattern else None


def run_find(cid, **kw):
    kw.setdefault("hunter", FakeHunter(None))
    kw.setdefault("verifiers", [FakeVerifier("reoon")])
    return emails.find_emails(cid, **kw)


# --- the finder runs first ------------------------------------------------------------------


def test_finder_email_is_verified_and_used_before_the_pattern(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(Found("prospeo", email="Aria.Tan@Northwind.example", credits=1))
    hunter = FakeHunter("{first}.{last}")
    out = run_find(cid, finders=[finder], hunter=hunter)
    assert [want for _url, want in finder.calls] == [False]
    [row] = email_rows(aria)
    assert (row["email"], row["status"], row["method"]) == (
        "aria.tan@northwind.example", "verified", "finder:prospeo",
    )  # fmt: skip
    assert budget.used(cid, "finder") == 1
    assert hunter.calls == []  # the pattern path did not run
    assert out["credits_used"]["finder"] == 1.0


def test_finder_with_no_email_falls_back_to_the_pattern_path(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(Found("prospeo"))
    hunter = FakeHunter("{first}.{last}")
    run_find(cid, finders=[finder], hunter=hunter)
    assert len(finder.calls) == 1
    assert hunter.calls == ["northwind.example"]
    [row] = email_rows(aria)
    assert (row["email"], row["status"], row["method"]) == (
        "aria.tan@northwind.example", "verified", "pattern:hunter",
    )  # fmt: skip


def test_invalid_finder_email_falls_back_to_the_pattern_path(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(Found("prospeo", email="aria.tan@northwind.example", credits=1))
    verifier = FakeVerifier("reoon", {"aria.tan@northwind.example": "invalid"})
    run_find(cid, finders=[finder], verifiers=[verifier], hunter=FakeHunter("{first}{last}"))
    [row] = email_rows(aria)
    assert (row["email"], row["status"], row["method"]) == (
        "ariatan@northwind.example", "verified", "pattern:hunter",
    )  # fmt: skip


def test_finder_email_without_a_verifier_is_only_a_guess(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(Found("prospeo", email="aria.tan@northwind.example", credits=1))
    run_find(cid, finders=[finder], verifiers=[])
    [row] = email_rows(aria)
    assert (row["email"], row["status"], row["method"]) == (
        "aria.tan@northwind.example", "guess", "finder:prospeo",
    )  # fmt: skip


def test_person_without_linkedin_url_is_not_sent_to_a_finder(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    with db.session() as conn:
        conn.execute("UPDATE people SET linkedin_url = '' WHERE id = ?", (aria,))
    finder = FakeFinder(Found("prospeo", email="aria.tan@northwind.example", credits=1))
    run_find(cid, finders=[finder])
    assert finder.calls == []
    [row] = email_rows(aria)
    assert row["method"] == "pattern:default"


def test_use_finders_false_never_calls_a_finder(cid, monkeypatch):
    calls = []

    def find(self, linkedin_url, want_phone=False):
        calls.append(linkedin_url)
        return Found("prospeo")

    monkeypatch.setattr(prospeo.ProspeoProvider, "find", find)
    secrets.set("PROSPEO_API_KEY", "prospeo-key-1234")
    company = add_company(cid)
    add_person(cid, company, "Aria", "Tan")
    add_person(cid, company, "Bob", "Lee")
    run_find(cid, use_finders=False)
    assert calls == []
    add_person(cid, company, "Cara", "Lee")  # the two above have emails now
    run_find(cid)  # finders are used by default
    assert len(calls) == 1


def test_quota_on_the_finder_runs_on_with_the_pattern_path(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(error=ProviderError("quota"))
    out = run_find(cid, finders=[finder], hunter=FakeHunter("{first}.{last}"))
    assert out["ok"] is True
    assert out["provider_issues"][0]["error"]["message"].startswith("Prospeo")
    [row] = email_rows(aria)
    assert row["method"] == "pattern:hunter"


# --- the finder budget ----------------------------------------------------------------------


def test_finder_budget_stops_the_run_until_confirmed(cid):
    camp.update_campaign(cid, budget={"finder": 0})
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(Found("prospeo", email="aria.tan@northwind.example", credits=1))
    out = run_find(cid, finders=[finder])
    assert out["ok"] is False and out["budget_reached"]["kind"] == "finder"
    assert finder.calls == []
    assert email_rows(aria) == []
    run_find(cid, finders=[finder], confirm=True)
    assert len(finder.calls) == 1
    assert [r["status"] for r in email_rows(aria)] == ["verified"]


# --- phone numbers --------------------------------------------------------------------------


def test_phone_from_a_finder_is_stored_as_found_not_checked(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(
        Found("prospeo", email="aria.tan@northwind.example", phone="+65 6000 0000",
              phone_kind="mobile", credits=1),
    )  # fmt: skip
    run_find(cid, finders=[finder], want_phone=True)
    assert finder.calls[0][1] is True
    [phone] = phone_rows(aria)
    assert (phone["number"], phone["kind"], phone["source"], phone["status"]) == (
        "+65 6000 0000", "mobile", "prospeo", "found_not_verified",
    )  # fmt: skip


def test_apollo_phone_request_is_polled_later_and_charged(cid, monkeypatch):
    secrets.set("APOLLO_API_KEY", "apollo-key-1234")
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(
        Found("apollo", email="aria.tan@northwind.example", phone_request="42", credits=1),
    )
    run_find(cid, finders=[finder], want_phone=True)
    [waiting] = phone_rows(aria)
    assert (waiting["status"], waiting["request_id"], waiting["number"]) == ("requested", "42", "")
    poll = {
        "webhook_status": "completed",
        "webhook_result": {"people": [{"phone_numbers": [
            {"sanitized_number": "+6560000001", "type_cd": "mobile"},
        ]}]},  # fmt: skip
    }
    with respx.mock:
        respx.get("https://api.apollo.io/api/v1/webhook_result/42").mock(
            return_value=httpx.Response(200, json=poll)
        )
        counts = emails.poll_phone_requests(cid)
    assert counts == {"ready": 1, "waiting": 0, "gone": 0}
    assert budget.used(cid, "finder") == 1 + apollo.PHONE_CREDITS
    [found] = phone_rows(aria)
    assert (found["number"], found["status"], found["request_id"]) == (
        "+6560000001", "found_not_verified", "",
    )  # fmt: skip


def test_apollo_phone_request_that_is_gone_is_marked_failed(cid):
    secrets.set("APOLLO_API_KEY", "apollo-key-1234")
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    finder = FakeFinder(
        Found("apollo", email="aria.tan@northwind.example", phone_request="43", credits=1),
    )
    run_find(cid, finders=[finder], want_phone=True)
    with respx.mock:
        respx.get("https://api.apollo.io/api/v1/webhook_result/43").mock(
            return_value=httpx.Response(410)
        )
        counts = emails.poll_phone_requests(cid)
    assert counts == {"ready": 0, "waiting": 0, "gone": 1}
    assert phone_rows(aria)[0]["status"] == "failed"


def test_studio_results_show_found_and_waiting_phones(cid):
    company = add_company(cid)
    aria = add_person(cid, company, "Aria", "Tan")
    bob = add_person(cid, company, "Bob", "Lee")
    cara = add_person(cid, company, "Cara", "Ong")
    emails._store_phone(aria, Found("prospeo", phone="+65 6000 0000", phone_kind="mobile"))
    emails._store_phone(bob, Found("apollo", phone_request="44"))
    project_id = camp.get_campaign(cid)["project_id"]
    out = browse.studio_results(project_id, cid)
    by_id = {lead["id"]: lead for lead in out["leads"]}
    assert (by_id[aria]["phone"], by_id[aria]["phone_status"]) == ("+65 6000 0000", "found")
    assert (by_id[bob]["phone"], by_id[bob]["phone_status"]) == ("", "waiting")
    assert (by_id[cara]["phone"], by_id[cara]["phone_status"]) == ("", "")


# --- the estimate ---------------------------------------------------------------------------


def test_estimate_counts_finder_credits_and_phones(cid):
    secrets.set("PROSPEO_API_KEY", "prospeo-key-1234")
    company = add_company(cid)
    for first in ("Aria", "Bob", "Cara"):
        add_person(cid, company, first, "Tan")
    est = emails.estimate_email_search(cid)
    assert (est["finder"], est["finder_credits"], est["phones_wanted"]) == ("prospeo", 3, False)
    assert est["budget_left"]["finder"] == 50
    assert est["needs_confirmation"] is False
    with_phones = emails.estimate_email_search(cid, want_phone=True)
    assert with_phones["finder_credits"] == 30
    assert with_phones["needs_confirmation"] is True
    assert emails.estimate_email_search(cid, use_finders=False)["finder"] is None


# --- the Settings field ---------------------------------------------------------------------


def test_finder_budget_round_trips_in_settings():
    reply = preferences.update_preferences({"budgets": {"finder": 30}})
    assert reply["budgets"]["finder"] == 30
    assert config.load_settings().budgets.finder == 30
