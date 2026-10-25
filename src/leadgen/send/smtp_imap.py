"""Send plain-text email through the user's own mailbox (SMTP) and open it for reading (IMAP).

The settings come from `secrets.env` (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, IMAP_HOST,
IMAP_PORT, IMAP_USER, IMAP_PASSWORD) and `settings.yaml` (sender name and email). Port 465 uses
implicit TLS; every other port must offer STARTTLS (a server that does not is refused, so the
password never travels in clear text). One message goes to exactly one recipient, never Cc/Bcc.
Passwords are never put in an error message or a log line.
"""

# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/integrations/smtp_mail.py and
# mail_provider.py (SendResult, the SMTP/IMAP hand-shakes). Harvey's async aiosmtplib code became
# synchronous stdlib `smtplib` / `imaplib`, and the provider abstraction was dropped.

from __future__ import annotations

import imaplib
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from leadgen.core import config, secrets
from leadgen.providers.base import ProviderInfo

INFO = ProviderInfo(
    key="smtp",
    name="Your mailbox (SMTP/IMAP)",
    purpose="Sends approved emails and reads replies from your own mailbox",
    env_keys=["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"],
    free_tier=(
        "Uses your own mailbox. Gmail or Google Workspace: an app password (needs 2-Step "
        "Verification), up to 2,000 messages a day. Outlook/Microsoft 365: SMTP AUTH is off by "
        "default and may need an admin to switch on."
    ),
    signup_url="",
    costs="",
    paid=False,
    default=False,
)

TIMEOUT_S = 30
TEST_TIMEOUT_S = 12  # `test()` runs inside a tool call, so it gives up sooner
DEFAULT_SMTP_PORT = 587
DEFAULT_IMAP_PORT = 993


class MailboxError(Exception):
    """A mailbox call failed. `code` is one of `CODES`; the message never holds a password."""

    CODES = ("not_configured", "invalid_message", "connect", "auth", "recipient_refused", "smtp")

    def __init__(self, code: str, message: str) -> None:
        if code not in self.CODES:
            raise ValueError(f"unknown mailbox error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SendResult:
    """What a successful send returns: the RFC `Message-ID` it was sent with."""

    message_id: str


def default_imap_host(smtp_host: str) -> str:
    """The usual IMAP name for an SMTP host: `smtp.x.com` -> `imap.x.com`; Outlook/Microsoft 365
    mail servers -> `outlook.office365.com`; anything else is returned unchanged."""
    host = smtp_host.strip().lower()
    if host in ("smtp.office365.com", "smtp-mail.outlook.com"):
        return "outlook.office365.com"
    if host.startswith("smtp."):
        return "imap." + host[len("smtp.") :]
    return host


def _short(exc: BaseException) -> str:
    """A short, safe description of a smtplib/imaplib/socket error."""
    code = getattr(exc, "smtp_code", None)
    detail = getattr(exc, "smtp_error", None)
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", "replace")
    if code is not None:
        text = f"{code} {detail or ''}".strip()
    elif exc.args and isinstance(exc.args[0], bytes):
        text = exc.args[0].decode("utf-8", "replace")
    else:
        text = f"{type(exc).__name__} {exc}".strip()
    return " ".join(text.split())[:200]


def _single_line(value: str, what: str) -> str:
    if "\r" in value or "\n" in value:
        raise MailboxError("invalid_message", f"The {what} must be a single line.")
    return value


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_name(name: str) -> str:
    """A display name safe for a From header (quotes added when it has special characters)."""
    name = " ".join(name.split()).replace('"', "")
    if any(ch in name for ch in "()<>[]:;@\\,."):
        return f'"{name}"'
    return name


def _quit(smtp: smtplib.SMTP) -> None:
    try:
        smtp.quit()
    except (smtplib.SMTPException, OSError):
        try:
            smtp.close()
        except OSError:
            pass


def _logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


MAILBOX_KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "IMAP_HOST", "IMAP_PORT")


