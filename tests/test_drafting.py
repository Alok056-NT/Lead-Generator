"""Drafting workflow: eligibility, batch content, cursor, saving, history and instant checks.

All people and companies are invented. No network is used.
"""

from __future__ import annotations

import itertools
import re

import pytest

from leadgen import db
from leadgen.services import campaign as camp
from leadgen.services import drafting, offer, qa_messages
from leadgen.services.drafting import CHANNEL_SPECS, DraftError

PROFILE = {
    "name": "Bright Harbour Training",
    "one_liner": "Practical training for supervisors and managers.",
    "description": "We run short courses for supervisors. Trainers are working practitioners.",
    "services": [
        {"name": "Supervisor courses", "what": "Short supervisor courses", "best_for": ["ops"]},
        {"name": "Leadership programmes", "what": "For new managers", "best_for": ["L&D"]},
    ],
    "audiences": ["HR and L&D heads"],
    "confirmed_claims": ["Courses are taught by working practitioners"],
    "forbidden_claims": ["Guaranteed results", "Client: Acme Corp"],
    "proof_points": [{"claim": "Trained 1,200 managers", "source": "About page"}],
    "ctas": ["15-minute call"],
    "sender": {"name": "Priya Nair", "company": "Bright Harbour Training", "role": "Partner"},
    "tone": "professional, plain, no hype",
    "language": "en-GB",
}

_n = itertools.count(1)


def make_campaign(
    channels=("email", "linkedin"), options=None, profile=True, sectors=("logistics",), **extra
):
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Bright Harbour", source_type="text")
    if profile:
        offer.save_profile(offer_id, PROFILE)
    return camp.create_campaign(
        name=f"SG logistics {next(_n)}",
        offer_id=offer_id,
        countries=["SG"],
        sectors=list(sectors),
        company_size="200-1000",
        channels=list(channels),
        designations=["ld", "hr"],
        options=options,
        **extra,
    )


def add_company(cid, name="Northwind Logistics", domain="northwind.example", **fields):
    with db.session() as conn:
        return db.insert(
            conn, "companies", campaign_id=cid, name=name, domain=domain, status="approved",
            sector="logistics", size_note="about 500 staff", **fields,
        )  # fmt: skip


def add_person(cid, company_id, first="Aria", last="Tan", status="confirmed", **fields):
    n = next(_n)
    values = {
        "greeting_name": first,
        "title": "Head of Learning & Development",
        "designation": "L&D",
        "seniority": "vp",
        "evidence_snippet": "Head of Learning & Development at Northwind Logistics. Singapore.",
        "linkedin_url": f"https://www.linkedin.com/in/p{n}",
    }
    values.update(fields)
    with db.session() as conn:
        return db.insert(
            conn, "people", campaign_id=cid, company_id=company_id, first_name=first,
            last_name=last, raw_name=f"{first} {last}", status=status,
            confirmed=int(status == "confirmed"), **values,
        )  # fmt: skip


def add_email(person_id, status="verified"):
    with db.session() as conn:
        return db.insert(
            conn, "emails", person_id=person_id, email=f"p{person_id}@northwind.example",
            status=status,
        )  # fmt: skip


def messages(person_id=None):
    with db.session() as conn:
        sql = "SELECT * FROM messages" + (" WHERE person_id = ?" if person_id else "")
        return db.query(conn, sql + " ORDER BY id", (person_id,) if person_id else ())


def set_message(person_id, channel, **fields):
    with db.session() as conn:
        return db.insert(conn, "messages", person_id=person_id, channel=channel, **fields)


def li_body(name="Aria", extra=""):
    return (
        f"Hi {name}, I noticed you lead learning and development at Northwind Logistics. "
        "We run practical programmes for new managers and I wondered if a short outline would "
        f"be useful to you? Priya {extra}"
    ).strip()


def email_body(name="Aria", words=60):
    filler = " ".join(["supervisor"] * (words - 15))
    return (
        f"Hi {name},\n\nI saw you lead learning at Northwind Logistics. {filler}."
        "\n\nWould that help?\n\nKind regards,\nPriya"
    )


@pytest.fixture
def world(ready_db):
    """A campaign (email + linkedin) with one company."""
    c = make_campaign()
    return c["id"], add_company(c["id"])


# --- specs and channels ---------------------------------------------------------------


def test_channel_specs_match_plan_section_13():
    inv, msg, mail = (CHANNEL_SPECS[k] for k in ("linkedin_invite", "linkedin_message", "email"))
    assert (inv.body_max, inv.body_unit, inv.has_subject) == (200, "chars", False)
    assert (msg.body_min, msg.body_max, msg.body_unit) == (150, 700, "chars")
    assert msg.links_allowed is False and inv.links_allowed is False
    assert (mail.body_min, mail.body_max, mail.body_unit) == (40, 160, "words")
    assert (mail.has_subject, mail.subject_max) == (True, 60)
    shown = mail.as_dict()
    assert shown["subject_max_chars"] == 60 and shown["needs_subject"] is True
    assert msg.as_dict()["subject_max_chars"] is None


