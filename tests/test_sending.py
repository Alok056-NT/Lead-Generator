"""Email sending: readiness, compliance, dry run, queue, caps, quiet hours, stop rules, worker.

Everything is invented. The mailbox is a fake and smtplib is blocked, so nothing touches the
network; the clock and the sleep function are replaced so gaps cost no time.
"""

from __future__ import annotations

import random
import smtplib
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from qa_world import add_company, add_email, add_message, add_person, make_campaign

from leadgen import db
from leadgen.core.config import Settings, load_settings, save_settings
from leadgen.send.smtp_imap import MailboxError, SendResult
from leadgen.services import campaign as camp
from leadgen.services import sending
from leadgen.services.sending import SendingError

# Monday 2026-10-19 02:00 UTC = 10:00 in Singapore (outside quiet hours); in Berlin it is 04:00
# (inside quiet hours 20:00-08:00).
NOW = datetime(2026, 10, 19, 2, 0, tzinfo=UTC)
NAMES = ("Aria", "Marcus", "Lena", "Omar", "Priya", "Sven")


class Clock:
    """A clock the fake sleep moves forward."""

    def __init__(self, start: datetime = NOW) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


class FakeMailbox:
    """Stands in for SmtpImapMailbox: records every send, can fail on demand."""

    def __init__(self, configured: bool = True, test_ok: bool = True, errors=None) -> None:
        self._configured = configured
        self.test_ok = test_ok
        self.errors = list(errors or [])  # one entry per send: None or a MailboxError
        self.sent: list[tuple[str, str, str]] = []
        self.tests = 0
        self.user = "priya@brightharbour.example"
        self.from_email = "priya@brightharbour.example"
        self.host = "smtp.mail.example"

    def configured(self) -> bool:
        return self._configured

    def test(self):
        self.tests += 1
        return self.test_ok, "SMTP and IMAP both work." if self.test_ok else "SMTP login failed."

    def send(self, to, subject, body, headers=None):
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
            raise error
        self.sent.append((to, subject, body))
        return SendResult(message_id=f"<msg{len(self.sent)}@brightharbour.example>")


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(sending, "_utcnow", c)
    monkeypatch.setattr(sending, "_local_tz", lambda: UTC)
    return c


@pytest.fixture(autouse=True)
def no_smtp(monkeypatch):
    """No test may open an SMTP connection."""

    def boom(*args, **kwargs):
        raise AssertionError("a test tried to open an SMTP connection")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)


def configure(**sending_over) -> Settings:
    settings = Settings()
    settings.sender.name = "Priya Nair"
    settings.sender.role = "Partner"
    settings.sender.company = "Bright Harbour Training"
    settings.postal_address = "1 Example Road, Singapore 000000"
    for key, value in sending_over.items():
        setattr(settings.sending, key, value)
    save_settings(settings)
    return settings


def add_ready_email(cid: int, co: int, name: str, **message_fields) -> int:
    """A confirmed person with a verified email and an approved email message."""
    person = add_person(cid, co, name, "Tan")
    add_email(person, address=f"{name.lower()}.tan@northwind.example")
    body = (
        f"Hi {name},\n\nI saw that you lead learning and development at Northwind Logistics.\n\n"
        "At Bright Harbour Training we run short supervisor courses that fit around shift "
        "rosters. The courses are practical and taught by working practitioners, and they suit "
        "teams that cannot leave site for long. We also run programmes for new managers.\n\n"
        f"Would a 15-minute call to compare notes be useful, {name}?\n\nKind regards,\nPriya"
    )
    fields = {"status": "approved", "ai_review": "pass", "body": body} | message_fields
    return add_message(person, "email", **fields)


@pytest.fixture
def world(ready_db, clock):
    """An email campaign for Singapore with three approved emails, ready to send."""
    configure()
    c = make_campaign(channels=("email",))
    cid = c["id"]
    co = add_company(cid)
    messages = [add_ready_email(cid, co, n) for n in NAMES[:3]]
    sending.acknowledge_compliance(cid, "SG")
    return cid, co, messages


