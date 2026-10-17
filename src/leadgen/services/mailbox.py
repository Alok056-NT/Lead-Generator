"""Read the user's mailbox for replies, bounces and unsubscribes (PLAN section 15, tool 33).

`sync_mailbox` opens the INBOX **read-only** (IMAP `EXAMINE`; this module never stores flags,
deletes, moves or expunges anything and fetches with `BODY.PEEK`, so no mail is marked as read),
fetches the headers and the first 4 KB of text of every message that arrived since the last
sync, and turns each one that belongs to an email the tool sent into an `events` row:

* `bounce`: a delivery report (RFC 3464 `multipart/report`, Microsoft NDR) or a message from a
  mail-delivery sender. Permanent failures set the email's status to `invalid` and put the address
  on the do-not-contact list; temporary ones ("delayed") are ignored.
* `auto_reply`: out-of-office and other automatic answers (RFC 3834 `Auto-Submitted`, `Precedence`,
  subject). Kept apart so they never count in the reply rate.
* `unsubscribe`: the person's own words (first 500 characters, quoted text removed) say
  unsubscribe / remove me / stop emailing / not interested, please remove. The address goes on the
  do-not-contact list and emails still queued for that person are cancelled.
* `reply`: everything else that matches a send. No automatic answer is ever sent.

A message belongs to a send when `In-Reply-To` / `References` (or, for a bounce, a Message-ID quoted
inside the report) equals the `Message-ID` the tool sent (`sends.provider_message_id`); failing that
a reply is matched by its From address to an email sent to that address in the last 30 days, and a
bounce by the failed recipient. Only sends with status `sent` are considered.

Full message bodies are never stored: `events.detail` holds at most 300 characters. The position is
kept in `settings` (`imap_last_uid`, `imap_uidvalidity`, `imap_last_sync`). A call handles at most
`MAX_PER_CALL` messages within `TIME_LIMIT_S` seconds and says `done: false` when more are waiting.

A server answer that is not OK never moves the position past unread mail: a failed SEARCH raises
`MailboxSyncError`; a FETCH answered `NO` (imaplib raises only on BAD) stops the call at that UID
(`done: false`) and the message is skipped as unreadable only after `MAX_REFUSALS` calls in a row
(`settings` key `imap_refused`). A FETCH that is OK but empty (expunged mail) is skipped at once.
"""

# Adapted from Harvey (MIT, Ethan Rogers): reference/harvey/integrations/mail_provider.py
# (looks_like_bounce and its sender / subject lists) and smtp_mail.py (the RFC 822 parsing).

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from leadgen import db
from leadgen.send.footer import OPT_OUT_LINE
from leadgen.send.smtp_imap import SmtpImapMailbox
from leadgen.services import suppression

log = logging.getLogger("leadgen.mailbox")

MAX_PER_CALL = 60
TIME_LIMIT_S = 40.0
FIRST_FETCH_BYTES = 4096  # headers plus this much text (the card)
REPORT_FETCH_BYTES = 32768  # a delivery report is read further, up to this much text
MATCH_WINDOW_DAYS = 30
DETAIL_CHARS = 300
UNSUB_CHARS = 500
MIN_DATE_YEAR = 1990  # a Date: header outside these years is treated as missing
MAX_DATE_YEAR = 2999
MAX_HIGHLIGHTS = 20

LAST_UID_KEY = "imap_last_uid"
UIDVALIDITY_KEY = "imap_uidvalidity"
LAST_SYNC_KEY = "imap_last_sync"
REFUSED_KEY = "imap_refused"  # "<uidvalidity>:<uid>:<tries>" of a message the server would not give
MAX_REFUSALS = 3  # the third refusal in a row skips that message so the sync cannot wedge

BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery subsystem")
BOUNCE_SUBJECTS = (
    "delivery status notification", "undeliverable", "returned mail",
    "delivery failure", "failure notice", "could not be delivered",
    "delivery incomplete", "address not found",
)  # fmt: skip
_AUTO_SUBJECT_RE = re.compile(
    r"^\s*(?:(?:re|aw|fw|fwd)\s*:\s*)*"
    r"(?:out of (?:the )?office|automatic reply|auto[- ]?reply|auto[- ]?response|autoreply|"
    r"autoresponse|away from (?:the )?office|abwesenheit|automatische antwort)",
    re.IGNORECASE,
)
_UNSUB_RE = re.compile(
    r"\bunsubscrib\w*|\bremove\s+me\b|\bstop\s+emailing\b|"
    r"\bnot\s+interested,?\s+please\s+remove\b",
    re.IGNORECASE,
)
_DELAY_RE = re.compile(r"\bdelay(?:ed)?\b|will keep trying|still trying", re.IGNORECASE)
_ID_RE = re.compile(r"<([^<>\s]+@[^<>\s]+)>")
_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_STATUS_RE = re.compile(r"(?<![\d.])([45]\.\d{1,3}\.\d{1,3})(?!\d|\.\d)")
_QUOTE_START_RE = re.compile(
    r"^\s*(?:-{2,}\s*(?:original message|forwarded message)\s*-{2,}"
    r"|_{10,}|on .{3,200}wrote:\s*|am .{3,200}schrieb .{0,100}:\s*)$",
    re.IGNORECASE,
)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailboxSyncError(Exception):
    """The mailbox could not be read. `code` is stable text; `hint` tells the AI what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The reply to give the AI."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


# --- reading one message -------------------------------------------------------------------------


@dataclass
class Inbound:
    """What the sync needs from one message. Bodies are cut short; nothing here is stored."""

    from_email: str = ""
    subject: str = ""
    message_id: str = ""
    refs: list[str] = field(default_factory=list)  # In-Reply-To then References, as `<id>`
    date: datetime | None = None
    reply_text: str = ""  # the person's own words: quoted text and our footer removed
    all_text: str = ""  # every text part (delivery reports quote the original here)
    auto_submitted: bool = False
    is_report: bool = False  # a delivery-status report (RFC 3464 / Microsoft NDR)
    report_recipients: list[str] = field(default_factory=list)
    report_status: str = ""  # e.g. 5.1.1
    report_action: str = ""  # failed / delayed / ...
    report_diagnostic: str = ""


def norm_id(value: str) -> str:
    """A Message-ID in comparison form: `<local@host>` without spaces, lower case."""
    match = _ID_RE.search(value or "")
    return f"<{match.group(1)}>".lower() if match else ""


def looks_like_bounce(from_email: str, subject: str) -> bool:
    """Harvey's check: a mail-delivery sender or a delivery-failure subject."""
    sender = (from_email or "").lower()
    title = (subject or "").lower()
    return any(m in sender for m in BOUNCE_SENDERS) or any(m in title for m in BOUNCE_SUBJECTS)


def _text_of(part: Message) -> str:
    try:
        return str(part.get_content())
    except (LookupError, ValueError, UnicodeError, AssertionError):
        pass
    try:
        raw = part.get_payload(decode=True)
        if isinstance(raw, bytes):
            return raw.decode(part.get_content_charset() or "utf-8", "replace")
    except (LookupError, ValueError, UnicodeError):
        pass
    return ""


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?(</\1>|$)", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    return re.sub(r"<[^>]+>", " ", text)


