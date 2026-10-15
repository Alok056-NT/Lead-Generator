"""People finder service: queries, estimate, chunked search, storing, status changes, recheck."""

from __future__ import annotations

import types

import pytest

from leadgen import db
from leadgen.providers.base import ProviderError
from leadgen.providers.fake import FakeSearchProvider
from leadgen.providers.search import SearchResult
from leadgen.services import budget, people
from leadgen.services import campaign as camp
from leadgen.services.campaign import CampaignError
from leadgen.services.people import PeopleError


@pytest.fixture
def cid(ready_db):
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Northwind Training", source_type="text")
    return camp.create_campaign(
        name="SG logistics",
        offer_id=offer_id,
        countries=["SG"],
        sectors=["logistics"],
        company_size="200-1000",
        channels=["linkedin"],
        designations=["ld", "hr"],
    )["id"]


def add_company(cid, name, status="approved", aliases=(), domain="", country=""):
    with db.session() as conn:
        return db.insert(
            conn, "companies", campaign_id=cid, name=name, aliases_json=list(aliases),
            domain=domain, country=country, status=status,
        )  # fmt: skip


def hit(name, headline, company, link=None, snippet=None, city="Singapore"):
    slug = name.lower().replace(" ", "-")
    return SearchResult(
        title=f"{name} - {headline} at {company} | LinkedIn",
        link=link or f"https://www.linkedin.com/in/{slug}",
        snippet=snippet or f"{headline} at {company} · {city}",
        position=1,
    )


def queries(cid, company_name):
    camp_dict = camp.get_campaign(cid)
    return people.build_queries(camp_dict, {"name": company_name, "id": 1})


def responder(mapping):
    """Fake provider answering by company name found inside the query, for any group."""

    def respond(query, gl, num):
        out = []
        for company, results in mapping.items():
            if f'"{company}"' in query:
                out.extend(results)
        return out

    return FakeSearchProvider(respond)


# --- queries and estimate ---------------------------------------------------------------------


def test_build_queries_format(cid):
    result = queries(cid, "Northwind Health")
    assert [label for label, _ in result] == ["L&D / Training heads", "HR leaders"]
    assert result[0][1] == (
        'site:linkedin.com/in ("learning and development manager" OR "L&D manager" OR '
        '"head of learning" OR "training manager" OR "head of training" OR '
        '"talent development manager") "Northwind Health" Singapore'
    )


def test_build_queries_skips_groups_without_terms_and_cleans_terms(cid):
    camp_dict = {
        "countries": ["SG"],
        "designations": [
            {
                "label": "A",
                "keywords": ["a"],
                "search_terms": ['"HR" director', "  HR   director ", ""],
            },
            {"label": "B", "keywords": ["b"], "search_terms": []},
        ],
    }
    out = people.build_queries(camp_dict, {"name": "Acme"})
    assert out == [("A", 'site:linkedin.com/in ("HR director") "Acme" Singapore')]


def test_build_queries_country_rules():
    group = {"label": "A", "keywords": ["a"], "search_terms": ["x"]}
    multi = {"countries": ["SG", "MY"], "designations": [group]}
    out = people.build_queries(multi, {"name": "Acme"})
    assert [q.rsplit(" ", 1)[-1] for _, q in out] == ["Singapore", "Malaysia"]
    out = people.build_queries(multi, {"name": "Acme", "country": "Malaysia"})
    assert len(out) == 1 and out[0][1].endswith("Malaysia")
    out = people.build_queries(multi, {"name": "Acme", "country": "Narnia"})
    assert len(out) == 2  # unknown company country falls back to the campaign's
    out = people.build_queries({"countries": [], "designations": [group]}, {"name": "Acme"})
    assert out == [("A", 'site:linkedin.com/in ("x") "Acme"')]