def sent_rows(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM sends" + (" WHERE status = ?" if status else "") + " ORDER BY id"
    with db.session() as conn:
        return db.query(conn, sql, (status,) if status else ())


def status_of(message_id: int) -> str:
    with db.session() as conn:
        return db.get(conn, "messages", message_id)["status"]


# --- quiet hours, caps ------------------------------------------------------------------------


def test_parse_hhmm():
    assert sending.parse_hhmm("08:00").hour == 8
    for bad in ("8", "24:00", "08:60", "abc", ""):
        with pytest.raises(ValueError):
            sending.parse_hhmm(bad)


@pytest.mark.parametrize(
    ("utc_hour", "tz", "quiet"),
    [
        (2, "Asia/Singapore", False),  # 10:00
        (12, "Asia/Singapore", True),  # 20:00 exactly: quiet starts
        (23, "Asia/Singapore", True),  # 07:00
        (0, "Asia/Singapore", False),  # 08:00 exactly: quiet ended
        (2, "Europe/Berlin", True),  # 04:00 (UTC+2 in October)
        (12, "Europe/Berlin", False),  # 14:00
        (2, "America/New_York", True),  # 22:00 the evening before
    ],
)
def test_quiet_hours_follow_the_recipients_time_zone(utc_hour, tz, quiet):
    moment = datetime(2026, 10, 19, utc_hour, 0, tzinfo=UTC)
    assert sending.in_quiet_hours(moment, tz, "20:00", "08:00") is quiet


def test_quiet_hours_window_without_wrap_and_equal_times():
    moment = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    assert sending.in_quiet_hours(moment, "UTC", "12:00", "14:00") is True
    assert sending.in_quiet_hours(moment, "UTC", "14:00", "16:00") is False
    assert sending.in_quiet_hours(moment, "UTC", "08:00", "08:00") is False


def test_daily_cap_and_warm_up_math():
    s = Settings().sending  # 20, +5 a day, max 50
    assert [sending.daily_cap(s, d) for d in (0, 1, 2, 6, 7, 30)] == [20, 25, 30, 50, 50, 50]
    s.max_daily_cap = 22
    assert sending.daily_cap(s, 5) == 22
    assert sending.daily_cap(s, -3) == 20


def test_cap_status_counts_all_campaigns_and_grows_with_days(world, clock):
    cid, co, messages = world
    other = make_campaign(channels=("email",))
    other_co = add_company(other["id"])
    other_msg = add_ready_email(other["id"], other_co, "Lena")
    settings = load_settings()
    assert sending.cap_status(settings, clock())["cap_today"] == 20
    with db.session() as conn:
        for mid, when in (
            (messages[0], clock.now - timedelta(days=3)),  # first send, 3 days ago
            (messages[1], clock.now - timedelta(hours=1)),
            (other_msg, clock.now - timedelta(minutes=5)),
        ):
            db.insert(conn, "sends", message_id=mid, status="sent", sent_at=sending._stamp(when))
    cap = sending.cap_status(settings, clock())
    assert cap["days_since_first_send"] == 3
    assert cap["cap_today"] == 35
    assert cap["sent_today"] == 2  # the 3-day-old one is not today's; both campaigns count
    assert cap["remaining"] == 33


def test_a_send_just_before_midnight_does_not_count_for_the_next_day(world, clock):
    cid, co, messages = world
    with db.session() as conn:
        db.insert(
            conn, "sends", message_id=messages[0], status="sent",
            sent_at=sending._stamp(datetime(2026, 10, 18, 23, 59, tzinfo=UTC)),
        )  # fmt: skip
    cap = sending.cap_status(load_settings(), datetime(2026, 10, 19, 0, 1, tzinfo=UTC))
    assert cap["sent_today"] == 0 and cap["days_since_first_send"] == 1


# --- compliance acknowledgement ---------------------------------------------------------------


def test_acknowledgement_is_stored_with_a_timestamp(world):
    cid, _, _ = world
    reply = sending.acknowledge_compliance(cid, "sg")
    assert reply["counts_for_sending"] is True
    assert reply["rule"]["country"] == "SG" and reply["rule"]["subject_label"] == "<ADV>"
    value = sending._acks(cid)["SG"]
    assert value["at"].endswith("Z") and value["consent_basis"] == ""


def test_acknowledging_a_country_outside_the_campaign_is_refused(world):
    with pytest.raises(SendingError) as err:
        sending.acknowledge_compliance(world[0], "DE")
    assert err.value.code == "not_a_campaign_country"
    with pytest.raises(SendingError) as err:
        sending.acknowledge_compliance(world[0], "Atlantis")
    assert err.value.code == "invalid_value"


def test_a_block_without_consent_country_needs_a_consent_basis(world):
    cid, co, _ = world
    camp.update_campaign(cid, countries=["SG", "DE"])
    reply = sending.acknowledge_compliance(cid, "DE")
    assert reply["counts_for_sending"] is False and "consent_basis" in reply["message"]
    report = sending.check_send_readiness(cid, mailbox=FakeMailbox())
    assert "compliance:DE" in report["missing"] and report["ready"] is False
    reply = sending.acknowledge_compliance(cid, "DE", "  Opted in via the\nsign-up form ")
    assert reply["counts_for_sending"] is True
    assert sending._acks(cid)["DE"]["consent_basis"] == "Opted in via the sign-up form"
    assert (
        "compliance:DE" not in sending.check_send_readiness(cid, mailbox=FakeMailbox())["missing"]
    )


def test_unknown_campaign_is_not_found(ready_db):
    with pytest.raises(camp.CampaignError):
        sending.check_send_readiness(99, mailbox=FakeMailbox())


# --- readiness --------------------------------------------------------------------------------


def readiness(cid, mailbox=None, **kw):
    return sending.check_send_readiness(cid, mailbox=mailbox or FakeMailbox(), **kw)


def item(report, item_id):
    return next(i for i in report["checklist"] if i["id"] == item_id)


def test_a_complete_setup_is_ready(world):
    cid, _, _ = world
    box = FakeMailbox()
    report = readiness(cid, box)
    assert report["ready"] is True and report["missing"] == []
    assert box.tests == 1
    assert item(report, "compliance:SG")["status"] == "ok"
    assert item(report, "approved_messages")["detail"].startswith("3 approved")
    assert report["cap"]["cap_today"] == 20 and report["queue"]["queued"] == 0
    assert "send_emails" in report["next_step"]


def test_mailbox_test_is_skipped_when_asked(world):
    box = FakeMailbox()
    report = readiness(world[0], box, test_mailbox=False)
    assert box.tests == 0
    assert item(report, "mailbox_test")["status"] == "not_checked"
    assert report["ready"] is False and report["missing"] == []


@pytest.mark.parametrize(
    ("change", "missing_id"),
    [
        (lambda cid: FakeMailbox(configured=False), "mailbox_configured"),
        (lambda cid: FakeMailbox(test_ok=False), "mailbox_test"),
    ],
)
def test_mailbox_problems_are_missing_items(world, change, missing_id):
    cid, _, _ = world
    report = readiness(cid, change(cid))
    assert missing_id in report["missing"] and report["ready"] is False
    assert item(report, missing_id)["fix"]


def test_sender_identity_and_postal_address_are_required(world):
    cid, _, _ = world
    settings = load_settings()
    settings.sender.company = ""
    settings.postal_address = ""
    save_settings(settings)
    report = readiness(cid)
    assert {"sender_identity", "postal_address"} <= set(report["missing"])
    assert "settings.yaml" in item(report, "postal_address")["fix"]


def test_the_sender_address_falls_back_to_the_mailbox_user(world):
    cid, _, _ = world
    assert load_settings().sender.email == ""
    assert "sender_identity" not in readiness(cid)["missing"]
    box = FakeMailbox()
    box.from_email = ""
    assert "sender_identity" in readiness(cid, box)["missing"]


def test_invalid_sending_settings_are_reported(world):
    cid, _, _ = world
    configure(quiet_start="8pm")
    report = readiness(cid)
    assert "sending_settings" in report["missing"]
    assert "quiet hours" in item(report, "sending_settings")["detail"]
    configure(daily_cap=60, max_daily_cap=50)
    assert "sending_settings" in readiness(cid)["missing"]
    configure(min_gap_s=200, max_gap_s=100)
    assert "sending_settings" in readiness(cid)["missing"]
    configure(max_bounce_rate=5)
    assert "sending_settings" in readiness(cid)["missing"]


def test_every_target_country_needs_its_own_acknowledgement(world):
    cid, _, _ = world
    camp.update_campaign(cid, countries=["SG", "MY"])
    assert readiness(cid)["missing"] == ["compliance:MY"]


def test_no_approved_email_means_not_ready(ready_db, clock):
    configure()
    c = make_campaign(channels=("email",))
    sending.acknowledge_compliance(c["id"], "SG")
    report = readiness(c["id"])
    assert report["missing"] == ["approved_messages"]


def test_a_linkedin_only_campaign_is_not_ready_for_email(ready_db, clock):
    configure()
    c = make_campaign(channels=("linkedin",))
    sending.acknowledge_compliance(c["id"], "SG")
    assert "email_channel" in readiness(c["id"])["missing"]


def test_unreviewed_emails_do_not_pass_the_send_check(ready_db, clock):
    configure()
    c = make_campaign(channels=("email",))
    co = add_company(c["id"])
    add_ready_email(c["id"], co, "Aria", ai_review="", status="qa_pass")
    sending.acknowledge_compliance(c["id"], "SG")
    assert "approved_messages" in readiness(c["id"])["missing"]


def test_paused_state_and_bounce_rate_and_cap_show_up(world, clock):
    cid, _, messages = world
    sending.pause("looking into it")
    report = readiness(cid)
    assert "not_paused" in report["missing"]
    assert "looking into it" in item(report, "not_paused")["detail"]
    sending.resume()
    assert "not_paused" not in readiness(cid)["missing"]
    make_bounces(messages, sent=2, refused=3)
    assert "bounce_rate" in readiness(cid)["missing"]
    configure(daily_cap=1, max_daily_cap=1)
    with db.session() as conn:
        db.insert(
            conn, "sends", message_id=messages[0], status="sent",
            sent_at=sending._stamp(clock.now - timedelta(minutes=1)),
        )  # fmt: skip
    assert "daily_cap" in readiness(cid)["missing"]


def make_bounces(messages, sent: int, refused: int, start_minutes_ago: int = 600) -> None:
    """Insert sends rows directly: `sent` delivered and `refused` refused addresses."""
    rows = ["sent"] * sent + ["refused"] * refused
    with db.session() as conn:
        for n, status in enumerate(rows):
            when = sending._stamp(NOW - timedelta(minutes=start_minutes_ago - n))
            db.insert(
                conn, "sends", message_id=messages[n % len(messages)], status=status,
                sent_at=when if status == "sent" else None,
            )  # fmt: skip


# --- dry run and the real run -----------------------------------------------------------------


def test_dry_run_shows_the_exact_emails_and_opens_no_connection(world):
    cid, _, messages = world
    box = FakeMailbox()
    reply = sending.send_emails(cid, limit=2, mailbox=box)  # defaults: dry_run=True
    assert reply["dry_run"] is True and reply["count"] == 2
    assert box.tests == 0 and box.sent == [] and sent_rows() == []
    first = reply["emails"][0]
    assert first["to"] == "aria.tan@northwind.example"
    assert first["subject"].startswith("<ADV> ")  # Singapore rule
    assert first["body"].rstrip().endswith("I won't email you again.")
    assert "\n\n--\nPriya Nair\nPartner, Bright Harbour Training\n1 Example Road" in first["body"]
    assert first["in_quiet_hours_now"] is False
    assert [e["message_id"] for e in reply["emails"]] == messages[:2]
    assert status_of(messages[0]) == "approved"  # nothing changed


def test_dry_run_flags_what_would_block_a_real_send(world):
    cid, _, _ = world
    settings = load_settings()
    settings.postal_address = ""
    save_settings(settings)
    reply = sending.send_emails(cid, mailbox=FakeMailbox())
    assert "postal_address" in reply["would_be_blocked_by"]
    assert "Not ready" in reply["next_step"]


def test_subject_label_is_applied_once_and_only_when_the_rule_requires_it():
    from leadgen.core.compliance import rule_for

    assert sending.labelled_subject("Training  for Northwind", rule_for("SG")) == (
        "<ADV> Training for Northwind"
    )
    assert sending.labelled_subject("<ADV> Hello", rule_for("SG")) == "<ADV> Hello"
    assert sending.labelled_subject("Hello", rule_for("MY")) == "Hello"
    assert sending.labelled_subject("Hello", None) == "Hello"


def test_unacknowledged_country_is_skipped_with_a_reason(world):
    cid, _, _ = world
    with db.session() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE 'compliance_ack:%'")
    reply = sending.send_emails(cid, mailbox=FakeMailbox())
    assert reply["count"] == 0
    assert reply["skipped_by_reason"] == {"compliance_not_acknowledged": 3}


def test_a_guessed_address_is_not_sent_to_a_block_without_consent_country(world):
    cid, co, _ = world
    camp.update_campaign(cid, countries=["DE"], allow_guess_emails=True)
    sending.acknowledge_compliance(cid, "DE", "Customers who opted in")
    person = add_person(cid, co, "Jonas", "Weber", country="Germany")
    add_email(person, status="guess", address="jonas.weber@northwind.example")
    mid = add_message(person, "email", status="approved", ai_review="pass")
    ctx = sending._load_ctx(cid)
    cand, why = sending._candidate(ctx, mid)
    assert cand is None and why == "guessed_address_needs_consent"


def test_candidate_skips_suppressed_role_and_invalid_addresses(world):
    cid, co, _ = world
    ctx = sending._load_ctx(cid)
    cases = {}
    for name, address in (("Eve", "info@northwind.example"), ("Finn", "finn@@northwind.example"),
                          ("Gus", "gus@northwind.example")):  # fmt: skip
        person = add_person(cid, co, name, "Ng")
        add_email(person, address=address)
        cases[name] = add_message(person, "email", status="approved", ai_review="pass")
    assert sending._candidate(ctx, cases["Eve"])[1] == "role_address"
    assert sending._candidate(ctx, cases["Finn"])[1] == "invalid_address"
    ok, why = sending._candidate(ctx, cases["Gus"])
    assert ok is not None and why == ""
    ctx.sup_emails.add("gus@northwind.example")
    assert sending._candidate(ctx, cases["Gus"])[1] == "suppressed"
    ctx.sup_emails.clear()
    ctx.sup_domains.add("northwind.example")
    assert sending._candidate(ctx, cases["Gus"])[1] == "suppressed"


def test_candidate_needs_a_known_country(world):
    cid, co, _ = world
    camp.update_campaign(cid, countries=["SG", "MY"])
    person = add_person(cid, co, "Hana", "Lim", country="")
    add_email(person, address="hana.lim@northwind.example")
    mid = add_message(person, "email", status="approved", ai_review="pass")
    assert sending._candidate(sending._load_ctx(cid), mid)[1] == "country_unknown"


def test_real_run_needs_confirm(world):
    cid, _, _ = world
    reply = sending.send_emails(cid, dry_run=False, mailbox=FakeMailbox())
    assert reply["needs_confirmation"] is True and reply["count"] == 3
    assert sent_rows() == []


def test_real_run_is_refused_when_not_ready(world):
    cid, _, _ = world
    with pytest.raises(SendingError) as err:
        sending.send_emails(cid, dry_run=False, confirm=True, mailbox=FakeMailbox(test_ok=False))
    assert err.value.code == "not_ready" and "mailbox_test" in err.value.message
    assert sent_rows() == []


@pytest.mark.parametrize("limit", [0, 51, -1, True, "3"])
def test_limit_must_be_in_range(world, limit):
    with pytest.raises(SendingError) as err:
        sending.send_emails(world[0], limit=limit)
    assert err.value.code == "invalid_value"


def test_real_run_queues_starts_the_worker_and_sends_nothing_itself(world):
    cid, _, messages = world
    started = []
    box = FakeMailbox()
    reply = sending.send_emails(
        cid,
        limit=2,
        dry_run=False,
        confirm=True,
        mailbox=box,
        spawn=lambda: started.append(1) or True,
    )
    assert reply["queued"] == 2 and reply["worker_started"] is True and started == [1]
    assert box.sent == []  # the worker sends, not this call
    rows = sent_rows()
    assert [r["message_id"] for r in rows] == messages[:2]
    assert {r["status"] for r in rows} == {"queued"}
    assert rows[0]["to_email"] == "aria.tan@northwind.example" and rows[0]["provider"] == "smtp"
    assert reply["queue"]["queued"] == 2
    # a second call queues the rest and never the same message twice
    again = sending.send_emails(
        cid, limit=5, dry_run=False, confirm=True, mailbox=box, spawn=lambda: True
    )
    assert again["queued"] == 1
    assert sorted(r["message_id"] for r in sent_rows()) == sorted(messages)


def test_worker_is_not_started_twice(world):
    cid, _, _ = world
    sending._heartbeat()  # a live worker
    started = []
    reply = sending.send_emails(
        cid, dry_run=False, confirm=True, mailbox=FakeMailbox(), spawn=lambda: started.append(1)
    )
    assert reply["worker_already_running"] is True and started == []


def test_queue_never_exceeds_the_daily_cap(world):
    cid, co, _ = world
    configure(daily_cap=2, max_daily_cap=2)
    reply = sending.send_emails(
        cid, limit=10, dry_run=False, confirm=True, mailbox=FakeMailbox(), spawn=lambda: True
    )
    assert reply["queued"] == 2
    full = sending.send_emails(
        cid, limit=10, dry_run=False, confirm=True, mailbox=FakeMailbox(), spawn=lambda: True
    )
    assert full["queued"] == 0 and "cap" in full["next_step"]


def test_spawn_starts_a_detached_leadgen_process(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    assert sending._spawn_worker() is True
    cmd, kwargs = calls[0]
    assert cmd[-2:] == ["leadgen", "send-worker"] and cmd[1] == "-m"
    assert kwargs["stdout"] is subprocess.DEVNULL and kwargs["close_fds"] is True
    assert "creationflags" in kwargs or kwargs.get("start_new_session") is True

    def fail(*a, **k):
        raise OSError("no")

    monkeypatch.setattr(subprocess, "Popen", fail)
    assert sending._spawn_worker() is False


# --- the worker -------------------------------------------------------------------------------


def queue_all(cid, limit=10):
    return sending.send_emails(
        cid, limit=limit, dry_run=False, confirm=True, mailbox=FakeMailbox(), spawn=lambda: True
    )


def run(clock, box, seed=1):
    return sending.run_worker(mailbox=box, sleep=clock.sleep, rng=random.Random(seed))


def test_worker_sends_the_queue_with_random_gaps(world, clock):
    cid, _, messages = world
    queue_all(cid)
    box = FakeMailbox()
    result = run(clock, box)
    assert result == {"started": True, "sent": 3, "reason": "queue_empty"}
    assert [s[0] for s in box.sent] == [f"{n.lower()}.tan@northwind.example" for n in NAMES[:3]]
    assert all(s[1].startswith("<ADV> ") and "reply 'unsubscribe'" in s[2] for s in box.sent)
    rows = sent_rows()
    assert [r["status"] for r in rows] == ["sent"] * 3
    assert rows[0]["provider_message_id"] == "<msg1@brightharbour.example>"
    assert all(status_of(m) == "sent" for m in messages)
    # two gaps (before the 2nd and 3rd email), each between min and max, cut into 20 s slices
    sent_times = [datetime.fromisoformat(r["sent_at"]) for r in rows]
    gaps = [(b - a).total_seconds() for a, b in zip(sent_times, sent_times[1:], strict=False)]
    assert len(gaps) == 2 and all(60 <= g <= 180 for g in gaps)
    assert all(s <= sending.NAP_SLICE_S for s in clock.slept)
    assert sum(clock.slept) == pytest.approx(sum(gaps), abs=0.01)
    assert sending.worker_status()["running"] is False
    assert sending.worker_status()["reason"] == "queue_empty"


def test_the_first_email_waits_for_the_gap_after_the_last_send(world, clock):
    cid, _, messages = world
    queue_all(cid, limit=1)
    with db.session() as conn:  # an email went out 10 seconds ago from another campaign
        db.insert(
            conn, "sends", message_id=messages[2], status="sent",
            sent_at=sending._stamp(clock.now - timedelta(seconds=10)),
        )  # fmt: skip
    box = FakeMailbox()
    run(clock, box)
    assert len(box.sent) == 1
    assert 50 <= sum(clock.slept) <= 170  # gap 60-180 minus the 10 s already passed


def test_worker_does_not_send_in_the_recipients_quiet_hours(world, clock):
    cid, _, messages = world
    queue_all(cid)
    clock.now = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)  # 22:00 in Singapore
    box = FakeMailbox()
    result = run(clock, box)
    assert result["sent"] == 0 and result["reason"] == "quiet_hours"
    assert box.sent == [] and {r["status"] for r in sent_rows()} == {"queued"}
    clock.now = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)  # 09:00 next day
    assert run(clock, FakeMailbox())["sent"] == 3


