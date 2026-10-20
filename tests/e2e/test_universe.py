"""The synthetic world of the end-to-end tests: is it sound, and does the provider code read it?

Part one checks the data itself (invented people, example domains, the planned counts, the
fabricated results). Part two feeds the canned Hunter, Reoon and Serper replies to the real
provider classes through respx, so a reply shape that the code does not parse fails here and
not in the middle of the long flow.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from universe import (
    COMPANIES,
    HUNTER_RESPONSES,
    OFFER_TEXT,
    REOON_RESPONSES,
    SEARCH_RESULTS,
)
from world import GROUPS, SECRETS, fast_http, group_and_company, serper_organic

from leadgen.core import secrets
from leadgen.providers import hunter, reoon, serper
from leadgen.providers.base import ProviderError
from leadgen.services.confirm import slug_matches_name

RESULTS = [r for rows in SEARCH_RESULTS.values() for r in rows]


# --- the data -------------------------------------------------------------------------------------


def test_companies_are_invented_and_on_reserved_domains():
    assert len(COMPANIES) == 12
    assert [c["country_code"] for c in COMPANIES].count("SG") == 7
    assert [c["country_code"] for c in COMPANIES].count("GB") == 5
    for company in COMPANIES:
        assert re.fullmatch(r"[a-z]+\.example\.(com|org)", company["domain"]), company
        assert company["name"] in company["aliases"]
    assert len({c["domain"] for c in COMPANIES}) == 12


def test_search_results_cover_every_company_and_group_five_times():
    names = {c["name"] for c in COMPANIES}
    labels = set(GROUPS.values())
    assert len(SEARCH_RESULTS) == 24 and len(RESULTS) == 120
    for key, rows in SEARCH_RESULTS.items():
        company, label = key.split("|")
        assert company in names and label in labels
        assert len(rows) == 5


def test_no_real_link_hosts_and_three_fabricated_results():
    for row in RESULTS:
        assert re.fullmatch(r"https://(www|sg|uk)\.linkedin\.com/in/[a-z0-9-]+", row["link"])
    fabricated = [r for r in RESULTS if r.get("fabricated")]
    assert len(fabricated) == 3
    for row in fabricated:
        name = row["title"].split(" - ", 1)[0].split()
        assert slug_matches_name(row["link"], name[0], name[-1]) == "mismatch"
    honest = [r for r in RESULTS if not r.get("fabricated")]
    for row in honest:
        name = row["title"].split(" - ", 1)[0].split()
        assert slug_matches_name(row["link"], name[0], name[-1]) == "match", row["link"]
    links = [r["link"] for r in honest]
    assert len(links) == len(set(links))  # one profile per person


def test_the_offer_text_is_about_four_hundred_words_with_two_confirm_lines():
    assert 350 <= len(OFFER_TEXT.split()) <= 500
    assert OFFER_TEXT.count("[CONFIRM") == 2


def test_every_email_in_the_world_is_on_a_company_domain_and_both_verifiers_agree():
    domains = {c["domain"] for c in COMPANIES}
    hunter_emails = set(HUNTER_RESPONSES["email_verifier"])
    reoon_emails = set(REOON_RESPONSES["verify"])
    assert hunter_emails == reoon_emails
    for address in reoon_emails:
        assert address.split("@", 1)[1] in domains
    assert set(HUNTER_RESPONSES["domain_search"]) == domains


# --- the provider code reads the canned replies ---------------------------------------------------


@pytest.fixture
def router(sleeps, ready_db, monkeypatch):
    for name, value in SECRETS.items():
        secrets.set(name, value)
    fast_http(monkeypatch)
    with respx.mock(assert_all_called=False) as mock:
        yield mock


def test_hunter_patterns_accounts_and_findings_are_parsed(router):
    def route(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "domain-search":
            return httpx.Response(200, json=HUNTER_RESPONSES["domain_search"][query["domain"][0]])
        if path == "account":
            return httpx.Response(200, json=HUNTER_RESPONSES["account"])
        if path == "email-finder":
            key = f"{query['domain'][0]}|{query['first_name'][0]} {query['last_name'][0]}"
            return httpx.Response(200, json=HUNTER_RESPONSES["email_finder"][key])
        return httpx.Response(200, json=HUNTER_RESPONSES["email_verifier"][query["email"][0]])

    router.get(url__regex=r"https://api\.hunter\.io/v2/.*").mock(side_effect=route)
    provider = hunter.HunterProvider()
    for domain, body in HUNTER_RESPONSES["domain_search"].items():
        found = provider.domain_pattern(domain)
        expected = body["data"]["pattern"]
        if expected is None:
            assert found is None, domain
        else:
            assert found is not None and found[0] == expected
            assert 0.7 <= found[1] <= 0.95
    ok, message = provider.test()
    assert ok and "38 credits left" in message
    assert provider.find("Mira", "Tan", "asterharbour.example.com") == (
        "mira.tan@asterharbour.example.com", 0.88,
    )  # fmt: skip
    for address, body in HUNTER_RESPONSES["email_verifier"].items():
        verdict = provider.verify(address)
        assert verdict.raw_status == body["data"]["status"]
        assert verdict.catch_all is (body["data"]["status"] == "accept_all")


@pytest.mark.parametrize(
    ("status", "code"), [(401, "auth"), (403, "rate_limited"), (429, "quota"), (500, "http")]
)
def test_hunter_status_codes_mean_what_the_provider_says(router, status, code):
    router.get(url__regex=r"https://api\.hunter\.io/v2/.*").respond(status, json={})
    with pytest.raises(ProviderError) as err:
        hunter.HunterProvider().domain_pattern("asterharbour.example.com")
    assert err.value.code == code


def test_hunter_a_running_check_is_pending_and_a_missing_finding_is_none(router):
    router.get(url__regex=r"https://api\.hunter\.io/v2/email-verifier.*").respond(202, json={})
    assert hunter.HunterProvider().verify("x.y@asterharbour.example.com").raw_status == "pending"
    router.get(url__regex=r"https://api\.hunter\.io/v2/email-finder.*").respond(404, json={})
    assert hunter.HunterProvider().find("No", "One", "asterharbour.example.com") is None


def test_reoon_every_status_word_of_the_world_has_a_meaning(router):
    router.get(url__regex=r"https://emailverifier\.reoon\.com/api/v1/verify.*").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=REOON_RESPONSES["verify"][parse_qs(urlparse(str(request.url)).query)["email"][0]],
        )
    )
    provider = reoon.ReoonProvider()
    seen: dict[str, str] = {}
    for address, body in REOON_RESPONSES["verify"].items():
        verdict = provider.verify(address)
        seen[body["status"]] = verdict.status
        assert verdict.catch_all is bool(body["is_catch_all"])
    assert seen == {
        "safe": "verified",
        "invalid": "invalid",
        "unknown": "unknown",
        "inbox_full": "risky",
        "catch_all": "risky",
    }


def test_reoon_balance_and_error_bodies_are_read_as_quota_and_auth(router):
    router.get(url__regex=r"https://emailverifier\.reoon\.com/api/v1/check-account.*").respond(
        200, json=REOON_RESPONSES["balance"]
    )
    ok, message = reoon.ReoonProvider().test()
    assert ok and "120 credits left" in message
    verify = router.get(url__regex=r"https://emailverifier\.reoon\.com/api/v1/verify.*")
    for kind, code in (("quota", "quota"), ("auth", "auth")):
        verify.respond(200, json=REOON_RESPONSES["errors"][kind])
        with pytest.raises(ProviderError) as err:
            reoon.ReoonProvider().verify("mira.tan@asterharbour.example.com")
        assert err.value.code == code


def test_serper_results_of_every_search_are_parsed(router):
    router.post("https://google.serper.dev/search").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"organic": serper_organic(*_key(request), include_fabricated=True)}
        )
    )
    provider = serper.SerperProvider()
    total = 0
    for key, rows in SEARCH_RESULTS.items():
        company, label = key.split("|")
        marker = '"HR director"' if label == GROUPS["hr"] else '"training manager"'
        found = provider.search(f'x ({marker}) "{company}" Singapore', "sg", 10)
        assert [h.link for h in found] == [r["link"] for r in rows]
        total += len(found)
    assert total == 120


def _key(request: httpx.Request) -> tuple[str, str]:
    return group_and_company(json.loads(request.content)["q"])