@pytest.mark.parametrize(
    ("channels", "options", "expected"),
    [
        (["linkedin"], {}, ["linkedin_message"]),
        (["linkedin"], {"invite_note": True}, ["linkedin_message", "linkedin_invite"]),
        (["email"], {"invite_note": True}, ["email"]),
        (["email", "linkedin"], {}, ["email", "linkedin_message"]),
        ([], {}, []),
    ],
)
def test_channels_for(channels, options, expected):
    assert drafting.channels_for({"channels": channels, "options": options}) == expected


def test_sendable_statuses_follow_the_campaign_option():
    assert drafting.sendable_email_statuses({"options": {}}) == ("verified",)
    assert drafting.sendable_email_statuses({"options": {"allow_risky_emails": True}}) == (
        "verified",
        "risky",
    )
    assert drafting.sendable_email_statuses({"options": {"allow_guess_emails": True}}) == (
        "verified",
        "guess",
    )
    both = {"allow_risky_emails": True, "allow_guess_emails": True}
    assert drafting.sendable_email_statuses({"options": both}) == ("verified", "risky", "guess")
    assert drafting.sendable_email_statuses({}) == ("verified",)


# --- errors ---------------------------------------------------------------------------


def test_no_offer_profile_is_a_structured_error(ready_db):
    c = make_campaign(profile=False)
    with pytest.raises(DraftError) as err:
        drafting.get_drafting_batch(c["id"], "email")
    assert err.value.code == "no_offer_profile"
    reply = err.value.as_response()
    assert reply["ok"] is False and "save_offer_profile" in reply["error"]["hint"]
    with pytest.raises(DraftError) as err2:
        drafting.save_drafts(c["id"], [{"person_id": 1, "channel": "email", "body": "x"}])
    assert err2.value.code == "no_offer_profile"


def test_unknown_campaign_raises_campaign_error(ready_db):
    with pytest.raises(camp.CampaignError):
        drafting.get_drafting_batch(999, "email")


def test_channel_must_exist_and_be_enabled(ready_db):
    c = make_campaign(channels=("linkedin",))
    with pytest.raises(DraftError) as err:
        drafting.get_drafting_batch(c["id"], "sms")
    assert err.value.code == "invalid_channel"
    with pytest.raises(DraftError) as err:
        drafting.get_drafting_batch(c["id"], "email")
    assert err.value.code == "channel_not_enabled"
    with pytest.raises(DraftError) as err:
        drafting.get_drafting_batch(c["id"], "linkedin_invite")
    assert err.value.code == "channel_not_enabled" and "invite_note" in err.value.hint
    other = make_campaign(channels=("linkedin",), options={"invite_note": True})
    assert drafting.get_drafting_batch(other["id"], "linkedin_invite")["ok"]


def test_batch_size_and_cursor_are_validated(world):
    cid, _ = world
    with pytest.raises(DraftError) as err:
        drafting.get_drafting_batch(cid, "email", cursor="abc")
    assert err.value.code == "invalid_cursor"
    with pytest.raises(DraftError) as err:
        drafting.get_drafting_batch(cid, "email", batch_size="5")  # type: ignore[arg-type]
    assert err.value.code == "invalid_value"
    assert drafting.get_drafting_batch(cid, "email", batch_size=0)["ok"]  # clamped to 1


# --- eligibility ----------------------------------------------------------------------


def ids(batch):
    return [p["person_id"] for p in batch["people"]]


def test_linkedin_batch_takes_confirmed_people_without_a_message(world):
    cid, co = world
    a = add_person(cid, co, "Aria", "Tan")
    b = add_person(cid, co, "Marcus", "Lim")
    add_person(cid, co, "Devika", "Rao", status="review")
    add_person(cid, co, "Chen", "Wei", status="removed")
    drafted = add_person(cid, co, "Sam", "Ong")
    set_message(drafted, "linkedin_message", body=li_body("Sam"), status="qa_pass")
    batch = drafting.get_drafting_batch(cid, "linkedin_message")
    assert ids(batch) == [a, b]
    assert batch["remaining"] == 0 and batch["done"] and batch["next_cursor"] is None


def test_redraft_candidates_are_qa_fail_and_ai_review_fix(world):
    cid, co = world
    failed = add_person(cid, co, "Aria", "Tan")
    fix = add_person(cid, co, "Marcus", "Lim")
    passed = add_person(cid, co, "Devika", "Rao")
    sent = add_person(cid, co, "Sam", "Ong")
    rejected = add_person(cid, co, "Lena", "Koh")
    set_message(failed, "linkedin_message", body="short", status="qa_fail")
    set_message(fix, "linkedin_message", body=li_body("Marcus"), status="qa_pass", ai_review="fix",
                ai_review_notes="Too generic")  # fmt: skip
    set_message(passed, "linkedin_message", body=li_body("Devika"), status="qa_pass",
                ai_review="pass")  # fmt: skip
    set_message(sent, "linkedin_message", body=li_body("Sam"), status="sent", ai_review="fix")
    set_message(rejected, "linkedin_message", body=li_body("Lena"), status="qa_pass",
                ai_review="reject")  # fmt: skip
    batch = drafting.get_drafting_batch(cid, "linkedin_message")
    assert ids(batch) == [failed, fix]
    by = {p["person_id"]: p for p in batch["people"]}
    assert by[failed]["previous_draft"]["body"] == "short"
    assert by[fix]["review_notes"] == "Too generic"


