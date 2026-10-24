"""Mailbox sync: classifying and matching real-shaped mails, and the read-only IMAP walk.

Everything is invented. The IMAP server is a fake that refuses every command except the few
the sync is allowed to use, so a test fails the moment the code tries to change the mailbox.
"""

from __future__ import annotations

import imaplib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mail_world import (
    NOW,
    add_send,
    email_status,
    eml,
    events,
    fixture,
    seed_person,
    seed_world,
    send_row,
    suppressed,
)
from qa_world import add_company, make_campaign

from leadgen import db
from leadgen.services import mailbox
from leadgen.services.mailbox import (
    Counters,
    Inbound,
    MailboxSyncError,
    classify,
    ingest,
    parse_inbound,
    strip_quoted,
)

FIXTURES = (
    "plain_reply.eml", "reply_no_thread_headers.eml", "gmail_bounce_dsn.eml", "outlook_ndr.eml",
    "out_of_office.eml", "unsubscribe_reply.eml", "not_interested_remove.eml",
    "plain_text_bounce.eml", "delayed_dsn.eml",
)  # fmt: skip


@pytest.fixture
def world(ready_db):
    return seed_world()


def run(name_or_bytes, counters=None, own=()):
    raw = fixture(name_or_bytes) if isinstance(name_or_bytes, str) else name_or_bytes
    return ingest(raw, counters or Counters(), NOW, own)


# --- fixtures keep their CRLF line endings --------------------------------------------------------


def test_fixtures_are_crlf_on_disk_and_marked_as_not_text():
    for name in FIXTURES:
        raw = fixture(name)
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), name
    attrs = (Path(__file__).parents[1] / ".gitattributes").read_text(encoding="utf-8")
    assert "tests/fixtures/mail/*.eml -text" in attrs


# --- parsing and classifying ----------------------------------------------------------------------


def test_plain_reply_is_parsed_without_the_quoted_original():
    msg = parse_inbound(fixture("plain_reply.eml"))
    assert msg.from_email == "aria.tan@example.org"
    assert msg.refs == ["<send-001@example.com>"]
    assert msg.date == datetime(2026, 10, 13, 1, 14, 22, tzinfo=UTC)
    assert msg.reply_text.startswith("Hi Jordan, Thanks for reaching out.")
    assert msg.reply_text.endswith("Best, Aria")
    assert "wrote" not in msg.reply_text and "Northwind-style" not in msg.reply_text
    assert classify(msg).name == "reply"


def test_gmail_dsn_is_read_as_a_permanent_bounce():
    msg = parse_inbound(fixture("gmail_bounce_dsn.eml"))
    assert msg.is_report and msg.report_recipients == ["chris.lowe@example.org"]
    assert msg.report_status == "5.1.1" and msg.report_action == "failed"
    kind = classify(msg)
    assert (kind.name, kind.status) == ("bounce", "5.1.1")


def test_outlook_ndr_is_read_as_a_permanent_bounce():
    msg = parse_inbound(fixture("outlook_ndr.eml"))
    assert msg.report_recipients == ["sam.dolan@example.org"]
    kind = classify(msg)
    assert (kind.name, kind.status) == ("bounce", "5.1.10")


def test_a_plain_text_bounce_is_found_by_the_sender_and_subject_heuristic():
    msg = parse_inbound(fixture("plain_text_bounce.eml"))
    assert not msg.is_report and msg.report_recipients == []
    kind = classify(msg)
    assert (kind.name, kind.status) == ("bounce", "5.1.1")


def test_a_delayed_report_is_a_soft_bounce():
    assert classify(parse_inbound(fixture("delayed_dsn.eml"))).name == "soft_bounce"


def test_out_of_office_is_an_auto_reply():
    assert classify(parse_inbound(fixture("out_of_office.eml"))).name == "auto_reply"


@pytest.mark.parametrize("name", ["unsubscribe_reply.eml", "not_interested_remove.eml"])
def test_unsubscribe_wording_is_found(name):
    assert classify(parse_inbound(fixture(name))).name == "unsubscribe"


@pytest.mark.parametrize(
    ("headers", "subject"),
    [
        ("Auto-Submitted: auto-replied", "Re: Quick question"),
        ("X-Autoreply: yes", "Re: Quick question"),
        ("Precedence: auto_reply", "Re: Quick question"),
        ("", "Automatic reply: Quick question"),
        ("", "Re: Out of Office - back Monday"),
    ],
)
def test_auto_reply_signals(headers, subject):
    raw = eml("Maya Ortiz <maya.ortiz@example.net>", subject, "I am away.", extra_headers=headers)
    assert classify(parse_inbound(raw)).name == "auto_reply"


def test_auto_submitted_no_is_a_normal_mail():
    raw = eml("A <a@example.net>", "Re: hi", "Sounds good.", extra_headers="Auto-Submitted: no")
    assert classify(parse_inbound(raw)).name == "reply"


