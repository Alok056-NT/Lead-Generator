"""Campaign create, update, read, list and archive."""

from __future__ import annotations

import pytest

from leadgen import db
from leadgen.core import config
from leadgen.services import campaign as c
from leadgen.services.campaign import CampaignError

CUSTOM = {"label": "Chefs", "keywords": ["chef"], "search_terms": ["head chef"]}


@pytest.fixture
def offer_id(ready_db):
    with db.session() as conn:
        return db.insert(
            conn,
            "offers",
            name="Northwind Training",
            source_type="text",
            profile_json={"language": "en-SG"},
        )


@pytest.fixture
def bare_offer_id(ready_db):
    with db.session() as conn:
        return db.insert(conn, "offers", name="No profile yet", source_type="text")


def make(offer_id, **overrides):
    args = {
        "name": "Singapore logistics Q4",
        "offer_id": offer_id,
        "countries": ["Singapore"],
        "sectors": ["logistics"],
        "company_size": "200-1000",
        "channels": ["linkedin"],
        "designations": ["ld", "hr"],
    }
    args.update(overrides)
    return c.create_campaign(**args)


def fails(code, **kwargs):
    with pytest.raises(CampaignError) as exc:
        make(**kwargs)
    assert exc.value.code == code
    assert exc.value.message
    assert exc.value.as_response() == {
        "ok": False,
        "error": {"code": code, "message": exc.value.message, "hint": exc.value.hint},
    }
    return exc.value


# --- create ---------------------------------------------------------------------------------


def test_create_stores_everything(offer_id):
    got = make(
        offer_id,
        countries=["sg", "United Kingdom", "UK"],
        sectors=[" logistics ", "Logistics", "freight"],
        channels=["LinkedIn", "email"],
        designations=["ld", CUSTOM],
        seniority_min="director",
        exclusions={"domains": ["https://www.Example.com/about", "example.org"],
                    "companies": ["Contoso"]},
        budget={"search": 50},
        options={"invite_note": True},
    )
    assert got["id"] >= 1 and got["status"] == "draft" and got["offer_id"] == offer_id
    assert got["name"] == "Singapore logistics Q4"
    assert got["countries"] == ["SG", "GB"]
    assert got["sectors"] == ["logistics", "freight"]
    assert got["company_size"] == "200-1000"
    assert got["channels"] == ["linkedin", "email"]
    assert [g["label"] for g in got["designations"]] == ["L&D / Training heads", "Chefs"]
    assert got["designations"][0]["keywords"][0] == "learning"
    assert got["seniority_min"] == "director"
    assert got["exclusions"] == {
        "companies": ["Contoso"],
        "domains": ["example.com", "example.org"],
        "people": [],
        "competitors": [],
    }
    assert got["budget"] == {"search": 50}
    assert got["options"] == {
        "invite_note": True,
        "allow_risky_emails": False,
        "allow_guess_emails": False,
        "search_mode": "auto",
        "ai_search_needs_recheck": False,
    }
    assert got["locale"] == "en-SG"  # from the offer profile
    assert got["counts"]["people"] == {"confirmed": 0, "review": 0, "rejected": 0, "removed": 0}


def test_defaults(offer_id):
    got = make(offer_id)
    assert got["seniority_min"] == "manager"
    assert got["options"] == c.DEFAULT_OPTIONS
    assert got["budget"] == {}
    assert got["exclusions"] == dict.fromkeys(c.EXCLUSION_KEYS, [])


def test_locale_order(offer_id, bare_offer_id):
    assert make(offer_id, locale="en_gb")["locale"] == "en-GB"  # explicit wins
    # No profile: falls back to the settings locale.
    settings = config.load_settings()
    settings.locale = "de-DE"
    config.save_settings(settings)
    assert make(bare_offer_id, name="Second")["locale"] == "de-DE"
    assert make(bare_offer_id, name="Third", locale="fr")["locale"] == "fr"


def test_template_group_is_copied_into_the_campaign(offer_id):
    got = make(offer_id, designations=["owners"])
    assert got["designations"][0]["label"] == "Business owners / MDs"
    assert "search_terms" in got["designations"][0]


def test_get_and_list(offer_id):
    first = make(offer_id)
    second = make(offer_id, name="Another")
    assert c.get_campaign(first["id"]) == first
    listed = c.list_campaigns()
    assert [x["id"] for x in listed] == [second["id"], first["id"]]
    assert listed[0]["designations"] == ["L&D / Training heads", "HR leaders"]
    c.archive_campaign(first["id"])
    assert [x["id"] for x in c.list_campaigns(include_archived=False)] == [second["id"]]
    assert len(c.list_campaigns()) == 2


