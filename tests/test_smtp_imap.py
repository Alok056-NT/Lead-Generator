"""The SMTP/IMAP mailbox and the footer. smtplib and imaplib are replaced by fakes: no network."""

from __future__ import annotations

import imaplib
import smtplib

import pytest

from leadgen.core import secrets
from leadgen.core.config import Settings, save_settings
from leadgen.send import smtp_imap
from leadgen.send.footer import OPT_OUT_LINE, footer, with_footer
from leadgen.send.smtp_imap import MailboxError, SmtpImapMailbox, default_imap_host

PASSWORD = "app-password-not-for-logs"


def mailbox(**over) -> SmtpImapMailbox:
    values = {
        "host": "smtp.mail.example",
        "port": 587,
        "user": "priya@brightharbour.example",
        "password": PASSWORD,
        "from_name": "Priya Nair",
        "from_email": "priya@brightharbour.example",
    }
    values.update(over)
    return SmtpImapMailbox(**values)


class FakeSMTP:
    """Records the conversation; `plan` makes chosen steps raise."""

    instances: list[FakeSMTP] = []
    plan: dict[str, Exception] = {}
    ssl = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.sent = []
        FakeSMTP.instances.append(self)
        self._maybe("connect")

    def _maybe(self, step):
        self.calls.append(step)
        if step in FakeSMTP.plan:
            raise FakeSMTP.plan[step]

    def ehlo(self):
        self._maybe("ehlo")

    def starttls(self, context=None):
        self._maybe("starttls")

    def login(self, user, password):
        self._maybe("login")

    def send_message(self, msg, to_addrs=None):
        self._maybe("send")
        self.sent.append((msg, to_addrs))

    def quit(self):
        self.calls.append("quit")

    def close(self):
        self.calls.append("close")


class FakeSMTPSSL(FakeSMTP):
    ssl = True


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.plan = {}
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


class FakeIMAP:
    fail: Exception | None = None
    instances: list[FakeIMAP] = []

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.host, self.port = host, port
        self.logged_out = False
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        if FakeIMAP.fail:
            raise FakeIMAP.fail

    def logout(self):
        self.logged_out = True


@pytest.fixture
def imap(monkeypatch):
    FakeIMAP.fail = None
    FakeIMAP.instances = []
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


# --- the message ------------------------------------------------------------------------------


def test_message_is_plain_text_with_one_recipient_and_the_unsubscribe_header():
    msg = mailbox().build_message("aria.tan@northwind.example", "Hello", "Hi Aria,\n\nBody.")
    assert msg["To"] == "aria.tan@northwind.example"
    assert msg["From"] == "Priya Nair <priya@brightharbour.example>"
    assert msg["Subject"] == "Hello"
    assert msg["Cc"] is None and msg["Bcc"] is None
    assert msg["List-Unsubscribe"] == "<mailto:priya@brightharbour.example?subject=unsubscribe>"
    assert msg["Message-ID"].endswith("@brightharbour.example>")
    assert msg.get_content_type() == "text/plain"
    assert not msg.is_multipart()


def test_display_name_with_special_characters_is_quoted():
    msg = mailbox(from_name="Nair, Priya").build_message("a@northwind.example", "Hi", "Body")
    assert msg["From"].startswith('"Nair, Priya" <')


@pytest.mark.parametrize(
    "to",
    [
        "a@x.example, b@x.example",
        "a@x.example;b@x.example",
        "Aria <a@x.example>",
        "nobody",
        "",
        "a@x.example\nBcc: b@x.example",
    ],
)
def test_only_one_plain_address_is_accepted(to):
    with pytest.raises(MailboxError) as err:
        mailbox().build_message(to, "Hi", "Body")
    assert err.value.code == "invalid_message"


def test_newlines_in_subject_or_headers_are_refused():
    box = mailbox()
    with pytest.raises(MailboxError):
        box.build_message("a@x.example", "Hi\nBcc: b@x.example", "Body")
    with pytest.raises(MailboxError):
        box.build_message("a@x.example", "Hi", "Body", {"X-Test": "a\r\nBcc: b@x.example"})


def test_extra_headers_cannot_add_recipients_or_change_the_sender():
    msg = mailbox().build_message(
        "a@x.example",
        "Hi",
        "Body",
        {"Cc": "b@x.example", "Bcc": "c@x.example", "X-Run": "1", "From": "evil@x.example"},
    )
    assert msg["Cc"] is None and msg["Bcc"] is None
    assert msg["X-Run"] == "1"
    assert "evil" not in msg["From"]


def test_missing_sender_address_is_a_not_configured_error():
    with pytest.raises(MailboxError) as err:
        mailbox(user="", from_email="").build_message("a@x.example", "Hi", "Body")
    assert err.value.code == "not_configured"


# --- sending ----------------------------------------------------------------------------------


def test_send_uses_starttls_login_and_one_recipient(smtp):
    result = mailbox().send("aria.tan@northwind.example", "Hello", "Body")
    conn = smtp.instances[0]
    assert conn.calls == ["connect", "ehlo", "starttls", "ehlo", "login", "send", "quit"]
    msg, to_addrs = conn.sent[0]
    assert to_addrs == ["aria.tan@northwind.example"]
    assert result.message_id == msg["Message-ID"]


def test_port_465_uses_implicit_tls(smtp):
    mailbox(port=465).send("a@northwind.example", "Hello", "Body")
    conn = smtp.instances[0]
    assert conn.ssl and "starttls" not in conn.calls and "send" in conn.calls


def test_a_server_without_starttls_is_refused_before_login(smtp):
    smtp.plan["starttls"] = smtplib.SMTPNotSupportedError("STARTTLS extension not supported")
    with pytest.raises(MailboxError) as err:
        mailbox().send("a@northwind.example", "Hello", "Body")
    assert err.value.code == "connect"
    assert "login" not in smtp.instances[0].calls