@pytest.mark.parametrize(
    "text",
    [
        "Please UNSUBSCRIBE me",
        "Please make sure I am unsubscribed",
        "get me unsubscribed",
        "I keep unsubscribing and nothing happens",
        "remove me from the list",
        "Remove   me.",
        "Stop emailing me, thanks",
        "Not interested, please remove us",
        "not interested,\nplease remove",
    ],
)
def test_unsubscribe_phrases(text):
    msg = Inbound(from_email="a@example.net", subject="Re: hello", reply_text=text)
    assert classify(msg).name == "unsubscribe"


@pytest.mark.parametrize(
    "text",
    [
        "Can we remove meeting notes from the doc?",
        "Please remove member access for Sam",
        "We should stop emailers from guessing here",
        "Not interested, please removed",
    ],
)
def test_unsubscribe_words_need_whole_words(text):
    msg = Inbound(from_email="a@example.net", subject="Re: hello", reply_text=text)
    assert classify(msg).name == "reply"


def test_remove_meeting_notes_is_a_reply_and_remove_me_is_an_unsubscribe(world):
    thread = "In-Reply-To: <send-001@example.com>"
    sender, subject = "Aria Tan <aria.tan@example.org>", "Re: Quick question"
    note = eml(sender, subject, "Can we remove meeting notes?", extra_headers=thread)
    assert run(note)["type"] == "reply"
    assert suppressed() == set()
    leave = eml(sender, subject, "Remove me.", extra_headers=thread)
    assert run(leave)["type"] == "unsubscribe"
    assert "aria.tan@example.org" in suppressed()


def test_unsubscribe_in_the_subject_counts():
    msg = Inbound(from_email="a@example.net", subject="unsubscribe", reply_text="")
    assert classify(msg).name == "unsubscribe"


def test_unsubscribe_words_after_500_characters_do_not_count():
    msg = Inbound(
        from_email="a@example.net", subject="Re: hi", reply_text="x " * 300 + "unsubscribe"
    )
    assert classify(msg).name == "reply"


def test_a_reply_that_only_quotes_our_opt_out_line_is_a_reply():
    body = (
        "Interesting, tell me more about the routing tool.\r\n\r\n"
        "On Wed, 14 Oct 2026 at 09:00, Jordan Reyes <jordan.reyes@example.com> wrote:\r\n"
        "> Hi Dana,\r\n> If you'd rather not hear from me, reply 'unsubscribe' and I won't "
        "email you again.\r\n"
    )
    msg = parse_inbound(eml("Dana <dana.whitfield@example.org>", "Re: hi", body))
    assert classify(msg).name == "reply"
    # even when the client did not prefix the quote
    flat = (
        "Sounds useful.\r\n\r\n-- \r\nJordan\r\n\r\nIf you'd rather not hear from me, reply "
        "'unsubscribe' and I won't email you again.\r\n"
    )
    assert classify(parse_inbound(eml("D <d@example.org>", "Re: hi", flat))).name == "reply"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Yes please.\n\nOn Mon, 1 Jan 2026 at 10:00, A B <a@example.com> wrote:\n> old",
            "Yes please.",
        ),
        ("Yes.\n\nOn Mon, 1 Jan 2026 at 10:00, A B\n<a@example.com> wrote:\n> old", "Yes."),
        ("Yes.\n-----Original Message-----\nFrom: A\nSent: x\nSubject: y\nold", "Yes."),
        ("Yes.\nFrom: A <a@example.com>\nSent: Monday\nTo: B\nSubject: x\nold", "Yes."),
        ("Yes.\n> quoted\nStill me.", "Yes. Still me."),
        ("Yes.\n________________________________\nFrom: A", "Yes."),
        ("A line about the From: field of a form", "A line about the From: field of a form"),
    ],
)
def test_strip_quoted(text, expected):
    assert strip_quoted(text) == expected


def test_html_only_mail_is_read_as_text():
    raw = (
        b"From: Aria Tan <aria.tan@example.org>\r\nSubject: Re: hi\r\n"
        b"Content-Type: text/html; charset=UTF-8\r\n\r\n"
        b"<html><style>p{}</style><body><p>Please <b>unsubscribe</b> me</p></body></html>"
    )
    msg = parse_inbound(raw)
    assert "unsubscribe" in msg.reply_text and "<" not in msg.reply_text
    assert classify(msg).name == "unsubscribe"


def test_encoded_subject_and_other_charset_are_decoded():
    raw = (
        "From: Dana <d@example.org>\r\nSubject: =?utf-8?q?Re=3A_caf=C3=A9?=\r\n"
        "Content-Type: text/plain; charset=ISO-8859-1\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
    ).encode("ascii") + "Merci pour le café".encode("latin-1")
    msg = parse_inbound(raw)
    assert msg.subject == "Re: café" and "café" in msg.reply_text


def test_a_message_without_a_sender_is_unreadable():
    assert parse_inbound(b"Subject: nothing\r\n\r\nbody") is None
    assert parse_inbound(b"") is None


def test_looks_like_bounce():
    assert mailbox.looks_like_bounce("MAILER-DAEMON@example.com", "x")
    assert mailbox.looks_like_bounce("a@example.com", "Undeliverable: hello")
    assert not mailbox.looks_like_bounce("aria.tan@example.org", "Re: hello")


# --- matching and recording -----------------------------------------------------------------------


