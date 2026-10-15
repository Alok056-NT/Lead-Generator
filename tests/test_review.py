"""QA run, issue listing, the AI review loop and the export gate.

All people and companies are invented. No network is used.
"""

from __future__ import annotations

import pytest
from qa_world import (
    COMPANY,
    PROFILE,
    add_company,
    add_email,
    add_message,
    add_person,
    email_message,
    get_message,
    get_person,
    make_campaign,
    message_row,
    suppress,
)

from leadgen import db
from leadgen.core.config import load_settings, save_settings
from leadgen.services import campaign as camp
from leadgen.services import drafting, review
from leadgen.services.review import ReviewError, export_gate, run_qa


@pytest.fixture
def world(ready_db):
    """A campaign with one company and three confirmed people, each with a clean LinkedIn
    message waiting for review."""
    c = make_campaign(channels=("linkedin",))
    cid = c["id"]
    co = add_company(cid)
    people = [add_person(cid, co, n, "Tan") for n in ("Aria", "Marcus", "Lena")]
    msgs = []
    for pid, name in zip(people, ("Aria", "Marcus", "Lena"), strict=True):
        body = message_row()["body"].replace("Aria", name)
        # slightly different wording keeps the near-duplicate check quiet
        body = body.replace("practical", ("practical", "hands-on", "applied")[len(msgs)])
        body = body.replace(
            "programmes for new managers",
            (
                "programmes for new managers",
                "workshops for first-time managers",
                "courses for team leads",
            )[len(msgs)],
        )
        msgs.append(add_message(pid, body=body))
    return cid, co, people, msgs


# Evidence that no longer shows the employer: the lead check L04 fails.
NO_EMPLOYER = {
    "evidence_snippet": "Singapore · Training leader.",
    "evidence_title": "Marcus Tan - Head of Learning & Development | LinkedIn",
}


def set_strict(value: bool) -> None:
    settings = load_settings()
    settings.strict_review = value
    save_settings(settings)


def issue_rows(cid):
    with db.session() as conn:
        return db.query(conn, "SELECT * FROM qa_issues ORDER BY id")


# --- run_qa -----------------------------------------------------------------------------------


def test_run_qa_clean_campaign(world):
    cid, _, people, msgs = world
    reply = run_qa(cid)
    assert reply["ok"] and reply["strict_review"] is True
    assert reply["people"] == {
        "checked": 3, "with_blocks": 0, "with_email_blocks_only": 0, "with_warnings_only": 0,
        "clean": 3,
    }  # fmt: skip
    assert reply["messages"]["checked"] == 3 and reply["messages"]["qa_pass"] == 3
    assert reply["messages"]["awaiting_ai_review"] == 3
    assert reply["counts"] == {"M16": {"block": 3, "warn": 0}}
    assert reply["codes"][0]["title"] == "AI review missing"
    assert [m["status"] for m in map(get_message, msgs)] == ["qa_pass"] * 3
    assert "get_review_batch" in reply["next_step"]
    assert reply["export_ready"] == {"messages": 0, "people": 3}  # strict: nothing reviewed yet


def test_run_qa_sets_statuses_and_stores_issues(world):
    cid, co, people, msgs = world
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], body=message_row()["body"] + " We cut costs by 43%.")
        db.update(conn, "people", people[1], **NO_EMPLOYER)
    reply = run_qa(cid)
    failed = get_message(msgs[0])
    assert failed["status"] == "qa_fail"
    assert "M06" in [i["code"] for i in failed["qa_json"]["issues"]]
    assert failed["qa_json"]["checker"] == "qa_messages"
    assert reply["messages"]["qa_fail"] == 1 and reply["messages"]["qa_pass"] == 2
    assert reply["people"]["with_blocks"] == 1
    rows = issue_rows(cid)
    person_rows = [r for r in rows if r["entity_type"] == "person"]
    assert {(r["entity_id"], r["code"]) for r in person_rows} >= {(people[1], "L04")}
    assert any(
        r["entity_type"] == "message" and r["entity_id"] == msgs[0] and r["code"] == "M06"
        for r in rows
    )
    top = reply["top_issues"][0]
    assert top["severity"] == "block" and top["code"] != "M16"
    assert {"person", "company", "title", "detail", "entity_type"} <= set(top)
    assert top["company"] == COMPANY


