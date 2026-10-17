"""lemlist provider (respx) and the push service. All people are invented; no real network."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from qa_world import (
    COMPANY,
    add_company,
    add_email,
    add_message,
    add_person,
    email_message,
    make_campaign,
    message_row,
)

from leadgen import db
from leadgen.core import secrets
from leadgen.providers import lemlist, registry
from leadgen.providers.base import ProviderError
from leadgen.services import lemlist as svc
from leadgen.services.lemlist import LemlistError, list_lemlist_campaigns, push_to_lemlist

API = "https://api.lemlist.com/api"
KEY = "lem-key-1234567890"
TARGET = "cam_test123"


@pytest.fixture
def key():
    secrets.set("LEMLIST_API_KEY", KEY)


def _auth_header() -> str:
    return "Basic " + base64.b64encode(f":{KEY}".encode()).decode()


# --- provider -----------------------------------------------------------------------------------


def test_registry_has_lemlist():
    assert registry.get_provider("lemlist") is not None
    assert registry.ALL_PROVIDERS["lemlist"] is lemlist.INFO


@respx.mock
def test_list_campaigns_auth_and_paging(key, sleeps):
    page1 = [{"_id": f"cam_{i}", "name": f"Campaign {i}", "status": "running"} for i in range(100)]
    page2 = [{"_id": "cam_last", "name": "Last", "status": "draft"}, {"name": "no id"}, "junk"]
    route = respx.get(f"{API}/campaigns").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    campaigns = lemlist.LemlistProvider().list_campaigns()
    assert len(campaigns) == 101
    assert campaigns[0] == {"id": "cam_0", "name": "Campaign 0", "status": "running"}
    assert campaigns[-1]["id"] == "cam_last"
    first, second = route.calls[0].request, route.calls[1].request
    assert first.headers["Authorization"] == _auth_header()
    assert first.url.params["version"] == "v2" and first.url.params["limit"] == "100"
    assert second.url.params["offset"] == "100"
    assert KEY not in str(first.url)


def test_provider_without_key():
    provider = lemlist.LemlistProvider()
    assert provider.configured() is False
    with pytest.raises(ProviderError) as err:
        provider.list_campaigns()
    assert err.value.code == "not_configured"
    assert provider.test() == (False, "LEMLIST_API_KEY is not set.")


@respx.mock
def test_provider_test_and_errors(key, sleeps):
    respx.get(f"{API}/campaigns").mock(return_value=httpx.Response(200, json=[]))
    assert lemlist.LemlistProvider().test() == (True, "lemlist key works.")
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(401, text="The authentication you supplied is incorrect")
    )
    ok, message = lemlist.LemlistProvider().test()
    assert ok is False and "rejected" in message and KEY not in message
    respx.get(f"{API}/campaigns").mock(return_value=httpx.Response(200, json={"oops": 1}))
    with pytest.raises(ProviderError) as err:
        lemlist.LemlistProvider().list_campaigns()
    assert err.value.code == "parse"
    respx.get(f"{API}/campaigns").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError) as err:
        lemlist.LemlistProvider().list_campaigns()
    assert err.value.code == "parse"


@respx.mock
def test_create_lead_success_and_payload(key, sleeps):
    route = respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        return_value=httpx.Response(
            200, json={"_id": "lea_1", "warnings": [{"code": "FIELDS_KEPT", "message": "m"}]}
        )
    )
    result = lemlist.LemlistProvider().create_lead(TARGET, {"email": "a@example.com", "x": "y"})
    assert result.status == "created" and result.lead_id == "lea_1"
    assert result.warnings == ["FIELDS_KEPT"]
    request = route.calls[0].request
    assert request.headers["Authorization"] == _auth_header()
    assert json.loads(request.content) == {"email": "a@example.com", "x": "y"}


@respx.mock
@pytest.mark.parametrize(
    ("status", "text", "expected"),
    [
        (400, "Lead already in the campaign", "duplicate"),
        (400, "Invalid email address", "rejected"),
        (409, "Lead already in other campaign", "rejected"),
        (405, '{"error": "Invalid email address"}', "rejected"),
    ],
)
def test_create_lead_per_lead_problems(key, sleeps, status, text, expected):
    respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        return_value=httpx.Response(status, text=text)
    )
    result = lemlist.LemlistProvider().create_lead(TARGET, {"email": "a@example.com"})
    assert result.status == expected and result.detail


@respx.mock
@pytest.mark.parametrize(
    ("status", "text", "code"),
    [
        (401, "The authentication you supplied is incorrect", "auth"),
        (403, "User linked to this API key is blocked", "auth"),
        (404, "No user found for this API key", "auth"),
        (404, "Campaign not found", "http"),
        (402, "pay up", "quota"),
        (500, "boom", "http"),
    ],
)
def test_create_lead_fatal_problems(key, sleeps, status, text, code):
    respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        return_value=httpx.Response(status, text=text)
    )
    with pytest.raises(ProviderError) as err:
        lemlist.LemlistProvider().create_lead(TARGET, {"email": "a@example.com"})
    assert err.value.code == code and KEY not in err.value.message


@respx.mock
def test_rate_limit_retries_with_retry_after(key, sleeps):
    respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, text="slow down"),
            httpx.Response(200, json={"_id": "lea_9"}),
        ]
    )
    assert lemlist.LemlistProvider().create_lead(TARGET, {"email": "a@example.com"}).lead_id == (
        "lea_9"
    )
    assert sleeps == [2.0]


# --- service ------------------------------------------------------------------------------------


FIRST = ("Aria", "Marcus", "Lena", "Omar", "Priya", "Chen", "Ines", "Tomas")
LAST = ("Tan", "Brooks", "Okafor", "Lindqvist")


def _build(n: int = 3, with_email: bool = True):
    c = make_campaign()
    cid = c["id"]
    co = add_company(cid)
    ids = []
    for i in range(n):
        name = FIRST[i % 8]
        pid = add_person(cid, co, name, LAST[i // 8])
        if with_email:
            add_email(pid, "verified", f"p{i}@northwind.example")
        body = message_row()["body"].replace("Aria", name)
        add_message(pid, "linkedin_message", body=body, ai_review="pass",
                    status="approved")
        if with_email:
            em = email_message()["body"].replace("Aria", name)
            add_message(pid, "email", body=em, ai_review="pass", status="approved")
        ids.append(pid)
    return cid, ids


@respx.mock
def test_dry_run_makes_no_http_call_and_previews_three(ready_db, key):
    cid, ids = _build(5)
    route = respx.post(url__regex=r".*").mock(return_value=httpx.Response(500))
    reply = push_to_lemlist(cid, TARGET)
    assert not route.called
    assert reply["dry_run"] is True and reply["ready"] == 5 and reply["to_push"] == 5
    assert len(reply["preview"]) == 3
    payload = reply["preview"][0]["payload"]
    assert payload["email"] == "p0@northwind.example"
    assert payload["companyDomain"] == "northwind.example"
    assert payload["companyName"] == COMPANY
    assert payload["greetingName"] == "Aria"
    for variable in ("designation", "linkedinMessage", "emailSubject", "emailBody"):
        assert payload[variable]
    assert "linkedinInvite" not in payload  # empty values are not sent
    assert "evidenceOrigin" not in payload
    assert KEY not in json.dumps(reply)


@respx.mock
def test_dry_run_without_key_is_fine(ready_db):
    cid, _ = _build(1)
    assert push_to_lemlist(cid, TARGET)["ok"] is True


@respx.mock
def test_confirm_is_required(ready_db, key):
    cid, _ = _build(2)
    route = respx.post(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
    reply = push_to_lemlist(cid, TARGET, dry_run=False)
    assert reply["needs_confirmation"] is True and not route.called


def test_real_run_without_key(ready_db):
    cid, _ = _build(1)
    reply = push_to_lemlist(cid, TARGET, dry_run=False, confirm=True)
    assert reply["ok"] is False and reply["error"]["code"] == "needs_key"


@respx.mock
def test_chunking_and_idempotency(ready_db, key, sleeps):
    cid, ids = _build(30)
    route = respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        return_value=httpx.Response(200, json={"_id": "lea_x"})
    )
    first = push_to_lemlist(cid, TARGET, dry_run=False, confirm=True)
    assert first["pushed"]["created"] == 25 and first["done"] is False
    assert first["next_cursor"] == str(ids[24])
    assert route.call_count == 25
    second = push_to_lemlist(cid, TARGET, dry_run=False, confirm=True, cursor=first["next_cursor"])
    assert second["pushed"]["created"] == 5 and second["done"] is True
    assert second["next_cursor"] is None and route.call_count == 30
    assert second["next_steps"]
    # run it all again: nothing is sent twice
    third = push_to_lemlist(cid, TARGET, dry_run=False, confirm=True)
    assert third["already_pushed"] == 30 and third["pushed"]["created"] == 0
    assert route.call_count == 30
    # a different lemlist campaign has its own log
    other = push_to_lemlist(cid, "cam_other", dry_run=True)
    assert other["already_pushed"] == 0 and other["to_push"] == 30
    # the log holds person ids and results only
    with db.session() as conn:
        value = db.query(conn, "SELECT value FROM settings WHERE key = ?",
                         (f"lemlist_push:{cid}:{TARGET}",))[0]["value"]
    assert "@" not in value and str(ids[0]) in json.loads(value)


@respx.mock
def test_duplicate_and_rejected_results(ready_db, key, sleeps):
    cid, ids = _build(3)
    respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        side_effect=[
            httpx.Response(400, text="Lead already in the campaign"),
            httpx.Response(400, text="Invalid email address"),
            httpx.Response(200, json={"_id": "lea_3"}),
        ]
    )
    reply = push_to_lemlist(cid, TARGET, dry_run=False, confirm=True)
    assert reply["pushed"] == {"created": 1, "duplicate": 1, "rejected": 1}
    again = push_to_lemlist(cid, TARGET)
    # the duplicate and the created one count as pushed; the rejected one can be retried
    assert again["already_pushed"] == 2 and again["to_push"] == 1


@respx.mock
def test_people_without_email_are_skipped(ready_db, key, sleeps):
    cid, ids = _build(3)
    with db.session() as conn:
        conn.execute("DELETE FROM emails WHERE person_id = ?", (ids[1],))
    route = respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        return_value=httpx.Response(200, json={"_id": "lea_1"})
    )
    dry = push_to_lemlist(cid, TARGET)
    assert dry["no_email_for_lemlist_api"] == 1 and dry["to_push"] == 2
    assert "CSV" in dry["no_email_hint"]
    reply = push_to_lemlist(cid, TARGET, dry_run=False, confirm=True)
    assert reply["pushed"]["created"] == 2 and route.call_count == 2


@respx.mock
def test_email_not_required_when_switched_off(ready_db, key, sleeps, monkeypatch):
    cid, ids = _build(2)
    with db.session() as conn:
        conn.execute("DELETE FROM emails WHERE person_id = ?", (ids[1],))
    monkeypatch.setattr(svc, "EMAIL_REQUIRED", False)
    assert push_to_lemlist(cid, TARGET)["to_push"] == 2


@respx.mock
def test_fatal_error_stops_the_chunk_and_keeps_progress(ready_db, key, sleeps):
    cid, ids = _build(4)
    respx.post(f"{API}/campaigns/{TARGET}/leads/").mock(
        side_effect=[
            httpx.Response(200, json={"_id": "lea_1"}),
            httpx.Response(401, text="The authentication you supplied is incorrect"),
        ]
    )
    reply = push_to_lemlist(cid, TARGET, dry_run=False, confirm=True)
    assert reply["ok"] is False and reply["error"]["code"] == "lemlist_auth"
    assert reply["pushed_so_far"]["created"] == 1 and reply["next_cursor"] == str(ids[0])
    assert push_to_lemlist(cid, TARGET)["already_pushed"] == 1


def test_only_approved_leads_are_pushed(ready_db, key):
    cid, ids = _build(2)
    with db.session() as conn:
        conn.execute("UPDATE messages SET ai_review = '' WHERE person_id = ?", (ids[0],))
    reply = push_to_lemlist(cid, TARGET)
    assert reply["ready"] == 1 and reply["blocked_by_code"].get("M16") == 2


def test_invalid_arguments(ready_db):
    cid, _ = _build(1)
    for bad in ("", "cam/../x", "a b", "x" * 65):
        with pytest.raises(LemlistError) as err:
            push_to_lemlist(cid, bad)
        assert err.value.code == "invalid_value"
    with pytest.raises(LemlistError) as err:
        push_to_lemlist(cid, TARGET, cursor="abc")
    assert err.value.code == "invalid_cursor" and err.value.as_response()["ok"] is False


@respx.mock
def test_list_lemlist_campaigns_service(key, sleeps):
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(200, json=[{"_id": "cam_1", "name": "Test", "status": "draft"}])
    )
    assert list_lemlist_campaigns()[0]["id"] == "cam_1"
    respx.get(f"{API}/campaigns").mock(return_value=httpx.Response(401, text="no"))
    with pytest.raises(LemlistError) as err:
        list_lemlist_campaigns()
    assert err.value.code == "lemlist_auth"


def test_list_lemlist_campaigns_needs_key():
    with pytest.raises(LemlistError) as err:
        list_lemlist_campaigns()
    assert err.value.code == "needs_key"