def test_reply_matched_by_in_reply_to_records_a_reply_event(world):
    counters = Counters()
    highlight = run("plain_reply.eml", counters)
    assert highlight["type"] == "reply" and highlight["person"] == "Aria Tan"
    assert highlight["company"] == "Northwind Logistics"
    [event] = events()
    assert event["send_id"] == world["send"]["send-001"] and event["type"] == "reply"
    assert event["at"] == "2026-10-13T01:14:22.000Z"
    assert event["detail"].startswith("Hi Jordan, Thanks for reaching out.")
    assert "Northwind-style" not in event["detail"]
    assert (counters.matched, counters.reply) == (1, 1)
    assert suppressed() == set()


def test_header_match_beats_the_address(world):
    # The reply comes from a colleague's address but answers send-001.
    raw = eml("Pat <pat@example.net>", "Re: hello", "Forwarded to me.", "<send-001@example.com>")
    run(raw)
    assert [e["send_id"] for e in events()] == [world["send"]["send-001"]]


def test_message_ids_compare_ignoring_case(world):
    run(eml("Pat <pat@example.net>", "Re: hi", "ok", "<SEND-001@Example.com>"))
    assert events()[0]["send_id"] == world["send"]["send-001"]


def test_reply_without_thread_headers_is_matched_by_address(world):
    run("reply_no_thread_headers.eml")
    [event] = events()
    assert event["send_id"] == world["send"]["send-009"] and event["type"] == "reply"


def test_address_fallback_takes_the_most_recent_send_within_30_days(world):
    with db.session() as conn:
        message = conn.execute(
            "SELECT message_id FROM sends WHERE id = ?", (world["send"]["send-009"],)
        ).fetchone()[0]
    newer = add_send(
        message, "ben.carter@example.net", "<send-010@example.com>", "2026-10-13T09:00:00.000Z"
    )
    add_send(
        message, "ben.carter@example.net", "<send-011@example.com>", "2026-08-01T09:00:00.000Z"
    )
    run("reply_no_thread_headers.eml")
    assert events()[0]["send_id"] == newer


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("sent_at", "2026-08-01T09:00:00.000Z"),  # more than 30 days before the reply
        ("sent_at", "2026-10-16T09:00:00.000Z"),  # after the reply
        ("status", "refused"),  # an attempt the mail server refused was never sent
        ("status", "failed"),
        ("status", "queued"),
    ],
)
def test_address_fallback_ignores_old_late_and_unsent_sends(ready_db, column, value):
    world = seed_world()
    with db.session() as conn:
        conn.execute(
            f"UPDATE sends SET {column} = ? WHERE id = ?", (value, world["send"]["send-009"])
        )
    counters = Counters()
    run("reply_no_thread_headers.eml", counters)
    assert events() == [] and counters.unmatched == 1


def test_a_stranger_is_unmatched(world):
    counters = Counters()
    run(eml("Stranger <nobody@example.com>", "Re: hi", "hello"), counters)
    assert events() == [] and counters.unmatched == 1


def test_a_reply_from_the_user_is_ignored(world):
    counters = Counters()
    run(
        eml(
            "Jordan <jordan.reyes@example.com>", "Re: hi", "note to self", "<send-001@example.com>"
        ),
        counters,
        own=("jordan.reyes@example.com",),
    )
    assert events() == [] and counters.ignored == 1


def test_unreadable_mail_is_counted(world):
    counters = Counters()
    assert run(b"Subject: x\r\n\r\ny", counters) is None
    assert counters.unreadable == 1


def test_the_same_mail_twice_is_recorded_once(world):
    counters = Counters()
    assert run("plain_reply.eml", counters) is not None
    assert run("plain_reply.eml", counters) is None
    assert len(events()) == 1 and counters.duplicates == 1 and counters.reply == 1


def test_gmail_bounce_invalidates_the_address_and_suppresses_it(world):
    pid = world["person"]["send-002"]
    # a second, still queued email to the same address must not go out
    with db.session() as conn:
        message = conn.execute(
            "SELECT message_id FROM sends WHERE id = ?", (world["send"]["send-002"],)
        ).fetchone()[0]
    queued = add_send(message, "chris.lowe@example.org", "", None, status="queued")
    other = add_send(message + 0, "someone.else@example.org", "", None, status="queued")
    highlight = run("gmail_bounce_dsn.eml")
    [event] = events()
    assert event["send_id"] == world["send"]["send-002"] and event["type"] == "bounce"
    assert event["detail"].startswith("5.1.1 chris.lowe@example.org:")
    assert "does not exist" in event["detail"] and len(event["detail"]) <= 300
    assert email_status(pid) == "invalid"
    assert "chris.lowe@example.org" in suppressed()
    assert send_row(queued)["status"] == "skipped" and send_row(queued)["error"] == "bounced"
    assert send_row(other)["status"] == "skipped"  # same person's queued email goes too
    assert highlight["person"] == "Chris Lowe"


def test_outlook_ndr_bounce(world):
    run("outlook_ndr.eml")
    [event] = events()
    assert event["send_id"] == world["send"]["send-003"] and event["type"] == "bounce"
    assert "5.1.10" in event["detail"]
    assert email_status(world["person"]["send-003"]) == "invalid"