def test_worker_stops_at_the_daily_cap(world, clock):
    cid, _, _ = world
    queue_all(cid)
    configure(daily_cap=2, max_daily_cap=2)
    box = FakeMailbox()
    result = run(clock, box)
    assert result["sent"] == 2 and result["reason"] == "daily_cap_reached"
    assert [r["status"] for r in sent_rows()] == ["sent", "sent", "queued"]


def test_pause_switch_stops_the_worker_before_any_email(world, clock):
    cid, _, _ = world
    queue_all(cid)
    sending.pause()
    box = FakeMailbox()
    result = run(clock, box)
    assert result["reason"] == "paused" and box.sent == []
    sending.resume()
    assert run(clock, box)["sent"] == 3


def test_pausing_while_the_worker_runs_stops_it_between_emails(world, clock):
    cid, _, _ = world
    queue_all(cid)
    box = FakeMailbox()
    original = clock.sleep

    def pausing_sleep(seconds):
        original(seconds)
        sending.pause()  # the user hits the kill switch during the first gap

    result = sending.run_worker(mailbox=box, sleep=pausing_sleep, rng=random.Random(1))
    assert result["sent"] == 1 and result["reason"] == "paused"


def test_a_high_bounce_rate_stops_and_pauses_sending(world, clock):
    cid, _, messages = world
    queue_all(cid)
    make_bounces(messages, sent=3, refused=2)  # 2 of 5 = 40% > 5%
    box = FakeMailbox()
    result = run(clock, box)
    assert result["reason"] == "bounce_rate" and box.sent == []
    assert load_settings().sending.paused is True
    assert "bounce" in sending.queue_status()["pause_reason"]


