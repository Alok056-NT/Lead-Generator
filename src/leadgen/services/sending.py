"""Opt-in sending of approved emails from the user's own mailbox (PLAN section 15.3, tools 31-32).

Everything here is guarded:

* `check_send_readiness` is a checklist (mailbox, sender identity, postal address, locale, a
  recorded compliance acknowledgement for every target country, approved messages, suppression
  list, kill switch, daily cap, bounce rate). Each item is `ok` or `missing` with how to fix it.
* `acknowledge_compliance` records that the user read a country's rule (and, for countries whose
  rule is `block_without_consent`, the basis on which they may contact people there).
* `send_emails` is a dry run by default (no network, shows each email exactly as it would be
  sent, footer and subject label included). A real run needs `confirm=True` and a ready
  checklist; it only *queues* the emails (`sends` rows with status `queued`) and starts the
  background worker, so a tool call never waits for the 60-180 s gaps between emails.
* `run_worker` (CLI `leadgen send-worker`) sends the queue: one recipient per email, daily cap
  with warm-up across all campaigns, quiet hours in the recipient's country, a random gap
  between emails, a re-check of the send gate and the suppression list before every email. It
  stops, and pauses sending until `leadgen send resume`, when the bounce rate of the last 20
  sends is above the limit or 3 sends in a row fail; `leadgen send pause` stops it at once.

Only messages that pass `review.export_gate(purpose="send")` are ever sent. Callers must run
`db.init_db()` first.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from leadgen import db
from leadgen.core import compliance, config
from leadgen.core import countries as countries_mod
from leadgen.core.compliance import EmailRule
from leadgen.send.footer import with_footer
from leadgen.send.smtp_imap import MailboxError, SmtpImapMailbox
from leadgen.services import campaign as campaign_mod
from leadgen.services import drafting, qa_leads, review
from leadgen.services.emails import clean_domain, is_role_address

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
BOUNCE_WINDOW = 20  # the last N sends are looked at
BOUNCE_MIN_SAMPLE = 5  # fewer sends than this never stop sending (one bounce would be 100%)
MAX_CONSECUTIVE_ERRORS = 3
HEARTBEAT_TTL_S = 120  # a worker that has not reported for this long is considered dead
NAP_SLICE_S = 20  # long waits are cut into slices so the worker keeps reporting
ACK_PREFIX = "compliance_ack"
WORKER_KEY = "send_worker"
STOP_KEY = "send_stop_reason"
ERRORS_RESET_KEY = "send_errors_reset_after"
ACTIVE_STATUSES = ("queued", "sending", "sent")
SKIP_SAMPLE = 50
_ADDRESS_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")


class SendingError(Exception):
    """A sending request failed. `code` is stable text; `hint` tells the AI what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, "error": {...}}` shape used by every tool."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


# --- clock and small helpers ----------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _local_tz() -> tzinfo:
    """The computer's own time zone (the user's "today" for the daily cap)."""
    return datetime.now().astimezone().tzinfo or UTC


def _stamp(moment: datetime) -> str:
    """Same text format as `db.now()`."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _kv_get(key: str) -> str:
    with db.session() as conn:
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return str(rows[0]["value"]) if rows else ""


def _kv_set(key: str, value: str) -> None:
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key=key, value=value)


def _kv_del(key: str) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def _json_or_empty(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# --- quiet hours and the daily cap ----------------------------------------------------------------


def parse_hhmm(text: str) -> dtime:
    """`"20:00"` -> time. Raises ValueError for anything else."""
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(text).strip())
    if not match:
        raise ValueError(f"{text!r} is not a time like 08:00")
    return dtime(int(match.group(1)), int(match.group(2)))


def in_quiet_hours(moment: datetime, tz_name: str, start: str, end: str) -> bool:
    """Is `moment` inside the quiet window `start`-`end` in the time zone `tz_name`?

    The window may wrap midnight (20:00-08:00). Equal start and end mean no quiet hours.
    """
    begin, finish = parse_hhmm(start), parse_hhmm(end)
    if begin == finish:
        return False
    local = moment.astimezone(ZoneInfo(tz_name)).time().replace(second=0, microsecond=0)
    if begin < finish:
        return begin <= local < finish
    return local >= begin or local < finish


def daily_cap(sending: config.Sending, days_since_first_send: int) -> int:
    """`min(daily_cap + warmup_step * days_since_first_send, max_daily_cap)`."""
    days = max(0, int(days_since_first_send))
    return max(0, min(sending.daily_cap + sending.warmup_step * days, sending.max_daily_cap))


def cap_status(settings: config.Settings, moment: datetime) -> dict[str, Any]:
    """Today's cap, how many were sent today (all campaigns) and how many are still allowed.

    "Today" is the computer's local calendar day. Queued emails are counted separately.
    """
    tz = _local_tz()
    today = moment.astimezone(tz).date()
    with db.session() as conn:
        first_row = db.query(conn, "SELECT MIN(sent_at) AS first FROM sends WHERE status = 'sent'")
        recent = db.query(
            conn,
            "SELECT sent_at FROM sends WHERE status = 'sent' AND sent_at >= ?",
            (_stamp(moment - timedelta(days=2)),),
        )
        queued = conn.execute("SELECT COUNT(*) FROM sends WHERE status = 'queued'").fetchone()[0]
    first = _parse(first_row[0]["first"]) if first_row and first_row[0]["first"] else None
    days = (today - first.astimezone(tz).date()).days if first else 0
    cap = daily_cap(settings.sending, days)
    sent_today = 0
    for row in recent:
        sent_at = _parse(row["sent_at"])
        if sent_at and sent_at.astimezone(tz).date() == today:
            sent_today += 1
    remaining = max(0, cap - sent_today)
    return {
        "cap_today": cap,
        "sent_today": sent_today,
        "remaining": remaining,
        "queued": int(queued),
        "remaining_after_queue": max(0, remaining - int(queued)),
        "days_since_first_send": days,
        "daily_cap": settings.sending.daily_cap,
        "warmup_step": settings.sending.warmup_step,
        "max_daily_cap": settings.sending.max_daily_cap,
    }


# --- stop conditions ------------------------------------------------------------------------------


def bounce_status(settings: config.Settings) -> dict[str, Any]:
    """Bounce rate over the last 20 sends: refused addresses plus sends with a `bounce` event."""
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT s.id, s.status, EXISTS (SELECT 1 FROM events e WHERE e.send_id = s.id "
            "AND e.type = 'bounce') AS bounced FROM sends s "
            "WHERE s.status IN ('sent', 'refused') ORDER BY s.id DESC LIMIT ?",
            (BOUNCE_WINDOW,),
        )
    total = len(rows)
    bounced = sum(1 for r in rows if r["status"] == "refused" or r["bounced"])
    rate = bounced / total if total else 0.0
    limit = settings.sending.max_bounce_rate
    return {
        "sample": total,
        "bounced": bounced,
        "rate": round(rate, 4),
        "limit": limit,
        "exceeded": total >= BOUNCE_MIN_SAMPLE and rate > limit,
    }


def consecutive_errors() -> int:
    """How many of the latest sends failed in a row (since the last `resume`), at most 3."""
    reset = int(_kv_get(ERRORS_RESET_KEY) or 0)
    with db.session() as conn:
        rows = db.query(
            conn,
            "SELECT status FROM sends WHERE id > ? AND status IN ('sent', 'failed') "
            "ORDER BY id DESC LIMIT ?",
            (reset, MAX_CONSECUTIVE_ERRORS),
        )
    count = 0
    for row in rows:
        if row["status"] != "failed":
            break
        count += 1
    return count


def stop_reason(settings: config.Settings) -> str:
    """Why sending must not go on right now: `paused`, `bounce_rate`, `send_errors` or ""."""
    if settings.sending.paused:
        return "paused"
    if bounce_status(settings)["exceeded"]:
        return "bounce_rate"
    if consecutive_errors() >= MAX_CONSECUTIVE_ERRORS:
        return "send_errors"
    return ""


def pause(reason: str = "paused by the user") -> None:
    """Switch sending off (`sending.paused = true`); the worker stops before its next email."""
    settings = config.load_settings()
    settings.sending.paused = True
    config.save_settings(settings)
    _kv_set(STOP_KEY, reason)


def resume() -> None:
    """Switch sending on again and forget earlier send errors (the bounce rate is not reset)."""
    settings = config.load_settings()
    settings.sending.paused = False
    config.save_settings(settings)
    _kv_del(STOP_KEY)
    with db.session() as conn:
        top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM sends").fetchone()[0]
    _kv_set(ERRORS_RESET_KEY, str(int(top)))


# --- compliance acknowledgements ------------------------------------------------------------------


def _ack_key(campaign_id: int, iso2: str) -> str:
    return f"{ACK_PREFIX}:{campaign_id}:{iso2}"


def _acks(campaign_id: int) -> dict[str, dict[str, Any]]:
    prefix = f"{ACK_PREFIX}:{campaign_id}:"
    with db.session() as conn:
        rows = db.query(conn, "SELECT key, value FROM settings WHERE key LIKE ?", (prefix + "%",))
    return {r["key"][len(prefix) :]: _json_or_empty(r["value"]) for r in rows}


def _rule_satisfied(rule: EmailRule, ack: dict[str, Any] | None) -> tuple[bool, str]:
    if not ack:
        return False, "not acknowledged"
    if (
        rule.default_policy == "block_without_consent"
        and not str(ack.get("consent_basis") or "").strip()
    ):
        return False, "this country needs a recorded basis for contacting people (consent_basis)"
    return True, ""


def _rule_view(rule: EmailRule) -> dict[str, Any]:
    return {
        "country": rule.country_iso2,
        "regime": rule.regime,
        "policy": rule.default_policy,
        "consent_model": rule.consent_model,
        "required_elements": list(rule.required_elements),
        "subject_label": rule.subject_label,
        "summary": rule.summary,
        "checked_on": rule.checked_on,
    }


def acknowledge_compliance(campaign_id: int, iso2: str, consent_basis: str = "") -> dict[str, Any]:
    """Record that the user read the rule for `iso2` and accepts it for this campaign.

    The AI must show the rule summary to the user first (the workflow guide says so). For a
    country whose rule is `block_without_consent` the acknowledgement counts only when
    `consent_basis` says on what basis the people there may be contacted. Raises `SendingError`.
    """
    camp = campaign_mod.get_campaign(campaign_id)
    try:
        code = countries_mod.get(iso2).iso2
    except countries_mod.UnknownCountryError:
        raise SendingError(
            "invalid_value", f"'{iso2}' is not a country in the table.", "Use a two-letter code."
        ) from None
    if code not in camp["countries"]:
        raise SendingError(
            "not_a_campaign_country",
            f"{code} is not one of this campaign's countries ({', '.join(camp['countries'])}).",
            "Acknowledge only the countries listed by check_send_readiness.",
        )
    rule = compliance.rule_for(code)
    basis = " ".join(str(consent_basis or "").split())
    record = {"at": db.now(), "consent_basis": basis, "rule_checked_on": rule.checked_on}
    _kv_set(_ack_key(camp["id"], code), json.dumps(record, sort_keys=True))
    ok, why = _rule_satisfied(rule, record)
    return {
        "ok": True,
        "campaign_id": camp["id"],
        "country": code,
        "recorded_at": record["at"],
        "counts_for_sending": ok,
        "rule": _rule_view(rule),
        "message": (
            f"Recorded for {code}."
            if ok
            else f"Recorded for {code}, but it does not count yet: {why}. Ask the user for the "
            "basis and call again with consent_basis."
        ),
    }


# --- recipients -----------------------------------------------------------------------------------


@dataclass
class _Ctx:
    """What is needed to judge any message of one campaign (loaded once per call)."""

    settings: config.Settings
    campaign: dict[str, Any]
    acks: dict[str, dict[str, Any]]
    sup_emails: set[str]
    sup_domains: set[str]


@dataclass
class Candidate:
    """One email as it would be sent."""

    message_id: int
    person_id: int
    person: str
    company: str
    to: str
    subject: str
    body: str
    country: str
    timezone: str
    email_status: str


def _load_ctx(campaign_id: int) -> _Ctx:
    camp = campaign_mod.get_campaign(campaign_id)
    with db.session() as conn:
        rows = db.query(
            conn, "SELECT value, kind FROM suppression WHERE kind IN ('email', 'domain')"
        )
    emails = {str(r["value"]).strip().lower() for r in rows if r["kind"] == "email"}
    domains = {clean_domain(str(r["value"])) for r in rows if r["kind"] == "domain"}
    return _Ctx(config.load_settings(), camp, _acks(camp["id"]), emails, {d for d in domains if d})


def _iso2(value: Any) -> str:
    if not value:
        return ""
    try:
        return countries_mod.get(str(value)).iso2
    except countries_mod.UnknownCountryError:
        return ""


def labelled_subject(subject: str, rule: EmailRule | None) -> str:
    """The subject with the rule's label (e.g. `<ADV>`) in front when the rule requires one."""
    text = " ".join(subject.split())
    label = rule.subject_label if rule else None
    if label and not text.startswith(label):
        return f"{label} {text}"
    return text


def _suppressed(ctx: _Ctx, address: str, company_domain: str) -> bool:
    host = clean_domain(address)
    for domain in (host, clean_domain(company_domain)):
        if domain and any(domain == d or domain.endswith("." + d) for d in ctx.sup_domains):
            return True
    return address in ctx.sup_emails


def _candidate(ctx: _Ctx, message_id: int) -> tuple[Candidate | None, str]:
    """Build the email for one message, or say why it must not go (`(None, reason)`)."""
    with db.session() as conn:
        msg = db.get(conn, "messages", message_id)
        if msg is None:
            return None, "message_missing"
        person = db.get(conn, "people", msg["person_id"])
        company = db.get(conn, "companies", person["company_id"]) if person else None
        email_rows = db.query(conn, "SELECT * FROM emails WHERE person_id = ?", (msg["person_id"],))
    if person is None or company is None or person["campaign_id"] != ctx.campaign["id"]:
        return None, "message_missing"
    if msg["channel"] != "email":
        return None, "not_an_email_message"
    sendable = drafting.sendable_email_statuses(ctx.campaign)
    row = qa_leads.pick_email([r for r in email_rows if r["status"] in sendable])
    if row is None:
        return None, "no_sendable_email"
    address = str(row["email"]).strip().lower()
    if not _ADDRESS_RE.match(address):
        return None, "invalid_address"
    if is_role_address(address):
        return None, "role_address"
    if _suppressed(ctx, address, str(company.get("domain") or "")):
        return None, "suppressed"
    if not str(msg["subject"]).strip() or not str(msg["body"]).strip():
        return None, "empty_message"
    campaign_countries = ctx.campaign["countries"]
    iso2 = _iso2(person.get("country")) or _iso2(company.get("country"))
    if not iso2 and len(campaign_countries) == 1:
        iso2 = campaign_countries[0]
    if not iso2:
        return None, "country_unknown"
    rule = compliance.rule_for(iso2)
    satisfied, _why = _rule_satisfied(rule, ctx.acks.get(iso2))
    if not satisfied:
        return None, "compliance_not_acknowledged"
    if rule.default_policy == "block_without_consent" and row["status"] == "guess":
        return None, "guessed_address_needs_consent"
    return (
        Candidate(
            message_id=msg["id"],
            person_id=person["id"],
            person=str(person["raw_name"]),
            company=str(company["name"]),
            to=address,
            subject=labelled_subject(msg["subject"], rule),
            body=with_footer(msg["body"], ctx.settings),
            country=iso2,
            timezone=countries_mod.get(iso2).timezone,
            email_status=str(row["status"]),
        ),
        "",
    )


def _active_message_ids(campaign_id: int) -> set[int]:
    """Messages with a queued, sending or sent row, or a refused address: never queued again."""
    with db.session() as conn:
        rows = conn.execute(
            "SELECT DISTINCT s.message_id FROM sends s JOIN messages m ON m.id = s.message_id "
            "JOIN people p ON p.id = m.person_id WHERE p.campaign_id = ? "
            "AND s.status IN ('queued', 'sending', 'sent', 'refused')",
            (campaign_id,),
        ).fetchall()
    return {int(r[0]) for r in rows}


def _email_message_ids(ids: list[int]) -> list[int]:
    out: list[int] = []
    with db.session() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" for _ in chunk)
            out += [
                int(r[0])
                for r in conn.execute(
                    f"SELECT id FROM messages WHERE channel = 'email' AND id IN ({marks})", chunk
                ).fetchall()
            ]
    return sorted(out)


def _pick(
    ctx: _Ctx, gate: dict[str, Any], limit: int
) -> tuple[list[Candidate], Counter[str], list[dict[str, Any]]]:
    """The next `limit` sendable emails of a campaign, the skip counts and a sample of skips."""
    busy = _active_message_ids(ctx.campaign["id"])
    wanted = [m for m in _email_message_ids(gate["messages"]["exportable_ids"]) if m not in busy]
    picked: list[Candidate] = []
    reasons: Counter[str] = Counter()
    sample: list[dict[str, Any]] = []
    for message_id in wanted:
        if len(picked) >= limit:
            break
        cand, why = _candidate(ctx, message_id)
        if cand is None:
            reasons[why] += 1
            if len(sample) < SKIP_SAMPLE:
                sample.append({"message_id": message_id, "reason": why})
        else:
            picked.append(cand)
    return picked, reasons, sample


# --- readiness ------------------------------------------------------------------------------------


def _item(item_id: str, ok: bool, detail: str, fix: str = "") -> dict[str, str]:
    return {
        "id": item_id,
        "status": "ok" if ok else "missing",
        "detail": detail,
        "fix": "" if ok else fix,
    }


def _sender_address(mailbox: SmtpImapMailbox, settings: config.Settings) -> str:
    """The From address: `sender.email`, else the mailbox user."""
    return settings.sender.email.strip() or str(mailbox.from_email or "").strip()


def _identity_ok(settings: config.Settings, mailbox: SmtpImapMailbox) -> bool:
    """Sender name, company and a From address are all set."""
    sender = settings.sender
    return bool(
        sender.name.strip() and sender.company.strip() and _sender_address(mailbox, settings)
    )


def _sender_problem(settings: config.Settings, mailbox: SmtpImapMailbox) -> str:
    """Why the sender details forbid sending right now (the readiness rules), or ""."""
    if not _identity_ok(settings, mailbox) or not settings.postal_address.strip():
        return "sender_details_missing"
    if _settings_problem(settings.sending):
        return "settings_invalid"
    return ""


def _settings_problem(sending: config.Sending) -> str:
    try:
        parse_hhmm(sending.quiet_start)
        parse_hhmm(sending.quiet_end)
    except ValueError as exc:
        return f"quiet hours: {exc}"
    if sending.min_gap_s < 0 or sending.max_gap_s < sending.min_gap_s:
        return "min_gap_s must be 0 or more and not above max_gap_s"
    if sending.daily_cap < 1 or sending.max_daily_cap < sending.daily_cap:
        return "daily_cap must be at least 1 and not above max_daily_cap"
    if sending.warmup_step < 0:
        return "warmup_step cannot be negative"
    if not 0 < sending.max_bounce_rate < 1:
        return "max_bounce_rate must be between 0 and 1 (for example 0.05)"
    return ""


def check_send_readiness(
    campaign_id: int,
    *,
    mailbox: SmtpImapMailbox | None = None,
    test_mailbox: bool = True,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The send-readiness checklist of a campaign (tool 31).

    `gate` is an already computed `export_gate(..., "send")`. `test_mailbox=False` skips the
    SMTP/IMAP log-in (the dry run must not touch the network); that item then shows
    `not_checked`. Raises `CampaignError` (not found).
    """
    camp = campaign_mod.get_campaign(campaign_id)
    settings = config.load_settings()
    mailbox = mailbox or SmtpImapMailbox.from_config()
    now = _utcnow()
    items: list[dict[str, str]] = []

    items.append(
        _item(
            "email_channel",
            "email" in camp["channels"],
            "The campaign uses the email channel."
            if "email" in camp["channels"]
            else "The campaign does not use the email channel.",
            "Add the email channel with update_campaign(channels=[..., 'email']), then draft "
            "emails.",
        )
    )
    configured = mailbox.configured()
    items.append(
        _item(
            "mailbox_configured",
            configured,
            f"Mailbox {mailbox.user} on {mailbox.host}." if configured else "No mailbox is set up.",
            "In a terminal run `leadgen mailbox set` (Gmail or Workspace: use an app password; "
            "see the user guide).",
        )
    )
    if not test_mailbox:
        items.append(
            {
                "id": "mailbox_test",
                "status": "not_checked",
                "detail": "Not tested in a dry run.",
                "fix": "",
            }
        )
    elif configured:
        ok, message = mailbox.test()
        items.append(
            _item(
                "mailbox_test",
                ok,
                message,
                "Fix the mailbox settings, then run `leadgen mailbox test`.",
            )
        )
    else:
        items.append(
            _item("mailbox_test", False, "Cannot test: no mailbox.", "Set the mailbox first.")
        )

    sender = settings.sender
    from_address = _sender_address(mailbox, settings)
    identity_ok = _identity_ok(settings, mailbox)
    items.append(
        _item(
            "sender_identity",
            identity_ok,
            f"{sender.name} at {sender.company} <{from_address}>."
            if identity_ok
            else "Sender name, company or email address is missing.",
            "Set sender.name, sender.company (and sender.role, sender.website) in settings.yaml; "
            "the sender email defaults to the mailbox user.",
        )
    )
    items.append(
        _item(
            "postal_address",
            bool(settings.postal_address.strip()),
            "A postal address is set." if settings.postal_address.strip() else "No postal address.",
            "Set postal_address in settings.yaml (a real address; several countries require it).",
        )
    )
    items.append(
        _item(
            "locale",
            bool(settings.locale.strip() and camp["locale"]),
            f"Locale {camp['locale'] or settings.locale}.",
            "Set locale in settings.yaml and on the campaign.",
        )
    )
    problem = _settings_problem(settings.sending)
    items.append(
        _item(
            "sending_settings",
            not problem,
            problem or "Caps, gaps and quiet hours look valid.",
            "Correct the `sending:` section of settings.yaml.",
        )
    )

    rules: list[dict[str, Any]] = []
    acks = _acks(camp["id"])
    if not camp["countries"]:
        items.append(
            _item("compliance", False, "The campaign has no target countries.", "Set countries.")
        )
    for rule in compliance.send_gate(camp["countries"]):
        ok, why = _rule_satisfied(rule, acks.get(rule.country_iso2))
        rules.append(_rule_view(rule) | {"acknowledged": ok})
        items.append(
            _item(
                f"compliance:{rule.country_iso2}",
                ok,
                f"Acknowledged on {acks[rule.country_iso2].get('at', '')}."
                if ok
                else f"{rule.country_iso2}: {why}.",
                "Show the user this country's rule (see `rules`), then call "
                "acknowledge_compliance(campaign_id, country, consent_basis). A country with "
                "policy block_without_consent also needs consent_basis: the basis on which the "
                "user may contact these people.",
            )
        )

    gate = gate or review.export_gate(camp["id"], "send")
    pickable = _email_message_ids(gate["messages"]["exportable_ids"])
    items.append(
        _item(
            "approved_messages",
            bool(pickable),
            f"{len(pickable)} approved email(s) pass the send check."
            if pickable
            else "No email passes the send check.",
            "Draft, run_qa and review the emails (record_review) until export_gate(purpose='send') "
            "shows some; blocked_by says what holds the rest back.",
        )
    )
    with db.session() as conn:
        suppressed = conn.execute("SELECT COUNT(*) FROM suppression").fetchone()[0]
    items.append(
        _item(
            "suppression_list",
            True,
            f"{suppressed} do-not-contact entr{'y' if suppressed == 1 else 'ies'} loaded.",
        )
    )
    items.append(
        _item(
            "not_paused",
            not settings.sending.paused,
            "Sending is on."
            if not settings.sending.paused
            else f"Sending is paused ({_kv_get(STOP_KEY) or 'by the user'}).",
            "After looking into the reason, run `leadgen send resume` in a terminal.",
        )
    )
    bounce = bounce_status(settings)
    items.append(
        _item(
            "bounce_rate",
            not bounce["exceeded"],
            f"{bounce['bounced']} of the last {bounce['sample']} sends bounced "
            f"({bounce['rate']:.0%}; stop above {bounce['limit']:.0%}).",
            "Clean the list (verified addresses only) before sending more; raising "
            "sending.max_bounce_rate in settings.yaml is not recommended.",
        )
    )
    cap = cap_status(settings, now)
    items.append(
        _item(
            "daily_cap",
            cap["remaining_after_queue"] > 0,
            f"{cap['sent_today']} sent today, {cap['queued']} queued, cap {cap['cap_today']} "
            f"(starts at {cap['daily_cap']}, +{cap['warmup_step']} a day, up to "
            f"{cap['max_daily_cap']}).",
            "Today's cap is used up; sending goes on tomorrow.",
        )
    )

    missing = [i["id"] for i in items if i["status"] == "missing"]
    ready = all(i["status"] == "ok" for i in items)
    reply: dict[str, Any] = {
        "ok": True,
        "campaign_id": camp["id"],
        "ready": ready,
        "missing": missing,
        "checklist": items,
        "rules": rules,
        "cap": cap,
        "queue": queue_status(camp["id"]),
        "disclaimer": compliance.DISCLAIMER,
    }
    if ready:
        reply["next_step"] = (
            "Ready. Call send_emails as a dry run (dry_run=true), show the user the emails, and "
            "only after a clear yes call it with dry_run=false and confirm=true."
        )
    else:
        reply["next_step"] = (
            "Not ready. Tell the user what is missing and how to fix it (see fix on each "
            f"item). Missing: {', '.join(missing) or 'mailbox test'}."
        )
    return reply


# --- queue and worker state -----------------------------------------------------------------------


def worker_status() -> dict[str, Any]:
    """The background worker's state: `running` (a live heartbeat), its last reason and counts."""
    state = _json_or_empty(_kv_get(WORKER_KEY))
    beat = _parse(state.get("heartbeat", "")) if state.get("heartbeat") else None
    live = bool(
        state.get("state") == "running"
        and beat
        and (_utcnow() - beat).total_seconds() < HEARTBEAT_TTL_S
    )
    return {
        "running": live,
        "state": "running"
        if live
        else (state.get("state") if state.get("state") != "running" else "stopped"),
        "reason": state.get("reason", ""),
        "sent_this_run": state.get("sent", 0),
        "last_heartbeat": state.get("heartbeat", ""),
    }


def queue_status(campaign_id: int | None = None) -> dict[str, Any]:
    """Counts of the `sends` rows by status (one campaign, or all), plus the worker and switches."""
    sql = (
        "SELECT s.status, COUNT(*) AS n FROM sends s JOIN messages m ON m.id = s.message_id "
        "JOIN people p ON p.id = m.person_id "
    )
    params: tuple[Any, ...] = ()
    if campaign_id is not None:
        sql += "WHERE p.campaign_id = ? "
        params = (campaign_id,)
    with db.session() as conn:
        rows = db.query(conn, sql + "GROUP BY s.status", params)
    counts = {r["status"]: r["n"] for r in rows}
    worker = worker_status()
    settings = config.load_settings()
    status: dict[str, Any] = {
        "queued": counts.get("queued", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "refused": counts.get("refused", 0),
        "skipped": counts.get("skipped", 0),
        "uncertain": counts.get("sending", 0),
        "worker": worker,
        "paused": settings.sending.paused,
        "pause_reason": _kv_get(STOP_KEY) if settings.sending.paused else "",
    }
    if status["uncertain"]:
        status["uncertain_hint"] = (
            "A send was interrupted before the result was known. Look in the mailbox's Sent "
            "folder before sending to that person again; it is not retried automatically."
        )
    return status


def _heartbeat(state: str = "running", reason: str = "", sent: int = 0, started: str = "") -> None:
    current = _json_or_empty(_kv_get(WORKER_KEY))
    current.update(
        {
            "pid": os.getpid(),
            "state": state,
            "reason": reason,
            "sent": sent,
            "heartbeat": _stamp(_utcnow()),
        }
    )
    if started:
        current["started"] = started
    _kv_set(WORKER_KEY, json.dumps(current, sort_keys=True))


def _acquire_lock() -> bool:
    """Take the single-worker lock unless a live worker holds it."""
    with db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = db.query(conn, "SELECT value FROM settings WHERE key = ?", (WORKER_KEY,))
        state = _json_or_empty(rows[0]["value"]) if rows else {}
        beat = _parse(state.get("heartbeat", "")) if state.get("heartbeat") else None
        if (
            state.get("state") == "running"
            and beat
            and (_utcnow() - beat).total_seconds() < HEARTBEAT_TTL_S
        ):
            return False
        now = _stamp(_utcnow())
        new = {"pid": os.getpid(), "state": "running", "reason": "", "sent": 0,
               "heartbeat": now, "started": now}  # fmt: skip
        db.upsert(conn, "settings", ["key"], key=WORKER_KEY, value=json.dumps(new, sort_keys=True))
    return True


def _spawn_worker() -> bool:
    """Start `leadgen send-worker` as a detached process. True if it was started."""
    cmd = [sys.executable, "-m", "leadgen", "send-worker"]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)  # noqa: S603 - fixed argument list, no shell
    except OSError:
        return False
    return True


# --- send_emails ----------------------------------------------------------------------------------


def _preview(cand: Candidate, moment: datetime, settings: config.Settings) -> dict[str, Any]:
    quiet = in_quiet_hours(
        moment, cand.timezone, settings.sending.quiet_start, settings.sending.quiet_end
    )
    return {
        "message_id": cand.message_id,
        "person_id": cand.person_id,
        "person": cand.person,
        "company": cand.company,
        "to": cand.to,
        "country": cand.country,
        "email_status": cand.email_status,
        "subject": cand.subject,
        "body": cand.body,
        "in_quiet_hours_now": quiet,
    }


def send_emails(
    campaign_id: int,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = True,
    confirm: bool = False,
    *,
    mailbox: SmtpImapMailbox | None = None,
    spawn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Preview (default) or queue the next `limit` approved emails of a campaign (tool 32).

    Dry run: no network; returns the emails exactly as they would be sent. A real run needs
    `confirm=True` and a ready checklist; it queues the emails and starts the background worker
    (it never sends inside this call) and returns the queue summary. Raises `SendingError`.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise SendingError(
            "invalid_value", f"limit must be 1 to {MAX_LIMIT}.", "Use a smaller limit."
        )
    camp = campaign_mod.get_campaign(campaign_id)
    ctx = _load_ctx(camp["id"])
    now = _utcnow()
    gate = review.export_gate(camp["id"], "send")
    cap = cap_status(ctx.settings, now)

    if dry_run:
        picked, reasons, sample = _pick(ctx, gate, limit)
        readiness = check_send_readiness(camp["id"], mailbox=mailbox, test_mailbox=False, gate=gate)
        blockers = [i for i in readiness["missing"]]
        return {
            "ok": True,
            "dry_run": True,
            "campaign_id": camp["id"],
            "count": len(picked),
            "emails": [_preview(c, now, ctx.settings) for c in picked],
            "skipped_by_reason": dict(reasons),
            "skipped": sample,
            "cap": cap,
            "would_be_blocked_by": blockers,
            "next_step": (
                "Show the user these emails. "
                + (
                    f"Not ready yet: {', '.join(blockers)}. Run check_send_readiness."
                    if blockers
                    else "To send, call again with dry_run=false and confirm=true after a clear "
                    "yes."
                )
            ),
        }

    if not confirm:
        picked, reasons, _ = _pick(ctx, gate, limit)
        return {
            "ok": True,
            "needs_confirmation": True,
            "campaign_id": camp["id"],
            "count": len(picked),
            "message": (
                f"{len(picked)} email(s) would be queued and sent from the user's mailbox, "
                "spaced out over time. Show the user a dry run, get a clear yes, then call "
                "again with dry_run=false and confirm=true."
            ),
        }

    readiness = check_send_readiness(camp["id"], mailbox=mailbox, test_mailbox=True, gate=gate)
    blockers = [m for m in readiness["missing"] if m != "daily_cap"]  # a full cap just queues 0
    if blockers:
        raise SendingError(
            "not_ready",
            f"Sending is not ready: {', '.join(blockers)}.",
            "Call check_send_readiness, tell the user what is missing and how to fix it.",
        )
    allowed = min(limit, cap["remaining_after_queue"])
    picked, reasons, sample = _pick(ctx, gate, allowed)
    queued_ids: list[int] = []
    with db.session() as conn:
        for cand in picked:
            queued_ids.append(
                db.insert(
                    conn,
                    "sends",
                    message_id=cand.message_id,
                    provider="smtp",
                    to_email=cand.to,
                    status="queued",
                )  # fmt: skip
            )
    started = False
    already = worker_status()["running"]
    waiting = int(queue_status()["queued"])  # also emails queued earlier and left by the worker
    if waiting and not already:
        started = (spawn or _spawn_worker)()
    reply: dict[str, Any] = {
        "ok": True,
        "dry_run": False,
        "campaign_id": camp["id"],
        "queued": len(queued_ids),
        "worker_started": started,
        "worker_already_running": already,
        "skipped_by_reason": dict(reasons),
        "skipped": sample,
        "cap": cap_status(ctx.settings, now),
        "queue": queue_status(camp["id"]),
    }
    if picked:
        reply["next_step"] = (
            f"{len(queued_ids)} email(s) queued. They go out one at a time with a "
            f"{ctx.settings.sending.min_gap_s}-{ctx.settings.sending.max_gap_s} second gap, "
            "never in the recipient's quiet hours, and sending stops by itself on a high bounce "
            "rate or repeated errors. Tell the user; check progress later with "
            "check_send_readiness (queue)."
        )
        if waiting and not started and not already:
            reply["next_step"] += (
                " The worker could not be started: run `leadgen send-worker` in a terminal."
            )
    else:
        reply["next_step"] = "Nothing new was queued: " + (
            "today's cap is used up."
            if allowed == 0
            else "no email is ready; see skipped_by_reason."
        )
        if started:
            reply["next_step"] += " The worker was started for the emails already queued."
    return reply


# --- the worker -----------------------------------------------------------------------------------


def _set_send(send_id: int, **values: Any) -> None:
    with db.session() as conn:
        db.update(conn, "sends", send_id, **values)


def _next_item(
    now: datetime, ctxs: dict[int, _Ctx], gates: dict[int, dict[str, Any]]
) -> tuple[tuple[dict[str, Any], Candidate] | None, bool]:
    """The first queued send that may go now, and whether some were held for quiet hours.

    Sends that must not go at all are marked `skipped` with the reason.
    """
    with db.session() as conn:
        queued = db.query(conn, "SELECT * FROM sends WHERE status = 'queued' ORDER BY id")
    deferred = False
    for row in queued:
        with db.session() as conn:
            msg = db.get(conn, "messages", row["message_id"])
            owner = (
                conn.execute(
                    "SELECT campaign_id FROM people WHERE id = ?", (msg["person_id"],)
                ).fetchone()
                if msg
                else None
            )
        if msg is None or owner is None:
            _set_send(row["id"], status="skipped", error="message_missing")
            continue
        if msg["status"] == "sent":
            _set_send(row["id"], status="skipped", error="already_sent")
            continue
        cid = int(owner[0])
        if cid not in ctxs:
            ctxs[cid] = _load_ctx(cid)
            gates[cid] = review.export_gate(cid, "send")
        if row["message_id"] not in set(gates[cid]["messages"]["exportable_ids"]):
            _set_send(row["id"], status="skipped", error="no_longer_approved")
            continue
        cand, why = _candidate(ctxs[cid], row["message_id"])
        if cand is None:
            _set_send(row["id"], status="skipped", error=why)
            continue
        sending = ctxs[cid].settings.sending
        if in_quiet_hours(now, cand.timezone, sending.quiet_start, sending.quiet_end):
            deferred = True
            continue
        return (row, cand), deferred
    return None, deferred


def _last_attempt_at() -> datetime | None:
    """When the newest send attempt (sent, refused or failed) was made, from any campaign."""
    with db.session() as conn:
        row = conn.execute(
            "SELECT MAX(sent_at) FROM sends WHERE status IN ('sent', 'refused', 'failed')"
        ).fetchone()
    return _parse(row[0]) if row and row[0] else None


def run_worker(
    *,
    mailbox: SmtpImapMailbox | None = None,
    sleep: Callable[[float], None] | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Send the queued emails one by one until the queue is empty or a stop rule applies.

    Returns `{"started", "sent", "reason"}`. `reason` is `queue_empty`, `quiet_hours`,
    `daily_cap_reached`, `paused`, `bounce_rate`, `send_errors`, `mailbox_not_configured`,
    `sender_details_missing` (sender name/company/address or postal address removed after
    queueing) or `settings_invalid`; the rows stay `queued`. The random gap is timed from the
    last *attempt* (sent, refused or failed), so a failing mailbox is not hammered.
    `sleep` and `rng` can be replaced in tests (the clock is `_utcnow`, also replaceable).
    """
    nap = sleep or time.sleep
    rng = rng or random.Random()
    if not _acquire_lock():
        return {"started": False, "sent": 0, "reason": "already_running"}
    started = _stamp(_utcnow())
    sent = 0
    reason = "queue_empty"
    try:
        mailbox = mailbox or SmtpImapMailbox.from_config()
        gap: float | None = None
        waited = False
        last_attempt = _last_attempt_at()
        while True:
            _heartbeat(sent=sent, started=started)
            if not mailbox.configured():
                reason = "mailbox_not_configured"
                break
            settings = config.load_settings()
            why = stop_reason(settings)
            if why:
                reason = why
                if why in ("bounce_rate", "send_errors"):
                    pause(
                        "the bounce rate went above the limit"
                        if why == "bounce_rate"
                        else f"{MAX_CONSECUTIVE_ERRORS} sends in a row failed"
                    )
                break
            problem = _sender_problem(settings, mailbox)
            if problem:
                reason = problem
                break
            now = _utcnow()
            if cap_status(settings, now)["remaining"] <= 0:
                reason = "daily_cap_reached"
                break
            found, deferred = _next_item(now, {}, {})
            if found is None:
                reason = "quiet_hours" if deferred else "queue_empty"
                break
            row, cand = found
            if gap is None:
                gap = rng.uniform(settings.sending.min_gap_s, settings.sending.max_gap_s)
            if last_attempt is not None and not waited:
                wait = (last_attempt + timedelta(seconds=gap) - now).total_seconds()
                if wait > 0:
                    left = wait
                    while left > 0:
                        step = min(NAP_SLICE_S, left)
                        nap(step)
                        _heartbeat(sent=sent, started=started)
                        left -= step
                    waited = True
                    continue  # everything is checked again after the wait
            waited = False
            gap = None
            _set_send(row["id"], status="sending")
            try:
                result = mailbox.send(cand.to, cand.subject, cand.body)
            except MailboxError as exc:
                stamp = _stamp(_utcnow())
                if exc.code == "invalid_message":  # rejected before any connection: no gap needed
                    _set_send(
                        row["id"], status="skipped", error=f"invalid_message: {exc.message}"[:300]
                    )
                    continue
                last_attempt = _parse(stamp)  # `sent_at` of a refused/failed row is the attempt
                if exc.code == "recipient_refused":
                    _set_send(
                        row["id"], status="refused", sent_at=stamp,
                        error=f"recipient_refused: {exc.message}"[:300],
                    )  # fmt: skip
                else:
                    _set_send(
                        row["id"], status="failed", sent_at=stamp,
                        error=f"{exc.code}: {exc.message}"[:300],
                    )  # fmt: skip
                continue
            stamp = _stamp(_utcnow())
            last_attempt = _parse(stamp)
            with db.session() as conn:
                db.update(
                    conn, "sends", row["id"], status="sent", sent_at=stamp,
                    provider_message_id=result.message_id, error="",
                )  # fmt: skip
                db.update(conn, "messages", cand.message_id, status="sent")
            sent += 1
    finally:
        _heartbeat(state="stopped", reason=reason, sent=sent, started=started)
    return {"started": True, "sent": sent, "reason": reason}