def test_estimate_counts_cached_and_budget(cid):
    add_company(cid, "Northwind Health")
    add_company(cid, "Lakeside Bank")
    add_company(cid, "Candidate Co", status="candidate")
    est = people.estimate_people_search(cid)
    assert est["companies"] == 2 and est["searches"] == 4 and est["cached"] == 0
    assert est["credits_needed"] == 4 and est["budget_left"] == 300
    assert est["needs_confirmation"] is False
    provider = FakeSearchProvider({})
    people.find_people(cid, provider=provider, max_searches_per_call=2)


def test_estimate_unknown_company_filter_and_bad_ids(cid):
    add_company(cid, "Northwind Health")
    assert people.estimate_people_search(cid, [9999])["searches"] == 0
    with pytest.raises(PeopleError) as err:
        people.estimate_people_search(cid, "1")
    assert err.value.code == "invalid_value"
    with pytest.raises(PeopleError):
        people.estimate_people_search(cid, [True])
    with pytest.raises(CampaignError):
        people.estimate_people_search(9999)


def test_estimate_uses_the_cache_of_the_configured_provider(cid, monkeypatch):
    add_company(cid, "Northwind Health")
    provider = FakeSearchProvider({})
    monkeypatch.setattr(people.registry, "get_search_provider", lambda: provider)
    people.find_people(cid, provider=provider)
    est = people.estimate_people_search(cid)
    assert est["searches"] == 2 and est["cached"] == 2 and est["credits_needed"] == 0


# --- find_people ------------------------------------------------------------------------------


def test_find_people_stores_confirmed_and_review(cid):
    add_company(cid, "Northwind Health", aliases=["Northwind"])
    good = hit("Mira Lee", "Training Manager", "Northwind Health")
    bad = hit("Jonah Reyes", "Machine Learning Lead", "Northwind Health")
    page = SearchResult(
        "Northwind Health | LinkedIn", "https://www.linkedin.com/company/nw", "x", 3
    )
    provider = responder({"Northwind Health": [good, bad, page]})
    out = people.find_people(cid, provider=provider)
    assert out["ok"] and out["done"] and out["next_cursor"] is None
    assert out["searches_done"] == out["searches_total"] == 2
    assert out["this_call"] == {"confirmed": 1, "review": 1, "duplicates": 2, "ignored": 2}
    assert out["campaign_people"]["confirmed"] == 1 and out["campaign_people"]["review"] == 1
    assert out["credits_used"] == 2.0 and budget.used(cid, "search") == 2.0
    assert [s["name"] for s in out["sample_confirmed"]] == ["Mira Lee"]
    assert out["sample_review"][0]["reasons"][0].startswith("L06")

    rows = people.list_people(cid)["people"]
    mira = next(p for p in rows if p["raw_name"] == "Mira Lee")
    assert mira["status"] == "confirmed" and mira["confirmed"] is True
    assert mira["evidence_origin"] == "api" and mira["company"] == "Northwind Health"
    assert mira["designation"] == "L&D / Training heads" and mira["seniority"] == "manager"
    assert mira["greeting_name"] == "Mira" and mira["country"] == "Singapore"
    assert mira["linkedin_url"] == "https://www.linkedin.com/in/mira-lee"
    assert mira["evidence_title"].startswith("Mira Lee - Training Manager")
    assert mira["source_query"].startswith("site:linkedin.com/in")
    jonah = next(p for p in rows if p["raw_name"] == "Jonah Reyes")
    assert jonah["status"] == "review" and jonah["confirmed"] is False
    assert jonah["reasons"] and jonah["reasons"][0].startswith("L06")
    assert provider.calls[0][1] == "sg" and provider.calls[0][2] == 10


def test_same_person_found_by_two_queries_is_stored_once(cid):
    add_company(cid, "Northwind Health")
    person = hit("Mira Lee", "Training Manager", "Northwind Health")
    out = people.find_people(cid, provider=responder({"Northwind Health": [person]}))
    assert out["this_call"]["confirmed"] == 1 and out["this_call"]["duplicates"] == 1
    assert people.list_people(cid)["total"] == 1