def strip_quoted(text: str) -> str:
    """The person's own words: everything from the first quote marker on is dropped, `>` lines
    and our own opt-out line are skipped (they would otherwise read as 'unsubscribe')."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for i, line in enumerate(lines):
        joined = line.strip()
        if _QUOTE_START_RE.match(line):
            break
        if joined.lower().startswith("on ") and i + 1 < len(lines):  # "On ... \n wrote:"
            two = f"{joined} {lines[i + 1].strip()}"
            if len(two) < 300 and two.rstrip().lower().endswith("wrote:"):
                break
        if re.match(r"(?i)^from:\s", joined) and any(
            re.match(r"(?i)^(sent|date|to|subject):", ln.strip()) for ln in lines[i + 1 : i + 4]
        ):
            break  # Outlook-style header block of the quoted mail
        if joined.startswith(">") or OPT_OUT_LINE.lower() in joined.lower():
            continue
        kept.append(line)
    return " ".join(" ".join(kept).split())


def _parse_date(value: str) -> datetime | None:
    """A `Date:` header as UTC; None when it is missing, malformed or absurdly far away."""
    try:
        when = parsedate_to_datetime(value)
        if when is None:
            return None
        when = when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if not MIN_DATE_YEAR <= when.year <= MAX_DATE_YEAR:
        return None  # keeps `date +/- timedelta` far from the limits of datetime
    return when


def _header(msg: Message, name: str) -> str:
    """One header as text. The stdlib parses lazily and can raise on a malformed value (a
    Message-ID such as `<<<>>>@@`); that must never stop the sync, so it reads as empty."""
    try:
        return str(msg.get(name, "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _report_blocks(msg: Message) -> list[Message]:
    blocks: list[Message] = []
    for part in msg.walk():
        if part.get_content_type() == "message/delivery-status":
            payload = part.get_payload()
            if isinstance(payload, list):
                blocks.extend(b for b in payload if isinstance(b, Message))
    return blocks


def _address_after_semicolon(value: str) -> str:
    found = _ADDRESS_RE.search(str(value).split(";", 1)[-1])
    return found.group(0).lower() if found else ""


def parse_inbound(raw: bytes) -> Inbound | None:
    """Parse header bytes (plus the start of the text) into an `Inbound`; None if unreadable.
    Whatever is wrong with the message, this never raises."""
    try:
        return _parse_inbound(raw)
    except Exception as exc:  # a broken message must never stop the sync  # noqa: BLE001
        log.warning("mailbox: a message could not be parsed (%s)", type(exc).__name__)
        return None


def _parse_inbound(raw: bytes) -> Inbound | None:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    from_email = parseaddr(_header(msg, "From"))[1].strip().lower()
    subject = " ".join(_header(msg, "Subject").split())
    if not from_email:
        return None

    refs: list[str] = []
    for header in ("In-Reply-To", "References"):
        for found in _ID_RE.findall(_header(msg, header)):
            ident = f"<{found}>".lower()
            if ident not in refs:
                refs.append(ident)

    auto = _header(msg, "Auto-Submitted").strip().lower()
    auto_submitted = bool(
        (auto and auto != "no")
        or _header(msg, "X-Autoreply")
        or _header(msg, "X-Autorespond")
        or _header(msg, "Precedence").strip().lower() == "auto_reply"
    )
    try:
        is_report = (
            msg.get_content_type() == "multipart/report"
            and str(msg.get_param("report-type") or "").lower() == "delivery-status"
        )
    except Exception:  # noqa: BLE001 - a malformed Content-Type
        is_report = False
    is_report = is_report or bool(_header(msg, "X-MS-Exchange-Message-Is-Ndr"))

    texts: list[str] = []
    try:
        for part in msg.walk():
            if part.is_multipart() or part.get_content_maintype() != "text":
                continue
            ctype = part.get_content_type()
            text = _text_of(part)
            texts.append(_strip_html(text) if ctype == "text/html" else text)
    except Exception:  # noqa: BLE001 - truncated or malformed parts
        pass

    recipients: list[str] = []
    status = action = diagnostic = ""
    try:
        blocks = _report_blocks(msg)
    except Exception:  # noqa: BLE001
        blocks = []
    for block in blocks:
        is_report = True
        for header in ("Final-Recipient", "Original-Recipient"):
            addr = _address_after_semicolon(_header(block, header))
            if addr and addr not in recipients:
                recipients.append(addr)
        status = status or _header(block, "Status").strip()
        action = action or _header(block, "Action").strip().lower()
        diagnostic = diagnostic or " ".join(_header(block, "Diagnostic-Code").split())
    all_text = "\n".join(texts)

    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            body = _text_of(part)
            if part.get_content_type() == "text/html":
                body = _strip_html(body)
    except Exception:  # noqa: BLE001
        body = texts[0] if texts else ""
    return Inbound(
        from_email=from_email,
        subject=subject,
        message_id=norm_id(_header(msg, "Message-ID")),
        refs=refs,
        date=_parse_date(_header(msg, "Date")),
        reply_text=strip_quoted(body),
        all_text=all_text,
        auto_submitted=auto_submitted,
        is_report=is_report,
        report_recipients=recipients,
        report_status=status,
        report_action=action,
        report_diagnostic=diagnostic,
    )


# --- what kind of message is it ------------------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    """The verdict: `bounce`, `soft_bounce`, `auto_reply`, `unsubscribe` or `reply`."""

    name: str
    status: str = ""  # bounce status code such as 5.1.1


def _status_code(msg: Inbound) -> str:
    if msg.report_status and _STATUS_RE.search(msg.report_status):
        return msg.report_status.strip()
    found = _STATUS_RE.search(msg.all_text)
    return found.group(1) if found else ""


def classify(msg: Inbound) -> Kind:
    """Decide what a message is. Order: delivery report, auto-reply, unsubscribe, reply."""
    if msg.is_report or looks_like_bounce(msg.from_email, msg.subject):
        code = _status_code(msg)
        if msg.is_report and msg.report_action:
            soft = msg.report_action in ("delayed", "relayed", "expanded", "delivered")
            soft = soft or (code.startswith("4") and not code.startswith("5"))
        elif code:
            soft = code.startswith("4")
        else:
            soft = bool(_DELAY_RE.search(f"{msg.subject} {msg.all_text[:2000]}"))
        return Kind("soft_bounce" if soft else "bounce", code)
    if msg.auto_submitted or _AUTO_SUBJECT_RE.match(msg.subject):
        return Kind("auto_reply")
    own = f"{msg.subject} {msg.reply_text[:UNSUB_CHARS]}"
    if _UNSUB_RE.search(own):
        return Kind("unsubscribe")
    return Kind("reply")


# --- matching to a send --------------------------------------------------------------------------

_SEND_SQL = (
    "SELECT s.id, s.to_email, s.sent_at, s.provider_message_id, s.message_id, m.person_id, "
    "p.campaign_id, p.first_name, p.last_name, c.name AS company "
    "FROM sends s JOIN messages m ON m.id = s.message_id JOIN people p ON p.id = m.person_id "
    "LEFT JOIN companies c ON c.id = p.company_id WHERE s.status = 'sent' "
)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _by_message_ids(conn, ids: list[str]):
    if not ids:
        return None
    marks = ",".join("?" for _ in ids)
    rows = db.query(
        conn,
        _SEND_SQL + f"AND lower(s.provider_message_id) IN ({marks}) ORDER BY s.sent_at DESC",
        tuple(ids),
    )
    order = {ident: i for i, ident in enumerate(ids)}
    rows = sorted(rows, key=lambda r: order.get(str(r["provider_message_id"]).lower(), 99))
    return rows[0] if rows else None


def _by_address(conn, addresses: list[str], when: datetime):
    if not addresses:
        return None
    marks = ",".join("?" for _ in addresses)
    rows = db.query(
        conn,
        _SEND_SQL + f"AND lower(s.to_email) IN ({marks}) AND s.sent_at >= ? AND s.sent_at <= ? "
        "ORDER BY s.sent_at DESC, s.id DESC LIMIT 1",
        (
            *addresses,
            _iso(when - timedelta(days=MATCH_WINDOW_DAYS)),
            _iso(when + timedelta(days=1)),
        ),
    )
    return rows[0] if rows else None


def find_send(conn, msg: Inbound, kind: Kind, now: datetime):
    """The `sent` send this message belongs to, or None. Message-ID evidence beats addresses."""
    when = msg.date or now
    if kind.name in ("bounce", "soft_bounce"):
        quoted = [i for i in dict.fromkeys(f"<{m.lower()}>" for m in _ID_RE.findall(msg.all_text))]
        row = _by_message_ids(conn, msg.refs + quoted)
        if row:
            return row
        wanted = list(msg.report_recipients)
        if not wanted:  # no machine-readable report: look for any address we sent to
            wanted = list(dict.fromkeys(a.lower() for a in _ADDRESS_RE.findall(msg.all_text)))
        return _by_address(conn, wanted[:25], when)
    return _by_message_ids(conn, msg.refs) or _by_address(conn, [msg.from_email], when)


# --- recording -----------------------------------------------------------------------------------


def _detail(kind: Kind, msg: Inbound, send) -> str:
    if kind.name == "bounce":
        who = msg.report_recipients[0] if msg.report_recipients else send["to_email"]
        why = msg.report_diagnostic or " ".join(msg.all_text.split())[:200]
        text = " ".join(part for part in (kind.status, f"{who}:", why) if part)
    else:
        text = msg.reply_text or msg.subject
    return " ".join(text.split())[:DETAIL_CHARS]


def _event_exists(conn, send_id: int, etype: str, at: str, detail: str, dated: bool) -> bool:
    column, value = ("at", at) if dated else ("detail", detail)
    sql = f"SELECT 1 FROM events WHERE send_id = ? AND type = ? AND {column} = ?"
    return conn.execute(sql, (send_id, etype, value)).fetchone() is not None


def _cancel_queued(conn, send, extra_addresses: list[str], reason: str) -> int:
    addresses = list(dict.fromkeys(a.lower() for a in [send["to_email"], *extra_addresses] if a))
    marks = ",".join("?" for _ in addresses)
    cur = conn.execute(
        "UPDATE sends SET status = 'skipped', error = ?, updated_at = ? WHERE status = 'queued' "
        f"AND (lower(to_email) IN ({marks}) OR message_id IN "
        "(SELECT id FROM messages WHERE person_id = ?))",
        (reason, db.now(), *addresses, send["person_id"]),
    )
    return int(cur.rowcount)


def apply(conn, msg: Inbound, kind: Kind, send, now: datetime) -> dict[str, Any] | None:
    """Record the event for a matched message and its side effects. Returns a highlight dict, or
    None when the event was already recorded (a repeated sync) or the message is ignored."""
    etype = kind.name
    at = _iso(msg.date or now)
    detail = _detail(kind, msg, send)
    if _event_exists(conn, send["id"], etype, at, detail, msg.date is not None):
        return None
    db.insert(conn, "events", send_id=send["id"], type=etype, at=at, detail=detail)
    note = ""
    if etype == "bounce":
        reason = f"bounced {kind.status}".strip()
        suppression.add_in(conn, [send["to_email"]], "email", reason)
        conn.execute(
            "UPDATE emails SET status = 'invalid', updated_at = ? "
            "WHERE person_id = ? AND lower(email) = lower(?)",
            (db.now(), send["person_id"], send["to_email"]),
        )
        cancelled = _cancel_queued(conn, send, [], "bounced")
        note = f"address marked invalid and listed as do-not-contact; {cancelled} queued cancelled"
    elif etype == "unsubscribe":
        who = [msg.from_email, send["to_email"]]
        suppression.add_in(conn, who, "email", "asked to unsubscribe")
        cancelled = _cancel_queued(conn, send, [msg.from_email], "unsubscribed")
        note = f"added to the do-not-contact list; {cancelled} queued email(s) cancelled"
    name = " ".join(p for p in (send["first_name"], send["last_name"]) if p).strip()
    return {
        "type": etype,
        "send_id": send["id"],
        "campaign_id": send["campaign_id"],
        "person": name,
        "company": send["company"] or "",
        "text": detail[:200],
        "action": note,
    }


@dataclass
class Counters:
    scanned: int = 0
    matched: int = 0
    reply: int = 0
    auto_reply: int = 0
    bounce: int = 0
    unsubscribe: int = 0
    unmatched: int = 0
    ignored: int = 0
    duplicates: int = 0
    unreadable: int = 0
    highlights: list[dict[str, Any]] = field(default_factory=list)


def ingest(raw: bytes, counters: Counters | None = None, now: datetime | None = None,
           own_addresses: tuple[str, ...] = ()) -> dict[str, Any] | None:  # fmt: skip
    """Parse, classify, match and record one message. Returns the highlight if an event was
    created. `own_addresses` are the user's own mailbox addresses (their mail is ignored)."""
    counters = counters if counters is not None else Counters()
    now = now or datetime.now(UTC)
    counters.scanned += 1
    msg = parse_inbound(raw)  # never raises: None means unreadable
    if msg is None:
        counters.unreadable += 1
        return None
    if msg.from_email in own_addresses:
        counters.ignored += 1
        return None
    try:
        kind = classify(msg)
    except Exception as exc:  # noqa: BLE001 - last line of defence: skip, never abort the sync
        log.warning("mailbox: a message could not be classified (%s)", type(exc).__name__)
        counters.unreadable += 1
        return None
    if kind.name == "soft_bounce":
        counters.ignored += 1
        return None
    with db.session() as conn:
        send = find_send(conn, msg, kind, now)
        if send is None:
            counters.unmatched += 1
            return None
        counters.matched += 1
        highlight = apply(conn, msg, kind, send, now)
    if highlight is None:
        counters.duplicates += 1
    else:
        setattr(counters, kind.name, getattr(counters, kind.name) + 1)
        if len(counters.highlights) < MAX_HIGHLIGHTS:
            counters.highlights.append(highlight)
    return highlight