@pytest.mark.parametrize(
    ("step", "exc", "code"),
    [
        ("login", smtplib.SMTPAuthenticationError(535, b"5.7.8 not accepted"), "auth"),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"a@northwind.example": (550, b"no such user")}),
            "recipient_refused",
        ),
        ("send", smtplib.SMTPDataError(554, b"message rejected as spam"), "smtp"),
        ("send", smtplib.SMTPSenderRefused(550, b"sender blocked", "x@y.example"), "smtp"),
        ("connect", OSError("timed out"), "connect"),
        ("connect", smtplib.SMTPConnectError(421, b"busy"), "connect"),
        ("send", smtplib.SMTPServerDisconnected("gone"), "connect"),
    ],
)
def test_errors_are_classified_and_never_contain_the_password(smtp, step, exc, code):
    smtp.plan[step] = exc
    with pytest.raises(MailboxError) as err:
        mailbox().send("a@northwind.example", "Hello", "Body")
    assert err.value.code == code
    assert PASSWORD not in err.value.message


def test_unconfigured_mailbox_does_not_connect(smtp):
    with pytest.raises(MailboxError) as err:
        mailbox(password="").send("a@northwind.example", "Hello", "Body")
    assert err.value.code == "not_configured"
    assert smtp.instances == []


def test_connection_is_closed_after_a_failed_send(smtp):
    smtp.plan["send"] = smtplib.SMTPDataError(554, b"no")
    with pytest.raises(MailboxError):
        mailbox().send("a@northwind.example", "Hello", "Body")
    assert smtp.instances[0].calls[-1] == "quit"


# --- test() -----------------------------------------------------------------------------------


def test_test_checks_smtp_and_imap_without_sending(smtp, imap):
    ok, message = mailbox().test()
    assert ok and "both work" in message
    assert "send" not in smtp.instances[0].calls
    assert imap.instances[0].host == "imap.mail.example" and imap.instances[0].logged_out


def test_test_reports_a_smtp_login_failure(smtp, imap):
    smtp.plan["login"] = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    ok, message = mailbox().test()
    assert not ok and "SMTP login failed" in message and PASSWORD not in message
    assert imap.instances == []


def test_test_reports_an_imap_failure(smtp, imap):
    imap.fail = imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
    ok, message = mailbox().test()
    assert not ok and "IMAP login failed" in message and PASSWORD not in message
    assert imap.instances[0].logged_out


def test_test_without_settings_makes_no_connection(smtp, imap):
    ok, message = mailbox(host="").test()
    assert not ok and "not all set" in message
    assert smtp.instances == [] and imap.instances == []


# --- configuration ----------------------------------------------------------------------------


def test_default_imap_host():
    assert default_imap_host("smtp.gmail.com") == "imap.gmail.com"
    assert default_imap_host("smtp.office365.com") == "outlook.office365.com"
    assert default_imap_host("mail.example.com") == "mail.example.com"


def test_from_config_reads_secrets_and_the_sender():
    for name, value in (
        ("SMTP_HOST", "smtp.gmail.com"),
        ("SMTP_PORT", "465"),
        ("SMTP_USER", "priya@bh.example"),
        ("SMTP_PASSWORD", PASSWORD),
    ):
        secrets.set(name, value)
    settings = Settings()
    settings.sender.name = "Priya Nair"
    save_settings(settings)
    box = SmtpImapMailbox.from_config()
    assert (box.host, box.port, box.imap_host, box.imap_port) == (
        "smtp.gmail.com",
        465,
        "imap.gmail.com",
        993,
    )
    assert box.from_name == "Priya Nair" and box.from_email == "priya@bh.example"
    assert box.configured()


def test_from_config_is_not_configured_without_secrets():
    assert not SmtpImapMailbox.from_config().configured()


def test_registry_builds_the_mailbox_provider(smtp, imap):
    from leadgen.providers import registry

    provider = registry.get_provider("smtp")
    assert provider is not None and not provider.configured()
    assert registry.check_provider("smtp")["ok"] is False
    assert registry.ALL_PROVIDERS["smtp"] is smtp_imap.INFO


# --- footer -----------------------------------------------------------------------------------


def full_settings() -> Settings:
    settings = Settings()
    settings.sender.name = "Priya Nair"
    settings.sender.role = "Partner"
    settings.sender.company = "Bright Harbour Training"
    settings.sender.website = "https://brightharbour.example"
    settings.postal_address = "1 Example Road\nSingapore 000000"
    return settings


def test_footer_has_sender_address_and_opt_out_line():
    text = footer(full_settings())
    assert text.splitlines() == [
        "--",
        "Priya Nair",
        "Partner, Bright Harbour Training",
        "https://brightharbour.example",
        "1 Example Road",
        "Singapore 000000",
        "",
        OPT_OUT_LINE,
    ]
    assert OPT_OUT_LINE == (
        "If you'd rather not hear from me, reply 'unsubscribe' and I won't email you again."
    )


def test_footer_leaves_out_empty_parts():
    settings = full_settings()
    settings.sender.website = ""
    settings.sender.role = ""
    lines = footer(settings).splitlines()
    assert "https://brightharbour.example" not in lines
    assert "Bright Harbour Training" in lines


def test_with_footer_adds_a_blank_line_and_normalises_line_endings():
    text = with_footer("Hi Aria,\r\n\r\nBody.\r\n\r\n", full_settings())
    assert "\r" not in text
    assert text.startswith("Hi Aria,\n\nBody.\n\n--\nPriya Nair")
    assert text.endswith(OPT_OUT_LINE + "\n")