def test_plain_text_bounce_is_matched_by_the_failed_address(world):
    run("plain_text_bounce.eml")
    [event] = events()
    assert event["send_id"] == world["send"]["send-008"] and event["type"] == "bounce"
    assert "dev.park@example.org" in suppressed()


def test_bounce_is_matched_by_recipient_when_the_original_id_is_missing(world):
    raw = fixture("gmail_bounce_dsn.eml").replace(b"send-002@example.com", b"gone@example.com")
    run(raw)
    assert events()[0]["send_id"] == world["send"]["send-002"]


def test_bounce_for_an_unknown_recipient_is_unmatched(world):
    counters = Counters()
    raw = (
        fixture("gmail_bounce_dsn.eml")
        .replace(b"chris.lowe", b"nobody.here")
        .replace(b"send-002@", b"unknown@")
    )
    run(raw, counters)
    assert events() == [] and counters.unmatched == 1 and suppressed() == set()


def test_a_delayed_report_changes_nothing(world):
    counters = Counters()
    run("delayed_dsn.eml", counters)
    assert events() == [] and counters.ignored == 1
    assert email_status(world["person"]["send-007"]) == "verified"


def test_out_of_office_is_recorded_as_auto_reply_only(world):
    counters = Counters()
    run("out_of_office.eml", counters)
    [event] = events()
    assert event["type"] == "auto_reply" and event["send_id"] == world["send"]["send-004"]
    assert (counters.auto_reply, counters.reply) == (1, 0)
    assert suppressed() == set()


@pytest.mark.parametrize(
    ("name", "key", "address"),
    [
        ("unsubscribe_reply.eml", "send-005", "dana.whitfield@example.org"),
        ("not_interested_remove.eml", "send-006", "felix.brandt@example.net"),
    ],
)
def test_unsubscribe_suppresses_the_sender_and_cancels_queued_emails(world, name, key, address):
    with db.session() as conn:
        message = conn.execute(
            "SELECT message_id FROM sends WHERE id = ?", (world["send"][key],)
        ).fetchone()[0]
    queued = add_send(message, address, "", None, status="queued")
    bystander = world["send"]["send-001"]
    other_queued = add_send(
        conn_message(world["send"]["send-001"]), "aria.tan@example.org", "", None, status="queued"
    )
    highlight = run(name)
    [event] = events()
    assert event["type"] == "unsubscribe" and event["send_id"] == world["send"][key]
    assert address in suppressed()
    assert send_row(queued)["status"] == "skipped" and send_row(queued)["error"] == "unsubscribed"
    assert send_row(other_queued)["status"] == "queued"  # a different person is untouched
    assert send_row(bystander)["status"] == "sent"
    assert "do-not-contact" in highlight["action"]


def conn_message(send_id: int) -> int:
    with db.session() as conn:
        return conn.execute("SELECT message_id FROM sends WHERE id = ?", (send_id,)).fetchone()[0]


def test_unsubscribe_from_another_address_suppresses_both(world):
    raw = eml("Dana W <dana@example.com>", "Re: hi", "Please remove me.", "<send-005@example.com>")
    run(raw)
    assert {"dana@example.com", "dana.whitfield@example.org"} <= suppressed()


def test_an_auto_reply_that_mentions_unsubscribe_is_still_an_auto_reply(world):
    raw = eml(
        "Maya <maya.ortiz@example.net>",
        "Automatic reply: hi",
        "Away. To unsubscribe click here.",
        "<send-004@example.com>",
        "Auto-Submitted: auto-replied",
    )
    run(raw)
    assert events()[0]["type"] == "auto_reply" and suppressed() == set()


def test_an_unmatched_unsubscribe_does_not_touch_the_list(world):
    counters = Counters()
    run(eml("Newsletter <news@example.com>", "unsubscribe", "unsubscribe"), counters)
    assert suppressed() == set() and counters.unmatched == 1


def test_only_300_characters_of_a_reply_are_stored(world):
    long_body = "Thanks for the note. " + "This is a very long reply. " * 100
    run(eml("Aria <aria.tan@example.org>", "Re: hi", long_body, "<send-001@example.com>"))
    detail = events()[0]["detail"]
    assert len(detail) == 300 and detail.startswith("Thanks for the note.")


def test_suppression_of_a_second_person_event_on_the_same_send(world):
    run("plain_reply.eml")
    run(
        eml(
            "Aria <aria.tan@example.org>",
            "Re: hi",
            "Another thought.",
            "<send-001@example.com>",
            date="Thu, 15 Oct 2026 10:00:00 +0000",
        )
    )
    assert [e["type"] for e in events()] == ["reply", "reply"]


# --- the IMAP walk --------------------------------------------------------------------------------