def test_same_name_and_company_with_another_url_is_a_duplicate(cid):
    add_company(cid, "Northwind Health")
    a = hit("Mira Lee", "Training Manager", "Northwind Health")
    b = hit("Mira Lee", "Training Manager", "Northwind Health",
            link="https://sg.linkedin.com/in/mira-lee-12345")  # fmt: skip
    c = hit(
        "Mira Lee",
        "Training Manager",
        "Northwind Health",
        link="https://sg.linkedin.com/in/MIRA-LEE",
    )
    out = people.find_people(cid, provider=responder({"Northwind Health": [a, b, c]}))
    assert out["this_call"]["confirmed"] == 1
    assert people.list_people(cid)["total"] == 1


def test_same_name_at_two_companies_is_kept(cid):
    add_company(cid, "Northwind Health")
    add_company(cid, "Lakeside Bank")
    provider = responder({
        "Northwind Health": [hit("Mira Lee", "Training Manager", "Northwind Health")],
        "Lakeside Bank": [hit("Mira Lee", "Training Manager", "Lakeside Bank",
                              link="https://www.linkedin.com/in/mira-lee-2")],
    })  # fmt: skip
    people.find_people(cid, provider=provider)
    assert people.list_people(cid)["total"] == 2


def test_chunking_with_cursor_gives_same_result(cid):
    for name in ("Northwind Health", "Lakeside Bank", "Kestrel Holdings"):
        add_company(cid, name)
    mapping = {
        "Northwind Health": [hit("Mira Lee", "Training Manager", "Northwind Health")],
        "Lakeside Bank": [hit("Ivy Tan", "HR Manager", "Lakeside Bank")],
        "Kestrel Holdings": [hit("Ben Adler", "HR Director", "Kestrel Holdings")],
    }
    provider = responder(mapping)
    first = people.find_people(cid, provider=provider, max_searches_per_call=4)
    assert first["searches_done"] == 4 and first["done"] is False
    assert first["searches_total"] == 6 and first["next_cursor"] == "4"
    second = people.find_people(cid, provider=provider, cursor=first["next_cursor"],
                                max_searches_per_call=4)  # fmt: skip
    assert second["done"] is True and second["next_cursor"] is None
    assert second["searches_done"] == 2
    assert people.list_people(cid)["counts"]["confirmed"] == 3
    # Companies are searched in a stable order, group by group.
    companies = ["Northwind Health", "Lakeside Bank", "Kestrel Holdings"]
    expected = [name for name in companies for _ in range(2)]
    assert [
        name for q, _, _ in provider.calls for name in companies if f'"{name}"' in q
    ] == expected


def test_cursor_validation(cid):
    add_company(cid, "Northwind Health")
    for bad in ("x", "-1", -3):
        with pytest.raises(PeopleError) as err:
            people.find_people(cid, cursor=bad, provider=FakeSearchProvider({}))
        assert err.value.code == "invalid_cursor"
    out = people.find_people(cid, cursor=2, provider=FakeSearchProvider({}))
    assert out["done"] is True and out["searches_done"] == 0
    out = people.find_people(cid, cursor="", provider=FakeSearchProvider({}))
    assert out["searches_done"] == 2


def test_confirmation_threshold(cid, monkeypatch):
    monkeypatch.setattr(
        people, "load_settings", lambda: types.SimpleNamespace(confirm_threshold_credits=3)
    )
    for name in ("A One", "B Two", "C Three"):
        add_company(cid, name)
    provider = FakeSearchProvider({})
    out = people.find_people(cid, provider=provider)
    assert out["needs_confirmation"] is True and out["credits_needed"] == 6
    assert out["searches"] == 6 and out["companies"] == 3 and "confirm=true" in out["message"]
    assert provider.calls == []  # nothing was searched
    out = people.find_people(cid, confirm=True, provider=provider)
    assert out["done"] is True and len(provider.calls) == 6
    # Cached searches do not count towards the threshold.
    out = people.find_people(cid, provider=provider)
    assert out["done"] is True and out["cache_hits"] == 6


