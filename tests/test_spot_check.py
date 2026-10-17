"""Spot check for AI-search leads (T21): sample rules, seed, verdicts, the L21 lifecycle."""

from __future__ import annotations

import pytest
from qa_world import add_company, add_message, add_person, make_campaign

from leadgen import db
from leadgen.services import campaign as camp
from leadgen.services import qa_leads, review, spot_check
from leadgen.services.spot_check import SpotCheckError


def build(count, origin="ai_web_search"):
    cid = make_campaign()["id"]
    company = add_company(cid)
    ids = [
        add_person(cid, company, f"Name{i}", f"Surname{i}", evidence_origin=origin)
        for i in range(count)
    ]
    return cid, ids


def state(cid):
    return qa_leads.load_leads(camp.get_campaign(cid)).context.ai_spot_check


def person(pid):
    with db.session() as conn:
        return db.get(conn, "people", pid)


def answer(cid, people, verdicts):
    pairs = zip(people, verdicts, strict=True)
    results = [{"person_id": p["person_id"], "verdict": v} for p, v in pairs]
    return spot_check.record_spot_check(cid, results)


@pytest.mark.parametrize(
    ("total", "expected"),
    [(1, 1), (3, 3), (5, 5), (40, 5), (100, 5), (101, 6), (200, 10), (300, 15), (1000, 15)],
)
def test_sample_size_is_five_or_five_percent_capped_at_fifteen(total, expected):
    assert spot_check.sample_size(total) == expected


def test_no_ai_leads_means_no_spot_check(ready_db):
    cid, _ = build(3, origin="api")
    got = spot_check.get_spot_check(cid)
    assert got == {"ok": True, "needed": False, "message": got["message"]}
    assert state(cid) == ""


def test_sample_is_random_pending_and_reproducible_from_the_seed(ready_db):
    cid, ids = build(30)
    first = spot_check.get_spot_check(cid, seed=7)
    assert first["needed"] and first["sample_size"] == 5 and first["seed"] == 7
    assert first["round"] == 1 and "'ok' or 'wrong'" in first["instruction"]
    item = first["people"][0]
    assert set(item) == {"person_id", "name", "title", "company", "linkedin_url"}
    assert item["linkedin_url"].startswith("https://www.linkedin.com/in/")
    picked = {p["person_id"] for p in first["people"]}
    assert {i for i in ids if person(i)["spot_check"] == "pending"} == picked
    assert state(cid) == "incomplete"
    # asking again while answers are pending gives the same people and draws nothing new
    again = spot_check.get_spot_check(cid)
    assert {p["person_id"] for p in again["people"]} == picked and again["seed"] == 7
    # the stored seed reproduces the sample on a fresh campaign with the same ids
    cid2, ids2 = build(30)
    second = spot_check.get_spot_check(cid2, seed=7)
    offsets = sorted(i - ids[0] for i in picked)
    assert sorted(p["person_id"] - ids2[0] for p in second["people"]) == offsets


def test_random_seed_is_stored_when_none_is_given(ready_db):
    cid, _ = build(12)
    got = spot_check.get_spot_check(cid)
    assert isinstance(got["seed"], int) and spot_check._load_state(cid)["seed"] == got["seed"]


def test_all_ok_unblocks_the_ai_leads(ready_db):
    cid, ids = build(10)
    got = spot_check.get_spot_check(cid)
    assert state(cid) == "incomplete"
    out = answer(cid, got["people"], ["ok"] * 5)
    assert out["recorded"] == {"ok": 5, "wrong": 0} and out["needs_recheck"] is False
    assert out["pending_left"] == 0 and "no longer blocked" in out["message"]
    assert state(cid) == "ok"
    done = spot_check.get_spot_check(cid)
    assert done["needed"] is False and done["status"] == "passed"
    gate = review.export_gate(cid)
    assert gate["blocked_by"].get("L21") is None
    assert len(ids) == 10