def test_few_sends_never_trigger_the_bounce_rule(world, clock):
    cid, _, messages = world
    make_bounces(messages, sent=1, refused=2)  # 3 sends: below the minimum sample
    assert sending.bounce_status(load_settings())["exceeded"] is False


def test_bounce_events_from_the_mailbox_sync_count(world, clock):
    cid, _, messages = world
    make_bounces(messages, sent=10, refused=0)
    settings = load_settings()
    assert sending.bounce_status(settings)["exceeded"] is False
    ids = [r["id"] for r in sent_rows()][:2]
    with db.session() as conn:
        for send_id in ids:
            db.insert(conn, "events", send_id=send_id, type="bounce", at=db.now())
    status = sending.bounce_status(settings)
    assert status["bounced"] == 2 and status["exceeded"] is True  # 20% > 5%


def test_three_failed_sends_in_a_row_stop_and_pause_sending(world, clock):
    cid, co, _ = world
    for n in NAMES[3:5]:
        add_ready_email(cid, co, n)
    queue_all(cid)
    down = MailboxError("connect", "Could not reach smtp.mail.example:587 (timed out).")
    box = FakeMailbox(errors=[down, down, down])
    result = run(clock, box)
    assert result["sent"] == 0 and result["reason"] == "send_errors"
    rows = sent_rows()
    assert [r["status"] for r in rows] == ["failed", "failed", "failed", "queued", "queued"]
    assert rows[0]["error"].startswith("connect:")
    assert load_settings().sending.paused is True
    # after the user fixes the problem and resumes, the old errors are forgotten
    sending.resume()
    assert sending.consecutive_errors() == 0
    assert run(clock, FakeMailbox())["sent"] == 2