# --- IMAP ----------------------------------------------------------------------------------------


def _kv_get(key: str) -> str:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return str(rows[0]["value"]) if rows else ""


def _kv_set(key: str, value: str) -> None:
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key=key, value=value)


def imap_date(when: datetime) -> str:
    """`D-Mon-YYYY` with English month names whatever the computer's language (RFC 3501)."""
    return f"{when.day:02d}-{_MONTHS[when.month - 1]}-{when.year}"


class _FetchRefused(Exception):
    """The server answered a FETCH with NO (imaplib only raises on BAD): a temporary failure,
    not proof that the message is gone, so the caller must not move past it yet."""


def _fetch(conn: imaplib.IMAP4, uid: int, parts: str) -> dict[str, bytes]:
    """FETCH by UID with `BODY.PEEK` parts; returns `{"header": ..., "text": ...}` as present
    (empty for an expunged message: `OK` with no data). Raises `_FetchRefused` on `NO`."""
    typ, data = conn.uid("FETCH", str(uid), parts)
    out: dict[str, bytes] = {}
    if typ != "OK":
        raise _FetchRefused(typ)
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            label = item[0].upper() if isinstance(item[0], bytes) else b""
            if b"HEADER" in label:
                out["header"] = item[1]
            elif b"TEXT" in label:
                out["text"] = item[1]
    return out