def test_messages_on_other_channels_do_not_count(world):
    cid, co = world
    p = add_person(cid, co)
    add_email(p)
    set_message(p, "email", subject="Hello", body=email_body(), status="qa_pass")
    assert ids(drafting.get_drafting_batch(cid, "linkedin_message")) == [p]
    assert ids(drafting.get_drafting_batch(cid, "email")) == []


def test_email_batch_needs_a_sendable_email(world):
    cid, co = world
    verified = add_person(cid, co, "Aria", "Tan")
    risky = add_person(cid, co, "Marcus", "Lim")
    guess = add_person(cid, co, "Devika", "Rao")
    nothing = add_person(cid, co, "Sam", "Ong")
    invalid = add_person(cid, co, "Lena", "Koh")
    add_email(verified, "verified")
    add_email(risky, "risky")
    add_email(guess, "guess")
    add_email(invalid, "invalid")
    assert nothing not in ids(drafting.get_drafting_batch(cid, "email"))
    assert ids(drafting.get_drafting_batch(cid, "email")) == [verified]


def test_email_batch_includes_risky_when_the_campaign_allows(ready_db):
    c = make_campaign(options={"allow_risky_emails": True})
    co = add_company(c["id"])
    verified = add_person(c["id"], co)
    risky = add_person(c["id"], co, "Marcus", "Lim")
    guess = add_person(c["id"], co, "Devika", "Rao")
    add_email(verified)
    add_email(risky, "risky")
    add_email(guess, "guess")
    assert ids(drafting.get_drafting_batch(c["id"], "email")) == [verified, risky]


def test_email_batch_includes_guess_only_when_allowed(ready_db):
    c = make_campaign(options={"allow_guess_emails": True})
    co = add_company(c["id"])
    verified = add_person(c["id"], co)
    risky = add_person(c["id"], co, "Marcus", "Lim")
    guess = add_person(c["id"], co, "Devika", "Rao")
    add_email(verified)
    add_email(risky, "risky")
    add_email(guess, "guess")
    assert ids(drafting.get_drafting_batch(c["id"], "email")) == [verified, guess]


def test_only_this_campaigns_people(world):
    cid, co = world
    mine = add_person(cid, co)
    other = make_campaign()
    other_co = add_company(other["id"], "Contoso Foods", "contoso.example")
    add_person(other["id"], other_co, "Zed", "Ng")
    assert ids(drafting.get_drafting_batch(cid, "linkedin_message")) == [mine]


def test_empty_batch_says_why(world):
    cid, _ = world
    batch = drafting.get_drafting_batch(cid, "email")
    assert batch["people"] == [] and batch["done"] and "find_emails" in batch["message"]
    assert "guide" in batch  # still the first call
    li = drafting.get_drafting_batch(cid, "linkedin_message")
    assert "Confirm people" in li["message"]


# --- batch content --------------------------------------------------------------------


def test_batch_carries_offer_spec_locale_and_guide_once(world):
    cid, co = world
    add_person(cid, co, "Aria", "Tan")
    add_person(cid, co, "Marcus", "Lim")
    add_person(cid, co, "Devika", "Rao")
    first = drafting.get_drafting_batch(cid, "linkedin_message", batch_size=2)
    assert "greeting_name" in first["guide"] and "forbidden_claims" in first["guide"]
    offer_part = first["offer"]
    assert offer_part["confirmed_claims"] == PROFILE["confirmed_claims"]
    assert offer_part["forbidden_claims"] == PROFILE["forbidden_claims"]
    assert offer_part["proof_points"][0]["claim"] == "Trained 1,200 managers"
    assert offer_part["sender"]["name"] == "Priya Nair"
    assert offer_part["services"][0]["name"] == "Supervisor courses"
    assert offer_part["tone"] and offer_part["language"] == "en-GB" and offer_part["ctas"]
    assert "raw_text" not in offer_part and "description" not in offer_part
    assert first["channel_spec"]["body_max"] == 700 and first["locale"] == "en-GB"
    assert first["count"] == 2 and first["remaining"] == 1 and first["next_cursor"]
    second = drafting.get_drafting_batch(
        cid, "linkedin_message", batch_size=2, cursor=first["next_cursor"]
    )
    assert "guide" not in second  # once per session
    assert second["offer"]["confirmed_claims"] == PROFILE["confirmed_claims"]
    forced = drafting.get_drafting_batch(
        cid, "linkedin_message", cursor=first["next_cursor"], include_guide=True
    )
    assert "guide" in forced
    quiet = drafting.get_drafting_batch(cid, "linkedin_message", include_guide=False)
    assert "guide" not in quiet