def test_threshold_is_only_checked_when_a_run_starts(cid, monkeypatch):
    monkeypatch.setattr(
        people, "load_settings", lambda: types.SimpleNamespace(confirm_threshold_credits=3)
    )
    for name in ("A One", "B Two", "C Three"):
        add_company(cid, name)
    provider = FakeSearchProvider({})
    out = people.find_people(cid, confirm=True, max_searches_per_call=2, provider=provider)
    assert out["next_cursor"] == "2"
    out = people.find_people(cid, cursor="2", max_searches_per_call=2, provider=provider)
    assert out["searches_done"] == 2 and "needs_confirmation" not in out


def test_budget_refusal_and_confirm(cid):
    camp.update_campaign(cid, budget={"search": 1})
    add_company(cid, "Northwind Health")
    provider = responder(
        {"Northwind Health": [hit("Mira Lee", "Training Manager", "Northwind Health")]}
    )
    out = people.find_people(cid, provider=provider)
    assert out["ok"] is False and out["budget_reached"]["kind"] == "search"
    assert out["budget_reached"]["used"] == 1 and out["budget_reached"]["limit"] == 1
    assert out["fallback_available"] == "ai_web_search"
    assert out["searches_done"] == 1 and out["done"] is False and out["next_cursor"] == "1"
    assert len(provider.calls) == 1 and budget.used(cid, "search") == 1
    out = people.find_people(cid, cursor="1", confirm=True, provider=provider)
    assert out["ok"] is True and out["done"] is True and budget.used(cid, "search") == 2


def test_cached_searches_are_free_even_over_budget(cid):
    add_company(cid, "Northwind Health")
    provider = responder(
        {"Northwind Health": [hit("Mira Lee", "Training Manager", "Northwind Health")]}
    )
    people.find_people(cid, provider=provider)
    camp.update_campaign(cid, budget={"search": 0})
    calls = len(provider.calls)
    out = people.find_people(cid, provider=provider)
    assert out["ok"] and out["cache_hits"] == 2 and out["credits_used"] == 0
    assert len(provider.calls) == calls and out["this_call"]["duplicates"] == 2


def test_provider_errors_become_structured_replies(cid):
    add_company(cid, "Northwind Health")
    provider = FakeSearchProvider({})
    provider.fail_after(1, ProviderError("quota", "no credits"))
    out = people.find_people(cid, provider=provider)
    assert out["ok"] is False and "quota_exhausted" in out
    assert out["fallback_available"] == "ai_web_search"
    assert out["searches_done"] == 1 and out["next_cursor"] == "1" and out["done"] is False
    assert budget.used(cid, "search") == 1  # the failed search was not charged
    provider = FakeSearchProvider({}, fail_with=ProviderError("not_configured", "no key"))
    assert "needs_key" in people.find_people(cid, provider=provider)
    provider = FakeSearchProvider({}, fail_with=ProviderError("rate_limited", "slow down"))
    out = people.find_people(cid, provider=provider)
    assert out["ok"] is False and out["error"]["code"] == "rate_limited"


def test_missing_key_returns_needs_key(cid):
    add_company(cid, "Northwind Health")
    with pytest.raises(PeopleError) as err:
        people.find_people(cid)
    reply = err.value.as_response()
    assert reply["ok"] is False and reply["needs_key"]["provider"] == "serper"
    assert reply["fallback_available"] == "ai_web_search"


def test_ai_search_mode_does_not_search(cid):
    camp.update_campaign(cid, search_mode="ai")
    with pytest.raises(PeopleError) as err:
        people.find_people(cid, provider=FakeSearchProvider({}))
    assert err.value.code == "ai_search_mode"
    assert err.value.as_response()["error"]["hint"]