def test_pending_answers_block_export_with_l21(ready_db):
    cid, ids = build(10)
    message = add_message(ids[0])
    spot_check.get_spot_check(cid)
    gate = review.export_gate(cid)
    assert gate["leads"]["exportable_ids"] == []
    assert message not in gate["messages"]["exportable_ids"]
    assert gate["blocked_by"]["L21"] == 1


def test_one_wrong_answer_goes_to_review_and_does_not_fail_the_batch(ready_db):
    cid, _ = build(10)
    got = spot_check.get_spot_check(cid)
    out = spot_check.record_spot_check(
        cid,
        [{"person_id": got["people"][0]["person_id"], "verdict": "wrong", "note": "other person"}]
        + [{"person_id": p["person_id"], "verdict": "ok"} for p in got["people"][1:]],
    )
    assert out["recorded"] == {"ok": 4, "wrong": 1} and out["needs_recheck"] is False
    bad = person(got["people"][0]["person_id"])
    assert bad["status"] == "review" and bad["confirmed"] == 0 and bad["spot_check"] == "wrong"
    assert bad["reasons_json"] == ["L21:spot check failed: other person"]
    assert state(cid) == "ok"
    assert camp.get_campaign(cid)["options"]["ai_search_needs_recheck"] is False


def test_two_wrong_answers_flag_the_campaign_until_a_second_check_passes(ready_db):
    cid, ids = build(20)
    first = spot_check.get_spot_check(cid, seed=1)
    out = answer(cid, first["people"], ["wrong", "wrong", "ok", "ok", "ok"])
    assert out["needs_recheck"] is True and out["round_wrong"] == 2
    assert camp.get_campaign(cid)["options"]["ai_search_needs_recheck"] is True
    assert state(cid) == "failed"
    assert qa_leads.LEAD_SEVERITY["L21"] == "warn"
    gate = review.export_gate(cid)
    assert gate["leads"]["exportable_ids"] == []
    # a second sample comes from leads not checked yet; the first round's answers stay
    second = spot_check.get_spot_check(cid, seed=2)
    assert second["round"] == 2 and second["after_recheck"] is True
    taken = {p["person_id"] for p in first["people"]}
    assert taken.isdisjoint({p["person_id"] for p in second["people"]})
    still = answer(cid, second["people"], ["ok", "ok", "ok", "wrong", "ok"])
    assert still["needs_recheck"] is False  # one wrong is below the limit: the second check passes
    assert state(cid) == "ok"
    assert camp.get_campaign(cid)["options"]["ai_search_needs_recheck"] is False


def test_second_check_with_two_wrong_keeps_the_block(ready_db):
    cid, _ = build(30)
    first = spot_check.get_spot_check(cid, seed=1)
    answer(cid, first["people"], ["wrong", "wrong", "ok", "ok", "ok"])
    second = spot_check.get_spot_check(cid, seed=2)
    out = answer(cid, second["people"], ["wrong", "wrong", "ok", "ok", "ok"])
    assert out["needs_recheck"] is True and state(cid) == "failed"
    third = spot_check.get_spot_check(cid, seed=3)
    assert third["round"] == 3


def test_answers_in_several_calls_add_up_within_one_sample(ready_db):
    cid, _ = build(10)
    got = spot_check.get_spot_check(cid)
    people = got["people"]
    one = answer(cid, people[:1], ["wrong"])
    assert one["needs_recheck"] is False and one["pending_left"] == 4
    assert "4 answer" in one["message"] and state(cid) == "incomplete"
    two = answer(cid, people[1:2], ["wrong"])
    assert two["needs_recheck"] is True and two["round_wrong"] == 2