def test_person_payload(world):
    cid, co = world
    long_snippet = "  Head of Learning   &\nDevelopment at Northwind. " + "word " * 100
    with db.session() as conn:
        db.update(
            conn, "companies", co,
            notes="Warning: domain redirects\nDomain check: ok\nFamily-owned depot network",
        )  # fmt: skip
    p = add_person(cid, co, "Aria", "Tan", evidence_snippet=long_snippet, greeting_name="Aria")
    person = drafting.get_drafting_batch(cid, "linkedin_message")["people"][0]
    assert person["person_id"] == p
    assert person["greeting_name"] == "Aria" and person["full_name"] == "Aria Tan"
    assert person["title"] == "Head of Learning & Development"
    assert person["designation"] == "L&D" and person["seniority"] == "vp"
    assert person["company"] == {
        "name": "Northwind Logistics",
        "sector": "logistics",
        "size_note": "about 500 staff",
        "notes": "Family-owned depot network",
    }
    assert len(person["evidence"]) <= 300 and person["evidence"].endswith("…")
    assert "\n" not in person["evidence"] and "  " not in person["evidence"]
    assert person["flags"] == []
    assert "previous_draft" not in person


def test_greeting_falls_back_to_first_name_and_evidence_to_title(world):
    cid, co = world
    add_person(cid, co, "Aria", "Tan", greeting_name="", evidence_snippet="",
               evidence_title="Aria Tan - Head of L&D - Northwind | LinkedIn")  # fmt: skip
    person = drafting.get_drafting_batch(cid, "linkedin_message")["people"][0]
    assert person["greeting_name"] == "Aria"
    assert person["evidence"].startswith("Aria Tan - Head of L&D")


def _flag_map(cid, channel="linkedin_message"):
    return {
        p["person_id"]: p["flags"] for p in drafting.get_drafting_batch(cid, channel)["people"]
    }


def test_flags(ready_db):
    c = make_campaign(exclusions={"competitors": ["Rival Training", "rival.example"]})
    cid = c["id"]
    co = add_company(cid)
    rival = add_company(cid, "Rival Training Pte Ltd", "rival.example")
    staffing = add_company(
        cid, "Contoso Staffing", "contoso.example", notes="Executive search firm"
    )
    headhunter = add_person(cid, co, "Aria", "Tan", title="Headhunter")
    partner = add_person(cid, co, "Marcus", "Lim", title="Head of Partnerships")
    competitor = add_person(cid, rival, "Devika", "Rao")
    agency = add_person(cid, staffing, "Sam", "Ong")
    seeker = add_person(cid, co, "Lena", "Koh", reasons_json=["L15:job-seeking hint"])
    plain = add_person(cid, co, "Ben", "Yeo")
    inhouse = add_person(cid, co, "Zoe", "Lau", title="Senior Recruiter")
    flags = _flag_map(cid)
    assert flags[headhunter] == ["recruiter"]
    assert flags[partner] == ["partner"]
    assert flags[competitor] == ["competitor"]
    assert flags[agency] == ["recruiter"]
    assert flags[seeker] == ["job_seeker"]
    assert flags[plain] == []
    assert flags[inhouse] == []  # in-house recruiting titles are not flagged


def test_flags_do_not_block_the_campaigns_own_targets(ready_db):
    c = make_campaign()
    cid = c["id"]
    co = add_company(cid)
    titles = [
        "Head of Recruitment", "Recruitment Manager", "Talent Acquisition Director",
        "Channel Sales Director", "Head of HR",
    ]  # fmt: skip
    people = [add_person(cid, co, f"P{i}", "Tan", title=t) for i, t in enumerate(titles)]
    flags = _flag_map(cid)
    assert all(flags[p] == [] for p in people)


def test_company_flags_are_skipped_when_the_sectors_name_that_firm(ready_db):
    staffing_note = {"notes": "Staffing and recruitment agency"}
    reseller_note = {"notes": "Authorised reseller"}
    # a logistics campaign: a staffing or reseller company is flagged
    c = make_campaign()
    cid = c["id"]
    firm = add_company(cid, "Contoso Staffing", "contoso.example", **staffing_note)
    shop = add_company(cid, "Fabrikam Resale", "fabrikam.example", **reseller_note)
    hr = add_person(cid, firm, "Aria", "Tan", title="Head of HR")
    buyer = add_person(cid, shop, "Marcus", "Lim", title="Operations Head")
    flags = _flag_map(cid)
    assert flags[hr] == ["recruiter"] and flags[buyer] == ["partner"]
    # a campaign that targets those firms: no company-based flag
    t = make_campaign(sectors=("staffing and recruitment", "reseller"))
    tid = t["id"]
    firm = add_company(tid, "Contoso Staffing", "contoso.example", **staffing_note)
    shop = add_company(tid, "Fabrikam Resale", "fabrikam.example", **reseller_note)
    hr = add_person(tid, firm, "Aria", "Tan", title="Head of HR")
    buyer = add_person(tid, shop, "Marcus", "Lim", title="Operations Head")
    flags = _flag_map(tid)
    assert flags[hr] == [] and flags[buyer] == []