def test_company_filter_and_only_approved_companies(cid):
    a = add_company(cid, "Northwind Health")
    add_company(cid, "Lakeside Bank")
    c = add_company(cid, "Candidate Co", status="candidate")
    provider = FakeSearchProvider({})
    out = people.find_people(cid, [a, c, 9999], provider=provider)
    assert out["searches_total"] == 2 and out["skipped_company_ids"] == [c, 9999]
    assert all("Northwind Health" in q for q, _, _ in provider.calls)
    out = people.find_people(cid, provider=FakeSearchProvider({}))
    assert out["searches_total"] == 4 and "skipped_company_ids" not in out


def test_no_companies_is_not_an_error(cid):
    out = people.find_people(cid, provider=FakeSearchProvider({}))
    assert out["ok"] and out["done"] and out["searches_total"] == 0


def test_time_limit_stops_a_chunk(cid, monkeypatch):
    for name in ("A One", "B Two"):
        add_company(cid, name)
    monkeypatch.setattr(people, "CALL_TIME_LIMIT_S", -1.0)
    out = people.find_people(cid, provider=FakeSearchProvider({}))
    assert out["searches_done"] == 1 and out["done"] is False and out["next_cursor"] == "1"


def test_company_country_decides_the_query_and_check(cid):
    add_company(cid, "Northwind Health", country="MY")
    provider = responder({"Northwind Health": [
        hit("Mira Lee", "Training Manager", "Northwind Health", city="Kuala Lumpur, Malaysia"),
    ]})  # fmt: skip
    out = people.find_people(cid, provider=provider)
    assert out["this_call"]["confirmed"] == 1
    assert all(gl == "my" and q.endswith("Malaysia") for q, gl, _ in provider.calls)


def test_company_aliases_are_used_for_matching(cid):
    add_company(cid, "Northwind Health Pte Ltd", aliases=["Northwind"])
    good = hit("Mira Lee", "Training Manager", "Northwind")
    out = people.find_people(cid, provider=responder({"Northwind Health Pte Ltd": [good]}))
    assert out["this_call"]["confirmed"] == 1


def test_suppression_list_and_exclusions_apply(cid):
    add_company(cid, "Northwind Health")
    with db.session() as conn:
        db.insert(conn, "suppression", value="https://sg.linkedin.com/in/mira-lee",
                  kind="linkedin", reason="asked")  # fmt: skip
        db.insert(conn, "suppression", value="x@example.com", kind="email", reason="asked")
    results = [hit("Mira Lee", "Training Manager", "Northwind Health")]
    people.find_people(cid, provider=responder({"Northwind Health": results}))
    row = people.list_people(cid)["people"][0]
    assert row["status"] == "review" and row["reasons"][0].startswith("L16")


def test_process_results_accepts_dicts_and_other_origins(cid):
    company_id = add_company(cid, "Northwind Health")
    camp_dict = camp.get_campaign(cid)
    company = {"id": company_id, "name": "Northwind Health", "aliases": []}
    items = [
        {"title": "Mira Lee - Training Manager at Northwind Health | LinkedIn",
         "url": "https://www.linkedin.com/in/mira-lee",
         "snippet": "Training Manager at Northwind Health · Singapore"},
        {"title": "Not a profile", "link": "https://example.com/x", "snippet": ""},
    ]  # fmt: skip
    out = people.process_results(
        camp_dict, company, "L&D / Training heads", "q", items, origin="ai_web_search"
    )
    assert (out["confirmed"], out["ignored"]) == (1, 1)
    row = people.list_people(cid)["people"][0]
    assert row["evidence_origin"] == "ai_web_search" and row["source_query"] == "q"
    out = people.process_results(camp_dict, company, "x", "q", items, origin="ai_web_search")
    assert out["duplicates"] == 1 and out["confirmed"] == 0


# --- list and status --------------------------------------------------------------------------


@pytest.fixture
def found(cid):
    a = add_company(cid, "Northwind Health")
    b = add_company(cid, "Lakeside Bank")
    provider = responder({
        "Northwind Health": [
            hit("Mira Lee", "Training Manager", "Northwind Health"),
            hit("Jonah Reyes", "HR Manager", "Northwind Health"),
            hit("Cara Lim", "Talent Acquisition Partner", "Northwind Health"),
        ],
        "Lakeside Bank": [hit("Ivy Tan", "HR Director", "Lakeside Bank")],
    })  # fmt: skip
    people.find_people(cid, provider=provider)
    return cid, a, b


