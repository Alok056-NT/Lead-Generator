"""Honest sending analytics from the `sends` and `events` tables (PLAN section 15, tool 34).

Counts are per email sent (`sends.status = 'sent'`; refused, failed and skipped rows carry a
`sent_at` too but were never delivered to the mail server, so they are not counted):

* `sent`: emails handed to the mail server.
* `bounced`: sends with a `bounce` event; `delivered` = sent - bounced.
* `replied`: sends with a `reply` event. Out-of-office and other automatic answers are
  `auto_replied` and never count here; an unsubscribe request is counted as `unsubscribed`.
* Rates use delivered as the base (`reply_rate`, `unsubscribe_rate`); `bounce_rate` = bounced/sent.
  A rate with nothing to divide by is `None`.

A group with fewer than `SMALL_SAMPLE` delivered emails is flagged `small_sample` ("too few to
judge"). A reply or bounce is attributed to the day and group of the email it answers (a cohort
view), and the day is the UTC date of `sent_at`. The numbers are only as fresh as the last
`sync_mailbox`.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from leadgen import db
from leadgen.services import campaign as campaign_mod
from leadgen.services.mailbox import LAST_SYNC_KEY

GROUP_BY = ("designation", "sector", "company", "country", "day")
SMALL_SAMPLE = 20
NONE_LABEL = "(none)"
SMALL_NOTE = f"too few to judge (fewer than {SMALL_SAMPLE} delivered)"


class AnalyticsError(Exception):
    """A request was refused. `code` is stable text; `hint` tells the AI what to do."""

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


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _row(key: str, counts: Counter) -> dict[str, Any]:
    sent, bounced = counts["sent"], counts["bounced"]
    delivered = sent - bounced
    row: dict[str, Any] = {
        "key": key,
        "sent": sent,
        "bounced": bounced,
        "delivered": delivered,
        "replied": counts["replied"],
        "auto_replied": counts["auto_replied"],
        "unsubscribed": counts["unsubscribed"],
        "reply_rate": _rate(counts["replied"], delivered),
        "bounce_rate": _rate(bounced, sent),
        "unsubscribe_rate": _rate(counts["unsubscribed"], delivered),
        "small_sample": delivered < SMALL_SAMPLE,
    }
    if row["small_sample"]:
        row["note"] = SMALL_NOTE
    return row


def _event_flag(kind: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM events e WHERE e.send_id = s.id AND e.type = "
        f"'{kind}') AS {kind}"
    )


def _load(campaign_id: int | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT s.id, substr(s.sent_at, 1, 10) AS day, p.designation, p.country, "
        "c.name AS company, c.sector, "
        + ", ".join(_event_flag(k) for k in ("bounce", "reply", "auto_reply", "unsubscribe"))
        + " FROM sends s JOIN messages m ON m.id = s.message_id JOIN people p ON p.id = m.person_id"
        " LEFT JOIN companies c ON c.id = p.company_id WHERE s.status = 'sent'"
    )
    args: tuple[Any, ...] = ()
    if campaign_id is not None:
        sql += " AND p.campaign_id = ?"
        args = (campaign_id,)
    with db.session() as conn:
        return [dict(r) for r in db.query(conn, sql, args)]


def _count(rows: list[dict[str, Any]], key_of) -> dict[str, Counter]:
    groups: dict[str, Counter] = {}
    for r in rows:
        counts = groups.setdefault(str(key_of(r) or NONE_LABEL), Counter())
        counts["sent"] += 1
        counts["bounced"] += int(bool(r["bounce"]))
        counts["replied"] += int(bool(r["reply"]))
        counts["auto_replied"] += int(bool(r["auto_reply"]))
        counts["unsubscribed"] += int(bool(r["unsubscribe"]))
    return groups


def _other_channels(campaign_id: int | None) -> dict[str, Any]:
    """Counts the tool knows locally for lemlist / LinkedIn (their results live in lemlist)."""
    prefix = "lemlist_push:" + (f"{campaign_id}:" if campaign_id is not None else "")
    pushed = 0
    ready = 0
    with db.session() as conn:
        for row in db.query(conn, "SELECT value FROM settings WHERE key LIKE ?", (prefix + "%",)):
            try:
                log = json.loads(row["value"])
            except ValueError:
                continue
            if isinstance(log, dict):
                pushed += sum(
                    1
                    for v in log.values()
                    if isinstance(v, dict) and v.get("status") in ("created", "duplicate")
                )
        sql = (
            "SELECT COUNT(*) FROM messages m JOIN people p ON p.id = m.person_id "
            "WHERE m.channel LIKE 'linkedin%' AND m.status = 'approved'"
        )
        args: tuple[Any, ...] = ()
        if campaign_id is not None:
            sql += " AND p.campaign_id = ?"
            args = (campaign_id,)
        ready = int(conn.execute(sql, args).fetchone()[0])
    return {
        "lemlist_pushed": pushed,
        "linkedin_messages_approved": ready,
        "note": (
            "LinkedIn invites, acceptances and replies are tracked in lemlist, not here; these "
            "are only the counts this tool knows locally."
        ),
    }


def get_analytics(campaign_id: int | None = None, group_by: str | None = None) -> dict[str, Any]:
    """Sent, bounced, delivered, replied, unsubscribed and the rates, for one campaign or all.

    `group_by` is `designation`, `sector`, `company`, `country` or `day`; groups are sorted by
    sent (most first). The reply always has `totals`, a per-day `timeseries` (oldest first),
    `other_channels` and `last_sync`. Raises `AnalyticsError` / `CampaignError`.
    """
    if group_by is not None and group_by not in GROUP_BY:
        raise AnalyticsError(
            "invalid_value",
            f"group_by must be one of: {', '.join(GROUP_BY)}.",
            "Call again with one of them, or leave group_by out for totals only.",
        )
    campaign: dict[str, Any] | None = None
    if campaign_id is not None:
        camp = campaign_mod.get_campaign(campaign_id)
        campaign = {"id": camp["id"], "name": camp["name"]}
    rows = _load(campaign_id)
    totals = _row("all", _count(rows, lambda r: "all").get("all", Counter()))
    totals.pop("key")
    days = _count(rows, lambda r: r["day"])
    timeseries = [_row(day, counts) for day, counts in sorted(days.items())]
    for item in timeseries:
        item["day"] = item.pop("key")
    out: dict[str, Any] = {
        "ok": True,
        "campaign": campaign,
        "totals": totals,
        "timeseries": timeseries,
        "other_channels": _other_channels(campaign_id),
    }
    if group_by:
        key_of = {
            "designation": lambda r: r["designation"],
            "sector": lambda r: r["sector"],
            "company": lambda r: r["company"],
            "country": lambda r: r["country"],
            "day": lambda r: r["day"],
        }[group_by]
        groups = [_row(k, c) for k, c in _count(rows, key_of).items()]
        groups.sort(key=lambda g: (-g["sent"], g["key"]))
        out["group_by"] = group_by
        out["groups"] = groups
    with db.session() as conn:
        found = db.query(conn, "SELECT value FROM settings WHERE key = ?", (LAST_SYNC_KEY,))
    out["last_sync"] = str(found[0]["value"]) if found else None
    notes = [
        "Replies exclude out-of-office and other automatic answers; an unsubscribe request is "
        "counted as unsubscribed, not as a reply.",
        "Rates use delivered emails (sent minus bounced) as the base; bounce_rate uses sent.",
    ]
    if totals["sent"] and out["last_sync"] is None:
        notes.append("The mailbox has never been synced: call sync_mailbox first.")
    if totals["small_sample"] and totals["sent"]:
        notes.append(f"Overall {SMALL_NOTE}; do not draw conclusions from these rates.")
    out["notes"] = notes
    return out