def test_title_flags_are_skipped_when_the_sectors_name_that_firm(ready_db):
    # a campaign selling to staffing firms: an agency title is the prospect, not a recruiter flag
    c = make_campaign(sectors=("staffing",))
    co = add_company(c["id"])
    p = add_person(c["id"], co, "Aria", "Tan", title="Executive Search Partner")
    q = add_person(c["id"], co, "Sam", "Ong", title="Recruitment Consultant")
    flags = _flag_map(c["id"])
    assert flags[p] == [] and flags[q] == []
    # the same titles in an unrelated campaign are still flagged
    o = make_campaign()
    r = add_person(o["id"], add_company(o["id"]), "Aria", "Tan", title="Executive Search Partner")
    assert _flag_map(o["id"])[r] == ["recruiter"]


def test_partner_title_is_not_flagged_in_a_reseller_campaign(ready_db):
    c = make_campaign(sectors=("resellers",))
    co = add_company(c["id"])
    p = add_person(c["id"], co, "Marcus", "Lim", title="Head of Partnerships")
    q = add_person(c["id"], co, "Zoe", "Lau", title="Channel Partner Manager")
    flags = _flag_map(c["id"])
    assert flags[p] == [] and flags[q] == []
    o = make_campaign()
    r = add_person(o["id"], add_company(o["id"]), "Marcus", "Lim", title="Head of Partnerships")
    assert _flag_map(o["id"])[r] == ["partner"]


def test_redraft_payload_lists_previous_issues(world):
    cid, co = world
    p = add_person(cid, co)
    result = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "linkedin_message", "body": "Hi"}]
    )
    assert result["qa_fail"] == 1
    person = drafting.get_drafting_batch(cid, "linkedin_message")["people"][0]
    assert person["previous_draft"]["body"] == "Hi"
    codes = {i["code"] for i in person["previous_issues"]}
    assert {"M01", "M04"} <= codes
    assert all(set(i) == {"code", "severity", "detail"} for i in person["previous_issues"])


def test_cursor_walks_every_eligible_person(world):
    cid, co = world
    expected = [add_person(cid, co, f"Person{i}", "Tan") for i in range(7)]
    add_person(cid, co, "Hidden", "Tan", status="review")
    seen, cursor, calls = [], None, 0
    while True:
        batch = drafting.get_drafting_batch(cid, "linkedin_message", batch_size=3, cursor=cursor)
        seen += ids(batch)
        calls += 1
        if batch["done"]:
            assert batch["next_cursor"] is None and batch["remaining"] == 0
            break
        assert batch["remaining"] == 7 - len(seen)
        cursor = batch["next_cursor"]
    assert seen == expected and calls == 3


def test_cursor_survives_saving_between_batches(world):
    cid, co = world
    expected = [add_person(cid, co, f"Person{i}", "Tan") for i in range(5)]
    seen, cursor = [], None
    while True:
        batch = drafting.get_drafting_batch(cid, "linkedin_message", batch_size=2, cursor=cursor)
        seen += ids(batch)
        drafts = [
            {"person_id": p["person_id"], "channel": "linkedin_message",
             "body": li_body(p["greeting_name"]) + " " + "x" * 120}
            for p in batch["people"]
        ]  # fmt: skip
        drafting.save_drafts(cid, drafts)
        if batch["done"]:
            break
        cursor = batch["next_cursor"]
    assert seen == expected
    assert drafting.get_drafting_batch(cid, "linkedin_message")["people"] == []


# --- saving and instant QA ------------------------------------------------------------


def good_li(name="Aria"):
    return li_body(name) + " " + "Our courses are practical and short. " * 2 + "Would that help?"


def test_save_pass_and_store(world):
    cid, co = world
    p = add_person(cid, co)
    reply = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "linkedin_message", "body": good_li()}]
    )
    assert reply["ok"] and reply["qa_pass"] == 1 and reply["qa_fail"] == 0
    assert "next_step" not in reply
    result = reply["results"][0]
    assert result["status"] == "qa_pass" and result["issues"] == [] and result["person_id"] == p
    row = messages(p)[0]
    assert row["status"] == "qa_pass" and row["channel"] == "linkedin_message"
    assert row["subject"] == "" and row["ai_review"] == ""
    assert row["qa_json"]["issues"] == [] and row["qa_json"]["history"] == []
    assert row["id"] == result["message_id"]
    assert camp.get_campaign(cid)["counts"]["messages"]["qa_pass"] == 1


def test_email_draft_needs_a_subject_and_sendable_email(world):
    cid, co = world
    p = add_person(cid, co)
    refused = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "email", "subject": "Hello Aria", "body": email_body()}]
    )["results"][0]
    assert refused["status"] == "not_saved" and refused["issues"][0]["code"] == "no_sendable_email"
    assert messages(p) == []
    add_email(p)
    ok = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "email", "subject": "  Training   for\nNorthwind  ",
               "body": email_body()}]
    )["results"][0]  # fmt: skip
    assert ok["status"] == "qa_pass"
    assert messages(p)[0]["subject"] == "Training for Northwind"
    missing = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "email", "body": email_body()}]
    )["results"][0]
    assert missing["status"] == "qa_fail"
    assert [i["code"] for i in missing["issues"]] == ["M09"]


