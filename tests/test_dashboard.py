"""The dashboard API and static page, through FastAPI's TestClient (no network, invented data)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from dashboard_world import SECRET, seed
from fastapi.testclient import TestClient

from leadgen.core import config, paths, secrets
from leadgen.dashboard import app as dash
from leadgen.services import browse, mailbox

SERPER_URL = "https://google.serper.dev/search"
OK_BODY = {"organic": [{"title": "T", "link": "https://example.com/", "position": 1}]}
BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}


@pytest.fixture
def world():
    return seed()


@pytest.fixture
def client(world):
    return TestClient(dash.create_app(), base_url=BASE)


def get(client, path):
    reply = client.get(path)
    assert reply.status_code == 200, reply.text
    return reply.json()


def post(client, path, body=None, method="POST", **kw):
    headers = {**CSRF, **kw.pop("headers", {})}
    return client.request(method, path, json=body, headers=headers, **kw)


# --- the page ------------------------------------------------------------------------------------


def test_index_and_static_files_are_served_offline(client):
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "/static/app.js" in page.text and "/static/app.css" in page.text
    assert "http://" not in page.text and "https://" not in page.text  # no outside resources
    js = client.get("/static/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "innerHTML" not in js.text  # server text is only ever put on the page as text
    css = client.get("/static/app.css")
    assert css.status_code == 200 and "prefers-color-scheme: dark" in css.text
    assert client.get("/static/missing.js").status_code == 404
    assert client.get("/nope").status_code == 404


def test_security_headers_on_every_reply(client):
    for path in ("/", "/api/ping", "/static/app.css", "/api/campaigns/999"):
        headers = client.get(path).headers
        assert "script-src 'self'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/ping").headers["cache-control"] == "no-store"


def test_package_data_includes_the_static_files():
    import tomllib
    from pathlib import Path

    root = Path(dash.__file__).parents[3]
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "dashboard/static/*" in meta["tool"]["setuptools"]["package-data"]["leadgen"]
    deps = " ".join(meta["project"]["dependencies"])
    assert "fastapi" in deps and "uvicorn" in deps
    for name in ("index.html", "app.js", "app.css"):
        assert (dash.STATIC_DIR / name).is_file()


# --- host and CSRF protection --------------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "testserver"])
def test_foreign_host_headers_are_refused(world, host):
    foreign = TestClient(dash.create_app(), base_url=f"http://{host}")
    reply = foreign.get("/api/ping")
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "bad_host"


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765", "localhost"])
def test_loopback_host_headers_are_accepted(world, host):
    assert dash._host_ok(host)
    assert not dash._host_ok("")


POSTS = [
    ("POST", "/api/providers/serper/key", {"key": "abc"}),
    ("DELETE", "/api/providers/serper/key", None),
    ("POST", "/api/providers/serper/test", None),
    ("POST", "/api/campaigns/1/companies/status", {"ids": [1], "status": "approved"}),
    ("POST", "/api/campaigns/1/leads/status", {"ids": [1], "status": "removed"}),
    ("POST", "/api/campaigns/1/qa/run", None),
    ("POST", "/api/campaigns/1/exports", {"format": "generic"}),
    ("POST", "/api/campaigns/1/exports/open-folder", None),
    ("POST", "/api/sending/pause", None),
    ("POST", "/api/sending/resume", None),
    ("POST", "/api/mailbox/sync", None),
    ("POST", "/api/mailbox/gmail", {"address": "a@example.com", "app_password": "x"}),
    ("POST", "/api/mailbox/other", {"host": "h", "port": "25", "address": "a", "password": "p"}),
    ("POST", "/api/mailbox/test", None),
    ("POST", "/api/suppression", {"kind": "email", "values": ["a@example.com"]}),
    ("POST", "/api/settings", {"locale": "en-US"}),
]


@pytest.mark.parametrize(("method", "path", "body"), POSTS)
def test_changes_without_the_header_are_forbidden(client, method, path, body):
    reply = client.request(method, path, json=body)
    assert reply.status_code == 403 and reply.json()["error"]["code"] == "csrf"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "http://127.0.0.1:9999"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_changes_from_another_origin_are_forbidden(client, headers):
    reply = post(client, "/api/sending/pause", headers=headers)
    assert reply.status_code == 403
    assert paths.settings_path().exists() is False or not config.load_settings().sending.paused


def test_same_origin_changes_are_allowed(client):
    reply = post(
        client, "/api/sending/pause", headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"}
    )
    assert reply.status_code == 200 and reply.json()["paused"] is True


def test_wrong_header_value_and_form_posts_are_refused(client):
    assert client.post("/api/sending/pause", headers={"X-LeadGen": "0"}).status_code == 403
    form = {**CSRF, "Content-Type": "application/x-www-form-urlencoded"}
    reply = client.post("/api/settings", content="locale=en-US", headers=form)
    assert reply.status_code in (400, 415, 422) and not config.load_settings().locale == "en-US"


# --- reads ---------------------------------------------------------------------------------------


def test_ping_and_status(client):
    ping = get(client, "/api/ping")
    assert ping["app"] == "leadgen-dashboard" and ping["ok"]
    status = get(client, "/api/status")
    assert status["workspace"] == str(paths.home())
    assert status["campaigns"][0]["counts"]["people"]["confirmed"] == 3
    assert status["next_step"]["message"]


def test_campaigns_and_overview(client, world):
    campaigns = get(client, "/api/campaigns")["campaigns"]
    assert len(campaigns) == 1 and campaigns[0]["counts"]["messages"]["qa_fail"] == 2
    overview = get(client, f"/api/campaigns/{world['campaign']}")
    assert overview["campaign"]["id"] == world["campaign"]
    assert overview["spot_check"]["ai_leads"] == 1 and "search_tasks" in overview
    missing = client.get("/api/campaigns/999")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_companies_leads_messages_qa_reads(client, world):
    cid = world["campaign"]
    companies = get(client, f"/api/campaigns/{cid}/companies?status=approved")
    assert companies["total"] == 2 and companies["counts"]["approved"] == 2
    assert client.get(f"/api/campaigns/{cid}/companies?status=bogus").status_code == 400
    leads = get(client, f"/api/campaigns/{cid}/leads?qa=block&search=ben")
    assert [x["raw_name"] for x in leads["leads"]] == ["Ben Carter"]
    assert client.get(f"/api/campaigns/{cid}/leads?qa=bogus").status_code == 400
    assert get(client, f"/api/campaigns/{cid}/lead-filters")["designations"]
    detail = get(client, f"/api/leads/{world['aria']}")
    assert detail["person"]["raw_name"] == "Aria Tan" and detail["messages"]
    assert client.get("/api/leads/9999").status_code == 404
    messages = get(client, f"/api/campaigns/{cid}/messages?channel=email")
    assert messages["total"] == 1
    qa = get(client, f"/api/campaigns/{cid}/qa?severity=block")
    assert qa["total"] > 0 and qa["issues"][0]["severity"] == "block"
    assert client.get(f"/api/campaigns/{cid}/qa?severity=loud").status_code == 400


def test_analytics_replies_sending_suppression_settings_reads(client, world):
    cid = world["campaign"]
    stats = get(client, f"/api/analytics?campaign_id={cid}&group_by=designation")
    assert stats["totals"]["sent"] == 1 and stats["groups"]
    assert get(client, "/api/analytics")["totals"]["replied"] == 1
    assert client.get("/api/analytics?group_by=moon").status_code == 400
    assert get(client, f"/api/replies?campaign_id={cid}")["events"][0]["type"] == "reply"
    queue = get(client, "/api/sending")
    assert queue["paused"] is False and "worker" in queue
    assert get(client, "/api/suppression")["entries"] == []
    prefs = get(client, "/api/settings")
    assert prefs["locale"] == "en-GB" and "sender" in prefs


def test_exports_listing(client, world):
    reply = get(client, f"/api/campaigns/{world['campaign']}/exports")
    assert reply["files"] == [] and reply["folder"].startswith(str(paths.home()))


# --- changes -------------------------------------------------------------------------------------


def test_set_company_and_person_status(client, world):
    cid = world["campaign"]
    out = post(
        client,
        f"/api/campaigns/{cid}/companies/status",
        {"ids": [world["company_a"], 999], "status": "excluded", "reason": "too big"},
    )
    assert (
        out.status_code == 200 and out.json()["updated"] == 1 and out.json()["not_found"] == [999]
    )
    assert (
        post(
            client, f"/api/campaigns/{cid}/companies/status", {"ids": [1], "status": "nope"}
        ).status_code
        == 400
    )
    out = post(
        client,
        f"/api/campaigns/{cid}/leads/status",
        {"ids": [world["cara"]], "status": "confirmed"},
    )
    assert out.status_code == 400 and out.json()["error"]["code"] == "reason_required"
    out = post(
        client,
        f"/api/campaigns/{cid}/leads/status",
        {"ids": [world["cara"]], "status": "confirmed", "reason": "checked the profile"},
    )
    assert out.status_code == 200
    assert get(client, f"/api/leads/{world['cara']}")["person"]["status"] == "confirmed"


def test_run_qa_and_export(client, world):
    cid = world["campaign"]
    assert post(client, f"/api/campaigns/{cid}/qa/run").json()["ok"]
    out = post(
        client, f"/api/campaigns/{cid}/exports", {"format": "generic", "include": "approved"}
    )
    body = out.json()
    assert out.status_code == 200 and body["ok"] and "warnings" in body
    listing = get(client, f"/api/campaigns/{cid}/exports")
    assert "qa_report.csv" in [f["name"] for f in listing["files"]]
    bad = post(client, f"/api/campaigns/{cid}/exports", {"format": "pdf"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_value"


def test_open_folder_calls_the_service(client, world, monkeypatch):
    seen = []
    monkeypatch.setattr(browse, "_platform", lambda: "win32")
    monkeypatch.setattr(browse, "_startfile", seen.append)
    out = post(client, f"/api/campaigns/{world['campaign']}/exports/open-folder")
    assert out.json()["ok"] and len(seen) == 1


def test_settings_round_trip_and_validation(client):
    out = post(
        client,
        "/api/settings",
        {
            "locale": "en-AU",
            "budgets": {"search": 50},
            "sender": {"name": "Priya Nair"},
            "strict_review": False,
        },
    )
    body = out.json()
    assert (
        out.status_code == 200
        and body["locale"] == "en-AU"
        and body["sender"]["name"] == "Priya Nair"
    )
    assert config.load_settings().budgets.search == 50
    bad = post(client, "/api/settings", {"locale": "klingon"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_value"
    unknown = post(client, "/api/settings", {"sending": {"paused": True}})
    assert unknown.status_code == 422
    assert post(client, "/api/settings", {"budgets": {"search": "lots"}}).status_code == 422


def test_pause_resume_suppression_and_mailbox_sync(client, monkeypatch):
    assert post(client, "/api/sending/pause").json()["paused"] is True
    assert get(client, "/api/sending")["paused"] is True
    assert post(client, "/api/sending/resume").json()["paused"] is False
    out = post(
        client, "/api/suppression", {"kind": "email", "values": ["No.Thanks@Example.org", "x"]}
    )
    assert out.json()["added"] == ["no.thanks@example.org"] and out.json()["invalid"]
    assert (
        get(client, "/api/suppression?kind=email")["entries"][0]["value"] == "no.thanks@example.org"
    )
    assert post(client, "/api/suppression", {"kind": "phone", "values": ["1"]}).status_code == 422

    monkeypatch.setattr(
        mailbox, "sync_mailbox", lambda: {"ok": True, "done": True, "message": "ok"}
    )
    assert post(client, "/api/mailbox/sync").json()["done"] is True

    def refuse():
        raise mailbox.MailboxSyncError("not_configured", "No mailbox is set up.", "Run it.")

    monkeypatch.setattr(mailbox, "sync_mailbox", refuse)
    reply = post(client, "/api/mailbox/sync")
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "not_configured"


# --- keys never come back ------------------------------------------------------------------------


@respx.mock
def test_save_key_returns_only_the_masked_key(client, sleeps):
    respx.post(SERPER_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    out = post(client, "/api/providers/serper/key", {"key": SECRET})
    assert out.status_code == 200 and out.json()["masked_key"] == "…cdef"
    assert SECRET not in out.text
    assert secrets.get("SERPER_API_KEY") == SECRET
    providers = client.get("/api/providers")
    assert SECRET not in providers.text
    serper = next(p for p in providers.json()["providers"] if p["key"] == "serper")
    assert serper["configured"] is True and serper["masked_key"] == "…cdef"
    assert SECRET not in client.get("/api/status").text


@respx.mock
def test_a_key_that_fails_its_test_is_not_kept_or_echoed(client, sleeps):
    respx.post(SERPER_URL).mock(return_value=httpx.Response(403))
    out = post(client, "/api/providers/serper/key", {"key": SECRET})
    assert out.status_code == 400 and out.json()["ok"] is False
    assert SECRET not in out.text and secrets.get("SERPER_API_KEY") == ""


def test_invalid_key_requests_never_echo_the_body(client):
    for body in ({"key": SECRET, "extra": 1}, {"key": ["x", SECRET]}, {"key": SECRET * 100}):
        out = post(client, "/api/providers/serper/key", body)
        assert out.status_code == 422 and SECRET not in out.text
        assert "input" not in out.json()
    unknown = post(client, "/api/providers/nope/key", {"key": SECRET})
    assert unknown.status_code == 400 and SECRET not in unknown.text
    assert secrets.get("SERPER_API_KEY") == ""


def test_delete_key(client):
    secrets.set("SERPER_API_KEY", SECRET)
    out = post(client, "/api/providers/serper/key", method="DELETE")
    assert out.json()["removed"] is True and out.json()["configured"] is False
    assert SECRET not in out.text and secrets.get("SERPER_API_KEY") == ""
    again = post(client, "/api/providers/serper/key", method="DELETE").json()
    assert again["ok"] and again["removed"] is False
    assert post(client, "/api/providers/smtp/key", method="DELETE").status_code == 400
    assert post(client, "/api/providers/nope/key", method="DELETE").status_code == 400


def test_delete_key_notes_a_key_still_set_in_the_environment(client, monkeypatch):
    secrets.set("SERPER_API_KEY", SECRET)
    monkeypatch.setenv("SERPER_API_KEY", "env-key-0000")
    out = post(client, "/api/providers/serper/key", method="DELETE").json()
    assert out["configured"] is True and "environment" in out["message"]
    assert "env-key" not in json.dumps(out)


@respx.mock
def test_test_saved_key(client, sleeps):
    secrets.set("SERPER_API_KEY", SECRET)
    respx.post(SERPER_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    out = post(client, "/api/providers/serper/test")
    assert out.status_code == 200 and out.json()["ok"] and SECRET not in out.text
    assert post(client, "/api/providers/nope/test").status_code == 400
    assert post(client, "/api/providers/hunter/test").json()["ok"] is False  # not configured


# --- errors never leak ---------------------------------------------------------------------------


def test_unexpected_errors_give_a_safe_reply_and_a_log_line(client, monkeypatch, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("password=hunter2 jane@example.org")

    monkeypatch.setattr(browse, "list_messages", boom)
    with caplog.at_level("ERROR", logger="leadgen.dashboard"):
        reply = client.get("/api/campaigns/1/messages")
    assert reply.status_code == 500 and reply.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in reply.text and "hunter2" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_broken_settings_file_is_reported_plainly(client):
    paths.settings_path().write_text("budgets:\n  search: lots\n", encoding="utf-8")
    reply = client.get("/api/settings")
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "settings_problem"


def test_provider_errors_become_replies(client, monkeypatch):
    from leadgen.providers.base import ProviderError
    from leadgen.services import status

    def fail():
        raise ProviderError("auth", "The key was refused.")

    monkeypatch.setattr(status, "list_providers", fail)
    reply = client.get("/api/providers")
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "provider_auth"


# --- serving -------------------------------------------------------------------------------------


def test_serve_refuses_a_public_address(world, capsys):
    settings = config.load_settings()
    settings.dashboard.host = "0.0.0.0"
    config.save_settings(settings)
    assert dash.serve() == 1
    assert "only runs on this computer" in capsys.readouterr().out
