"""Connect a mailbox from the dashboard help page: Gmail in two fields, or any mailbox by hand.

No sending logic lives here. Saving and testing reuse `send.smtp_imap.save_mailbox` and
`SmtpImapMailbox.test` (T14): the test logs in to SMTP and IMAP and sends nothing, and a failed
save puts the old settings back. This module cleans the input, fills in Gmail's server names, and
turns the technical answers into plain sentences. The password is never returned (only its last
four characters) and never put in a message; bad input is described, not echoed.
"""

from __future__ import annotations

import re
from typing import Any

from leadgen.core import secrets
from leadgen.send.smtp_imap import SmtpImapMailbox, save_mailbox

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587
GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_HOSTS = (GMAIL_SMTP_HOST, "smtp.googlemail.com")
APP_PASSWORD_LENGTH = 16

_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


def _fail(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def _plain_text(value: str) -> bool:
    """Standard keyboard characters only (a password with other characters cannot be sent)."""
    return value.isascii() and all(ch.isprintable() for ch in value)


def get_state() -> dict[str, Any]:
    """What is saved, safe to show: the address, the servers and the last 4 password characters."""
    mailbox = SmtpImapMailbox.from_config()
    configured = mailbox.configured()
    return {
        "configured": configured,
        "kind": "gmail" if mailbox.host.lower() in GMAIL_HOSTS else "other",
        "address": mailbox.user,
        "smtp_host": mailbox.host,
        "smtp_port": mailbox.port,
        "imap_host": mailbox.imap_host,
        "password": secrets.masked("SMTP_PASSWORD"),
    }


def _plain_failure(raw: str, gmail: bool, password_length: int | None = None) -> dict[str, Any]:
    """The plain-English failure for the technical answer of `save_mailbox` / `test`."""
    who = "Google" if gmail else "The mailbox"
    if "IMAP login failed" in raw:
        return _fail(
            "reading_failed",
            "Sending works, but Lead Generator could not open the inbox to read replies.",
            "If this is a Google Workspace account, ask your administrator to turn on IMAP "
            "access for you, then test again."
            if gmail
            else "Check the name of the incoming mail server, then test again.",
        )
    if "refused the login" in raw:
        note = ""
        if gmail and password_length not in (None, APP_PASSWORD_LENGTH):
            note = " An app password is 16 letters long, so it may not have been copied in full."
        return _fail(
            "wrong_password",
            f"{who} did not accept that address and password.{note} "
            + (
                "Check that you used the 16-letter app password, not your normal Google "
                "password. If unsure, make a new app password and paste that."
                if gmail
                else "Check both and try again."
            ),
            "If Google will not let you make an app password, which happens with some work or "
            "school accounts and with Advanced Protection, ask your administrator or use "
            "another mailbox."
            if gmail
            else "Some providers need an app password, or an administrator who allows "
            "sending from other programs.",
        )
    if "Could not reach" in raw or "STARTTLS" in raw:
        return _fail(
            "cannot_reach",
            "Could not reach the mail server. Check your internet connection"
            + ("" if gmail else " and the server name and port")
            + ", then try again.",
        )
    return _fail(
        "not_accepted",
        f"{who} did not accept the settings. Check them and try again.",
    )


def _saved(message: str) -> dict[str, Any]:
    state = get_state()
    return {"ok": True, "message": message, "mailbox": state}


def connect_gmail(address: str, app_password: str) -> dict[str, Any]:
    """Save and test a Gmail / Google Workspace mailbox from an address and an app password.

    All spaces are removed from the password (Google shows it in four groups); the servers and
    ports are filled in. On a failed log-in nothing is kept.
    """
    address = (address or "").strip()
    password = "".join((app_password or "").split())
    if not _ADDRESS.match(address) or len(address) > 254 or not _plain_text(address):
        return _fail("invalid_address", "Type the full email address you use with Google.")
    if not password:
        return _fail("missing_password", "Paste the app password from Google.")
    if not _plain_text(password) or len(password) > 200:
        return _fail(
            "invalid_password",
            "An app password only has letters. Copy it again from the Google page.",
        )
    ok, raw = save_mailbox(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, address, password, GMAIL_IMAP_HOST)
    if not ok:
        return _plain_failure(raw, True, len(password))
    return _saved(
        f"Connected. Lead Generator could log in to {address} to send and to read replies. "
        "Nothing was sent."
    )


def connect_other(
    host: str, port: str | int, address: str, password: str, imap_host: str = ""
) -> dict[str, Any]:
    """Save and test any mailbox from the fields filled in by hand (Outlook and others)."""
    host, address, imap_host = (
        (host or "").strip(),
        (address or "").strip(),
        (imap_host or "").strip(),
    )
    if not (host and address and password):
        return _fail("missing_fields", "The server, your address and the password are all needed.")
    if not _HOST.match(host) or (imap_host and not _HOST.match(imap_host)):
        return _fail(
            "invalid_server",
            "A server name looks like smtp.example.com: letters, digits, dots and dashes only.",
        )
    try:
        number = int(str(port).strip())
    except ValueError:
        number = 0
    if not 1 <= number <= 65535:
        return _fail("invalid_port", "The port is a number such as 587 or 465.")
    if not (_plain_text(address) and _plain_text(password)) or len(address) > 254:
        return _fail(
            "invalid_password",
            "The address and password may only use standard keyboard characters.",
        )
    ok, raw = save_mailbox(host, number, address, password, imap_host)
    if not ok:
        return _plain_failure(raw, host.lower() in GMAIL_HOSTS)
    return _saved(
        f"Connected. Lead Generator could log in to {address} to send and to read replies. "
        "Nothing was sent."
    )


def test_saved() -> dict[str, Any]:
    """Log in to the saved mailbox (sending and reading) without sending anything."""
    mailbox = SmtpImapMailbox.from_config()
    if not mailbox.configured():
        return _fail(
            "not_configured",
            "No mailbox is saved yet.",
            "Fill in step 3 on this page first.",
        )
    ok, raw = mailbox.test()
    if not ok:
        return _plain_failure(raw, mailbox.host.lower() in GMAIL_HOSTS)
    return _saved(
        f"The mailbox works. Lead Generator could log in to {mailbox.user} to send and to read "
        "replies. Nothing was sent."
    )