def test_subject_is_ignored_on_linkedin(world):
    cid, co = world
    p = add_person(cid, co)
    drafting.save_drafts(
        cid, [{"person_id": p, "channel": "linkedin_message", "subject": "Hi", "body": good_li()}]
    )
    assert messages(p)[0]["subject"] == ""


@pytest.mark.parametrize(
    ("channel", "body", "subject", "code", "needle"),
    [
        ("linkedin_message", "Hi Aria, " + "x" * 800, "", "M04", "Too long"),
        ("linkedin_message", "Hi Aria, hello.", "", "M04", "Too short"),
        ("linkedin_invite", "Hi Aria, " + "x" * 200, "", "M04", "Too long"),
        ("linkedin_message", "", "", "M04", "empty"),
        ("linkedin_message", "Hello Marcus, " + "x" * 200, "", "M01", "Aria"),
        ("linkedin_message", "Hi there,\n" + "x" * 200, "", "M01", "Aria"),
        ("linkedin_message", "Hi Aria, Dear [Name], " + "x" * 200, "", "M03", "[Name]"),
        ("linkedin_message", "Hi Aria, {{company}} " + "x" * 200, "", "M03", "{{"),
        ("linkedin_message", "Hi Aria, <company> " + "x" * 200, "", "M03", "<company>"),
        ("linkedin_message", "Hi Aria, TODO " + "x" * 200, "", "M03", "TODO"),
        ("linkedin_message", "Hi Aria, TBD " + "x" * 200, "", "M03", "TBD"),
        ("linkedin_message", "Hi Aria, Lorem Ipsum " + "x" * 200, "", "M03", "Lorem Ipsum"),
        ("email", email_body(), "RE: your enquiry", "M09", "Re:"),
        ("email", email_body(), "x" * 61, "M09", "61"),
        ("email", email_body(), "SPECIAL OFFER INSIDE", "M09", "ALL CAPS"),
        ("email", email_body(), "Hi [Name]", "M03", "subject"),
        ("email", email_body(words=30), "Hello", "M04", "words"),
        ("email", email_body(words=170), "Hello", "M04", "Too long"),
    ],
)
def test_instant_checks(world, channel, body, subject, code, needle):
    cid, co = world
    with db.session() as conn:
        db.update(conn, "campaigns", cid, options_json={
            "invite_note": True, "allow_risky_emails": False,
            "allow_guess_emails": False, "search_mode": "auto",
        })  # fmt: skip
    p = add_person(cid, co)
    add_email(p)
    result = drafting.save_drafts(
        cid, [{"person_id": p, "channel": channel, "subject": subject, "body": body}]
    )["results"][0]
    assert result["status"] == "qa_fail"
    hits = [i for i in result["issues"] if i["code"] == code]
    assert hits and needle.lower() in hits[0]["detail"].lower()
    assert all(i["severity"] == "block" for i in hits)
    assert messages(p)[0]["status"] == "qa_fail"


def test_greeting_matches_whole_names_only(world):
    cid, co = world
    p = add_person(cid, co, "Al", "Tan")
    bad = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "linkedin_message", "body": good_li("Alan")}]
    )["results"][0]
    assert [i["code"] for i in bad["issues"]] == ["M01"]
    ok = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "linkedin_message", "body": good_li("al")}]
    )["results"][0]
    assert ok["status"] == "qa_pass"


def test_person_without_a_greeting_name_cannot_pass(world):
    cid, co = world
    with db.session() as conn:
        pid = db.insert(conn, "people", campaign_id=cid, company_id=co, first_name="",
                        last_name="", raw_name="", status="confirmed",
                        linkedin_url="https://www.linkedin.com/in/anon")  # fmt: skip
    result = drafting.save_drafts(
        cid, [{"person_id": pid, "channel": "linkedin_message", "body": good_li()}]
    )["results"][0]
    assert result["status"] == "qa_fail" and "greeting_name" in result["issues"][0]["detail"]


def test_mixed_results_and_next_step(world):
    cid, co = world
    a, b = add_person(cid, co, "Aria", "Tan"), add_person(cid, co, "Marcus", "Lim")
    reply = drafting.save_drafts(cid, [
        {"person_id": a, "channel": "linkedin_message", "body": good_li("Aria")},
        {"person_id": b, "channel": "linkedin_message", "body": "Hi Marcus"},
    ])  # fmt: skip
    assert (reply["qa_pass"], reply["qa_fail"], reply["not_saved"]) == (1, 1, 0)
    assert [r["status"] for r in reply["results"]] == ["qa_pass", "qa_fail"]
    assert [r["index"] for r in reply["results"]] == [0, 1]
    assert "qa_fail" in reply["next_step"] and "Skip" not in reply["next_step"]
    # only the failure is offered again
    assert ids(drafting.get_drafting_batch(cid, "linkedin_message")) == [b]


@pytest.mark.parametrize(
    "text",
    [
        "todo el equipo de operaciones y todo o time de vendas",
        "vamos a tbd e tbc mais tarde",
    ],
)
def test_lowercase_todo_words_are_not_placeholders(text):
    body = "Hi Aria, " + text + " " + "equipo " * 30

    def codes(text):
        message = {"channel": "linkedin_message", "subject": "", "body": text}
        person = {"greeting_name": "Aria", "first_name": "Aria", "title": "Head of HR"}
        issues = qa_messages.check_message(
            message, person, {"name": "Northwind"}, {"locale": "es"}, {}, companies=[]
        )
        return [i.code for i in issues if i.code == "M03"]

    assert codes(body) == []
    assert codes(body + " TODO") == ["M03"]


