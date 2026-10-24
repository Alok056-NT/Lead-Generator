"""The "Connect your mailbox" help page and its API (T27).

smtplib and imaplib are replaced by fakes (no network); every address and password is invented.
"""

from __future__ import annotations

import imaplib
import smtplib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from leadgen.core import secrets
from leadgen.dashboard import app as dash

BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}
STATIC = Path(dash.STATIC_DIR)
ADDRESS = "jordan.reyes@example.com"
GROUPED = "qzxw  vbnm hjkl pstr"  # as copied from Google: groups, a double space, a no-break space
CLEAN = "qzxwvbnmhjklpstr"


class FakeSMTP:
    """Records the conversation; `fail_login` makes the log-in step raise."""

    instances: list[FakeSMTP] = []
    fail_login: Exception | None = None
    fail_connect: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSMTP.fail_connect:
            raise FakeSMTP.fail_connect
        self.host, self.port, self.calls = host, port, ["connect"]
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append("login")
        self.login_with = (user, password)
        if FakeSMTP.fail_login:
            raise FakeSMTP.fail_login

    def send_message(self, *args, **kwargs):
        self.calls.append("send")

    def quit(self):
        self.calls.append("quit")

    def close(self):
        self.calls.append("close")


class FakeIMAP:
    fail: Exception | None = None
    instances: list[FakeIMAP] = []

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.host, self.port = host, port
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        if FakeIMAP.fail:
            raise FakeIMAP.fail

    def logout(self):
        pass


@pytest.fixture(autouse=True)
def fake_servers(monkeypatch):
    FakeSMTP.instances, FakeSMTP.fail_login, FakeSMTP.fail_connect = [], None, None
    FakeIMAP.instances, FakeIMAP.fail = [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)


@pytest.fixture
def client(ready_db):
    return TestClient(dash.create_app(), base_url=BASE)


def post(client, path, body=None, headers=CSRF):
    return client.post(path, json=body, headers=headers)


def gmail(client, address=ADDRESS, password=GROUPED):
    return post(client, "/api/mailbox/gmail", {"address": address, "app_password": password})


def bad_login():
    return smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")


# --- Gmail: save and test through the existing mailbox code ---------------------------------


def test_gmail_is_saved_with_spaces_removed_and_the_servers_filled_in(client):
    reply = gmail(client)
    assert reply.status_code == 200 and reply.json()["ok"]
    assert (
        reply.json()["message"].startswith("Connected.")
        and "Nothing was sent" in reply.json()["message"]
    )
    assert secrets.get("SMTP_HOST") == "smtp.gmail.com" and secrets.get("SMTP_PORT") == "587"
    assert secrets.get("IMAP_HOST") == "imap.gmail.com"
    assert secrets.get("SMTP_USER") == ADDRESS and secrets.get("SMTP_PASSWORD") == CLEAN
    smtp = FakeSMTP.instances[0]
    assert (
        smtp.login_with == (ADDRESS, CLEAN) and "send" not in smtp.calls
    )  # the test sends nothing
    assert FakeIMAP.instances and FakeIMAP.instances[0].host == "imap.gmail.com"


def test_the_password_is_never_returned_only_its_last_four_characters(client):
    reply = gmail(client)
    state = client.get("/api/mailbox").json()
    for body in (reply.text, client.get("/api/mailbox").text):
        assert CLEAN not in body and GROUPED not in body and "qzxw" not in body
    assert state["configured"] and state["address"] == ADDRESS and state["kind"] == "gmail"
    assert state["password"] == "…" + CLEAN[-4:] and state["imap_host"] == "imap.gmail.com"
    assert "app_password" not in state and "SMTP_PASSWORD" not in str(state)


def test_the_state_is_empty_before_anything_is_saved(client):
    state = client.get("/api/mailbox").json()
    assert state["ok"] and state["configured"] is False and state["password"] == ""


# --- the two most common failures, in plain English -------------------------------------------