def save_mailbox(
    host: str, port: str | int, user: str, password: str, imap_host: str = ""
) -> tuple[bool, str]:
    """Save the mailbox settings to `secrets.env`, test the log-in, and undo the save on failure.

    Returns `(ok, plain message)`. When the SMTP or IMAP log-in fails, the previously saved values
    are put back (or removed if there were none), so a wrong password is never kept.
    """
    host, user, imap_host = host.strip(), user.strip(), imap_host.strip()
    if not (host and user and password):
        return False, "Server, address and password are all needed. Nothing was saved."
    imap_host = imap_host or default_imap_host(host)
    previous = {name: secrets.stored(name) for name in MAILBOX_KEYS}
    for name, value in (
        ("SMTP_HOST", host),
        ("SMTP_PORT", str(port)),
        ("SMTP_USER", user),
        ("SMTP_PASSWORD", password),
        ("IMAP_HOST", imap_host),
    ):
        secrets.set(name, value)
    ok, message = SmtpImapMailbox.from_config().test()
    if not ok:
        for name, old in previous.items():
            if old:
                secrets.set(name, old)
            else:
                secrets.delete(name)
        return False, f"The mailbox was not saved. {message}"
    return True, f"Saved. {message}"


class SmtpImapMailbox:
    """One mailbox: `send` through SMTP, `test` both protocols, `imap_connection` for T15."""

    info = INFO

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        imap_host: str = "",
        imap_port: int = DEFAULT_IMAP_PORT,
        from_name: str = "",
        from_email: str = "",
        imap_user: str = "",
        imap_password: str = "",
    ) -> None:
        self.host = host.strip()
        self.port = int(port or DEFAULT_SMTP_PORT)
        self.user = user.strip()
        self.password = password
        self.imap_host = imap_host.strip() or default_imap_host(self.host)
        self.imap_port = int(imap_port or DEFAULT_IMAP_PORT)
        self.imap_user = imap_user.strip() or self.user
        self.imap_password = imap_password or password
        self.from_name = from_name.strip()
        self.from_email = (from_email or user).strip()

    @classmethod
    def from_config(cls) -> SmtpImapMailbox:
        """Build the mailbox from `secrets.env` and the sender in `settings.yaml`."""
        try:
            sender = config.load_settings().sender
        except config.ConfigError:  # a broken settings.yaml must not break `list_providers`
            sender = config.Sender()
        return cls(
            host=secrets.get("SMTP_HOST"),
            port=_int(secrets.get("SMTP_PORT"), DEFAULT_SMTP_PORT),
            user=secrets.get("SMTP_USER"),
            password=secrets.get("SMTP_PASSWORD"),
            imap_host=secrets.get("IMAP_HOST"),
            imap_port=_int(secrets.get("IMAP_PORT"), DEFAULT_IMAP_PORT),
            imap_user=secrets.get("IMAP_USER"),
            imap_password=secrets.get("IMAP_PASSWORD"),
            from_name=sender.name,
            from_email=sender.email or secrets.get("SMTP_USER"),
        )

    # --- Provider protocol ----------------------------------------------------------------

    def configured(self) -> bool:
        """True when host, user and password are set (no network)."""
        return bool(self.host and self.user and self.password)

    def test(self) -> tuple[bool, str]:
        """Log in to SMTP and then IMAP without sending anything. Returns (ok, plain message)."""
        if not self.configured():
            return False, "SMTP_HOST, SMTP_USER and SMTP_PASSWORD are not all set."
        try:
            smtp = self._smtp(TEST_TIMEOUT_S)
            try:
                smtp.login(self.user, self.password)
            finally:
                _quit(smtp)
        except MailboxError as exc:
            return False, f"SMTP login failed: {exc.message}"
        except (smtplib.SMTPException, OSError) as exc:
            return False, f"SMTP login failed: {self._classify(exc).message}"
        try:
            conn = self.imap_connection(TEST_TIMEOUT_S)
        except (imaplib.IMAP4.error, OSError) as exc:
            return False, f"SMTP works but IMAP login failed ({self.imap_host}): {_short(exc)}"
        _logout(conn)
        return True, f"SMTP and IMAP both work for {self.user}."

    # --- sending --------------------------------------------------------------------------

    def build_message(
        self, to: str, subject: str, body_text: str, headers: Mapping[str, str] | None = None
    ) -> EmailMessage:
        """The plain-text message exactly as it would be sent (one recipient, no Cc/Bcc)."""
        to = _single_line(to.strip(), "recipient")
        _, address = parseaddr(to)
        if not address or address != to or "," in to or ";" in to or "@" not in address:
            raise MailboxError("invalid_message", "The recipient must be one email address.")
        _single_line(subject, "subject")
        sender = self.from_email
        if not sender or "@" not in sender:
            raise MailboxError("not_configured", "No sender email address is set.")
        msg = EmailMessage()
        msg["From"] = f"{_clean_name(self.from_name)} <{sender}>" if self.from_name else sender
        msg["To"] = address
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
        msg["List-Unsubscribe"] = f"<mailto:{sender}?subject=unsubscribe>"
        for name, value in (headers or {}).items():
            _single_line(name, "header name")
            _single_line(str(value), "header value")
            if name.lower() in ("to", "cc", "bcc", "from", "message-id"):
                continue
            if name in msg:
                del msg[name]
            msg[name] = str(value)
        msg.set_content(body_text)
        return msg

    def send(
        self, to: str, subject: str, body_text: str, headers: Mapping[str, str] | None = None
    ) -> SendResult:
        """Send one plain-text email to one recipient. Raises `MailboxError` on any failure."""
        if not self.configured():
            raise MailboxError("not_configured", "The mailbox is not set up.")
        msg = self.build_message(to, subject, body_text, headers)
        try:
            smtp = self._smtp()
            try:
                smtp.login(self.user, self.password)
                smtp.send_message(msg, to_addrs=[str(msg["To"])])
            finally:
                _quit(smtp)
        except MailboxError:
            raise
        except (smtplib.SMTPException, OSError) as exc:
            raise self._classify(exc) from None
        return SendResult(message_id=str(msg["Message-ID"]))

    def imap_connection(self, timeout: float = TIMEOUT_S) -> imaplib.IMAP4_SSL:
        """A logged-in IMAP connection (the caller must `logout()`); used by the mailbox sync."""
        conn = imaplib.IMAP4_SSL(
            self.imap_host,
            self.imap_port,
            ssl_context=ssl.create_default_context(),
            timeout=timeout,
        )
        try:
            conn.login(self.imap_user, self.imap_password)
        except BaseException:
            _logout(conn)
            raise
        return conn

    # --- helpers --------------------------------------------------------------------------

    def _smtp(self, timeout: float = TIMEOUT_S) -> smtplib.SMTP:
        context = ssl.create_default_context()
        try:
            if self.port == 465:
                return smtplib.SMTP_SSL(self.host, self.port, timeout=timeout, context=context)
            smtp = smtplib.SMTP(self.host, self.port, timeout=timeout)
        except (smtplib.SMTPException, OSError) as exc:
            raise self._classify(exc) from None
        try:
            smtp.ehlo()
            smtp.starttls(context=context)  # refuses to carry on if STARTTLS is not offered
            smtp.ehlo()
        except (smtplib.SMTPException, OSError) as exc:
            _quit(smtp)
            raise MailboxError(
                "connect", f"{self.host}:{self.port} did not accept STARTTLS ({_short(exc)})."
            ) from None
        return smtp

    def _classify(self, exc: BaseException) -> MailboxError:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return MailboxError("auth", f"The mailbox refused the login ({_short(exc)}).")
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            return MailboxError("recipient_refused", "The mail server refused the recipient.")
        # smtplib.SMTPException is itself an OSError, so the plain network errors come last.
        is_smtp_answer = isinstance(exc, smtplib.SMTPException) and not isinstance(
            exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)
        )
        if not is_smtp_answer:
            return MailboxError(
                "connect", f"Could not reach {self.host}:{self.port} ({_short(exc)})."
            )
        return MailboxError("smtp", f"The mail server answered: {_short(exc)}.")