def test_run_qa_is_idempotent(world):
    cid, _, _, msgs = world
    first = run_qa(cid)
    rows = issue_rows(cid)
    again = run_qa(cid)
    assert first == again
    assert [(r["entity_id"], r["code"], r["detail"]) for r in issue_rows(cid)] == [
        (r["entity_id"], r["code"], r["detail"]) for r in rows
    ]
    assert len(issue_rows(cid)) == len(rows)
    stamp = get_message(msgs[0])["updated_at"]
    run_qa(cid)
    assert get_message(msgs[0])["updated_at"] == stamp  # unchanged rows are not rewritten


def test_run_qa_only_touches_its_own_campaign(world):
    cid, *_ = world
    other = make_campaign()["id"]
    pid = add_person(other, add_company(other))
    mid = add_message(pid)

    def mine():
        return [r for r in issue_rows(other) if (r["entity_type"], r["entity_id"]) in
                {("person", pid), ("message", mid)}]  # fmt: skip

    run_qa(other)
    before = mine()
    assert before
    run_qa(cid)
    assert mine() == before


def test_run_qa_keeps_approved_and_fails_an_approved_message_that_now_breaks(world):
    cid, _, people, msgs = world
    review.record_review(cid, [{"message_id": msgs[0], "verdict": "pass", "notes": "ok"}])
    assert get_message(msgs[0])["status"] == "approved"
    reply = run_qa(cid)
    assert get_message(msgs[0])["status"] == "approved" and reply["messages"]["approved"] == 1
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], body="Hi Aria, short.")
    run_qa(cid)
    assert get_message(msgs[0])["status"] == "qa_fail"
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], body=message_row()["body"])
    run_qa(cid)
    assert get_message(msgs[0])["status"] == "approved"  # still has its pass verdict


def test_run_qa_skips_sent_rejected_and_unconfirmed(world):
    cid, co, people, msgs = world
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], status="sent")
        db.update(conn, "messages", msgs[1], status="rejected")
        db.update(conn, "people", people[2], status="review")
    reply = run_qa(cid)
    assert reply["messages"]["checked"] == 0 and reply["messages"]["not_checked"] == 1
    assert get_message(msgs[0])["status"] == "sent" and get_message(msgs[1])["status"] == "rejected"
    assert reply["people"]["checked"] == 2


def test_run_qa_needs_a_campaign_and_an_offer_profile(ready_db):
    with pytest.raises(camp.CampaignError):
        run_qa(999)
    cid = make_campaign(profile=False)["id"]
    with pytest.raises(ReviewError) as err:
        run_qa(cid)
    assert err.value.code == "no_offer_profile"
    assert err.value.as_response()["error"]["hint"]


def test_run_qa_with_email_channel_checks_leads_and_message_blocks(world):
    cid, co, people, msgs = world
    camp.update_campaign(cid, channels=["linkedin", "email"])
    email_id = add_message(people[0], channel="email")
    reply = run_qa(cid)  # Aria has no email address: L17
    codes = reply["counts"]
    assert codes["L17"]["block"] == 3
    assert get_message(email_id)["status"] == "qa_pass"
    # no address: they cannot be emailed, but LinkedIn messages are still fine
    assert reply["people"]["with_blocks"] == 0 and reply["people"]["with_email_blocks_only"] == 3
    assert "LinkedIn" in reply["next_step"]


# --- list_qa_issues ---------------------------------------------------------------------------