def test_refused_answers(ready_db):
    cid, ids = build(10)
    other, other_ids = build(3)
    got = spot_check.get_spot_check(cid)
    sample = {p["person_id"] for p in got["people"]}
    outside = next(i for i in ids if i not in sample)
    out = spot_check.record_spot_check(
        cid,
        [
            {"person_id": outside, "verdict": "ok"},  # not in the sample
            {"person_id": other_ids[0], "verdict": "ok"},  # another campaign
            {"person_id": 99999, "verdict": "ok"},
        ],
    )
    assert out["recorded"] == {"ok": 0, "wrong": 0}
    assert [r["why"] for r in out["refused"]] == [
        "not in the pending sample", "not found", "not found",
    ]  # fmt: skip
    first = got["people"][0]["person_id"]
    spot_check.record_spot_check(cid, [{"person_id": first, "verdict": "ok"}])
    twice = spot_check.record_spot_check(cid, [{"person_id": first, "verdict": "wrong"}])
    assert twice["refused"][0]["why"] == "not in the pending sample"
    assert person(first)["spot_check"] == "ok" and other != cid


@pytest.mark.parametrize(
    "bad",
    ["x", [5], [{"person_id": "1", "verdict": "ok"}], [{"person_id": True, "verdict": "ok"}],
     [{"person_id": 1, "verdict": "maybe"}]],
)  # fmt: skip
def test_bad_input_is_refused(ready_db, bad):
    cid, _ = build(5)
    with pytest.raises(SpotCheckError) as err:
        spot_check.record_spot_check(cid, bad)
    assert err.value.code == "invalid_value"
    assert err.value.as_response()["ok"] is False


def test_unknown_campaign(ready_db):
    with pytest.raises(camp.CampaignError):
        spot_check.get_spot_check(999)
    with pytest.raises(camp.CampaignError):
        spot_check.record_spot_check(999, [])


def test_resample_draws_a_new_sample_after_more_leads_arrive(ready_db):
    cid, _ = build(10)
    answer(cid, spot_check.get_spot_check(cid)["people"], ["ok"] * 5)
    company = add_company(cid, "Lakeside Bank", "lakeside.example")
    for i in range(10):
        add_person(cid, company, f"New{i}", f"Face{i}", evidence_origin="ai_web_search")
    assert spot_check.get_spot_check(cid)["needed"] is False
    fresh = spot_check.get_spot_check(cid, resample=True)
    assert fresh["needed"] and fresh["round"] == 2
    assert {p["person_id"] for p in fresh["people"]}  # drawn only from leads not yet checked
    assert all(person(p["person_id"])["spot_check"] == "pending" for p in fresh["people"])


def test_small_campaign_samples_everyone(ready_db):
    cid, _ = build(3)
    got = spot_check.get_spot_check(cid)
    assert got["sample_size"] == 3


def test_recheck_with_no_unchecked_leads_resamples_the_ok_ones(ready_db):
    cid, _ = build(5)
    first = spot_check.get_spot_check(cid)
    answer(cid, first["people"], ["ok", "ok", "ok", "wrong", "wrong"])
    assert state(cid) == "failed"
    second = spot_check.get_spot_check(cid)  # only the three ok leads are left to sample
    assert second["sample_size"] == 3 and second["round"] == 2
    out = answer(cid, second["people"], ["ok"] * 3)
    assert out["needs_recheck"] is False and state(cid) == "ok"


def test_the_flag_set_by_hand_clears_after_a_passing_first_check(ready_db):
    cid, _ = build(8)
    camp.update_campaign(cid, ai_search_needs_recheck=True)
    assert state(cid) == "failed"
    got = spot_check.get_spot_check(cid)
    assert got["after_recheck"] is True
    out = answer(cid, got["people"], ["ok"] * 5)
    assert out["needs_recheck"] is False and state(cid) == "ok"


def _passed_with_one_wrong_then_promoted(count=10):
    """One 'wrong' answer, then the user promotes that person by hand."""
    from leadgen.services import people as people_mod

    cid, ids = build(count)
    got = spot_check.get_spot_check(cid, seed=3)
    wrong = got["people"][0]["person_id"]
    results = [{"person_id": wrong, "verdict": "wrong"}] + [
        {"person_id": p["person_id"], "verdict": "ok"} for p in got["people"][1:]
    ]
    spot_check.record_spot_check(cid, results)
    people_mod.set_person_status(cid, [wrong], "confirmed", "checked the profile by hand")
    return cid, ids, wrong, got