def test_guess_email_is_drafted_only_when_the_option_is_on(ready_db):
    saved = {}
    for label, options in (("off", None), ("on", {"allow_guess_emails": True})):
        c = make_campaign(options=options)
        p = add_person(c["id"], add_company(c["id"]))
        add_email(p, "guess")
        saved[label] = (c["id"], p)
    draft = {"channel": "email", "subject": "Hello Aria", "body": email_body()}

    cid, p = saved["off"]
    refused = drafting.save_drafts(cid, [{"person_id": p, **draft}])["results"][0]
    assert refused["status"] == "not_saved" and refused["issues"][0]["code"] == "no_sendable_email"
    assert messages(p) == []

    cid, p = saved["on"]
    ok = drafting.save_drafts(cid, [{"person_id": p, **draft}])["results"][0]
    assert ok["status"] == "qa_pass" and len(messages(p)) == 1


def test_next_step_separates_fixable_from_unfixable(world):
    cid, co = world
    a = add_person(cid, co, "Aria", "Tan")
    review = add_person(cid, co, "Marcus", "Lim", status="review")
    only_unfixable = drafting.save_drafts(
        cid, [{"person_id": review, "channel": "linkedin_message", "body": good_li("Marcus")}]
    )
    step = only_unfixable["next_step"]
    assert "person_not_confirmed" in step and "Skip those people" in step
    assert "Fix the drafts" not in step
    mixed = drafting.save_drafts(cid, [
        {"person_id": a, "channel": "linkedin_message", "body": "Hi"},
        {"person_id": review, "channel": "linkedin_message", "body": good_li("Marcus")},
        {"person_id": 9999, "channel": "linkedin_message", "body": good_li()},
    ])  # fmt: skip
    step = mixed["next_step"]
    assert "Fix the drafts" in step and "Skip those people" in step
    assert "person_not_confirmed" in step and "unknown_person" in step


def test_refused_drafts_do_not_abort_the_rest(world):
    cid, co = world
    good = add_person(cid, co)
    review = add_person(cid, co, "Marcus", "Lim", status="review")
    other = make_campaign()
    foreign = add_person(other["id"], add_company(other["id"], "Contoso", "contoso.example"))
    reply = drafting.save_drafts(cid, [
        {"person_id": good, "channel": "linkedin_message", "body": good_li()},
        {"person_id": good, "channel": "linkedin_message", "body": good_li()},
        {"person_id": review, "channel": "linkedin_message", "body": good_li("Marcus")},
        {"person_id": foreign, "channel": "linkedin_message", "body": good_li()},
        {"person_id": 9999, "channel": "linkedin_message", "body": good_li()},
        {"person_id": good, "channel": "linkedin_invite", "body": "Hi Aria"},
        {"person_id": good, "channel": "fax", "body": "Hi Aria"},
        {"person_id": "x", "channel": "linkedin_message", "body": "Hi"},
        {"person_id": good, "channel": "linkedin_message"},
        {"person_id": good, "channel": "email", "body": 5},
        "nonsense",
    ])  # fmt: skip
    codes = [r["issues"][0]["code"] if r["status"] == "not_saved" else r["status"]
             for r in reply["results"]]  # fmt: skip
    assert codes == [
        "qa_pass", "duplicate_in_call", "person_not_confirmed", "unknown_person",
        "unknown_person", "channel_not_enabled", "invalid_draft", "invalid_draft",
        "invalid_draft", "invalid_draft", "invalid_draft",
    ]  # fmt: skip
    assert reply["qa_pass"] == 1 and reply["not_saved"] == 10
    assert len(messages()) == 1


def test_bad_draft_lists_are_errors(world):
    cid, co = world
    p = add_person(cid, co)
    for bad in ([], None, "x"):
        with pytest.raises(DraftError) as err:
            drafting.save_drafts(cid, bad)  # type: ignore[arg-type]
        assert err.value.code == "invalid_drafts"
    too_many = [{"person_id": p, "channel": "linkedin_message", "body": "x"}] * 51
    with pytest.raises(DraftError) as err:
        drafting.save_drafts(cid, too_many)
    assert err.value.code == "too_many_drafts"


def test_sent_messages_cannot_be_replaced(world):
    cid, co = world
    p = add_person(cid, co)
    set_message(p, "linkedin_message", body=good_li(), status="sent")
    result = drafting.save_drafts(
        cid, [{"person_id": p, "channel": "linkedin_message", "body": good_li() + " more"}]
    )["results"][0]
    assert result["status"] == "not_saved" and result["issues"][0]["code"] == "already_sent"
    assert messages(p)[0]["status"] == "sent"