class FakeImap:
    """A tiny IMAP server in memory. Only the commands the sync may use are allowed."""

    ALLOWED_UID = {"SEARCH", "FETCH"}

    def __init__(self, messages: dict[int, bytes], validity: str = "4242") -> None:
        self.messages = dict(messages)
        self.validity = validity
        self.commands: list[tuple] = []
        self.fetches: list[tuple[int, str]] = []
        self.fail_on_uid: int | None = None
        self.logged_out = False

    def select(self, mailbox="INBOX", readonly=False):
        self.commands.append(("EXAMINE" if readonly else "SELECT", mailbox))
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        assert code == "UIDVALIDITY"
        return code, [self.validity.encode()]

    def uid(self, command, *args):
        command = command.upper()
        self.commands.append((command, *args))
        if command not in self.ALLOWED_UID:
            raise AssertionError(f"the sync used the forbidden IMAP command UID {command}")
        if command == "SEARCH":
            criteria = args[1]
            found = sorted(self.messages)
            match = re.fullmatch(r"UID (\d+):\*", criteria)
            if match:
                start = int(match.group(1))
                found = [u for u in found if u >= start] or found[-1:]  # the server quirk
            else:
                assert re.fullmatch(r"SINCE \d{2}-[A-Z][a-z]{2}-\d{4}", criteria), criteria
            return "OK", [" ".join(str(u) for u in found).encode()]
        uid = int(args[0])
        parts = args[1]
        assert "BODY.PEEK" in parts and "RFC822" not in parts, parts
        assert re.sub(r"BODY\.PEEK", "", parts).count("BODY[") == 0, parts
        self.fetches.append((uid, parts))
        if uid == self.fail_on_uid:
            raise imaplib.IMAP4.abort("connection lost")
        raw = self.messages[uid]
        head, _, body = raw.partition(b"\r\n\r\n")
        out: list = []
        if "HEADER" in parts:
            out.append(
                (f"{uid} (UID {uid} BODY[HEADER] {{{len(head)}}}".encode(), head + b"\r\n\r\n")
            )
        limit = int(re.search(r"TEXT\]<0\.(\d+)>", parts).group(1))
        text = body[:limit]
        out.append((f" BODY[TEXT]<0> {{{len(text)}}}".encode(), text))
        out.append(b")")
        return "OK", out

    def store(self, *a, **k):
        raise AssertionError("STORE is forbidden")

    def expunge(self, *a, **k):
        raise AssertionError("EXPUNGE is forbidden")

    def copy(self, *a, **k):
        raise AssertionError("COPY is forbidden")

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


class FakeMailbox:
    def __init__(self, imap: FakeImap | Exception | None = None, configured: bool = True) -> None:
        self.imap = imap
        self._configured = configured
        self.user = "jordan.reyes@example.com"
        self.from_email = "jordan.reyes@example.com"
        self.imap_host = "imap.example.com"

    def configured(self) -> bool:
        return self._configured

    def imap_connection(self, timeout: float = 30):
        if isinstance(self.imap, Exception):
            raise self.imap
        return self.imap


def inbox(*names: str, start: int = 1) -> dict[int, bytes]:
    return {start + i: fixture(n) for i, n in enumerate(names)}


def kv(key: str) -> str:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return rows[0]["value"] if rows else ""


def test_first_sync_reads_everything_and_remembers_the_last_message(world):
    imap = FakeImap(inbox(*FIXTURES))
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert result["ok"] and result["done"] and result["scanned"] == 9
    assert (
        result["replies"],
        result["auto_replies"],
        result["bounces"],
        result["unsubscribes"],
    ) == (2, 1, 3, 2)
    assert result["ignored"] == 1  # the delayed report
    assert {h["type"] for h in result["highlights"]} == {
        "reply",
        "auto_reply",
        "bounce",
        "unsubscribe",
    }
    assert kv("imap_last_uid") == "9" and kv("imap_uidvalidity") == "4242" and kv("imap_last_sync")
    assert imap.logged_out
    assert imap.commands[0] == ("EXAMINE", "INBOX")
    assert imap.commands[1][:2] == ("SEARCH", None) and imap.commands[1][2].startswith("SINCE ")
    assert "get_analytics" in result["next_step"]


def test_the_sync_only_ever_examines_and_searches_and_fetches(world):
    imap = FakeImap(inbox(*FIXTURES))
    mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    names = {c[0] for c in imap.commands}
    assert names == {"EXAMINE", "SEARCH", "FETCH"}  # never SELECT, STORE, EXPUNGE, COPY


def test_imaplib_sends_examine_for_a_read_only_select(monkeypatch):
    sent = []
    conn = imaplib.IMAP4.__new__(imaplib.IMAP4)
    conn.untagged_responses = {}
    conn._encoding = "ascii"  # set by __init__ in newer Python (3.13.16+), which we skip
    monkeypatch.setattr(
        imaplib.IMAP4,
        "_simple_command",
        lambda self, name, *a: (sent.append(name), ("OK", [b""]))[1],
    )
    conn.select("INBOX", readonly=True)
    assert sent == ["EXAMINE"]