def test_a_success_resets_the_error_streak(world, clock):
    cid, _, _ = world
    queue_all(cid)
    err = MailboxError("smtp", "The mail server answered: 451 try later.")
    box = FakeMailbox(errors=[err, None, err])
    result = run(clock, box)
    assert result["reason"] == "queue_empty" and result["sent"] == 1
    assert [r["status"] for r in sent_rows()] == ["failed", "sent", "failed"]


def test_a_refused_recipient_is_not_retried_and_counts_as_a_bounce(world, clock):
    cid, _, messages = world
    queue_all(cid)
    refused = MailboxError("recipient_refused", "The mail server refused the recipient.")
    box = FakeMailbox(errors=[refused])
    result = run(clock, box)
    assert result["sent"] == 2
    assert [r["status"] for r in sent_rows()] == ["refused", "sent", "sent"]
    assert status_of(messages[0]) == "approved"
    # never queued again
    again = sending.send_emails(cid, mailbox=FakeMailbox())
    assert messages[0] not in [e["message_id"] for e in again["emails"]]


def test_an_unsent_but_failed_message_can_be_queued_again(world, clock):
    cid, _, messages = world
    queue_all(cid, limit=1)
    err = MailboxError("connect", "timed out")
    run(clock, FakeMailbox(errors=[err]))
    assert sent_rows()[0]["status"] == "failed"
    again = sending.send_emails(cid, limit=1, mailbox=FakeMailbox())
    assert [e["message_id"] for e in again["emails"]] == [messages[0]]