def test_a_wrong_app_password_gets_a_plain_message_and_nothing_is_kept(client):
    secrets.set("SMTP_HOST", "old.example.com")
    secrets.set("SMTP_USER", "old@example.com")
    FakeSMTP.fail_login = bad_login()
    reply = gmail(client)
    error = reply.json()["error"]
    assert reply.status_code == 400 and error["code"] == "wrong_password"
    assert "Google did not accept that address and password" in error["message"]
    assert (
        "16-letter app password" in error["message"]
        and "make a new app password" in error["message"]
    )
    assert "535" not in reply.text and CLEAN not in reply.text and "5.7.8" not in reply.text
    assert secrets.get("SMTP_HOST") == "old.example.com" and secrets.get("SMTP_PASSWORD") == ""


def test_app_passwords_not_allowed_points_to_the_administrator_or_another_mailbox(client):
    FakeSMTP.fail_login = smtplib.SMTPAuthenticationError(
        534, b"5.7.9 Application-specific password required"
    )
    error = gmail(client).json()["error"]
    assert error["code"] == "wrong_password"
    assert "work or school accounts" in error["hint"] and "Advanced Protection" in error["hint"]
    assert "ask your administrator or use another mailbox" in error["hint"]
    assert not any(ch in error["hint"] for ch in "_()=")  # the page hides hints that look like code


def test_a_password_of_the_wrong_length_is_mentioned_after_a_refused_login(client):
    FakeSMTP.fail_login = bad_login()
    message = gmail(client, password="my old pass").json()["error"]["message"]
    assert "16 letters long" in message
    assert "16 letters long" not in gmail(client).json()["error"]["message"]


def test_inbox_access_off_is_explained_for_workspace(client):
    FakeIMAP.fail = imaplib.IMAP4.error("[ALERT] IMAP access is disabled for your domain")
    error = gmail(client).json()["error"]
    assert error["code"] == "reading_failed" and "Sending works" in error["message"]
    assert "administrator" in error["hint"] and "IMAP" in error["hint"]
    assert secrets.get("SMTP_PASSWORD") == ""  # not kept


def test_no_connection_is_explained(client):
    FakeSMTP.fail_connect = OSError("getaddrinfo failed")
    error = gmail(client).json()["error"]
    assert error["code"] == "cannot_reach" and "Could not reach the mail server" in error["message"]
    assert "getaddrinfo" not in str(error)


# --- bad input is described, never echoed ------------------------------------------------------


@pytest.mark.parametrize(
    "address", ["", "no-at-sign", "a b@example.com", "x@y", "<b>x</b>@example.com a"]
)
def test_a_bad_address_is_refused_without_echoing_it(client, address):
    reply = gmail(client, address=address)
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "invalid_address"
    assert address not in reply.text or address == ""
    assert not FakeSMTP.instances


def test_an_empty_or_odd_password_is_refused_without_a_login(client):
    empty = gmail(client, password="    ")
    assert empty.json()["error"]["code"] == "missing_password"
    odd = gmail(client, password="pässwörd1234567")
    assert odd.status_code == 400 and odd.json()["error"]["code"] == "invalid_password"
    assert "pässwörd" not in odd.text and not FakeSMTP.instances


def test_unknown_fields_and_huge_values_are_refused_and_not_echoed(client):
    extra = post(
        client, "/api/mailbox/gmail", {"address": ADDRESS, "app_password": CLEAN, "x": "SECRETBIT"}
    )
    assert extra.status_code == 422 and "SECRETBIT" not in extra.text and CLEAN not in extra.text
    huge = post(client, "/api/mailbox/gmail", {"address": ADDRESS, "app_password": "p" * 400})
    assert huge.status_code == 422 and "pppp" not in huge.text


# --- the test button ---------------------------------------------------------------------------


def test_the_test_button_needs_a_saved_mailbox(client):
    reply = post(client, "/api/mailbox/test")
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "not_configured"
    assert "step 3" in reply.json()["error"]["hint"]


def test_the_test_button_logs_in_and_sends_nothing(client):
    gmail(client)
    FakeSMTP.instances.clear()
    reply = post(client, "/api/mailbox/test")
    assert reply.json()["ok"] and "The mailbox works" in reply.json()["message"]
    assert CLEAN not in reply.text and reply.json()["mailbox"]["password"] == "…" + CLEAN[-4:]
    assert FakeSMTP.instances and all("send" not in s.calls for s in FakeSMTP.instances)