def ids_by_name(cid):
    return {p["raw_name"]: p["id"] for p in people.list_people(cid)["people"]}


def test_list_people_filters_and_paging(found):
    cid, a, b = found
    everyone = people.list_people(cid)
    assert everyone["total"] == 4 and everyone["counts"] == {
        "confirmed": 3, "review": 1, "rejected": 0, "removed": 0,
    }  # fmt: skip
    assert people.list_people(cid, status="review")["total"] == 1
    assert people.list_people(cid, company_id=b)["total"] == 1
    assert people.list_people(cid, designation="HR leaders")["total"] == 3
    page = people.list_people(cid, page=2, page_size=3)
    assert len(page["people"]) == 1 and page["has_more"] is False and page["page"] == 2
    assert people.list_people(cid, page_size=500)["page_size"] == 50
    assert people.list_people(cid, page=0)["page"] == 1
    with pytest.raises(PeopleError):
        people.list_people(cid, status="maybe")
    with pytest.raises(CampaignError):
        people.list_people(9999)


def test_list_people_has_email(found):
    cid, _, _ = found
    ids = ids_by_name(cid)
    with db.session() as conn:
        db.insert(conn, "emails", person_id=ids["Mira Lee"], email="m@example.com",
                  method="pattern", status="verified")  # fmt: skip
    assert [p["raw_name"] for p in people.list_people(cid, has_email=True)["people"]] == [
        "Mira Lee"
    ]
    assert people.list_people(cid, has_email=False)["total"] == 3


def test_promoting_needs_a_reason_and_is_recorded(found):
    cid, _, _ = found
    ids = ids_by_name(cid)
    cara = ids["Cara Lim"]
    with pytest.raises(PeopleError) as err:
        people.set_person_status(cid, [cara], "confirmed")
    assert err.value.code == "reason_required"
    out = people.set_person_status(cid, [cara, 9999], "confirmed", "checked the profile")
    assert out["updated"] == 1 and out["not_found"] == [9999] and out["refused"] == []
    row = next(p for p in people.list_people(cid)["people"] if p["id"] == cara)
    assert row["status"] == "confirmed" and row["confirmed"] is True
    assert row["reasons"][-1] == "MANUAL:confirmed: checked the profile"
    assert row["reasons"][0].startswith("L07")  # the original reasons are kept


def test_reject_remove_and_review_do_not_need_a_reason(found):
    cid, _, _ = found
    ids = ids_by_name(cid)
    people.set_person_status(cid, [ids["Mira Lee"]], "rejected")
    people.set_person_status(cid, [ids["Ivy Tan"]], "removed", "left the company")
    people.set_person_status(cid, [ids["Jonah Reyes"]], "review")
    counts = people.list_people(cid)["counts"]
    assert counts == {"confirmed": 0, "review": 2, "rejected": 1, "removed": 1}
    row = next(p for p in people.list_people(cid)["people"] if p["raw_name"] == "Mira Lee")
    assert row["confirmed"] is False and row["reasons"] == []
    with pytest.raises(PeopleError):
        people.set_person_status(cid, [1], "maybe")
    with pytest.raises(PeopleError):
        people.set_person_status(cid, "1", "review")


def test_cannot_promote_suppressed_or_excluded_people(found):
    cid, a, _ = found
    camp.update_campaign(cid, exclusions={"competitors": ["Northwind Health"]})
    people.recheck_people(cid)
    ids = ids_by_name(cid)
    out = people.set_person_status(cid, [ids["Mira Lee"]], "confirmed", "I know her")
    assert out["updated"] == 0 and out["refused"][0]["why"].startswith("L03")
    out = people.set_person_status(cid, [ids["Mira Lee"]], "rejected")
    assert out["updated"] == 1