def test_a_message_that_is_no_longer_approved_is_skipped(world, clock):
    cid, _, messages = world
    queue_all(cid)
    with db.session() as conn:
        db.update(conn, "messages", messages[1], ai_review="reject", status="rejected")
    box = FakeMailbox()
    run(clock, box)
    assert [r["status"] for r in sent_rows()] == ["sent", "skipped", "sent"]
    assert sent_rows()[1]["error"] == "no_longer_approved"
    assert len(box.sent) == 2


def test_an_address_suppressed_after_queueing_is_not_emailed(world, clock):
    cid, _, _ = world
    queue_all(cid)
    with db.session() as conn:
        db.insert(
            conn, "suppression", kind="email", value="marcus.tan@northwind.example", reason="asked"
        )
    box = FakeMailbox()
    run(clock, box)
    assert [s[0] for s in box.sent] == ["aria.tan@northwind.example", "lena.tan@northwind.example"]
    assert sent_rows()[1]["status"] == "skipped"


def test_an_acknowledgement_removed_after_queueing_blocks_the_send(world, clock):
    cid, _, _ = world
    queue_all(cid)
    with db.session() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE 'compliance_ack:%'")
    box = FakeMailbox()
    result = run(clock, box)
    assert box.sent == [] and result["reason"] == "queue_empty"
    assert {r["error"] for r in sent_rows()} == {"compliance_not_acknowledged"}


