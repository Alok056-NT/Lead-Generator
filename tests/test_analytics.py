"""Analytics: counts, rates, grouping, the small-sample flag. Everything is invented."""

from __future__ import annotations

import json

import pytest
from mail_world import add_send, seed_person
from qa_world import add_company, add_message, add_person, make_campaign

from leadgen import db
from leadgen.services import analytics
from leadgen.services.analytics import AnalyticsError
from leadgen.services.campaign import CampaignError


def make_world(designation="L&D / Training heads", sector="logistics", company="Northwind", **kw):
    """A campaign with one company; returns `(campaign_id, company_id)`."""
    db.init_db()
    cid = make_campaign(channels=("email",))["id"]
    with db.session() as conn:
        co = db.insert(
            conn, "companies", campaign_id=cid, name=company, domain=f"{company.lower()}.example",
            status="approved", sector=sector, **kw,
        )  # fmt: skip
    return cid, co


def send_to(cid, co, n, *, designation="L&D / Training heads", country="Singapore",
            day="2026-10-12", events=(), status="sent") -> int:  # fmt: skip
    """One send to a new person; `events` are event types recorded for it."""
    person = add_person(cid, co, "Pat", f"Lee{n}", designation=designation, country=country)
    message = add_message(person, "email", status="approved")
    send = add_send(message, f"pat.lee{n}@example.org", f"<s{n}@example.com>",
                    f"{day}T09:00:00.000Z", status=status)  # fmt: skip
    with db.session() as conn:
        for kind in events:
            db.insert(conn, "events", send_id=send, type=kind, at=f"{day}T12:00:00.000Z", detail="")
    return send


def test_totals_and_rates_use_delivered_as_the_base():
    cid, co = make_world()
    n = iter(range(1000))
    for _ in range(20):
        send_to(cid, co, next(n))
    send_to(cid, co, next(n), events=("bounce",))
    send_to(cid, co, next(n), events=("bounce",))
    for _ in range(3):
        send_to(cid, co, next(n), events=("reply",))
    send_to(cid, co, next(n), events=("reply", "unsubscribe"))
    send_to(cid, co, next(n), events=("auto_reply",))
    t = analytics.get_analytics(cid)["totals"]
    assert (t["sent"], t["bounced"], t["delivered"]) == (27, 2, 25)
    assert (t["replied"], t["auto_replied"], t["unsubscribed"]) == (4, 1, 1)
    assert t["reply_rate"] == round(4 / 25, 4)
    assert t["bounce_rate"] == round(2 / 27, 4)
    assert t["unsubscribe_rate"] == round(1 / 25, 4)
    assert t["small_sample"] is False and "note" not in t


def test_auto_replies_never_count_as_replies():
    cid, co = make_world()
    for i in range(3):
        send_to(cid, co, i, events=("auto_reply",))
    t = analytics.get_analytics(cid)["totals"]
    assert t["replied"] == 0 and t["auto_replied"] == 3 and t["reply_rate"] == 0.0


def test_repeated_events_on_one_send_count_once():
    cid, co = make_world()
    send_to(cid, co, 1, events=("reply", "reply", "reply"))
    send_to(cid, co, 2, events=("bounce", "bounce"))
    t = analytics.get_analytics(cid)["totals"]
    assert (t["sent"], t["replied"], t["bounced"]) == (2, 1, 1)


def test_refused_failed_queued_and_skipped_rows_are_not_sent():
    cid, co = make_world()
    send_to(cid, co, 1)
    for i, status in enumerate(("refused", "failed", "queued", "skipped", "sending"), start=2):
        send_to(cid, co, i, status=status)
    assert analytics.get_analytics(cid)["totals"]["sent"] == 1


def test_nothing_sent_gives_none_rates_and_a_note():
    cid, _ = make_world()
    out = analytics.get_analytics(cid)
    t = out["totals"]
    assert t["sent"] == 0 and t["reply_rate"] is None and t["bounce_rate"] is None
    assert out["timeseries"] == [] and out["last_sync"] is None
    assert out["campaign"] == {"id": cid, "name": out["campaign"]["name"]}


def test_small_sample_flag_flips_at_20_delivered():
    cid, co = make_world()
    for i in range(19):
        send_to(cid, co, i)
    send_to(cid, co, 99, events=("bounce",))  # 20 sent, 19 delivered
    t = analytics.get_analytics(cid)["totals"]
    assert t["delivered"] == 19 and t["small_sample"] is True
    assert "too few to judge" in t["note"]
    send_to(cid, co, 100)
    t = analytics.get_analytics(cid)["totals"]
    assert t["delivered"] == 20 and t["small_sample"] is False


def test_group_by_designation_sorted_by_sent_with_flags():
    cid, co = make_world()
    for i in range(25):
        send_to(cid, co, i, designation="HR heads", events=("reply",) if i < 5 else ())
    for i in range(100, 103):
        send_to(cid, co, i, designation="Finance heads")
    send_to(cid, co, 200, designation="")
    out = analytics.get_analytics(cid, "designation")
    assert out["group_by"] == "designation"
    assert [g["key"] for g in out["groups"]] == ["HR heads", "Finance heads", "(none)"]
    hr = out["groups"][0]
    assert (hr["sent"], hr["replied"], hr["reply_rate"], hr["small_sample"]) == (25, 5, 0.2, False)
    assert out["groups"][1]["small_sample"] is True and "too few" in out["groups"][1]["note"]


