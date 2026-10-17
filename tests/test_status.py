"""`get_status` and its rule-based next step (T13). All data is invented."""

from __future__ import annotations

import pytest
from qa_world import add_company, add_message, add_person, make_campaign

from leadgen import db
from leadgen.core import secrets
from leadgen.services import offer, status


def camp(**over):
    """A campaign dict shaped like `get_campaign`, with zero counts."""
    counts = {
        "companies": {"candidate": 0, "approved": 0, "excluded": 0},
        "people": {"confirmed": 0, "review": 0, "rejected": 0, "removed": 0},
        "emails": {"verified": 0, "risky": 0, "guess": 0, "invalid": 0, "unknown": 0},
        "messages": {"draft": 0, "qa_fail": 0, "qa_pass": 0, "approved": 0, "sent": 0},
    }
    for kind, values in over.pop("counts", {}).items():
        counts[kind].update(values)
    base = {
        "id": 7, "channels": ["linkedin"], "options": {"search_mode": "auto"}, "counts": counts,
    }  # fmt: skip
    base.update(over)
    return base


def step(c, **kw):
    kw.setdefault("has_profile", True)
    kw.setdefault("has_search_key", True)
    return status.next_step_for(c, **kw)["tool"]


@pytest.mark.parametrize(
    ("counts", "kw", "options", "channels", "expected"),
    [
        ({}, {"has_profile": False}, {}, ["linkedin"], "save_offer_profile"),
        ({}, {}, {}, ["linkedin"], "add_companies"),
        ({"companies": {"candidate": 3}}, {}, {}, ["linkedin"], "approve_companies"),
        ({"companies": {"approved": 3}}, {}, {}, ["linkedin"], "estimate_people_search"),
        ({"companies": {"approved": 3}}, {"has_search_key": False}, {}, ["linkedin"],
         "list_providers"),
        ({"companies": {"approved": 3}}, {"has_search_key": False}, {"search_mode": "ai"},
         ["linkedin"], "get_search_tasks"),
        ({"companies": {"approved": 3}, "people": {"review": 2}}, {}, {}, ["linkedin"],
         "list_people"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2}}, {}, {}, ["email"],
         "estimate_email_search"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2}}, {}, {}, ["linkedin"],
         "get_drafting_batch"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2},
          "messages": {"qa_fail": 1}}, {}, {}, ["linkedin"], "run_qa"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2},
          "messages": {"qa_pass": 1, "approved": 1}}, {}, {}, ["linkedin"], "get_review_batch"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2},
          "messages": {"approved": 2}}, {}, {}, ["linkedin"], "export_campaign"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2},
          "messages": {"approved": 2}}, {"spot": "incomplete"}, {}, ["linkedin"], "get_spot_check"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2},
          "messages": {"sent": 2}}, {}, {}, ["linkedin"], "get_analytics"),
        ({"companies": {"approved": 3}, "people": {"confirmed": 2},
          "messages": {"rejected": 2}}, {}, {}, ["linkedin"], "run_qa"),
    ],
)  # fmt: skip
def test_next_step_rules(counts, kw, options, channels, expected):
    c = camp(counts=counts, channels=channels, options={"search_mode": "auto", **options})
    assert step(c, **kw) == expected


def test_status_lists_campaigns_with_counts_and_steps(ready_db):
    campaign = make_campaign(channels=("linkedin",))
    cid = campaign["id"]
    company = add_company(cid)
    person = add_person(cid, company)
    add_message(person, "linkedin_message", status="approved", ai_review="pass")
    result = status.get_status()
    assert result["ok"] and [c["id"] for c in result["campaigns"]] == [cid]
    entry = result["campaigns"][0]
    assert entry["counts"]["messages"]["approved"] == 1
    assert entry["next_step"]["tool"] == "export_campaign"
    assert result["next_step"] == entry["next_step"]
    assert any("No search key" in note for note in result["notes"])


def test_status_offer_without_profile_and_with_profile(ready_db):
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Raw", source_type="text", raw_text="x")
    first = status.get_status()
    assert first["next_step"]["tool"] == "save_offer_profile"
    assert first["next_step"]["offer_id"] == offer_id
    from qa_world import PROFILE

    offer.save_profile(offer_id, PROFILE)
    second = status.get_status()
    assert second["next_step"]["tool"] == "create_campaign"
    assert second["next_step"]["offer_id"] == offer_id


def test_status_reports_a_broken_settings_file(ready_db):
    from leadgen.core import paths

    paths.settings_path().write_text("budgets:\n  search: lots\n", encoding="utf-8")
    result = status.get_status()
    assert result["ok"] and any("settings.yaml" in note for note in result["notes"])


def test_status_search_key_note_disappears_with_a_key(ready_db):
    secrets.set("SERPER_API_KEY", "sk-serper-0123456789abcd")
    assert not any("No search key" in n for n in status.get_status()["notes"])


def test_save_key_unknown_provider():
    reply = status.save_key("nope", "abc")
    assert reply["ok"] is False and reply["error"]["code"] == "unknown_provider"