def test_upsert_keeps_one_row_and_resets_review(world):
    cid, co = world
    p = add_person(cid, co)
    drafting.save_drafts(cid, [{"person_id": p, "channel": "linkedin_message", "body": good_li()}])
    with db.session() as conn:
        db.update(conn, "messages", messages(p)[0]["id"], ai_review="fix", ai_review_notes="meh")
    new_body = good_li() + " Thank you for reading."
    drafting.save_drafts(cid, [{"person_id": p, "channel": "linkedin_message", "body": new_body}])
    rows = messages(p)
    assert len(rows) == 1
    assert rows[0]["body"] == new_body
    assert rows[0]["ai_review"] == "" and rows[0]["ai_review_notes"] == ""
    assert len(rows[0]["qa_json"]["history"]) == 1
    assert rows[0]["qa_json"]["history"][0]["body"] == good_li().strip()


def test_history_keeps_the_last_three_versions(world):
    cid, co = world
    p = add_person(cid, co)
    bodies = [good_li() + f" Version {i}." for i in range(6)]
    for body in bodies:
        drafting.save_drafts(cid, [{"person_id": p, "channel": "linkedin_message", "body": body}])
    rows = messages(p)
    assert len(rows) == 1 and rows[0]["body"] == bodies[5]
    history = rows[0]["qa_json"]["history"]
    assert [h["body"] for h in history] == [bodies[4], bodies[3], bodies[2]]
    assert all({"subject", "body", "status", "saved_at", "issues"} <= set(h) for h in history)


def test_history_records_failed_versions_and_skips_identical_resaves(world):
    cid, co = world
    p = add_person(cid, co)
    drafting.save_drafts(cid, [{"person_id": p, "channel": "linkedin_message", "body": "Hi Aria"}])
    drafting.save_drafts(cid, [{"person_id": p, "channel": "linkedin_message", "body": good_li()}])
    history = messages(p)[0]["qa_json"]["history"]
    assert len(history) == 1 and history[0]["status"] == "qa_fail"
    assert "M04" in [i["code"] for i in history[0]["issues"]]
    drafting.save_drafts(cid, [{"person_id": p, "channel": "linkedin_message", "body": good_li()}])
    assert len(messages(p)[0]["qa_json"]["history"]) == 1  # same text: no new history entry


def test_body_whitespace_is_tidied_but_paragraphs_are_kept(world):
    cid, co = world
    p = add_person(cid, co)
    add_email(p)
    body = email_body().replace("\n\n", "\r\n\r\n\r\n\r\n") + "   \n"
    drafting.save_drafts(
        cid, [{"person_id": p, "channel": "email", "subject": "Hello", "body": body}]
    )
    stored = messages(p)[0]["body"]
    assert "\r" not in stored and "\n\n\n" not in stored and "\n\n" in stored
    assert stored == stored.strip()


# --- the style guide ------------------------------------------------------------------


def _blocks(text):
    return re.findall(r"```text\n(.*?)```", text, re.DOTALL)


def test_guide_has_three_good_and_three_bad_examples():
    guide = drafting.guide_text()
    assert guide.count("**Good ") == 3 and guide.count("**Bad ") == 3
    assert len(_blocks(guide)) == 6
    for needle in ("greeting_name", "confirmed_claims", "forbidden_claims", "proof_points",
                   "200 characters", "150 to 700", "40 to 160", "partnership-style",
                   "10%", "exclamation", "emoji"):  # fmt: skip
        assert needle in guide, needle


# The batch data each good example in the guide is written from.
GOOD_EXAMPLE_DATA = {
    "Aria": ("Northwind Logistics", "Head of Learning & Development"),
    "Marcus": ("Contoso Foods", "HR Director"),
    "Devika": ("Fabrikam Freight", "Operations Head"),
}


def test_good_examples_pass_all_the_checks():
    guide = drafting.guide_text()
    good = [b for b in _blocks(guide) if b.startswith("channel:")]
    assert len(good) == 3
    profile = {
        **PROFILE,
        "confirmed_claims": [
            *PROFILE["confirmed_claims"],
            "Sessions are delivered by certified trainers",
            "Courses are taught by working trainers",
        ],
        "ctas": ["15-minute call", "send a one-page outline"],
    }
    seen = set()
    for block in good:
        head, _, body = block.partition("\n\n")
        fields = dict(line.split(": ", 1) for line in head.splitlines())
        channel, subject = fields["channel"], fields.get("subject", "")
        body = body.strip()
        name = re.match(r"(?:Hi|Hello|Dear) (\w+)", body).group(1)
        company, title = GOOD_EXAMPLE_DATA[name]
        person = {"greeting_name": name, "first_name": name, "last_name": "Test", "title": title}
        message = {"channel": channel, "subject": subject, "body": body}
        issues = qa_messages.check_message(
            message, person, {"name": company}, {"locale": "en-GB", "sectors": ["logistics"]},
            profile, companies=[],
        )  # fmt: skip
        assert [i.as_dict() for i in issues] == [], (channel, body)
        assert "!" not in body and not re.search(r"[^\x00-⿿]", body)
        seen.add(channel)
    assert seen == set(CHANNEL_SPECS)


def test_guide_is_packaged_text():
    assert drafting.guide_text().startswith("# How to write the messages")