def test_list_qa_issues_filters_and_pages(world):
    cid, _, people, msgs = world
    assert "run_qa" in review.list_qa_issues(cid)["message"]
    with db.session() as conn:
        flat = get_message(msgs[0])["body"].replace(
            "Would a short outline be useful to you?", "Thanks for reading."
        )
        db.update(conn, "messages", msgs[0], body=flat)
        db.update(conn, "people", people[1], **NO_EMPLOYER)
    run_qa(cid)
    everything = review.list_qa_issues(cid)
    assert everything["total"] == 5 and everything["page"] == 1 and not everything["has_more"]
    assert [i["severity"] for i in everything["issues"]][:3] == ["block"] * 3  # blocks first
    first = everything["issues"][0]
    assert {"person", "company", "code", "severity", "detail", "title", "channel"} <= set(first)
    assert first["code"] == "L04" and first["person"] == "Marcus Tan" and first["channel"] == ""
    only = review.list_qa_issues(cid, severity="block", code="m16")
    assert only["total"] == 3 and all(i["code"] == "M16" for i in only["issues"])
    assert only["issues"][0]["channel"] == "linkedin_message"
    warn = review.list_qa_issues(cid, severity="warn")
    assert warn["total"] == 1 and warn["issues"][0]["code"] == "M10"
    assert review.list_qa_issues(cid, code="L17")["total"] == 0
    paged = review.list_qa_issues(cid, page=3, page_size=2)
    assert paged["page"] == 3 and len(paged["issues"]) == 1 and not paged["has_more"]
    assert review.list_qa_issues(cid, page_size=2)["has_more"] is True


def test_list_qa_issues_rejects_bad_input(world):
    cid, *_ = world
    for kwargs in ({"severity": "info"}, {"code": "X99"}, {"page": "1"}):
        with pytest.raises(ReviewError):
            review.list_qa_issues(cid, **kwargs)
    with pytest.raises(camp.CampaignError):
        review.list_qa_issues(999)


# --- get_review_batch -------------------------------------------------------------------------


def test_review_batch_contents_and_cursor(world):
    cid, co, people, msgs = world
    add_email(people[0], "verified", "aria.tan@northwind.example")
    batch = review.get_review_batch(cid, batch_size=2)
    assert batch["ok"] and batch["count"] == 2 and batch["remaining"] == 1 and not batch["done"]
    assert batch["next_cursor"] == str(msgs[1])
    assert len(batch["checklist"]) == 6 and "evidence" in batch["checklist"][0]
    assert batch["offer"]["forbidden_claims"] == PROFILE["forbidden_claims"]
    assert batch["offer"]["proof_points"] and batch["offer"]["confirmed_claims"]
    item = batch["items"][0]
    assert item["message_id"] == msgs[0] and item["body"].startswith("Hi Aria")
    person = item["person"]
    assert person["title"] == "Head of Learning & Development"
    assert COMPANY in person["evidence_snippet"] and person["evidence_origin"] == "api"
    assert item["company"]["name"] == COMPANY
    assert item["email"] == {"address": "aria.tan@northwind.example", "status": "verified"}
    assert batch["items"][1]["email"] is None and item["qa_warnings"] == []
    second = review.get_review_batch(cid, batch_size=2, cursor=batch["next_cursor"])
    assert second["count"] == 1 and second["done"] and second["next_cursor"] is None


def test_review_batch_skips_everything_that_is_not_waiting(world):
    cid, co, people, msgs = world
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], status="qa_fail")
        db.update(conn, "messages", msgs[1], ai_review="pass", status="approved")
        db.update(conn, "people", people[2], status="removed")
    batch = review.get_review_batch(cid)
    assert batch["items"] == [] and batch["done"] and "No messages" in batch["message"]