def test_the_test_button_explains_a_password_that_stopped_working(client):
    gmail(client)
    FakeSMTP.fail_login = bad_login()
    error = post(client, "/api/mailbox/test").json()["error"]
    assert error["code"] == "wrong_password" and "Google did not accept" in error["message"]
    assert secrets.get("SMTP_PASSWORD") == CLEAN  # testing never changes what is saved


# --- Outlook or another mailbox: the manual fields ---------------------------------------------


def other(client, **over):
    body = {"host": "smtp.mail.example.com", "port": "587", "address": ADDRESS,
            "password": "not-a-real-password", "imap_host": ""}  # fmt: skip
    return post(client, "/api/mailbox/other", {**body, **over})


def test_another_mailbox_is_saved_with_the_usual_incoming_server(client):
    reply = other(client)
    assert reply.json()["ok"] and secrets.get("SMTP_HOST") == "smtp.mail.example.com"
    assert secrets.get("IMAP_HOST") == "imap.mail.example.com" and secrets.get("SMTP_PORT") == "587"
    state = client.get("/api/mailbox").json()
    assert state["kind"] == "other" and state["password"] == "…word"
    assert "not-a-real-password" not in reply.text


def test_another_mailbox_failure_is_plain_and_keeps_nothing(client):
    FakeSMTP.fail_login = bad_login()
    error = other(client).json()["error"]
    assert error["code"] == "wrong_password" and error["message"].startswith(
        "The mailbox did not accept"
    )
    assert secrets.get("SMTP_HOST") == ""


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"host": ""}, "missing_fields"),
        ({"password": ""}, "missing_fields"),
        ({"host": "smtp example.com"}, "invalid_server"),
        ({"host": "smtp.example.com/x"}, "invalid_server"),
        ({"imap_host": "bad host"}, "invalid_server"),
        ({"port": "abc"}, "invalid_port"),
        ({"port": "0"}, "invalid_port"),
        ({"port": "70000"}, "invalid_port"),
        ({"password": "café-pass"}, "invalid_password"),
    ],
)
def test_bad_manual_fields_are_refused_in_plain_words(client, over, code):
    reply = other(client, **over)
    assert reply.status_code == 400 and reply.json()["error"]["code"] == code
    assert not FakeSMTP.instances
    for value in over.values():
        assert not value or value not in reply.text


# --- the safety model of the dashboard still holds -------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/mailbox/gmail", {"address": ADDRESS, "app_password": CLEAN}),
        ("/api/mailbox/other", {"host": "h", "port": "587", "address": "a@b.cc", "password": "p"}),
        ("/api/mailbox/test", None),
    ],
)
def test_every_change_needs_the_header_and_the_same_origin(client, path, body):
    refused = client.post(path, json=body)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "csrf"
    foreign = client.post(path, json=body, headers={**CSRF, "Origin": "http://evil.example"})
    assert foreign.status_code == 403
    assert secrets.get("SMTP_PASSWORD") == "" and not FakeSMTP.instances


def test_the_mailbox_answers_are_not_cached_and_carry_the_security_headers(client):
    reply = client.get("/api/mailbox")
    assert reply.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in reply.headers["content-security-policy"]


# --- the page and the links to it --------------------------------------------------------------


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start : source.index("\n}\n", start)]


def test_the_page_has_the_three_steps_and_both_google_buttons():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    page = _function(js, "pageMailbox")
    assert "coming soon" not in js and "next update" not in js
    for text in ("Turn on 2-Step Verification", 'Make an app password called "Lead Generator"',
                 "Paste your email address and the 16 letters here",
                 "Outlook or another mailbox", "does not need it"):  # fmt: skip
        assert text in page, text
    assert "https://myaccount.google.com/signinoptions/two-step-verification" in js
    assert "https://myaccount.google.com/apppasswords" in js
    assert "'/api/mailbox/gmail'" in page and "'/api/mailbox/other'" in page
    assert "noopener noreferrer" in _function(js, "googleButton")


def test_home_settings_and_the_send_section_link_to_the_page():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for name in ("pageHome", "pageSettings", "pageExport"):
        assert "href: '#/mailbox'" in _function(js, name), name
    assert "innerHTML" not in js  # server text is only ever put on the page as text