def fetch_message(conn: imaplib.IMAP4, uid: int) -> bytes | None:
    """Headers plus the start of the text of one message (read-only `BODY.PEEK`); None when the
    server has no such message (expunged). A delivery report's text is read further, because the
    failed recipient comes after the explanation. A server `NO` raises `_FetchRefused`."""
    got = _fetch(conn, uid, f"(BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.{FIRST_FETCH_BYTES}>)")
    header, text = got.get("header", b""), got.get("text", b"")
    if not header:
        return None
    if len(text) >= FIRST_FETCH_BYTES:
        lower = header.lower()
        subject_hint = looks_like_bounce(
            parseaddr(_header_value(header, b"from"))[1], _header_value(header, b"subject")
        )
        if b"multipart/report" in lower or b"x-ms-exchange-message-is-ndr" in lower or subject_hint:
            more = _fetch(conn, uid, f"(BODY.PEEK[TEXT]<0.{REPORT_FETCH_BYTES}>)")
            text = more.get("text", text)
    return header.rstrip(b"\r\n") + b"\r\n\r\n" + text


def _header_value(header: bytes, name: bytes) -> str:
    match = re.search(rb"(?im)^" + name + rb":[ \t]*(.*)$", header)
    return match.group(1).decode("utf-8", "replace").strip() if match else ""