def test_review_batch_shows_warnings_and_validates(world):
    cid, _, _, msgs = world
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], qa_json={"issues": [
            {"code": "M10", "severity": "warn", "detail": "no ending"},
            {"code": "M04", "severity": "block", "detail": "ignored"},
        ]})  # fmt: skip
    warnings = review.get_review_batch(cid)["items"][0]["qa_warnings"]
    assert warnings == [{"code": "M10", "detail": "no ending"}]
    with pytest.raises(ReviewError):
        review.get_review_batch(cid, cursor="abc")
    with pytest.raises(ReviewError):
        review.get_review_batch(cid, batch_size="5")
    assert review.get_review_batch(cid, batch_size=1000)["count"] == 3


# --- record_review ----------------------------------------------------------------------------


def test_pass_approves_and_removes_from_the_queue(world):
    cid, _, _, msgs = world
    reply = review.record_review(
        cid, [{"message_id": msgs[0], "verdict": "pass", "notes": "Checked."}]
    )
    assert reply["recorded"] == {"pass": 1, "fix": 0, "reject": 0} and reply["errors"] == 0
    assert reply["remaining_to_review"] == 2 and "get_review_batch" in reply["next_step"]
    row = get_message(msgs[0])
    assert (row["status"], row["ai_review"], row["ai_review_notes"]) == (
        "approved",
        "pass",
        "Checked.",
    )
    assert row["qa_json"]["review_log"][0]["verdict"] == "pass"
    assert msgs[0] not in [i["message_id"] for i in review.get_review_batch(cid)["items"]]


def test_pass_and_gate_do_not_build_the_near_duplicate_index(world, monkeypatch):
    cid, _, _, msgs = world
    set_strict(False)
    stored = [{"code": "M12", "severity": "warn", "detail": "90% similar to message 99."}]
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], qa_json={"issues": stored})

    def boom(_cid):
        raise AssertionError("build_index must not be called")

    monkeypatch.setattr("leadgen.services.qa_messages.build_index", boom)
    assert export_gate(cid)["messages"]["exportable"] == 3
    reply = review.record_review(cid, [{"message_id": msgs[0], "verdict": "pass", "notes": "ok"}])
    assert reply["errors"] == 0
    codes_ = [i["code"] for i in get_message(msgs[0])["qa_json"]["issues"]]
    assert codes_ == ["M12"]  # the warning found at save time is kept


def test_fix_saves_corrected_text_clears_the_review_and_requeues(world):
    cid, _, people, msgs = world
    original = get_message(msgs[0])["body"]
    better = original.replace("practical", "hands-on")
    reply = review.record_review(cid, [
        {"message_id": msgs[0], "verdict": "fix", "notes": "Tone", "corrected_body": better}
    ])  # fmt: skip
    result = reply["results"][0]
    assert result["ok"] and result["status"] == "qa_pass"
    row = get_message(msgs[0])
    assert row["body"] == better and row["ai_review"] == "" and row["status"] == "qa_pass"
    assert row["qa_json"]["history"][0]["body"] == original
    assert row["qa_json"]["review_log"][0] == {
        "verdict": "fix", "notes": "Tone", "at": row["qa_json"]["review_log"][0]["at"],
    }  # fmt: skip
    # back in the review queue, and not offered for redrafting
    assert msgs[0] in [i["message_id"] for i in review.get_review_batch(cid)["items"]]
    drafts = drafting.get_drafting_batch(cid, "linkedin_message")
    assert people[0] not in [p["person_id"] for p in drafts["people"]]
    # a fresh review is required again: strict gate does not pass it
    assert msgs[0] not in export_gate(cid)["messages"]["exportable_ids"]


def test_fix_that_still_fails_goes_to_qa_fail_and_redrafting(world):
    cid, _, people, msgs = world
    reply = review.record_review(cid, [
        {"message_id": msgs[0], "verdict": "fix", "notes": "Tried", "corrected_body": "Hi Aria"}
    ])  # fmt: skip
    result = reply["results"][0]
    assert result["ok"] and result["status"] == "qa_fail"
    assert "M04" in [i["code"] for i in result["issues"]]
    assert "still fail" in reply["next_step"]
    assert people[0] in [
        p["person_id"] for p in drafting.get_drafting_batch(cid, "linkedin_message")["people"]
    ]