def test_people_of_another_campaign_are_not_touched(found):
    cid, _, _ = found
    with db.session() as conn:
        offer_id = db.insert(conn, "offers", name="Other", source_type="text")
    other = camp.create_campaign(name="Other", offer_id=offer_id, countries=["SG"], sectors=[],
                                 company_size="any", channels=["linkedin"],
                                 designations=["ld"])["id"]  # fmt: skip
    ids = ids_by_name(cid)
    out = people.set_person_status(other, [ids["Mira Lee"]], "rejected")
    assert out["updated"] == 0 and out["not_found"] == [ids["Mira Lee"]]


# --- recheck ----------------------------------------------------------------------------------


def test_recheck_moves_people_and_returns_the_diff(found):
    cid, _, _ = found
    first = people.recheck_people(cid)
    assert first["checked"] == 4 and first["unchanged"] == 4
    assert first["to_confirmed"] == [] and first["to_review"] == [] and first["refreshed"] == 0

    camp.update_campaign(cid, seniority_min="director")
    out = people.recheck_people(cid)
    moved = {p["name"] for p in out["to_review"]}
    assert moved == {"Mira Lee", "Jonah Reyes"}  # managers; the HR director stays
    assert out["to_confirmed"] == []
    assert out["to_review"][0]["reasons"][0].startswith("L07")
    counts = people.list_people(cid)["counts"]
    assert counts["confirmed"] == 1 and counts["review"] == 3

    camp.update_campaign(cid, seniority_min="manager")
    out = people.recheck_people(cid)
    assert {p["name"] for p in out["to_confirmed"]} == {"Mira Lee", "Jonah Reyes"}
    assert people.list_people(cid)["counts"]["confirmed"] == 3


def test_recheck_after_designation_and_country_changes(found):
    cid, _, _ = found
    camp.update_campaign(cid, designations=["hr"])
    out = people.recheck_people(cid)
    assert {p["name"] for p in out["to_review"]} == {"Mira Lee"}  # training is not HR
    camp.update_campaign(cid, countries=["MY"])
    out = people.recheck_people(cid)
    assert {p["name"] for p in out["to_review"]} == {"Jonah Reyes", "Ivy Tan"}
    reasons = people.list_people(cid, status="review")["people"][0]["reasons"]
    assert any(r.startswith("L05") for r in reasons)


def test_recheck_refreshes_reasons_without_moving(found):
    cid, _, _ = found
    camp.update_campaign(cid, designations=["hr"])
    people.recheck_people(cid)  # Mira -> review with L06
    camp.update_campaign(cid, countries=["MY"])
    out = people.recheck_people(cid)
    assert out["refreshed"] == 2  # Mira and Cara stay in review, now with L05 as well


def test_recheck_applies_new_suppression_and_exclusions(found):
    cid, a, _ = found
    with db.session() as conn:
        db.insert(conn, "suppression", value="https://www.linkedin.com/in/ivy-tan",
                  kind="linkedin", reason="asked")  # fmt: skip
    out = people.recheck_people(cid)
    assert [p["name"] for p in out["to_review"]] == ["Ivy Tan"]
    with db.session() as conn:
        db.update(conn, "companies", a, status="excluded")
    out = people.recheck_people(cid)
    assert {p["name"] for p in out["to_review"]} == {"Mira Lee", "Jonah Reyes"}
    assert all(p["reasons"][-1].startswith("L03") for p in out["to_review"])


def test_recheck_leaves_manual_rejected_and_removed_alone(found):
    cid, _, _ = found
    ids = ids_by_name(cid)
    people.set_person_status(cid, [ids["Cara Lim"]], "confirmed", "checked")
    people.set_person_status(cid, [ids["Ivy Tan"]], "rejected")
    people.set_person_status(cid, [ids["Mira Lee"]], "removed")
    camp.update_campaign(cid, seniority_min="c_suite")
    out = people.recheck_people(cid)
    assert out["kept_manual"] == 1 and out["checked"] == 1
    assert {p["name"] for p in out["to_review"]} == {"Jonah Reyes"}
    counts = people.list_people(cid)["counts"]
    assert counts == {"confirmed": 1, "review": 1, "rejected": 1, "removed": 1}