def test_the_module_has_no_way_to_change_the_mailbox():
    source = Path(mailbox.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\.(store|expunge|copy|delete|rename|create|move)\(", source)
    for word in ("STORE", "EXPUNGE", "RFC822", "readonly=False", '"SELECT"'):
        assert word not in source, word
    assert "readonly=True" in source and "BODY.PEEK" in source
    assert not re.search(r"BODY\[", source)


def test_later_syncs_continue_after_the_last_message(world):
    imap = FakeImap(inbox("plain_reply.eml", "out_of_office.eml"))
    first = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert first["scanned"] == 2
    imap.messages[3] = fixture("unsubscribe_reply.eml")
    imap.fetches.clear()
    second = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert second["scanned"] == 1 and second["unsubscribes"] == 1
    assert [u for u, _ in imap.fetches] == [3]
    assert any(c[0] == "SEARCH" and c[2] == "UID 3:*" for c in imap.commands)
    third = mailbox.sync_mailbox(
        30, mailbox=FakeMailbox(imap)
    )  # nothing new: the server repeats uid 3
    assert third["scanned"] == 0 and third["done"] and "Nothing new" in third["next_step"]


def test_a_big_mailbox_is_read_in_chunks(world):
    imap = FakeImap(inbox(*FIXTURES))
    first = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap), max_messages=4)
    assert first["scanned"] == 4 and first["done"] is False
    assert first["remaining"] == 5 and first["next_cursor"] == "4"
    assert "call sync_mailbox again" in first["next_step"]
    second = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap), max_messages=4)
    third = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap), max_messages=4)
    assert (second["scanned"], third["scanned"], third["done"]) == (4, 1, True)
    assert len(events()) == 8  # all but the delayed report, each exactly once


def test_the_time_limit_stops_a_call_after_at_least_one_message(world):
    imap = FakeImap(inbox(*FIXTURES))
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap), time_limit=-1)
    assert result["scanned"] == 1 and result["done"] is False


def test_a_renumbered_mailbox_starts_again_from_the_date(world):
    imap = FakeImap(inbox("plain_reply.eml"))
    mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    imap2 = FakeImap(inbox("plain_reply.eml", "out_of_office.eml"), validity="9999")
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap2))
    assert result["scanned"] == 2 and result["already_recorded"] == 1
    assert imap2.commands[1][2].startswith("SINCE ") and kv("imap_uidvalidity") == "9999"


def test_a_renumbered_mailbox_with_nothing_since_the_date_resets_the_position(world):
    imap = FakeImap(inbox("plain_reply.eml", start=500))
    mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert kv("imap_last_uid") == "500"
    imap2 = FakeImap({}, validity="9999")  # renumbered, and nothing is dated within the window
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap2))
    assert result["scanned"] == 0 and result["done"]
    assert kv("imap_last_uid") == "0" and kv("imap_uidvalidity") == "9999"
    imap2.messages[7] = fixture("unsubscribe_reply.eml")  # a low number, below the old position
    later = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap2))
    assert later["scanned"] == 1 and later["unsubscribes"] == 1
    assert kv("imap_last_uid") == "7"


BAD_ID = (
    b"From: Aria Tan <aria.tan@example.org>\r\nSubject: Re: hi\r\n"
    b"In-Reply-To: <send-001@example.com>\r\nMessage-ID: <<<>>>@@\r\n"
    b"Date: Wed, 14 Oct 2026 10:00:00 +0000\r\n\r\nhi"
)
BAD_DATE = (
    b"From: Aria Tan <aria.tan@example.org>\r\nSubject: Re: hi\r\n"
    b"In-Reply-To: <send-001@example.com>\r\n"
    b"Date: Fri, 31 Dec 9999 23:59:59 -1200\r\n\r\nhello"
)


@pytest.mark.parametrize("raw", [BAD_ID, BAD_DATE])
def test_malformed_headers_never_raise_out_of_ingest(world, raw):
    counters = Counters()
    ingest(raw, counters, NOW, ())
    assert counters.scanned == 1


@pytest.mark.parametrize(
    "value",
    ["Fri, 31 Dec 9999 23:59:59 -1200", "Sun, 01 Jan 1066 00:00:00 +0000", "not a date", ""],
)
def test_out_of_range_or_broken_dates_read_as_none(value):
    assert mailbox._parse_date(value) is None


def test_a_normal_date_is_read_as_utc():
    assert mailbox._parse_date("Wed, 14 Oct 2026 10:00:00 +0200") == datetime(
        2026, 10, 14, 8, 0, tzinfo=UTC
    )