def test_fix_keeps_the_subject_unless_a_new_one_is_given(world):
    cid, _, people, msgs = world
    camp.update_campaign(cid, channels=["linkedin", "email"])
    add_email(people[0])
    mail = add_message(people[0], channel="email", subject="Old subject here")
    body = email_message()["body"].replace("practical", "applied")
    review.record_review(
        cid, [{"message_id": mail, "verdict": "fix", "notes": "x y z", "corrected_body": body}]
    )
    assert get_message(mail)["subject"] == "Old subject here"
    review.record_review(cid, [
        {"message_id": mail, "verdict": "fix", "notes": "x y z", "corrected_body": body,
         "corrected_subject": "New subject here"}
    ])  # fmt: skip
    assert get_message(mail)["subject"] == "New subject here"


def test_fix_needs_text_and_notes_and_a_saveable_message(world):
    cid, _, people, msgs = world
    out = review.record_review(cid, [
        {"message_id": msgs[0], "verdict": "fix", "notes": "No text"},
        {"message_id": msgs[1], "verdict": "fix", "corrected_body": "x" * 200},
        {"message_id": msgs[2], "verdict": "fix", "notes": "Bad", "corrected_body": "x" * 200,
         "corrected_subject": 5},
    ])["results"]  # fmt: skip
    assert [r["error"]["code"] for r in out] == [
        "corrected_body_required", "notes_required", "invalid_value",
    ]  # fmt: skip
    with db.session() as conn:
        db.update(conn, "people", people[0], status="review")
    gone = review.record_review(cid, [
        {"message_id": msgs[0], "verdict": "fix", "notes": "Late", "corrected_body": "x" * 200}
    ])["results"][0]  # fmt: skip
    assert gone["error"]["code"] == "person_not_confirmed"


def test_reject_marks_the_message_and_removes_the_person(world):
    cid, _, people, msgs = world
    reply = review.record_review(cid, [
        {"message_id": msgs[1], "verdict": "reject", "notes": "Looks like a recruiter"}
    ])  # fmt: skip
    assert reply["results"][0]["person_removed"] == people[1]
    row = get_message(msgs[1])
    assert (row["status"], row["ai_review"]) == ("rejected", "reject")
    assert "recruiter" in row["ai_review_notes"]
    person = get_person(people[1])
    assert person["status"] == "removed"
    assert any("Looks like a recruiter" in r for r in person["reasons_json"])
    # never redrafted, never reviewed, never exported
    assert people[1] not in [
        p["person_id"] for p in drafting.get_drafting_batch(cid, "linkedin_message")["people"]
    ]
    assert msgs[1] not in [i["message_id"] for i in review.get_review_batch(cid)["items"]]
    assert export_gate(cid)["messages"]["rejected"] == 1
    again = review.record_review(cid, [{"message_id": msgs[1], "verdict": "pass", "notes": ""}])
    assert again["results"][0]["error"]["code"] == "not_reviewable"


def test_pass_is_refused_for_failed_or_changed_messages(world):
    cid, _, people, msgs = world
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], status="qa_fail")
        db.update(
            conn,
            "messages",
            msgs[1],
            body=message_row()["body"].replace("Aria", "Marcus") + " 43% more.",
        )
    out = review.record_review(cid, [
        {"message_id": msgs[0], "verdict": "pass"},
        {"message_id": msgs[1], "verdict": "pass"},
    ])["results"]  # fmt: skip
    assert out[0]["error"]["code"] == "qa_failed"
    assert out[1]["error"]["code"] == "has_block_issues" and "M06" in out[1]["error"]["message"]
    assert get_message(msgs[1])["ai_review"] == ""