def _save_position(uid: int, validity: str) -> None:
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key=LAST_UID_KEY, value=str(uid))
        db.upsert(conn, "settings", ["key"], key=UIDVALIDITY_KEY, value=validity)


def _refusal_load(validity: str) -> tuple[int, int]:
    """(uid, tries) of the message the server last refused, or (0, 0)."""
    head, _, rest = _kv_get(REFUSED_KEY).partition(":")
    uid, _, tries = rest.partition(":")
    return (_int(uid), _int(tries)) if head == validity and uid else (0, 0)


def _refusal_save(validity: str, uid: int, tries: int) -> None:
    _kv_set(REFUSED_KEY, f"{validity}:{uid}:{tries}" if uid else "")


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _open(mailbox: SmtpImapMailbox) -> imaplib.IMAP4:
    if not mailbox.configured():
        raise MailboxSyncError(
            "not_configured",
            "The mailbox is not set up, so there is nothing to read.",
            "Ask the user to run `leadgen mailbox set` (Gmail needs an app password).",
        )
    try:
        return mailbox.imap_connection()
    except imaplib.IMAP4.error as exc:
        raise MailboxSyncError(
            "auth",
            "The mailbox refused the IMAP log-in.",
            "Ask the user to run `leadgen mailbox test`; IMAP may be switched off or the "
            f"password wrong ({' '.join(str(exc).split())[:120]}).",
        ) from None
    except OSError as exc:
        raise MailboxSyncError(
            "connect",
            f"Could not reach the IMAP server {mailbox.imap_host}.",
            f"Check the internet connection and the IMAP server name ({type(exc).__name__}).",
        ) from None