def test_get_counts(offer_id):
    got = make(offer_id)
    other = make(offer_id, name="Other campaign")
    with db.session() as conn:
        def company(cid, status):
            return db.insert(conn, "companies", campaign_id=cid, name=f"Co {status} {cid}",
                             status=status)

        a = company(got["id"], "approved")
        company(got["id"], "candidate")
        company(got["id"], "candidate")
        b = company(other["id"], "approved")
        p1 = db.insert(conn, "people", campaign_id=got["id"], company_id=a, status="confirmed")
        p2 = db.insert(conn, "people", campaign_id=got["id"], company_id=a, status="review")
        p3 = db.insert(conn, "people", campaign_id=other["id"], company_id=b, status="confirmed")
        db.insert(conn, "emails", person_id=p1, email="aria@example.com", status="verified")
        db.insert(conn, "emails", person_id=p2, email="ben@example.com", status="guess")
        db.insert(conn, "emails", person_id=p3, email="cy@example.com", status="verified")
        db.insert(conn, "messages", person_id=p1, channel="email", status="qa_pass")
        db.insert(conn, "messages", person_id=p2, channel="email", status="draft")
        db.insert(conn, "messages", person_id=p3, channel="email", status="sent")
    counts = c.get_campaign(got["id"])["counts"]
    assert counts["companies"] == {"candidate": 2, "approved": 1, "excluded": 0}
    assert counts["people"] == {"confirmed": 1, "review": 1, "rejected": 0, "removed": 0}
    assert counts["emails"] == {"verified": 1, "risky": 0, "guess": 1, "invalid": 0, "unknown": 0}
    assert counts["messages"] == {
        "draft": 1, "qa_fail": 0, "qa_pass": 1, "approved": 0, "sent": 0,
    }


# --- create: validation errors --------------------------------------------------------------


def test_bad_country(offer_id):
    err = fails("invalid_country", offer_id=offer_id, countries=["Atlantis"])
    assert "Atlantis" in err.message and err.hint


def test_no_countries(offer_id):
    fails("invalid_country", offer_id=offer_id, countries=[])
    fails("invalid_value", offer_id=offer_id, countries="SG")
    fails("invalid_value", offer_id=offer_id, countries=[1])


def test_bad_channel(offer_id):
    err = fails("invalid_channel", offer_id=offer_id, channels=["sms"])
    assert "sms" in err.message
    fails("invalid_channel", offer_id=offer_id, channels=[])
    fails("invalid_channel", offer_id=offer_id, channels=["email", "whatsapp"])


def test_zero_or_nine_groups(offer_id):
    fails("invalid_designations", offer_id=offer_id, designations=[])
    nine = [
        {"label": f"Group {i}", "keywords": [f"kw{i}"], "search_terms": [f"term {i}"]}
        for i in range(9)
    ]
    err = fails("invalid_designations", offer_id=offer_id, designations=nine)
    assert "1 to 8" in err.message
    assert len(make(offer_id, name="Eight", designations=nine[:8])["designations"]) == 8


def test_bad_designation_values(offer_id):
    fails("invalid_designations", offer_id=offer_id, designations=["nope"])
    fails("invalid_designations", offer_id=offer_id, designations="ld")
    fails("invalid_designations", offer_id=offer_id, designations={"label": "x"})
    fails("invalid_designations", offer_id=offer_id, designations=[{"label": "x"}])
    fails("invalid_designations", offer_id=offer_id, designations=["ld", "ld"])  # same label


def test_bad_simple_values(offer_id):
    fails("invalid_name", offer_id=offer_id, name="  ")
    fails("invalid_name", offer_id=offer_id, name="!!!")
    fails("invalid_name", offer_id=offer_id, name=None)
    fails("invalid_seniority", offer_id=offer_id, seniority_min="boss")
    fails("invalid_value", offer_id=offer_id, sectors="logistics")
    fails("invalid_value", offer_id=offer_id, company_size=5)
    fails("invalid_value", offer_id=offer_id, locale="english")
    fails("invalid_value", offer_id=offer_id, locale=5)


def test_bad_offer(offer_id):
    fails("invalid_offer", offer_id=999)
    fails("invalid_offer", offer_id="1")
    fails("invalid_offer", offer_id=True)


def test_bad_exclusions_options_budget(offer_id):
    fails("invalid_value", offer_id=offer_id, exclusions={"cities": ["x"]})
    fails("invalid_value", offer_id=offer_id, exclusions=["x"])
    fails("invalid_value", offer_id=offer_id, exclusions={"people": "Aria"})
    fails("invalid_value", offer_id=offer_id, options={"colour": "red"})
    fails("invalid_value", offer_id=offer_id, options={"search_mode": "magic"})
    fails("invalid_value", offer_id=offer_id, options={"invite_note": "yes"})
    fails("invalid_value", offer_id=offer_id, options="ai")
    fails("invalid_value", offer_id=offer_id, budget={"search": -1})
    fails("invalid_value", offer_id=offer_id, budget={"search": True})
    fails("invalid_value", offer_id=offer_id, budget=[1])


def test_search_modes(offer_id):
    for mode in ("api", "ai", "auto"):
        assert make(offer_id, name=f"m {mode}", options={"search_mode": mode})[
            "options"]["search_mode"] == mode