def test_record_review_reports_bad_entries_and_keeps_going(world):
    cid, _, _, msgs = world
    other = make_campaign()["id"]
    foreign = add_message(add_person(other, add_company(other)))
    reply = review.record_review(cid, [
        "nope",
        {"message_id": msgs[0], "verdict": "maybe"},
        {"message_id": 9999, "verdict": "pass"},
        {"message_id": foreign, "verdict": "pass"},
        {"message_id": msgs[1], "verdict": "pass", "notes": "ok"},
        {"message_id": msgs[1], "verdict": "pass"},
    ])  # fmt: skip
    codes = [r.get("error", {}).get("code") for r in reply["results"]]
    assert codes == ["invalid_result", "invalid_verdict", "unknown_message", "unknown_message",
                     None, "duplicate_in_call"]  # fmt: skip
    assert reply["recorded"]["pass"] == 1 and reply["errors"] == 5
    assert "ok=false" in reply["next_step"]
    assert get_message(foreign)["ai_review"] == ""


def test_record_review_validates_the_list(world):
    cid, *_ = world
    for bad in ([], "x", None):
        with pytest.raises(ReviewError):
            review.record_review(cid, bad)
    with pytest.raises(ReviewError) as err:
        review.record_review(cid, [{"message_id": 1, "verdict": "pass"}] * 51)
    assert err.value.code == "too_many_results"


def test_empty_queue_message(world):
    cid, _, _, msgs = world
    reply = review.record_review(cid, [{"message_id": m, "verdict": "pass"} for m in msgs])
    assert reply["remaining_to_review"] == 0 and "queue is empty" in reply["next_step"]


# --- export gate ------------------------------------------------------------------------------


def test_gate_strict_mode_needs_an_ai_pass(world):
    cid, _, _, msgs = world
    gate = export_gate(cid)
    assert gate["strict_review"] is True and gate["messages"]["exportable"] == 0
    assert gate["blocked_by"] == {"M16": 3}
    assert gate["blocked_messages"][0]["reasons"][0]["code"] == "M16"
    review.record_review(cid, [{"message_id": msgs[0], "verdict": "pass", "notes": "ok"}])
    gate = export_gate(cid)
    assert gate["messages"]["exportable_ids"] == [msgs[0]] and gate["messages"]["blocked"] == 2
    assert "1 message(s) can go" in gate["next_step"]


def test_gate_strict_mode_off_allows_unreviewed_messages(world):
    cid, _, _, msgs = world
    set_strict(False)
    gate = export_gate(cid)
    assert gate["strict_review"] is False
    assert gate["messages"]["exportable_ids"] == msgs and gate["blocked_by"] == {}
    assert "can be exported" in gate["next_step"]
    assert export_gate(cid, strict=True)["messages"]["exportable"] == 0  # explicit override
    # a fix or reject verdict still blocks without strict mode
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], ai_review="fix")
    assert msgs[0] not in export_gate(cid)["messages"]["exportable_ids"]


def test_gate_blocks_failed_messages_and_blocked_leads(world):
    cid, _, people, msgs = world
    set_strict(False)
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], body="Hi Aria")
        db.update(conn, "people", people[1], **NO_EMPLOYER)
    gate = export_gate(cid)
    assert gate["messages"]["exportable_ids"] == [msgs[2]]
    assert gate["blocked_by"]["M04"] == 1 and gate["blocked_by"]["L04"] == 1
    assert gate["leads"]["blocked"] == 1 and people[1] not in gate["leads"]["exportable_ids"]
    reasons = {m["message_id"]: [r["code"] for r in m["reasons"]] for m in gate["blocked_messages"]}
    assert "L04" in reasons[msgs[1]] and "M04" in reasons[msgs[0]]