def test_a_last_line_of_defence_counts_a_failing_parse_and_goes_on(world, monkeypatch):
    def boom(raw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(mailbox, "_parse_inbound", boom)
    counters = Counters()
    assert ingest(fixture("plain_reply.eml"), counters, NOW, ()) is None
    assert (counters.scanned, counters.unreadable) == (1, 1)


def test_a_failing_parse_is_logged_by_class_name_only(world, monkeypatch, caplog):
    def boom(raw):
        raise RuntimeError("secret-looking text from the message body")

    monkeypatch.setattr(mailbox, "_parse_inbound", boom)
    with caplog.at_level("WARNING", logger="leadgen.mailbox"):
        assert mailbox.parse_inbound(fixture("plain_reply.eml")) is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("RuntimeError" in m and "parsed" in m for m in messages)
    assert not any("secret-looking" in m for m in messages)
    assert all(r.exc_info is None for r in caplog.records)


def test_a_failing_classify_is_counted_and_logged_by_class_name_only(world, monkeypatch, caplog):
    def boom(msg):
        raise KeyError("secret-looking text from the message body")

    monkeypatch.setattr(mailbox, "classify", boom)
    counters = Counters()
    with caplog.at_level("WARNING", logger="leadgen.mailbox"):
        assert ingest(fixture("plain_reply.eml"), counters, NOW, ()) is None
    assert (counters.scanned, counters.unreadable) == (1, 1)
    messages = [r.getMessage() for r in caplog.records]
    assert any("KeyError" in m and "classified" in m for m in messages)
    assert not any("secret-looking" in m for m in messages)
    assert all(r.exc_info is None for r in caplog.records)
    assert events() == []


def test_bad_mail_does_not_block_the_sync_for_good(world):
    imap = FakeImap({1: BAD_ID, 2: BAD_DATE, 3: fixture("unsubscribe_reply.eml")})
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert result["ok"] and result["done"] and result["scanned"] == 3
    assert result["unsubscribes"] == 1
    assert kv("imap_last_uid") == "3"
    again = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert again["scanned"] == 0 and again["done"]


def test_a_long_delivery_report_is_read_further(world):
    filler = "x" * 70 + "\r\n"
    dsn = fixture("gmail_bounce_dsn.eml").replace(
        b"Delivery to the following recipient failed permanently:\r\n",
        b"Delivery to the following recipient failed permanently:\r\n" + filler.encode() * 80,
    )
    assert len(dsn.partition(b"\r\n\r\n")[2].split(b"Final-Recipient")[0]) > 4096
    imap = FakeImap({1: dsn})
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert result["bounces"] == 1
    assert len(imap.fetches) == 2 and "<0.32768>" in imap.fetches[1][1]


def test_a_long_ordinary_reply_is_not_read_twice(world):
    body = "Thanks!\r\n" + "word " * 2000
    imap = FakeImap(
        {1: eml("Aria <aria.tan@example.org>", "Re: hi", body, "<send-001@example.com>")}
    )
    mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert len(imap.fetches) == 1


def test_mail_from_the_users_own_address_is_ignored(world):
    imap = FakeImap(
        {1: eml("Jordan <jordan.reyes@example.com>", "Re: hi", "n", "<send-001@example.com>")}
    )
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert result["ignored"] == 1 and events() == []


class RefusingImap(FakeImap):
    """Answers FETCH of one UID with `NO` (imaplib only raises on BAD) while `refusals` lasts,
    like Gmail's "[UNAVAILABLE] Temporary System Error"."""

    def __init__(self, messages, refuse_uid: int, refusals: int, **kw) -> None:
        super().__init__(messages, **kw)
        self.refuse_uid = refuse_uid
        self.refusals = refusals

    def uid(self, command, *args):
        if command.upper() == "FETCH" and int(args[0]) == self.refuse_uid and self.refusals > 0:
            self.refusals -= 1
            self.commands.append((command.upper(), *args))
            return "NO", [b"[UNAVAILABLE] Temporary System Error"]
        return super().uid(command, *args)


def _three_mails():
    return {
        1: fixture("plain_reply.eml"),
        2: fixture("not_interested_remove.eml"),
        3: fixture("out_of_office.eml"),
    }


def test_a_refused_fetch_stops_the_call_and_a_later_ok_records_the_unsubscribe(world):
    imap = RefusingImap(_three_mails(), refuse_uid=2, refusals=1)
    first = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert first["ok"] and first["done"] is False and first["scanned"] == 1
    assert first["unreadable"] == 0 and first["remaining"] == 2
    assert first["next_cursor"] == "1" and "later" in first["next_step"]
    assert kv("imap_last_uid") == "1" and suppressed() == set()
    second = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert second["done"] and second["scanned"] == 2 and second["unsubscribes"] == 1
    assert second["unreadable"] == 0 and suppressed()
    assert kv("imap_last_uid") == "3" and kv("imap_refused") == ""


def test_three_refusals_in_a_row_skip_the_message_and_the_sync_goes_on(world, caplog):
    imap = RefusingImap(_three_mails(), refuse_uid=2, refusals=99)
    with caplog.at_level("WARNING", logger="leadgen.mailbox"):
        one = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
        two = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
        assert one["done"] is False and two["done"] is False
        assert two["scanned"] == 0 and kv("imap_last_uid") == "1"
        assert kv("imap_refused") == "4242:2:2"
        three = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert three["done"] and three["unreadable"] == 1 and three["scanned"] == 1
    assert three["unsubscribes"] == 0 and kv("imap_last_uid") == "3"
    assert kv("imap_refused") == ""
    assert any("refused" in r.getMessage() for r in caplog.records)


def test_a_recovering_server_clears_the_count_and_a_stale_count_is_ignored(world):
    imap = RefusingImap(_three_mails(), refuse_uid=2, refusals=2)
    mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert kv("imap_refused") == "4242:2:2"
    done = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))  # the server recovers
    assert done["done"] and done["unreadable"] == 0 and done["unsubscribes"] == 1
    assert kv("imap_refused") == ""
    mailbox._kv_set("imap_refused", "other:2:2")  # a count from another numbering of the mailbox
    assert mailbox._refusal_load("4242") == (0, 0)