def test_an_unconfigured_mailbox_stops_the_worker(world, clock):
    cid, _, _ = world
    queue_all(cid)
    result = run(clock, FakeMailbox(configured=False))
    assert result["reason"] == "mailbox_not_configured" and result["sent"] == 0


def test_only_one_worker_runs_at_a_time(world, clock):
    cid, _, _ = world
    queue_all(cid)
    sending._heartbeat()  # another worker is alive
    assert (
        sending.run_worker(mailbox=FakeMailbox(), sleep=clock.sleep)["reason"] == "already_running"
    )
    clock.now += timedelta(seconds=sending.HEARTBEAT_TTL_S + 1)  # it died: heartbeat is stale
    assert run(clock, FakeMailbox())["sent"] == 3


def test_an_interrupted_send_is_reported_and_never_retried(world, clock):
    cid, _, messages = world
    queue_all(cid, limit=1)
    with db.session() as conn:
        conn.execute("UPDATE sends SET status = 'sending'")
    status = sending.queue_status(cid)
    assert status["uncertain"] == 1 and "Sent folder" in status["uncertain_hint"]
    again = sending.send_emails(cid, limit=5, mailbox=FakeMailbox())
    assert messages[0] not in [e["message_id"] for e in again["emails"]]


def test_every_email_goes_to_exactly_one_recipient(world, clock):
    cid, _, _ = world
    queue_all(cid)
    box = FakeMailbox()
    run(clock, box)
    for to, _subject, _body in box.sent:
        assert "," not in to and ";" not in to and to.count("@") == 1


def test_emails_left_in_the_queue_get_a_new_worker_on_the_next_confirmed_call(world, clock):
    cid, _, _ = world
    queue_all(cid)
    clock.now = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)  # Singapore night: worker stops
    assert run(clock, FakeMailbox())["reason"] == "quiet_hours"
    clock.now = NOW
    started = []
    reply = sending.send_emails(
        cid,
        dry_run=False,
        confirm=True,
        mailbox=FakeMailbox(),
        spawn=lambda: started.append(1) or True,
    )
    assert reply["queued"] == 0 and started == [1] and reply["worker_started"] is True
    assert "already queued" in reply["next_step"]