def test_gate_unconfirmed_people_and_sent_messages(world):
    cid, _, people, msgs = world
    set_strict(False)
    with db.session() as conn:
        db.update(conn, "people", people[0], status="review")
        db.update(conn, "messages", msgs[1], status="sent")
    gate = export_gate(cid)
    assert gate["messages"]["already_sent"] == 1
    assert gate["blocked_by"]["person_not_confirmed"] == 1
    assert gate["messages"]["exportable_ids"] == [msgs[2]]


def test_gate_email_blocks_do_not_stop_linkedin_messages(world):
    cid, _, people, msgs = world
    set_strict(False)
    camp.update_campaign(cid, channels=["linkedin", "email"])
    mail = add_message(people[0], channel="email")  # no email address for Aria: L17
    gate = export_gate(cid)
    assert mail not in gate["messages"]["exportable_ids"]
    assert msgs[0] in gate["messages"]["exportable_ids"]
    assert [
        r["code"] for m in gate["blocked_messages"] if m["message_id"] == mail for r in m["reasons"]
    ] == ["L17"]
    assert people[0] in gate["leads"]["exportable_ids"]
    add_email(people[0])
    assert mail in export_gate(cid)["messages"]["exportable_ids"]


def test_gate_company_domain_suppression_blocks_linkedin_messages(world):
    cid, _, people, msgs = world
    set_strict(False)
    suppress("domain", "northwind.example")
    gate = export_gate(cid)
    assert gate["messages"]["exportable_ids"] == []
    assert gate["blocked_by"]["L16"] == 3
    assert gate["leads"]["exportable_ids"] == []


def test_gate_guess_email_is_exportable_but_not_sendable(world):
    cid, _, people, msgs = world
    set_strict(False)
    camp.update_campaign(cid, channels=["linkedin", "email"])
    add_email(people[0], "guess")
    mail = add_message(people[0], channel="email")
    assert mail in export_gate(cid)["messages"]["exportable_ids"]
    send = export_gate(cid, purpose="send")
    assert send["purpose"] == "send" and mail not in send["messages"]["exportable_ids"]
    assert send["blocked_by"]["L23"] == 1
    camp.update_campaign(cid, allow_guess_emails=True)
    assert mail in export_gate(cid, purpose="send")["messages"]["exportable_ids"]
    with pytest.raises(ReviewError):
        export_gate(cid, purpose="print")


def test_gate_does_not_need_run_qa_and_does_not_write(world):
    cid, *_ = world
    export_gate(cid)
    assert issue_rows(cid) == []
    assert get_message(1)["status"] == "qa_pass"


def test_gate_hand_promoted_person_can_be_exported(world):
    cid, _, people, msgs = world
    set_strict(False)
    with db.session() as conn:
        db.update(conn, "people", people[0],
                  evidence_snippet="Singapore · Training leader.",
                  evidence_title="Aria Tan - Head of Learning & Development | LinkedIn",
                  reasons_json=["L04:x", "MANUAL:confirmed: checked the profile"])  # fmt: skip
    gate = export_gate(cid)
    assert msgs[0] in gate["messages"]["exportable_ids"]
    # but the warning stays visible
    reply = run_qa(cid)
    assert reply["counts"]["L04"] == {"block": 0, "warn": 1}


def test_run_qa_summary_lists_next_steps(world):
    cid, _, people, msgs = world
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], body="Hi Aria")
    reply = run_qa(cid)
    assert "redraft" in reply["next_step"] and "AI review" in reply["next_step"]
    review.record_review(cid, [{"message_id": m, "verdict": "pass"} for m in msgs[1:]])
    with db.session() as conn:
        db.update(conn, "messages", msgs[0], body=message_row()["body"])
    run_qa(cid)
    review.record_review(cid, [{"message_id": msgs[0], "verdict": "pass"}])
    clean = run_qa(cid)
    assert clean["next_step"].startswith("Nothing is blocked")
    assert clean["export_ready"] == {"messages": 3, "people": 3}
