"""V13 connector providers (Tavily, Apollo, Prospeo, GetProspect, Emailable): HTTP mocked with
respx, invented people and companies, no network. Keys are checked to stay out of URLs."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from leadgen.core import secrets
from leadgen.providers import apollo, base, emailable, getprospect, prospeo, tavily
from leadgen.providers.base import Found, ProviderError
from leadgen.providers.search import SearchResult

KEY = "test-connector-key-7788"
PERSON_URL = "https://www.linkedin.com/in/aria-tan-example"


@pytest.fixture(autouse=True)
def no_waits(sleeps, monkeypatch):
    """Rate limiters of the new modules never wait (retry sleeps are replaced by `sleeps`)."""
    for module in (tavily, apollo, prospeo, getprospect, emailable):
        monkeypatch.setattr(module, "_limiter", base.RateLimiter(10_000))


def _key(env: str) -> None:
    secrets.set(env, KEY)


def _no_key_in_url(request: httpx.Request) -> None:
    assert KEY not in str(request.url)


# --- Tavily ---------------------------------------------------------------------------------


@respx.mock
def test_tavily_search_sends_site_as_include_domains(ready_db):
    _key("TAVILY_API_KEY")
    route = respx.post(tavily.SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Aria Tan - Head of Training",
                        "url": "https://sg.linkedin.com/in/aria-tan-example",
                        "content": "Head of Training at Northwind Logistics.",
                    },
                    {"title": "No url here"},
                ]
            },
        )
    )
    results = tavily.TavilyProvider().search("site:linkedin.com/in Head of Learning", num=5)
    request = route.calls[0].request
    assert request.headers["authorization"] == f"Bearer {KEY}"
    _no_key_in_url(request)
    assert json.loads(request.content) == {
        "query": "Head of Learning",
        "search_depth": "basic",
        "max_results": 5,
        "include_domains": ["linkedin.com/in"],
    }
    assert results == [
        SearchResult(
            title="Aria Tan - Head of Training",
            link="https://sg.linkedin.com/in/aria-tan-example",
            snippet="Head of Training at Northwind Logistics.",
            position=1,
        )
    ]
    assert base.usage("tavily") == {"calls": 1, "credits": 1.0}


@respx.mock
def test_tavily_query_without_site_has_no_domains(ready_db):
    _key("TAVILY_API_KEY")
    route = respx.post(tavily.SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    assert tavily.TavilyProvider().search("Northwind Logistics Singapore") == []
    body = json.loads(route.calls[0].request.content)
    assert "include_domains" not in body and body["query"] == "Northwind Logistics Singapore"


@respx.mock
def test_tavily_test_uses_usage_and_reports_credits(ready_db):
    _key("TAVILY_API_KEY")
    route = respx.get(tavily.USAGE_URL).mock(
        return_value=httpx.Response(
            200, json={"key": {"usage": 3}, "account": {"plan_usage": 40, "plan_limit": 1000}}
        )
    )
    assert tavily.TavilyProvider().test() == (True, "Tavily key works (960 credits left).")
    assert route.called and "search" not in str(route.calls[0].request.url)


@respx.mock
def test_tavily_bad_key_is_auth_error(ready_db):
    _key("TAVILY_API_KEY")
    respx.post(tavily.SEARCH_URL).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(ProviderError) as info:
        tavily.TavilyProvider().search("anything")
    assert info.value.code == "auth"
    assert base.usage("tavily")["credits"] == 0


# --- Apollo ---------------------------------------------------------------------------------


@respx.mock
def test_apollo_found_with_phone_request_uses_poll_only(ready_db):
    _key("APOLLO_API_KEY")
    route = respx.post(apollo.MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "person": {"email": "Aria.Tan@Northwind.example", "match_confidence": "high"},
                "request_id": "42",
            },
        )
    )
    found = apollo.ApolloProvider().find(PERSON_URL, want_phone=True)
    request = route.calls[0].request
    assert request.headers["x-api-key"] == KEY
    _no_key_in_url(request)
    assert not request.content  # query parameters only
    params = dict(request.url.params)
    assert params["linkedin_url"] == PERSON_URL
    assert params["reveal_phone_number"] == "true" and params["poll_only"] == "true"
    assert "webhook_url" not in params
    assert found == Found("apollo", email="aria.tan@northwind.example", phone_request="42",
                          credits=1.0)
    assert base.usage("apollo") == {"calls": 1, "credits": 1.0}


@respx.mock
def test_apollo_no_person_is_empty_and_free(ready_db):
    _key("APOLLO_API_KEY")
    respx.post(apollo.MATCH_URL).mock(return_value=httpx.Response(200, json={"person": None}))
    assert apollo.ApolloProvider().find(PERSON_URL) == Found("apollo", credits=0.0)
    assert base.usage("apollo")["credits"] == 0


@respx.mock
def test_apollo_locked_placeholder_email_is_dropped(ready_db):
    _key("APOLLO_API_KEY")
    respx.post(apollo.MATCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"person": {"email": "email_not_unlocked@northwind.example",
                             "match_confidence": "medium"}},
        )
    )
    assert apollo.ApolloProvider().find(PERSON_URL).email == ""


@respx.mock
def test_apollo_key_errors(ready_db):
    _key("APOLLO_API_KEY")
    respx.post(apollo.MATCH_URL).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(ProviderError) as info:
        apollo.ApolloProvider().find(PERSON_URL)
    assert info.value.code == "auth"
    respx.post(apollo.MATCH_URL).mock(return_value=httpx.Response(403, json={}))
    with pytest.raises(ProviderError) as info:
        apollo.ApolloProvider().find(PERSON_URL)
    assert info.value.code == "auth" and "People Enrichment" in info.value.message


@respx.mock
def test_apollo_collect_phone_ready_pending_and_gone(ready_db):
    _key("APOLLO_API_KEY")
    provider = apollo.ApolloProvider()
    route = respx.get(apollo.RESULT_URL.format(request_id="42")).mock(
        return_value=httpx.Response(
            200,
            json={
                "webhook_status": "done",
                "webhook_result": {
                    "people": [{"phone_numbers": [{"sanitized_number": "+6560000000",
                                                   "type_cd": "mobile"}]}]
                },
            },
        )
    )
    assert provider.collect_phone("42") == ("ready", "+6560000000", "mobile")
    _no_key_in_url(route.calls[0].request)
    assert base.usage("apollo")["credits"] == 8.0

    respx.get(apollo.RESULT_URL.format(request_id="43")).mock(
        return_value=httpx.Response(404, text='{"error": "result_pending"}')
    )
    assert provider.collect_phone("43") == ("pending", "", "")
    respx.get(apollo.RESULT_URL.format(request_id="44")).mock(return_value=httpx.Response(410))
    assert provider.collect_phone("44") == ("gone", "", "")


@respx.mock
def test_apollo_test_uses_free_result_call(ready_db):
    _key("APOLLO_API_KEY")
    route = respx.get(apollo.RESULT_URL.format(request_id="1")).mock(
        return_value=httpx.Response(404, json={})
    )
    assert apollo.ApolloProvider().test() == (True, "Apollo key works.")
    assert route.called
    respx.get(apollo.RESULT_URL.format(request_id="1")).mock(return_value=httpx.Response(403))
    ok, message = apollo.ApolloProvider().test()
    assert ok is False and "did not accept" in message


# --- Prospeo --------------------------------------------------------------------------------


@respx.mock
def test_prospeo_revealed_email_and_request_shape(ready_db):
    _key("PROSPEO_API_KEY")
    route = respx.post(prospeo.ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "error": False,
                "person": {"email": {"email": "Aria.Tan@Northwind.example", "revealed": True}},
            },
        )
    )
    found = prospeo.ProspeoProvider().find(PERSON_URL)
    request = route.calls[0].request
    assert request.headers["x-key"] == KEY
    _no_key_in_url(request)
    assert json.loads(request.content)["data"] == {"linkedin_url": PERSON_URL}
    assert found == Found("prospeo", email="aria.tan@northwind.example", credits=1.0)


@respx.mock
def test_prospeo_no_match_is_empty_and_free(ready_db):
    _key("PROSPEO_API_KEY")
    respx.post(prospeo.ENRICH_URL).mock(
        return_value=httpx.Response(400, json={"error": True, "error_code": "NO_MATCH"})
    )
    assert prospeo.ProspeoProvider().find(PERSON_URL) == Found("prospeo")
    assert base.usage("prospeo")["credits"] == 0


@respx.mock
def test_prospeo_error_codes_map_to_codes(ready_db):
    _key("PROSPEO_API_KEY")
    respx.post(prospeo.ENRICH_URL).mock(
        return_value=httpx.Response(
            400, json={"error": True, "error_code": "INSUFFICIENT_CREDITS"}
        )
    )
    with pytest.raises(ProviderError) as info:
        prospeo.ProspeoProvider().find(PERSON_URL)
    assert info.value.code == "quota"
    respx.post(prospeo.ENRICH_URL).mock(
        return_value=httpx.Response(400, json={"error": True, "error_code": "INVALID_API_KEY"})
    )
    with pytest.raises(ProviderError) as info:
        prospeo.ProspeoProvider().find(PERSON_URL)
    assert info.value.code == "auth"


@respx.mock
def test_prospeo_test_reads_remaining_credits(ready_db):
    _key("PROSPEO_API_KEY")
    respx.get(prospeo.ACCOUNT_URL).mock(
        return_value=httpx.Response(
            200, json={"error": False, "response": {"remaining_credits": 120}}
        )
    )
    assert prospeo.ProspeoProvider().test() == (True, "Prospeo key works (120 credits left).")
    respx.get(prospeo.ACCOUNT_URL).mock(
        return_value=httpx.Response(200, json={"error": True, "error_code": "INVALID_API_KEY"})
    )
    assert prospeo.ProspeoProvider().test() == (False, "Prospeo did not accept the key.")


# --- GetProspect ----------------------------------------------------------------------------


@respx.mock
def test_getprospect_found_email_and_key_header(ready_db):
    _key("GETPROSPECT_API_KEY")
    route = respx.post(getprospect.ENRICH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"success": True,
                  "data": {"email": {"address": "Aria.Tan@Northwind.example", "status": "valid"}}},
        )
    )
    found = getprospect.GetProspectProvider().find(PERSON_URL)
    request = route.calls[0].request
    assert request.headers["x-api-key"] == KEY
    _no_key_in_url(request)
    assert json.loads(request.content)["enrich_email"] is True
    assert found == Found("getprospect", email="aria.tan@northwind.example", credits=1.0)


@respx.mock
def test_getprospect_not_found_is_empty_and_free(ready_db):
    _key("GETPROSPECT_API_KEY")
    respx.post(getprospect.ENRICH_URL).mock(
        return_value=httpx.Response(
            200, json={"success": False, "errors": [{"name": "NOT_FOUND"}]}
        )
    )
    assert getprospect.GetProspectProvider().find(PERSON_URL) == Found("getprospect")
    assert base.usage("getprospect")["credits"] == 0


@respx.mock
def test_getprospect_status_codes(ready_db):
    _key("GETPROSPECT_API_KEY")
    provider = getprospect.GetProspectProvider()
    for status_code, code in ((401, "auth"), (402, "quota"), (408, "http")):
        respx.post(getprospect.ENRICH_URL).mock(return_value=httpx.Response(status_code, json={}))
        with pytest.raises(ProviderError) as info:
            provider.find(PERSON_URL)
        assert info.value.code == code


@respx.mock
def test_getprospect_test_is_free_match_on_invented_profile(ready_db):
    _key("GETPROSPECT_API_KEY")
    route = respx.post(getprospect.ENRICH_URL).mock(
        return_value=httpx.Response(200, json={"success": False, "errors": []})
    )
    assert getprospect.GetProspectProvider().test() == (True, "GetProspect key works.")
    body = json.loads(route.calls[0].request.content)
    assert body["enrich_email"] is False and body["enrich_phone"] is False
    assert body["data"]["linkedin_url"] == getprospect._CHECK_URL
    respx.post(getprospect.ENRICH_URL).mock(return_value=httpx.Response(401, json={}))
    assert getprospect.GetProspectProvider().test() == (
        False,
        "GetProspect did not accept the key.",
    )


# --- Emailable ------------------------------------------------------------------------------


@respx.mock
def test_emailable_states_and_catch_all(ready_db):
    _key("EMAILABLE_API_KEY")
    provider = emailable.EmailableProvider()
    route = respx.get(emailable.VERIFY_URL).mock(
        return_value=httpx.Response(200, json={"state": "deliverable", "accept_all": False})
    )
    verdict = provider.verify("aria.tan@northwind.example")
    assert verdict.status == "verified" and verdict.raw_status == "deliverable"
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    _no_key_in_url(route.calls[0].request)
    assert base.usage("emailable")["credits"] == 1.0

    respx.get(emailable.VERIFY_URL).mock(
        return_value=httpx.Response(200, json={"state": "deliverable", "accept_all": True})
    )
    verdict = provider.verify("sam.lim@northwind.example")
    assert verdict.status == "risky" and verdict.catch_all is True


@respx.mock
def test_emailable_still_checking_is_unknown_and_free(ready_db):
    _key("EMAILABLE_API_KEY")
    respx.get(emailable.VERIFY_URL).mock(return_value=httpx.Response(249, json={}))
    verdict = emailable.EmailableProvider().verify("aria.tan@northwind.example")
    assert verdict.status == "unknown" and verdict.raw_status == "pending"
    assert base.usage("emailable")["credits"] == 0


@respx.mock
def test_emailable_test_reads_account(ready_db):
    _key("EMAILABLE_API_KEY")
    route = respx.get(emailable.ACCOUNT_URL).mock(
        return_value=httpx.Response(200, json={"available_credits": 240})
    )
    assert emailable.EmailableProvider().test() == (True, "Emailable key works (240 credits left).")
    assert route.called


def test_new_providers_are_not_configured_without_keys(ready_db):
    for cls in (
        tavily.TavilyProvider,
        apollo.ApolloProvider,
        prospeo.ProspeoProvider,
        getprospect.GetProspectProvider,
        emailable.EmailableProvider,
    ):
        assert cls().configured() is False


def test_verifier_chain_order(ready_db, monkeypatch):
    """Emailable sits after ZeroBounce and before Hunter (Hunter costs half a credit per check)."""
    from leadgen.services import emails

    for env in ("REOON_API_KEY", "ZEROBOUNCE_API_KEY", "EMAILABLE_API_KEY", "HUNTER_API_KEY"):
        secrets.set(env, KEY)
    keys = [v.info.key for v in emails._default_verifiers()]
    assert keys == ["reoon", "zerobounce", "emailable", "hunter"]


def test_missing_key_raises_not_configured(ready_db):
    with pytest.raises(ProviderError) as info:
        apollo.ApolloProvider().find(PERSON_URL)
    assert info.value.code == "not_configured"