def test_duplicate_export_folder_name(offer_id):
    make(offer_id, name="Q4 Push")
    err = fails("duplicate_name", offer_id=offer_id, name="q4  push!")
    assert "Q4 Push" in err.message


# --- update / archive -----------------------------------------------------------------------


def test_update_changes_fields(offer_id):
    created = make(offer_id)
    got = c.update_campaign(
        created["id"],
        name="Renamed",
        countries=["MY"],
        sectors=["retail"],
        company_size="50-200",
        channels=["email", "linkedin"],
        designations=["finance"],
        seniority_min="vp",
        locale="en-MY",
        budget={"search": 10},
        status="active",
    )
    assert got["name"] == "Renamed" and got["countries"] == ["MY"]
    assert got["sectors"] == ["retail"] and got["company_size"] == "50-200"
    assert got["channels"] == ["email", "linkedin"]
    assert got["designations"][0]["label"] == "Finance"
    assert got["seniority_min"] == "vp" and got["locale"] == "en-MY"
    assert got["budget"] == {"search": 10} and got["status"] == "active"
    assert got["updated_at"] >= created["updated_at"]
    assert got["offer_id"] == offer_id


def test_update_search_mode_directly_and_merge_options(offer_id):
    created = make(offer_id, options={"invite_note": True})
    got = c.update_campaign(created["id"], search_mode="ai")
    assert got["options"]["search_mode"] == "ai" and got["options"]["invite_note"] is True
    got = c.update_campaign(created["id"], options={"allow_risky_emails": True},
                            allow_guess_emails=True)
    assert got["options"] == {
        "invite_note": True,
        "allow_risky_emails": True,
        "allow_guess_emails": True,
        "search_mode": "ai",
        "ai_search_needs_recheck": False,
    }
    got = c.update_campaign(created["id"], ai_search_needs_recheck=True)
    assert got["options"]["ai_search_needs_recheck"] is True
    with pytest.raises(c.CampaignError):
        c.update_campaign(created["id"], ai_search_needs_recheck="yes")


def test_update_exclusions_merge_per_key(offer_id):
    created = make(offer_id, exclusions={"companies": ["Contoso"], "people": ["Ben Lee"]})
    got = c.update_campaign(created["id"], exclusions={"companies": ["Fabrikam"]})
    assert got["exclusions"]["companies"] == ["Fabrikam"]
    assert got["exclusions"]["people"] == ["Ben Lee"]
    got = c.update_campaign(created["id"], exclusions={"people": []})
    assert got["exclusions"]["people"] == []


def test_update_offer_and_same_name(offer_id, bare_offer_id):
    created = make(offer_id)
    got = c.update_campaign(created["id"], offer_id=bare_offer_id, name=created["name"])
    assert got["offer_id"] == bare_offer_id and got["name"] == created["name"]


def test_update_validation_errors(offer_id):
    created = make(offer_id)
    other = make(offer_id, name="Other")
    cid = created["id"]
    bad = {
        "invalid_country": {"countries": ["Atlantis"]},
        "invalid_channel": {"channels": ["fax"]},
        "invalid_designations": {"designations": []},
        "invalid_seniority": {"seniority_min": "boss"},
        "invalid_name": {"name": ""},
        "duplicate_name": {"name": other["name"]},
        "invalid_offer": {"offer_id": 999},
        "invalid_value": {"status": "paused"},
        "unknown_field": {"colour": "red"},
    }
    for code, changes in bad.items():
        with pytest.raises(CampaignError) as exc:
            c.update_campaign(cid, **changes)
        assert exc.value.code == code, code
    with pytest.raises(CampaignError) as exc:
        c.update_campaign(cid, options="ai")
    assert exc.value.code == "invalid_value"
    with pytest.raises(CampaignError):
        c.update_campaign(cid, search_mode="magic")
    # Nothing changed by the failures.
    assert c.get_campaign(cid) == created


def test_nine_groups_on_update(offer_id):
    created = make(offer_id)
    nine = [{"label": f"G{i}", "keywords": ["k"], "search_terms": ["t"]} for i in range(9)]
    with pytest.raises(CampaignError) as exc:
        c.update_campaign(created["id"], designations=nine)
    assert exc.value.code == "invalid_designations"


def test_update_with_no_changes(offer_id):
    created = make(offer_id)
    assert c.update_campaign(created["id"]) == created


def test_not_found(ready_db):
    for call in (c.get_campaign, c.archive_campaign):
        with pytest.raises(CampaignError) as exc:
            call(42)
        assert exc.value.code == "not_found"
    with pytest.raises(CampaignError):
        c.update_campaign(42, name="x")
    with pytest.raises(CampaignError):
        c.get_campaign("1")  # type: ignore[arg-type]


def test_archive_keeps_the_data(offer_id):
    created = make(offer_id)
    got = c.archive_campaign(created["id"])
    assert got["status"] == "archived" and got["name"] == created["name"]
    assert c.update_campaign(created["id"], status="draft")["status"] == "draft"