def test_group_by_sector_company_country_and_day():
    db.init_db()
    cid = make_campaign(channels=("email",))["id"]
    with db.session() as conn:
        a = db.insert(
            conn,
            "companies",
            campaign_id=cid,
            name="Alpha Freight",
            domain="alpha.example",
            status="approved",
            sector="logistics",
        )  # noqa: E501
        b = db.insert(
            conn,
            "companies",
            campaign_id=cid,
            name="Beta Retail",
            domain="beta.example",
            status="approved",
            sector="retail",
        )  # noqa: E501
    send_to(cid, a, 1, country="Singapore", day="2026-10-12", events=("reply",))
    send_to(cid, a, 2, country="Singapore", day="2026-10-13")
    send_to(cid, b, 3, country="Malaysia", day="2026-10-13", events=("bounce",))
    by = {
        g: analytics.get_analytics(cid, g)["groups"]
        for g in analytics.GROUP_BY
        if g != "designation"
    }
    assert [(g["key"], g["sent"]) for g in by["sector"]] == [("logistics", 2), ("retail", 1)]
    assert [(g["key"], g["sent"]) for g in by["company"]] == [
        ("Alpha Freight", 2),
        ("Beta Retail", 1),
    ]
    assert [(g["key"], g["sent"]) for g in by["country"]] == [("Singapore", 2), ("Malaysia", 1)]
    assert [(g["key"], g["sent"]) for g in by["day"]] == [("2026-10-13", 2), ("2026-10-12", 1)]
    assert by["company"][1]["bounced"] == 1 and by["company"][1]["delivered"] == 0
    assert by["company"][1]["reply_rate"] is None


def test_timeseries_is_oldest_first_and_always_present():
    cid, co = make_world()
    send_to(cid, co, 1, day="2026-10-14")
    send_to(cid, co, 2, day="2026-10-12", events=("reply",))
    send_to(cid, co, 3, day="2026-10-12")
    series = analytics.get_analytics(cid)["timeseries"]
    assert [(d["day"], d["sent"], d["replied"]) for d in series] == [
        ("2026-10-12", 2, 1), ("2026-10-14", 1, 0)
    ]  # fmt: skip
    assert "groups" not in analytics.get_analytics(cid)


def test_a_campaign_filter_and_all_campaigns():
    cid, co = make_world()
    cid2, co2 = make_world(company="Other")
    send_to(cid, co, 1)
    send_to(cid2, co2, 2)
    send_to(cid2, co2, 3)
    assert analytics.get_analytics(cid)["totals"]["sent"] == 1
    assert analytics.get_analytics(cid2)["totals"]["sent"] == 2
    assert analytics.get_analytics()["totals"]["sent"] == 3
    assert analytics.get_analytics()["campaign"] is None


def test_unknown_campaign_and_bad_group_by_are_errors():
    db.init_db()
    with pytest.raises(CampaignError):
        analytics.get_analytics(999)
    with pytest.raises(AnalyticsError) as err:
        analytics.get_analytics(None, "colour")
    assert err.value.as_response()["error"]["code"] == "invalid_value"


def test_other_channels_use_the_local_lemlist_log_and_approved_linkedin_messages():
    cid, co = make_world()
    cid2, co2 = make_world(company="Other")
    person = add_person(cid, co, "Una", "Vale")
    add_message(person, "linkedin_invite", status="approved")
    add_message(person, "linkedin_message", status="draft")
    other = add_person(cid2, co2, "Ivo", "Reed")
    add_message(other, "linkedin_message", status="approved")
    log = {"1": {"status": "created"}, "2": {"status": "duplicate"}, "3": {"status": "rejected"}}
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key=f"lemlist_push:{cid}:abc", value=json.dumps(log))
        db.upsert(conn, "settings", ["key"], key=f"lemlist_push:{cid2}:abc", value="not json")
    mine = analytics.get_analytics(cid)["other_channels"]
    assert (mine["lemlist_pushed"], mine["linkedin_messages_approved"]) == (2, 1)
    assert "lemlist" in mine["note"]
    everyone = analytics.get_analytics()["other_channels"]
    assert (everyone["lemlist_pushed"], everyone["linkedin_messages_approved"]) == (2, 2)


def test_a_never_synced_mailbox_is_mentioned_once_something_was_sent():
    cid, co = make_world()
    assert not any("synced" in n for n in analytics.get_analytics(cid)["notes"])
    send_to(cid, co, 1)
    assert any("sync_mailbox" in n for n in analytics.get_analytics(cid)["notes"])
    with db.session() as conn:
        db.upsert(conn, "settings", ["key"], key="imap_last_sync", value="2026-10-17T10:00:00.000Z")
    out = analytics.get_analytics(cid)
    assert out["last_sync"] == "2026-10-17T10:00:00.000Z"
    assert not any("never been synced" in n for n in out["notes"])


def test_a_company_without_a_sector_is_grouped_as_none():
    db.init_db()
    cid = make_campaign(channels=("email",))["id"]
    person, message = seed_person(cid, add_company(cid), "Lone", "Wolf", "lone.wolf@example.org")
    with db.session() as conn:
        conn.execute("UPDATE companies SET sector = ''")
    add_send(message, "lone.wolf@example.org", "<l@example.com>", "2026-10-12T09:00:00.000Z")
    out = analytics.get_analytics(cid, "sector")
    assert out["totals"]["sent"] == 1 and out["groups"][0]["key"] == "(none)"