def test_recheck_handles_a_stored_link_that_is_not_a_profile(found):
    cid, _, _ = found
    ids = ids_by_name(cid)
    with db.session() as conn:
        db.update(conn, "people", ids["Mira Lee"], linkedin_url="")
    out = people.recheck_people(cid)
    assert [p["name"] for p in out["to_review"]] == ["Mira Lee"]
    assert out["to_review"][0]["reasons"][0].startswith("L01")
    assert people.recheck_people(cid)["unchanged"] == 4  # second run: nothing new


def test_recheck_unknown_campaign(ready_db):
    with pytest.raises(CampaignError):
        people.recheck_people(12345)


# --- hooks used by AI search mode (T21) ---------------------------------------------------------


def test_ai_search_mode_reply_points_to_the_ai_tools(cid):
    camp.update_campaign(cid, search_mode="ai")
    with pytest.raises(PeopleError) as err:
        people.find_people(cid, provider=FakeSearchProvider({}))
    reply = err.value.as_response()
    assert reply["ok"] is False and reply["use_ai_search"] is True
    assert reply["hint"] == "call get_search_tasks" and reply["error"]["code"] == "ai_search_mode"


def test_process_results_screen_and_outcomes(cid):
    company_id = add_company(cid, "Northwind Health")
    camp_dict = camp.get_campaign(cid)
    company = {"id": company_id, "name": "Northwind Health", "aliases": []}
    items = [
        hit("Mira Lee", "Training Manager", "Northwind Health"),
        hit("Jonah Reyes", "HR Manager", "Northwind Health"),
        SearchResult(title="Careers", link="https://example.com/jobs", snippet="", position=3),
    ]
    seen = []

    def screen(cand, result):
        seen.append((cand.raw_name, result.link.rsplit("/", 1)[-1]))
        return ["L21:test reason"] if cand.raw_name == "Jonah Reyes" else []

    outcomes = []
    out = people.process_results(
        camp_dict, company, "g", "q", items, origin="ai_web_search", screen=screen,
        outcomes=outcomes,
    )  # fmt: skip
    assert (out["confirmed"], out["review"], out["ignored"]) == (1, 1, 1)
    # the hook is not called for the non-profile link
    assert seen == [("Mira Lee", "mira-lee"), ("Jonah Reyes", "jonah-reyes")]
    assert [(o["index"], o["outcome"]) for o in outcomes] == [
        (0, "confirmed"), (1, "review"), (2, "ignored"),
    ]  # fmt: skip
    assert outcomes[1]["reasons"] == ["L21:test reason"]
    again = []
    people.process_results(camp_dict, company, "g", "q", items[:1], outcomes=again)
    assert again[0]["outcome"] == "duplicate"


def test_recheck_keeps_the_ai_search_reasons(cid):
    company_id = add_company(cid, "Northwind Health")
    camp_dict = camp.get_campaign(cid)
    company = {"id": company_id, "name": "Northwind Health", "aliases": []}
    people.process_results(
        camp_dict, company, "g", "q", [hit("Mira Lee", "Training Manager", "Northwind Health")],
        origin="ai_web_search", screen=lambda cand, result: ["L22:link does not fit the name"],
    )  # fmt: skip
    api = hit("Jonah Reyes", "HR Manager", "Northwind Health")
    people.process_results(camp_dict, company, "g", "q", [api])
    out = people.recheck_people(cid)
    assert out["to_confirmed"] == [] and out["to_review"] == []
    rows = {p["raw_name"]: p for p in people.list_people(cid)["people"]}
    assert rows["Mira Lee"]["status"] == "review"
    assert rows["Mira Lee"]["reasons"] == ["L22:link does not fit the name"]
    assert rows["Jonah Reyes"]["status"] == "confirmed"