def test_ok_with_no_data_is_skipped_straight_away(world):
    class Gone(FakeImap):
        def uid(self, command, *args):
            if command.upper() == "FETCH" and int(args[0]) == 2:
                self.commands.append(("FETCH", *args))
                return "OK", [None]  # the message was expunged between SEARCH and FETCH
            return super().uid(command, *args)

    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(Gone(_three_mails())))
    assert result["done"] and result["unreadable"] == 1 and result["scanned"] == 2
    assert kv("imap_last_uid") == "3" and kv("imap_refused") == ""


def test_a_refused_second_fetch_of_a_long_report_also_waits(world):
    class SecondNo(FakeImap):
        def uid(self, command, *args):
            if command.upper() == "FETCH" and f"<0.{mailbox.REPORT_FETCH_BYTES}>" in args[1]:
                self.commands.append(("FETCH", *args))
                return "NO", [b"busy"]
            return super().uid(command, *args)

    raw = fixture("gmail_bounce_dsn.eml") + b"\r\n" + b"x" * (mailbox.FIRST_FETCH_BYTES + 10)
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(SecondNo({1: raw})))
    assert result["done"] is False and result["scanned"] == 0 and kv("imap_last_uid") == ""


@pytest.mark.parametrize("answer", [("NO", [b"search failed"]), ("OK", [b"12 x"])])
def test_a_bad_search_answer_never_moves_the_position(world, answer):
    class BadSearch(FakeImap):
        def uid(self, command, *args):
            if command.upper() == "SEARCH":
                self.commands.append(("SEARCH", *args))
                return answer
            return super().uid(command, *args)

    imap = BadSearch(_three_mails())
    with pytest.raises(MailboxSyncError) as err:
        mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert err.value.code == "search" and imap.logged_out
    assert kv("imap_last_uid") == "" and events() == []


def test_a_connection_lost_midway_keeps_what_was_done(world):
    imap = FakeImap(inbox("plain_reply.eml", "out_of_office.eml", "unsubscribe_reply.eml"))
    imap.fail_on_uid = 3
    with pytest.raises(MailboxSyncError) as err:
        mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert err.value.code == "connect" and imap.logged_out
    assert kv("imap_last_uid") == "2" and len(events()) == 2
    imap.fail_on_uid = None
    result = mailbox.sync_mailbox(30, mailbox=FakeMailbox(imap))
    assert result["scanned"] == 1 and result["unsubscribes"] == 1


def test_mailbox_problems_are_structured_errors(world):
    with pytest.raises(MailboxSyncError) as err:
        mailbox.sync_mailbox(30, mailbox=FakeMailbox(configured=False))
    assert err.value.code == "not_configured" and "leadgen mailbox set" in err.value.hint
    response = err.value.as_response()
    assert response["ok"] is False and response["error"]["code"] == "not_configured"
    with pytest.raises(MailboxSyncError) as err:
        mailbox.sync_mailbox(30, mailbox=FakeMailbox(imaplib.IMAP4.error("LOGIN failed")))
    assert err.value.code == "auth"
    with pytest.raises(MailboxSyncError) as err:
        mailbox.sync_mailbox(30, mailbox=FakeMailbox(OSError("no route")))
    assert err.value.code == "connect" and "imap.example.com" in err.value.message


@pytest.mark.parametrize("bad", [0, 366, -1, True, "30", 2.5, None])
def test_since_days_is_validated(world, bad):
    with pytest.raises(MailboxSyncError) as err:
        mailbox.sync_mailbox(bad, mailbox=FakeMailbox(FakeImap({})))
    assert err.value.code == "invalid_value"


def test_imap_date_uses_english_month_names():
    assert mailbox.imap_date(datetime(2026, 3, 5, tzinfo=UTC)) == "05-Mar-2026"
    assert mailbox.imap_date(datetime(2026, 12, 31, tzinfo=UTC)) == "31-Dec-2026"


def test_nothing_in_the_mailbox_leaves_a_full_message_body_anywhere(world):
    mailbox.sync_mailbox(30, mailbox=FakeMailbox(FakeImap(inbox(*FIXTURES))))
    with db.session() as conn:
        details = [r["detail"] for r in db.query(conn, "SELECT detail FROM events")]
    assert details and all(len(d) <= 300 for d in details)
    assert not any("Northwind-style" in d for d in details)  # the quoted original is gone


def test_a_second_campaign_does_not_confuse_matching(world):
    other = make_campaign(channels=("email",))
    company = add_company(other["id"])
    _, message = seed_person(other["id"], company, "Zed", "Moss", "zed.moss@example.org")
    add_send(message, "zed.moss@example.org", "<send-100@example.com>", "2026-10-12T00:00:00.000Z")
    run(eml("Zed <zed.moss@example.org>", "Re: hi", "Yes.", "<send-100@example.com>"))
    with db.session() as conn:
        camp = conn.execute(
            "SELECT p.campaign_id FROM events e JOIN sends s ON s.id = e.send_id "
            "JOIN messages m ON m.id = s.message_id JOIN people p ON p.id = m.person_id"
        ).fetchone()[0]
    assert camp == other["id"]