def sync_mailbox(
    since_days: int = 30,
    *,
    mailbox: SmtpImapMailbox | None = None,
    max_messages: int = MAX_PER_CALL,
    time_limit: float = TIME_LIMIT_S,
) -> dict[str, Any]:
    """Read new INBOX mail (read-only) and record replies, bounces and unsubscribes.

    `since_days` only matters for the first sync (or after the mailbox was re-numbered): later
    syncs continue after the last message seen. At most `max_messages` messages are handled per
    call; when `done` is false, call again. Raises `MailboxSyncError`.
    """
    valid = isinstance(since_days, int) and not isinstance(since_days, bool)
    if not (valid and 1 <= since_days <= 365):
        raise MailboxSyncError(
            "invalid_value", "since_days must be a whole number from 1 to 365.", "Try 30."
        )
    mailbox = mailbox or SmtpImapMailbox.from_config()
    started = time.monotonic()
    now = datetime.now(UTC)
    conn = _open(mailbox)
    counters = Counters()
    own = tuple({a.lower() for a in (mailbox.user, mailbox.from_email) if a})
    try:
        typ, _ = conn.select("INBOX", readonly=True)  # EXAMINE: nothing can be changed
        if typ != "OK":
            raise MailboxSyncError(
                "inbox", "The INBOX could not be opened.", "Ask the user to check the mailbox."
            )
        validity = ""
        _, vdata = conn.response("UIDVALIDITY")
        if vdata and vdata[0]:
            validity = vdata[0].decode() if isinstance(vdata[0], bytes) else str(vdata[0])
        last_uid = _int(_kv_get(LAST_UID_KEY))
        if last_uid and validity != _kv_get(UIDVALIDITY_KEY):
            last_uid = 0  # the server re-numbered the mailbox: start from the date again
        if last_uid:
            criteria = f"UID {last_uid + 1}:*"
        else:
            criteria = f"SINCE {imap_date(now - timedelta(days=since_days))}"
        typ, data = conn.uid("SEARCH", None, criteria)
        if typ != "OK":
            raise MailboxSyncError("search", "The mailbox search failed.", "Try again later.")
        try:
            found = sorted({int(x) for x in (data[0] or b"").split()} if data else set())
        except (ValueError, TypeError, AttributeError):
            raise MailboxSyncError(
                "search", "The mailbox search gave an unreadable answer.", "Try again later."
            ) from None
        waiting = [u for u in found if u > last_uid]
        done_here = 0
        stalled = False
        refused_uid, refused_tries = _refusal_load(validity)
        for uid in waiting[:max_messages]:
            if done_here and time.monotonic() - started > time_limit:
                break
            try:
                raw = fetch_message(conn, uid)
            except _FetchRefused:
                # A `NO` is usually temporary (Gmail: "[UNAVAILABLE] Temporary System Error"):
                # stop here without moving past the message, and give up on it only when the
                # server has refused it MAX_REFUSALS calls in a row.
                tries = refused_tries + 1 if refused_uid == uid else 1
                if tries < MAX_REFUSALS:
                    _refusal_save(validity, uid, tries)
                    refused_uid, refused_tries = uid, tries
                    stalled = True
                    break
                log.warning("mailbox sync: the server refused one message %d times; skipped", tries)
                raw = None
            if raw is None:
                counters.unreadable += 1
            else:
                ingest(raw, counters, now, own)
            done_here += 1
            last_uid = uid
            _save_position(last_uid, validity)
            if refused_uid == uid:
                _refusal_save(validity, 0, 0)
                refused_uid = refused_tries = 0
        if not waiting and validity:
            _save_position(last_uid, validity)  # last_uid is 0 after a renumbering
        remaining = len(waiting) - done_here
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailboxSyncError(
            "connect",
            f"The mailbox connection failed while reading ({type(exc).__name__}).",
            "Call sync_mailbox again; it continues where it stopped.",
        ) from None
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
    _kv_set(LAST_SYNC_KEY, db.now())
    log.info(
        "mailbox sync: %d scanned, %d replies, %d bounces, %d unsubscribes",
        counters.scanned, counters.reply, counters.bounce, counters.unsubscribe,
    )  # fmt: skip
    return _reply(counters, remaining, last_uid, stalled)


def _reply(c: Counters, remaining: int, last_uid: int, stalled: bool = False) -> dict[str, Any]:
    done = remaining <= 0
    out: dict[str, Any] = {
        "ok": True,
        "scanned": c.scanned,
        "matched": c.matched,
        "replies": c.reply,
        "auto_replies": c.auto_reply,
        "bounces": c.bounce,
        "unsubscribes": c.unsubscribe,
        "unmatched": c.unmatched,
        "ignored": c.ignored,
        "already_recorded": c.duplicates,
        "unreadable": c.unreadable,
        "highlights": c.highlights,
        "done": done,
        "remaining": max(0, remaining),
    }
    if not done:
        out["next_cursor"] = str(last_uid)
        out["next_step"] = (
            "The mail server refused to hand over one message just now. Try again later: "
            "sync_mailbox retries that message and skips it only after 3 refusals in a row."
            if stalled
            else "More mail is waiting: call sync_mailbox again."
        )
    else:
        out["next_step"] = (
            "Call get_analytics to see the numbers."
            if c.matched
            else "Nothing new that belongs to a sent email."
        )
    return out
