"""Hunter, Reoon and ZeroBounce providers: parsing, error mapping, usage, key checks (respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from leadgen.core import secrets
from leadgen.providers import base, hunter, registry, reoon, zerobounce
from leadgen.providers.base import ProviderError, Verdict

HUNTER = "https://api.hunter.io/v2"
REOON_VERIFY = reoon.VERIFY_URL
REOON_BALANCE = reoon.BALANCE_URL
ZB_VALIDATE = zerobounce.VALIDATE_URL
ZB_CREDITS = zerobounce.CREDITS_URL


@pytest.fixture
def keys(ready_db, sleeps):
    secrets.set("HUNTER_API_KEY", "hunter-key-1234")
    secrets.set("REOON_API_KEY", "reoon-key-1234")
    secrets.set("ZEROBOUNCE_API_KEY", "zb-key-1234")


# --- registry -------------------------------------------------------------------------------


def test_registered_with_verified_signup_info():
    for key in ("hunter", "reoon", "zerobounce"):
        assert registry.get_provider(key) is not None
    assert registry.ALL_PROVIDERS["hunter"].signup_url == "https://hunter.io/users/sign_up"
    assert registry.ALL_PROVIDERS["reoon"].signup_url == "https://emailverifier.reoon.com/register/"
    assert registry.ALL_PROVIDERS["zerobounce"].signup_url.endswith("/members/signin/register")
    assert "100" in registry.ALL_PROVIDERS["zerobounce"].free_tier
    assert "50 credits" in registry.ALL_PROVIDERS["hunter"].free_tier
    assert "20 free checks per day" in registry.ALL_PROVIDERS["reoon"].free_tier


def test_verdict_rejects_unknown_status():
    with pytest.raises(ValueError):
        Verdict("maybe", "x", "y")


# --- Hunter ---------------------------------------------------------------------------------


@respx.mock
def test_hunter_domain_pattern(keys):
    route = respx.get(f"{HUNTER}/domain-search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"domain": "example.com", "pattern": "{First}.{Last}", "emails": [{}]},
                "meta": {"results": 4, "limit": 1},
            },
        )
    )
    pattern, confidence = hunter.HunterProvider().domain_pattern("example.com")
    assert pattern == "{first}.{last}"
    assert confidence == pytest.approx(0.9)
    request = route.calls[0].request
    assert request.headers["X-API-KEY"] == "hunter-key-1234"
    assert "hunter-key" not in str(request.url)
    assert dict(request.url.params) == {"domain": "example.com", "limit": "1"}
    assert base.usage("hunter", "all") == {"calls": 1, "credits": 1.0}


@respx.mock
def test_hunter_domain_pattern_missing_or_odd(keys):
    route = respx.get(f"{HUNTER}/domain-search")
    route.mock(
        return_value=httpx.Response(
            200, json={"data": {"domain": "x.com", "pattern": None, "emails": []}}
        )
    )
    assert hunter.HunterProvider().domain_pattern("x.com") is None
    assert base.usage("hunter", "all")["credits"] == 0  # nothing found, nothing charged
    route.mock(
        return_value=httpx.Response(
            200, json={"data": {"pattern": "{middle}.{last}", "emails": [{}]}}
        )
    )
    assert hunter.HunterProvider().domain_pattern("x.com") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{first}", "{first}"),
        (" {F}{Last} ", "{f}{last}"),
        ("{last}-{first}", "{last}-{first}"),
        ("{first}{l}", "{first}{l}"),
        ("", None),
        ("{first}@{last}", None),
        ("first.last", None),
        ("...", None),
        (None, None),
        (5, None),
    ],
)
def test_normalize_pattern(raw, expected):
    assert hunter.normalize_pattern(raw) == expected


@respx.mock
def test_hunter_find(keys):
    route = respx.get(f"{HUNTER}/email-finder")
    route.mock(
        return_value=httpx.Response(
            200, json={"data": {"email": "Aria.Tan@Example.com", "score": 91}}
        )
    )
    assert hunter.HunterProvider().find("Aria", "Tan", "example.com") == (
        "aria.tan@example.com",
        pytest.approx(0.91),
    )
    assert dict(route.calls[0].request.url.params) == {
        "domain": "example.com",
        "first_name": "Aria",
        "last_name": "Tan",
    }
    assert base.usage("hunter", "all")["credits"] == 1.0
    route.mock(return_value=httpx.Response(200, json={"data": {"email": None, "score": None}}))
    assert hunter.HunterProvider().find("Aria", "Tan", "example.com") is None
    route.mock(return_value=httpx.Response(404, json={"errors": []}))
    assert hunter.HunterProvider().find("Aria", "Tan", "example.com") is None
    assert base.usage("hunter", "all")["credits"] == 1.0  # not found = not charged


@pytest.mark.parametrize(
    ("raw", "status", "catch_all", "credits"),
    [
        ("valid", "verified", False, 0.5),
        ("invalid", "invalid", False, 0.5),
        ("accept_all", "risky", True, 0.5),
        ("webmail", "risky", False, 0.5),
        ("disposable", "invalid", False, 0.5),
        ("unknown", "unknown", False, 0),
        ("something-new", "unknown", False, 0),
    ],
)
@respx.mock
def test_hunter_verify_statuses(keys, raw, status, catch_all, credits):
    respx.get(f"{HUNTER}/email-verifier").mock(
        return_value=httpx.Response(200, json={"data": {"status": raw}})
    )
    verdict = hunter.HunterProvider().verify("a@example.com")
    assert (verdict.status, verdict.catch_all, verdict.raw_status) == (status, catch_all, raw)
    assert verdict.provider == "hunter"
    assert base.usage("hunter", "all")["credits"] == credits


@respx.mock
def test_hunter_verify_polls_while_running(keys):
    route = respx.get(f"{HUNTER}/email-verifier").mock(
        side_effect=[
            httpx.Response(202, json={}),
            httpx.Response(200, json={"data": {"status": "valid"}}),
        ]
    )
    assert hunter.HunterProvider().verify("a@example.com").status == "verified"
    assert route.call_count == 2


@respx.mock
def test_hunter_verify_still_running_is_unknown(keys):
    respx.get(f"{HUNTER}/email-verifier").mock(return_value=httpx.Response(202, json={}))
    verdict = hunter.HunterProvider().verify("a@example.com")
    assert (verdict.status, verdict.raw_status) == ("unknown", "pending")


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "auth"), (429, "quota"), (403, "rate_limited"), (400, "http"), (500, "http")],
)
@respx.mock
def test_hunter_error_mapping(keys, status, code):
    # Hunter: 401 bad key, 403 rate limit, 429 usage limit (opposite of the generic mapping).
    respx.get(f"{HUNTER}/email-verifier").mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(ProviderError) as info:
        hunter.HunterProvider().verify("a@example.com")
    assert info.value.code == code
    assert "hunter-key" not in info.value.message


@respx.mock
def test_hunter_bad_json_and_missing_key(keys):
    respx.get(f"{HUNTER}/email-verifier").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError) as info:
        hunter.HunterProvider().verify("a@example.com")
    assert info.value.code == "parse"
    secrets.delete("HUNTER_API_KEY")
    with pytest.raises(ProviderError) as info:
        hunter.HunterProvider().verify("a@example.com")
    assert info.value.code == "not_configured"
    assert hunter.HunterProvider().test() == (False, "HUNTER_API_KEY is not set.")


@respx.mock
def test_hunter_test_uses_free_account_call(keys):
    respx.get(f"{HUNTER}/account").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"requests": {"credits": {"used": 4, "available": 50, "remaining": 46}}}},
        )
    )
    ok, message = hunter.HunterProvider().test()
    assert ok and "46" in message
    assert base.usage("hunter", "all")["calls"] == 0  # the account call is free and not counted
    respx.get(f"{HUNTER}/account").mock(return_value=httpx.Response(200, json={"data": {}}))
    assert hunter.HunterProvider().test() == (True, "Hunter key works.")
    respx.get(f"{HUNTER}/account").mock(return_value=httpx.Response(401))
    ok, message = hunter.HunterProvider().test()
    assert not ok and "rejected" in message


@respx.mock
def test_save_api_key_for_hunter_keeps_good_key_only(ready_db, sleeps):
    respx.get(f"{HUNTER}/account").mock(return_value=httpx.Response(401))
    result = registry.save_api_key("hunter", "bad-key-0001")
    assert result["ok"] is False and secrets.get("HUNTER_API_KEY") == ""
    respx.get(f"{HUNTER}/account").mock(return_value=httpx.Response(200, json={"data": {}}))
    assert registry.save_api_key("hunter", "good-key-0002")["ok"] is True
    assert secrets.get("HUNTER_API_KEY") == "good-key-0002"


# --- Reoon ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "status", "catch_all", "credits"),
    [
        ({"status": "safe", "is_catch_all": False}, "verified", False, 1),
        ({"status": "valid"}, "verified", False, 1),
        ({"status": "invalid"}, "invalid", False, 1),
        ({"status": "disabled"}, "invalid", False, 1),
        ({"status": "disposable"}, "invalid", False, 1),
        ({"status": "spamtrap"}, "invalid", False, 1),
        ({"status": "catch_all", "is_catch_all": True}, "risky", True, 1),
        ({"status": "safe", "is_catch_all": True}, "risky", True, 1),
        ({"status": "role_account"}, "risky", False, 1),
        ({"status": "inbox_full"}, "risky", False, 1),
        ({"status": "unknown"}, "unknown", False, 0),
        ({"status": "brand-new"}, "unknown", False, 0),
    ],
)
@respx.mock
def test_reoon_verify_statuses(keys, body, status, catch_all, credits):
    route = respx.get(REOON_VERIFY).mock(return_value=httpx.Response(200, json=body))
    verdict = reoon.ReoonProvider().verify("a@example.com")
    assert (verdict.status, verdict.catch_all) == (status, catch_all)
    assert verdict.provider == "reoon"
    params = dict(route.calls[0].request.url.params)
    assert params == {"email": "a@example.com", "mode": "power", "key": "reoon-key-1234"}
    assert base.usage("reoon", "all")["credits"] == credits


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, json={"status": "error", "reason": "Daily credits over"}), "quota"),
        (httpx.Response(200, json={"status": "error", "reason": "Invalid API key"}), "auth"),
        (httpx.Response(200, json={"status": "error", "reason": "weird"}), "http"),
        (httpx.Response(401), "auth"),
        (httpx.Response(402), "quota"),
        (httpx.Response(200, text="nope"), "parse"),
    ],
)
@respx.mock
def test_reoon_errors(keys, response, code):
    respx.get(REOON_VERIFY).mock(return_value=response)
    with pytest.raises(ProviderError) as info:
        reoon.ReoonProvider().verify("a@example.com")
    assert info.value.code == code
    assert "reoon-key" not in info.value.message


@respx.mock
def test_reoon_test_reads_balance(keys):
    respx.get(REOON_BALANCE).mock(
        return_value=httpx.Response(
            200,
            json={
                "api_status": "active",
                "remaining_daily_credits": 18,
                "remaining_instant_credits": 100,
                "status": "success",
            },
        )
    )
    ok, message = reoon.ReoonProvider().test()
    assert ok and "118" in message
    respx.get(REOON_BALANCE).mock(return_value=httpx.Response(200, json={"api_status": "off"}))
    assert reoon.ReoonProvider().test()[0] is False
    respx.get(REOON_BALANCE).mock(return_value=httpx.Response(200, json={"api_status": "active"}))
    assert reoon.ReoonProvider().test() == (True, "Reoon key works.")
    secrets.delete("REOON_API_KEY")
    assert reoon.ReoonProvider().test()[0] is False
    assert base.usage("reoon", "all")["calls"] == 0


# --- ZeroBounce -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "status", "catch_all", "credits"),
    [
        ({"status": "valid", "sub_status": ""}, "verified", False, 1),
        ({"status": "invalid", "sub_status": "mailbox_not_found"}, "invalid", False, 1),
        ({"status": "catch-all", "sub_status": "accept_all"}, "risky", True, 1),
        ({"status": "unknown", "sub_status": "greylisted"}, "unknown", False, 0),
        ({"status": "spamtrap", "sub_status": "possible_trap"}, "invalid", False, 1),
        ({"status": "abuse", "sub_status": ""}, "invalid", False, 1),
        ({"status": "do_not_mail", "sub_status": "disposable"}, "invalid", False, 1),
        ({"status": "do_not_mail", "sub_status": "role_based"}, "risky", False, 1),
        ({"status": "do_not_mail", "sub_status": "role_based_catch_all"}, "risky", True, 1),
    ],
)
@respx.mock
def test_zerobounce_verify_statuses(keys, body, status, catch_all, credits):
    route = respx.get(ZB_VALIDATE).mock(return_value=httpx.Response(200, json=body))
    verdict = zerobounce.ZeroBounceProvider().verify("a@example.com")
    assert (verdict.status, verdict.catch_all) == (status, catch_all)
    assert verdict.provider == "zerobounce"
    assert body["status"] in verdict.raw_status
    params = dict(route.calls[0].request.url.params)
    assert params["email"] == "a@example.com" and params["api_key"] == "zb-key-1234"
    assert base.usage("zerobounce", "all")["credits"] == credits


@respx.mock
def test_zerobounce_error_body_and_http_errors(keys):
    respx.get(ZB_VALIDATE).mock(
        return_value=httpx.Response(
            200, json={"error": "Invalid API Key or your account ran out of credits"}
        )
    )
    with pytest.raises(ProviderError) as info:
        zerobounce.ZeroBounceProvider().verify("a@example.com")
    assert info.value.code == "auth"
    assert "out of credits" in info.value.message and "zb-key" not in info.value.message
    respx.get(ZB_VALIDATE).mock(return_value=httpx.Response(402))
    with pytest.raises(ProviderError) as info:
        zerobounce.ZeroBounceProvider().verify("a@example.com")
    assert info.value.code == "quota"


@respx.mock
def test_zerobounce_test_uses_getcredits(keys):
    respx.get(ZB_CREDITS).mock(return_value=httpx.Response(200, json={"Credits": 95}))
    ok, message = zerobounce.ZeroBounceProvider().test()
    assert ok and "95" in message
    respx.get(ZB_CREDITS).mock(return_value=httpx.Response(200, json={"Credits": -1}))
    ok, message = zerobounce.ZeroBounceProvider().test()
    assert not ok and "rejected" in message
    respx.get(ZB_CREDITS).mock(return_value=httpx.Response(200, json={"Credits": "x"}))
    assert zerobounce.ZeroBounceProvider().test()[0] is False
    secrets.delete("ZEROBOUNCE_API_KEY")
    assert zerobounce.ZeroBounceProvider().test()[0] is False
    with pytest.raises(ProviderError) as info:
        zerobounce.ZeroBounceProvider().verify("a@example.com")
    assert info.value.code == "not_configured"


# --- timeouts: a paid check is never sent twice ---------------------------------------------


@respx.mock
def test_reoon_read_timeout_is_one_call_and_an_http_error(keys, sleeps):
    route = respx.get(REOON_VERIFY).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError) as info:
        reoon.ReoonProvider().verify("a@example.com")
    assert info.value.code == "http"
    assert route.call_count == 1 and sleeps == []
    assert base.usage("reoon", "all")["calls"] == 0


@respx.mock
def test_reoon_verify_waits_longer_than_the_default_client(keys):
    seen = []

    def reply(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"status": "safe"})

    respx.get(REOON_VERIFY).mock(side_effect=reply)
    reoon.ReoonProvider().verify("a@example.com")
    assert seen[0]["read"] == reoon.READ_TIMEOUT_SECONDS > 20


@respx.mock
def test_zerobounce_read_timeout_is_one_call(keys, sleeps):
    route = respx.get(ZB_VALIDATE).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError) as info:
        zerobounce.ZeroBounceProvider().verify("a@example.com")
    assert info.value.code == "http" and route.call_count == 1


@pytest.mark.parametrize("path", ["email-verifier", "domain-search", "email-finder"])
@respx.mock
def test_hunter_read_timeout_is_one_call(keys, sleeps, path):
    route = respx.get(f"{HUNTER}/{path}").mock(side_effect=httpx.ReadTimeout("slow"))
    provider = hunter.HunterProvider()
    with pytest.raises(ProviderError) as info:
        if path == "email-verifier":
            provider.verify("a@example.com")
        elif path == "domain-search":
            provider.domain_pattern("example.com")
        else:
            provider.find("Aria", "Tan", "example.com")
    assert info.value.code == "http" and route.call_count == 1