def test_one_wrong_person_promoted_by_hand_does_not_fail_the_campaign(ready_db):
    cid, ids, wrong, got = _passed_with_one_wrong_then_promoted()
    assert person(wrong)["status"] == "confirmed" and person(wrong)["spot_check"] == "wrong"
    assert state(cid) == "ok"  # one wrong never fails the campaign
    assert camp.get_campaign(cid)["options"]["ai_search_needs_recheck"] is False
    again = spot_check.get_spot_check(cid)
    assert again["needed"] is False and again["status"] == "passed"
    ok_one = next(p["person_id"] for p in got["people"] if p["person_id"] != wrong)
    add_message(wrong)
    add_message(ok_one)
    gate = review.export_gate(cid)
    # only the hand-promoted wrong person is blocked (L21 per person); the ok one is not
    assert gate["blocked_by"].get("L21") == 1
    assert wrong not in gate["leads"]["exportable_ids"]
    assert ok_one in gate["leads"]["exportable_ids"]
    # a resample that passes agrees with QA: "no longer blocked" and state ok
    fresh = spot_check.get_spot_check(cid, resample=True)
    assert fresh["needed"] is True and wrong not in {p["person_id"] for p in fresh["people"]}
    out = answer(cid, fresh["people"], ["ok"] * len(fresh["people"]))
    assert "no longer blocked" in out["message"] and state(cid) == "ok"


def test_removing_the_ok_people_draws_a_new_sample_instead_of_saying_passed(ready_db):
    from leadgen.services import people as people_mod

    cid, ids = build(10)
    got = spot_check.get_spot_check(cid, seed=5)
    answer(cid, got["people"], ["ok"] * len(got["people"]))
    assert state(cid) == "ok"
    sampled = [p["person_id"] for p in got["people"]]
    people_mod.set_person_status(cid, sampled, "review")
    assert state(cid) == "incomplete"
    unchecked = [i for i in ids if i not in sampled]
    add_message(unchecked[0])
    gate = review.export_gate(cid)
    assert gate["leads"]["exportable_ids"] == [] and gate["blocked_by"]["L21"] == 1
    new = spot_check.get_spot_check(cid)
    assert new["needed"] is True and new["round"] == 2
    assert not {p["person_id"] for p in new["people"]} & set(sampled)
    out = answer(cid, new["people"], ["ok"] * len(new["people"]))
    assert "no longer blocked" in out["message"] and state(cid) == "ok"
    assert review.export_gate(cid)["blocked_by"].get("L21") is None


def test_only_a_wrong_promoted_lead_left_has_nobody_to_sample(ready_db):
    from leadgen.services import people as people_mod

    cid, ids = build(1)
    got = spot_check.get_spot_check(cid)
    answer(cid, got["people"], ["wrong"])
    people_mod.set_person_status(cid, ids, "confirmed", "checked by hand")
    assert state(cid) == "incomplete"
    out = spot_check.get_spot_check(cid)
    assert out["needed"] is False and out["status"] == "nothing_to_sample"


def test_record_message_says_blocked_when_no_ok_lead_remains(ready_db):
    from leadgen.services import people as people_mod

    cid, ids = build(10)
    got = spot_check.get_spot_check(cid)
    ok = got["people"][:-1]
    spot_check.record_spot_check(
        cid, [{"person_id": p["person_id"], "verdict": "ok"} for p in ok]
    )
    last = got["people"][-1]["person_id"]
    people_mod.set_person_status(cid, [p["person_id"] for p in ok], "review")
    out = spot_check.record_spot_check(cid, [{"person_id": last, "verdict": "wrong"}])
    assert "no longer blocked" not in out["message"]
    assert "No confirmed AI-search lead" in out["message"]
