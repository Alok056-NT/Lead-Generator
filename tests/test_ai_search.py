"""AI search mode (T21): task queue, verbatim submission, anti-fabrication checks, email examples.

All people, companies and addresses are invented; nothing here touches the network.
"""

from __future__ import annotations

import pytest

from leadgen import db
from leadgen.providers.search import SearchResult, cached_search
from leadgen.services import ai_search, companies, emails, people, qa_leads
from leadgen.services import campaign as camp
from leadgen.services.ai_search import AISearchError

CO = "Northwind Health"


@pytest.fixture
def offer_id(ready_db):
    with db.session() as conn:
        return db.insert(conn, "offers", name="Northwind Training", source_type="text")


def make_campaign(offer_id, name="AI run", channels=("linkedin",), **extra):
    return camp.create_campaign(
        name=name, offer_id=offer_id, countries=["SG"], sectors=["logistics"],
        company_size="200-1000", channels=list(channels), designations=["ld", "hr"],
        options={"search_mode": "ai"}, **extra,
    )["id"]  # fmt: skip


@pytest.fixture
def cid(offer_id):
    return make_campaign(offer_id)


def add_company(cid, name=CO, domain="", status="approved"):
    with db.session() as conn:
        return db.insert(
            conn, "companies", campaign_id=cid, name=name, domain=domain, status=status
        )


def result(name, headline, company=CO, url=None, snippet=None):
    slug = name.lower().replace(" ", "-")
    return {
        "title": f"{name} - {headline} at {company} | LinkedIn",
        "url": url or f"https://www.linkedin.com/in/{slug}",
        "snippet": snippet or f"{headline} at {company} · Singapore · Experience: {company}",
    }


def first_task(cid, kind="people"):
    return ai_search.get_search_tasks(cid, kind)["tasks"][0]["task_id"]


def person_rows(cid):
    with db.session() as conn:
        return db.query(conn, "SELECT * FROM people WHERE campaign_id = ? ORDER BY id", (cid,))


# --- creating tasks ---------------------------------------------------------------------------


def test_people_tasks_use_the_api_queries_and_are_not_duplicated(cid):
    add_company(cid, CO)
    add_company(cid, "Lakeside Bank")
    add_company(cid, "Candidate Co", status="candidate")
    assert ai_search.ensure_tasks(cid) == 4
    assert ai_search.ensure_tasks(cid) == 0
    with db.session() as conn:
        rows = db.query(
            conn, "SELECT * FROM search_tasks WHERE campaign_id = ? ORDER BY id", (cid,)
        )
    camp_dict = camp.get_campaign(cid)
    expected = people.build_queries(camp_dict, {"name": CO}) + people.build_queries(
        camp_dict, {"name": "Lakeside Bank"}
    )
    assert [(r["group_label"], r["query"]) for r in rows] == expected
    assert {r["kind"] for r in rows} == {"people"} and {r["status"] for r in rows} == {"open"}


class _CachedApi:
    """A search provider that answers from a dict (stands in for an earlier API campaign)."""

    info = type("Info", (), {"key": "serper"})()

    def __init__(self, answers):
        self.answers = answers

    def search(self, query, country_gl="", num=10):
        return self.answers.get(query, [])


def api_hit(name, headline, company=CO):
    item = result(name, headline, company)
    return SearchResult(title=item["title"], link=item["url"], snippet=item["snippet"], position=1)


def test_pairs_that_already_have_people_are_skipped(cid):
    a = add_company(cid, CO)
    add_company(cid, "Lakeside Bank")
    with db.session() as conn:  # people of company A already found for the HR group
        db.insert(
            conn, "people", campaign_id=cid, company_id=a, raw_name="X Y",
            designation="HR leaders", linkedin_url="https://www.linkedin.com/in/x-y",
        )  # fmt: skip
    assert ai_search.ensure_tasks(cid) == 3  # A/ld, Lakeside/ld and Lakeside/hr
    groups = {
        t["designation_group"]
        for t in ai_search.get_search_tasks(cid)["tasks"] if t["company"] == CO
    }
    assert "HR leaders" not in groups and len(groups) == 1


def test_another_campaigns_cached_search_fills_this_campaign_instead_of_hiding_it(
    offer_id, cid
):
    add_company(cid, CO)
    plan = people.build_queries(camp.get_campaign(cid), {"name": CO})
    assert len(plan) == 2
    (ld_label, ld_query), (hr_label, hr_query) = plan
    # an earlier API campaign searched the first pair; the cache is shared by all campaigns
    api = _CachedApi({ld_query: [api_hit("Aria Tan", "Head of Learning and Development")]})
    cached_search(api, ld_query, "sg", people.SEARCH_NUM)
    cached_search(api, hr_query, "sg", people.SEARCH_NUM)  # cached with no results at all
    first = ai_search.get_search_tasks(cid)
    assert first["filled_from_cache"] == 2 and first["tasks_created"] == 0
    assert first["tasks"] == [] and "cache" in first["cache_note"]
    rows = person_rows(cid)
    assert [(r["raw_name"], r["evidence_origin"], r["status"]) for r in rows] == [
        ("Aria Tan", "api", "confirmed")
    ]
    with db.session() as conn:
        done = db.query(conn, "SELECT status, result_count FROM search_tasks WHERE campaign_id=?",
                        (cid,))  # fmt: skip
    assert sorted((r["status"], r["result_count"]) for r in done) == [
        ("submitted", 0), ("submitted", 1)
    ]
    again = ai_search.get_search_tasks(cid)  # nothing is loaded twice
    assert again["filled_from_cache"] == 0 and len(person_rows(cid)) == 1
    # a pair that is not cached is still queued
    add_company(cid, "Lakeside Bank")
    queued = ai_search.get_search_tasks(cid)
    assert queued["tasks_created"] == 2 and queued["filled_from_cache"] == 0
    assert {t["company"] for t in queued["tasks"]} == {"Lakeside Bank"}
    assert ld_label != hr_label


def test_cache_fill_is_chunked_and_pending_pairs_are_not_queued(offer_id, cid, monkeypatch):
    monkeypatch.setattr(people, "MAX_SEARCHES_PER_CALL", 2)
    add_company(cid, CO)
    add_company(cid, "Lakeside Bank")
    camp_row = camp.get_campaign(cid)
    plan = {
        name: people.build_queries(camp_row, {"name": name}) for name in (CO, "Lakeside Bank")
    }
    api = _CachedApi({})
    cached = [plan[CO][0][1], plan[CO][1][1], plan["Lakeside Bank"][0][1]]
    for query in cached:
        cached_search(api, query, "sg", people.SEARCH_NUM)
    uncached = plan["Lakeside Bank"][1][1]
    loaded = []
    real = people.process_results

    def spy(camp_, company, label, query, results, origin="api", **kw):
        loaded.append(query)
        return real(camp_, company, label, query, results, origin=origin, **kw)

    monkeypatch.setattr(people, "process_results", spy)

    first = ai_search.get_search_tasks(cid)
    assert first["filled_from_cache"] == 2 and first["cache_pending"] == 1
    # only the uncached pair is queued; the cached pair that is still waiting is not
    assert first["tasks_created"] == 1 and [t["query"] for t in first["tasks"]] == [uncached]
    assert "again" in first["message"] and "cache_pending" in first["message"]
    assert loaded == cached[:2]
    with db.session() as conn:
        queries = {r["query"] for r in db.query(
            conn, "SELECT query FROM search_tasks WHERE campaign_id = ?", (cid,))}  # fmt: skip
    assert queries == set(cached[:2]) | {uncached}

    second = ai_search.get_search_tasks(cid)
    assert second["filled_from_cache"] == 1 and second["cache_pending"] == 0
    assert second["tasks_created"] == 0 and [t["query"] for t in second["tasks"]] == [uncached]
    assert loaded == cached  # each cached pair was loaded exactly once

    third = ai_search.get_search_tasks(cid)
    assert third["filled_from_cache"] == 0 and third["cache_pending"] == 0
    assert third["tasks_created"] == 0 and loaded == cached


def _three_group_campaign(offer_id):
    return camp.create_campaign(
        name="three groups", offer_id=offer_id, countries=["SG"], sectors=["logistics"],
        company_size="200-1000", channels=["linkedin"], designations=["ld", "hr", "sales"],
        options={"search_mode": "ai"},
    )["id"]  # fmt: skip


def _cache_everything(cid, names, answers=None):
    """Cache every pair of the given companies (as an earlier API campaign would); returns
    {company: [(label, query), ...]}."""
    camp_row = camp.get_campaign(cid)
    plan = {n: people.build_queries(camp_row, {"name": n}) for n in names}
    api = _CachedApi(answers or {})
    for pairs in plan.values():
        for _label, query in pairs:
            cached_search(api, query, "sg", people.SEARCH_NUM)
    return plan


def _loaded_queries(cid):
    with db.session() as conn:
        rows = db.query(
            conn, "SELECT query FROM search_tasks WHERE campaign_id = ? AND status = 'submitted'",
            (cid,),
        )
    return {r["query"] for r in rows}


@pytest.mark.parametrize("stop", ["pair_cap", "time_limit"])
def test_a_chunk_that_stops_inside_a_company_keeps_its_later_cached_groups(
    offer_id, monkeypatch, stop
):
    cid = _three_group_campaign(offer_id)
    add_company(cid, CO)
    # the L&D search also returns an HR manager: a person's group comes from the job title
    ld_query = people.build_queries(camp.get_campaign(cid), {"name": CO})[0][1]
    plan = _cache_everything(
        cid, [CO], {ld_query: [api_hit("Aria Tan", "Head of Learning and Development"),
                               api_hit("Jonah Reyes", "HR Manager")]},
    )  # fmt: skip
    queries = [q for _label, q in plan[CO]]
    assert len(queries) == 3
    if stop == "pair_cap":
        monkeypatch.setattr(people, "MAX_SEARCHES_PER_CALL", 1)
    else:
        monkeypatch.setattr(people, "CALL_TIME_LIMIT_S", -1.0)  # out of time after one pair
    got = ai_search.get_search_tasks(cid)
    assert got["filled_from_cache"] == 1 and got["cache_pending"] == 2 and got["tasks"] == []
    assert {p["designation"] for p in person_rows(cid)} >= {"HR leaders"}  # the cross-over exists
    for _ in range(5):
        if not ai_search.get_search_tasks(cid)["cache_pending"]:
            break
    assert _loaded_queries(cid) == set(queries)  # no cached group of the company was lost
    last = ai_search.get_search_tasks(cid)
    assert last["filled_from_cache"] == 0 and last["cache_pending"] == 0 and last["tasks"] == []


def test_paging_with_a_cursor_still_loads_cached_pairs_and_reports_them_truthfully(
    offer_id, monkeypatch
):
    monkeypatch.setattr(people, "MAX_SEARCHES_PER_CALL", 1)
    cid = _three_group_campaign(offer_id)
    add_company(cid, CO)
    add_company(cid, "Lakeside Bank")
    plan = _cache_everything(cid, [CO])  # Lakeside Bank is not cached: three open tasks
    first = ai_search.get_search_tasks(cid, batch_size=1)
    assert first["filled_from_cache"] == 1 and first["cache_pending"] == 2
    assert first["next_cursor"] and len(first["tasks"]) == 1
    second = ai_search.get_search_tasks(cid, batch_size=1, cursor=first["next_cursor"])
    assert second["filled_from_cache"] == 1 and second["cache_pending"] == 1
    assert "cache_pending" in second["message"] and second["next_cursor"]
    # an AI that only follows the tool text (next_cursor while there is one, otherwise call
    # again until cache_pending is 0) ends with every cached pair loaded
    cursor, reply = second["next_cursor"], second
    for _ in range(10):
        reply = ai_search.get_search_tasks(cid, batch_size=1, cursor=cursor)
        cursor = reply["next_cursor"]
        if cursor is None and not reply["cache_pending"]:
            break
    assert reply["cache_pending"] == 0
    assert _loaded_queries(cid) == {q for _label, q in plan[CO]}


def test_email_tasks_need_confirmed_people_a_domain_and_no_known_pattern(offer_id):
    cid = make_campaign(offer_id, "mail", channels=("email",))
    a = add_company(cid, CO, "northwind.example")
    b = add_company(cid, "Lakeside Bank", "lakeside.example")
    add_company(cid, "No Domain Ltd")
    for company in (a, b):
        with db.session() as conn:
            db.insert(conn, "people", campaign_id=cid, company_id=company, raw_name=f"A{company}",
                      first_name="Aria", last_name="Tan", status="confirmed", confirmed=1,
                      linkedin_url=f"https://www.linkedin.com/in/aria-tan-{company}ab")  # fmt: skip
    emails.learn_pattern_from_examples("lakeside.example", ["bob.lee@lakeside.example"])
    assert ai_search.ensure_tasks(cid, "email") == 2
    tasks = ai_search.get_search_tasks(cid, "email")["tasks"]
    assert [t["query"] for t in tasks] == [
        '"@northwind.example"',
        "site:northwind.example (contact OR email)",
    ]
    assert tasks[0]["domain"] == "northwind.example" and tasks[0]["company"] == CO


def test_company_tasks_are_the_research_queries(cid):
    count = ai_search.ensure_tasks(cid, "company")
    assert count == len(companies.research_queries(cid)) > 0
    got = ai_search.get_search_tasks(cid, "company", batch_size=2)
    assert [t["query"] for t in got["tasks"]] == companies.research_queries(cid)[:2]
    assert ai_search.ensure_tasks(cid, "company") == 0


def test_bad_kind_and_unknown_campaign(cid):
    with pytest.raises(AISearchError):
        ai_search.ensure_tasks(cid, "phone")
    with pytest.raises(camp.CampaignError):
        ai_search.ensure_tasks(9999)


# --- handing tasks out ------------------------------------------------------------------------


def test_get_search_tasks_batches_with_cursor_and_instructions(cid):
    for i in range(6):
        add_company(cid, f"Company {i}")
    first = ai_search.get_search_tasks(cid, batch_size=5)
    assert first["ok"] and len(first["tasks"]) == 5 and first["remaining"] == 7
    assert first["open_total"] == 12 and first["tasks_created"] == 12
    text = " ".join(first["instructions"])
    for needle in ("VERBATIM", "empty list", "LinkedIn", "build, guess"):
        assert needle in text
    assert set(first["result_fields"]) == {"title", "url", "snippet"}
    second = ai_search.get_search_tasks(cid, batch_size=5, cursor=first["next_cursor"])
    assert len(second["tasks"]) == 5 and second["remaining"] == 2
    third = ai_search.get_search_tasks(cid, batch_size=5, cursor=second["next_cursor"])
    assert len(third["tasks"]) == 2 and third["next_cursor"] is None and third["remaining"] == 0
    assert all(t["designation_group"] and t["company"] for t in first["tasks"])
    assert ai_search.get_search_tasks(cid, batch_size=500)["batch_size"] == ai_search.BATCH_MAX
    with pytest.raises(AISearchError):
        ai_search.get_search_tasks(cid, cursor="-4")


def test_nothing_to_do_says_so(cid):
    got = ai_search.get_search_tasks(cid)
    assert got["tasks"] == [] and got["message"] and got["remaining"] == 0


# --- submitting: the same rules as the API path ------------------------------------------------

GOOD = [
    result("Mira Lee", "Training Manager"),
    result("Jonah Reyes", "HR Manager"),
]


def test_valid_results_confirm_exactly_like_the_api_path(offer_id):
    ai = make_campaign(offer_id, "ai side")
    api = make_campaign(offer_id, "api side")
    ai_co, api_co = add_company(ai), add_company(api)
    items = [
        *GOOD,
        result("Dana Wu", "Senior Training Officer"),  # not a manager: review
        {"title": "Northwind Health - Careers", "url": "https://example.com/jobs", "snippet": ""},
    ]
    task = first_task(ai)
    out = ai_search.submit_search_results(task, items, "web_search")
    assert out["ok"] and out["status"] == "submitted" and out["result_count"] == 4
    company = people.company_view({**_company(api_co), "id": api_co})
    people.process_results(
        camp.get_campaign(api), company, "L&D / Training heads", "q",
        [{"title": i["title"], "link": i["url"], "snippet": i["snippet"]} for i in items],
        origin="api",
    )  # fmt: skip

    def view(cid):
        return [(p["raw_name"], p["status"], p["reasons_json"]) for p in person_rows(cid)]

    assert view(ai) == view(api) and len(view(ai)) == 3
    assert {p["evidence_origin"] for p in person_rows(ai)} == {"ai_web_search"}
    assert {p["evidence_origin"] for p in person_rows(api)} == {"api"}
    assert ai_co != api_co
    by_url = {r["url"]: r for r in out["results"]}
    assert by_url["https://www.linkedin.com/in/mira-lee"]["outcome"] == "confirmed"
    assert by_url["https://example.com/jobs"]["outcome"] == "rejected"
    assert out["summary"]["confirmed"] == 2 and out["summary"]["review"] == 1
    assert out["summary"]["rejected"] == 1
    with db.session() as conn:
        row = db.get(conn, "search_tasks", task)
    assert row["status"] == "submitted" and row["result_count"] == 4 and row["submitted_at"]
    assert ai_search.task_note(task)["tool_name"] == "web_search"


def _company(company_id):
    with db.session() as conn:
        return db.get(conn, "companies", company_id)


def test_empty_list_closes_the_task_as_nothing_found(cid):
    add_company(cid)
    task = first_task(cid)
    out = ai_search.submit_search_results(task, [], "web_search")
    assert out["ok"] and out["result_count"] == 0 and out["results"] == []
    assert person_rows(cid) == []
    with pytest.raises(AISearchError) as err:
        ai_search.submit_search_results(task, [], "web_search")
    assert err.value.code == "task_not_open"


def test_tool_name_task_id_and_shape_are_validated(cid):
    add_company(cid)
    task = first_task(cid)
    for bad in ("", "  ", None):
        with pytest.raises(AISearchError) as err:
            ai_search.submit_search_results(task, [], bad)
        assert err.value.code == "invalid_value"
    with pytest.raises(AISearchError) as err:
        ai_search.submit_search_results(99999, [], "web_search")
    assert err.value.code == "task_not_found"
    with pytest.raises(AISearchError):
        ai_search.submit_search_results("1", [], "web_search")
    with pytest.raises(AISearchError):
        ai_search.submit_search_results(task, "not a list", "web_search")
    with pytest.raises(AISearchError) as err:
        ai_search.submit_search_results(task, [GOOD[0]] * 31, "web_search")
    assert err.value.code == "too_many_results"
    # a task stays open after every refusal
    out = ai_search.submit_search_results(task, [GOOD[0], {"title": "x"}, 5], "web_search")
    reasons = [r["reasons"][0] for r in out["results"][1:]]
    assert reasons == ["missing url", "each result must be an object with title, url and snippet"]
    assert out["summary"]["confirmed"] == 1 and out["summary"]["rejected"] == 2


def test_invented_url_for_another_name_goes_to_review_and_qa_blocks_it(cid):
    company_id = add_company(cid)
    task = first_task(cid)
    bad = result("Mira Lee", "Training Manager", url="https://www.linkedin.com/in/john-smith-77aa11")
    out = ai_search.submit_search_results(task, [bad], "web_search")
    outcome = out["results"][0]
    assert outcome["outcome"] == "review"
    assert any(r.startswith("L22:") for r in outcome["reasons"])
    row = person_rows(cid)[0]
    assert row["status"] == "review" and row["confirmed"] == 0
    # even if someone promotes the person by hand, the lead check blocks the AI-search lead
    company = {**_company(company_id), "aliases": []}
    issues = qa_leads.check_person({**row, "reasons_json": []}, company, camp.get_campaign(cid))
    l22 = next(i for i in issues if i.code == "L22")
    assert l22.severity == "block"


def test_paraphrased_short_and_missing_snippets_only_reach_review(cid):
    add_company(cid)
    task = first_task(cid)
    paraphrase = result(
        "Mira Lee", "Training Manager",
        snippet="Mira Lee appears to be a Training Manager at Northwind Health in Singapore.",
    )  # fmt: skip
    short = result("Jonah Reyes", "HR Manager", snippet="HR Manager at Northwind Health")
    none = {**result("Dana Wu", "HR Manager"), "snippet": None}
    missing = {k: v for k, v in result("Priya Rao", "HR Director").items() if k != "snippet"}
    out = ai_search.submit_search_results(task, [paraphrase, short, none, missing], "web_search")
    assert [r["outcome"] for r in out["results"]] == ["review"] * 4
    assert "paraphrase" in out["results"][0]["reasons"][-1]
    assert "shorter than 40" in out["results"][1]["reasons"][-1]
    assert "no snippet" in out["results"][2]["reasons"][-1]
    assert "no snippet" in out["results"][3]["reasons"][-1]
    assert {p["status"] for p in person_rows(cid)} == {"review"}


@pytest.mark.parametrize(
    "marker", ["appears to be", "likely", "probably", "I found", "seems to", "based on"]
)
def test_every_paraphrase_marker_is_caught(marker):
    snippet = f"Training Manager at Northwind Health, {marker} in Singapore for years"
    assert "paraphrase" in ai_search._snippet_problem(snippet)
    fine = "Training Manager at Northwind Health · Singapore · Experience"
    assert ai_search._snippet_problem(fine) == ""
    assert "paraphrase" not in ai_search._snippet_problem(  # whole words only
        "Training Manager at Unlikelyton Health · Singapore · Experience at Unlikelyton"
    )


def test_all_bad_submission_is_rejected_and_task_stays_open(cid):
    add_company(cid)
    task = first_task(cid)
    junk = [
        {"title": "Mira Lee", "url": "https://example.com/mira", "snippet": "x" * 60},
        {"title": "Jonah", "url": "linkedin.com/company/northwind", "snippet": "y" * 60},
    ]
    with pytest.raises(AISearchError) as err:
        ai_search.submit_search_results(task, junk, "web_search")
    assert err.value.code == "not_verbatim"
    assert err.value.as_response() == {
        "ok": False,
        "error": {
            "code": "not_verbatim",
            "message": err.value.message,
            "hint": "Copy titles, URLs and snippets exactly from your search tool; "
            "don't build URLs.",
        },
    }
    assert person_rows(cid) == []
    with db.session() as conn:
        assert db.get(conn, "search_tasks", task)["status"] == "open"
    # with one good link among the bad ones the submission is accepted
    out = ai_search.submit_search_results(task, [*junk, GOOD[0]], "web_search")
    assert out["summary"]["confirmed"] == 1 and out["summary"]["rejected"] == 2


def test_same_link_for_two_names_in_one_submission_sends_both_to_review(cid):
    add_company(cid)
    task = first_task(cid)
    link = "https://www.linkedin.com/in/mira-lee"
    items = [
        result("Mira Lee", "Training Manager", url=link),
        result("Jonah Reyes", "HR Manager", url=link),
    ]
    out = ai_search.submit_search_results(task, items, "web_search")
    assert [r["outcome"] for r in out["results"]] == ["review", "review"]
    assert out["link_conflicts"] == 1
    rows = person_rows(cid)
    assert len(rows) == 1 and rows[0]["status"] == "review" and rows[0]["confirmed"] == 0
    assert any("same LinkedIn address" in r for r in rows[0]["reasons_json"])


def test_same_link_for_a_different_name_later_downgrades_the_earlier_person(cid):
    add_company(cid)
    add_company(cid, "Lakeside Bank")
    tasks = ai_search.get_search_tasks(cid)["tasks"]
    link = "https://www.linkedin.com/in/mira-lee"
    ai_search.submit_search_results(
        tasks[0]["task_id"], [result("Mira Lee", "Training Manager", url=link)], "web_search"
    )
    assert person_rows(cid)[0]["status"] == "confirmed"
    out = ai_search.submit_search_results(
        tasks[1]["task_id"], [result("Jonah Reyes", "HR Manager", url=link)], "web_search"
    )
    assert out["results"][0]["outcome"] == "review"
    assert person_rows(cid)[0]["status"] == "review"
    # the same person under the same name again is only a duplicate
    again = ai_search.submit_search_results(
        tasks[2]["task_id"], [result("Mira Lee", "Training Manager", url=link)], "web_search"
    )
    assert again["results"][0]["outcome"] == "duplicate"


def test_id_slug_is_noted_but_not_blocked(cid):
    add_company(cid)
    task = first_task(cid)
    item = result("Mira Lee", "Training Manager", url="https://www.linkedin.com/in/ACoAAB1x2y3z4")
    out = ai_search.submit_search_results(task, [item], "web_search")
    assert out["results"][0]["outcome"] == "confirmed"
    assert "ID" in out["results"][0]["notes"][0]


def test_skip_task(cid):
    add_company(cid)
    task = first_task(cid)
    with pytest.raises(AISearchError) as err:
        ai_search.skip_task(task, "  ")
    assert err.value.code == "reason_required"
    out = ai_search.skip_task(task, "user said skip this company")
    assert out["status"] == "skipped" and out["remaining_open"] == 1
    assert ai_search.task_note(task)["reason"] == "user said skip this company"
    with pytest.raises(AISearchError):
        ai_search.skip_task(task, "again")
    with pytest.raises(AISearchError):
        ai_search.submit_search_results(task, [], "web_search")
    assert task not in [t["task_id"] for t in ai_search.get_search_tasks(cid)["tasks"]]


def test_task_note_is_empty_without_a_record(cid):
    assert ai_search.task_note(12345) == {}


# --- email and company tasks ---------------------------------------------------------------------


@pytest.fixture
def mail_cid(offer_id):
    cid = make_campaign(offer_id, "mail run", channels=("email",))
    company = add_company(cid, CO, "northwind.example")
    with db.session() as conn:
        db.insert(conn, "people", campaign_id=cid, company_id=company, raw_name="Aria Tan",
                  first_name="Aria", last_name="Tan", status="confirmed", confirmed=1,
                  linkedin_url="https://www.linkedin.com/in/aria-tan-1a2b3c")  # fmt: skip
    return cid


def page(url, snippet):
    return {"title": "Northwind Health - Contact", "url": url, "snippet": snippet}


def test_email_results_teach_the_domain_pattern(mail_cid):
    task = first_task(mail_cid, "email")
    items = [
        page("https://northwind.example/team",
             "Our people: bob.lee@northwind.example and carl.ong@northwind.example work in Ops."),
        page("https://other.example/x", "Write to someone.else@other.example for details here."),
        page("https://northwind.example/contact", "General enquiries: info@northwind.example ok."),
        page("https://northwind.example/about", "We are a health group with many sites in Asia."),
        page("https://northwind.example/news",
             "Dana Wu is probably reachable at dana.wu@northwind.example these days."),
    ]  # fmt: skip
    out = ai_search.submit_search_results(task, items, "web_search")
    assert [r["outcome"] for r in out["results"]] == [
        "used", "no_address", "used", "no_address", "rejected",
    ]
    learned = out["email_pattern"]
    assert learned["pattern"] == "{first}.{last}" and learned["saved"] is True
    assert learned["examples_used"] == 2 and learned["role_addresses"] == 1
    assert learned["source_urls"] == [
        "https://northwind.example/team",
        "https://northwind.example/contact",
    ]
    assert emails.known_pattern("northwind.example") == ("{first}.{last}", "ai_search")
    assert ai_search.ensure_tasks(mail_cid, "email") == 0  # pattern known: nothing left to ask


def test_role_addresses_alone_teach_nothing(mail_cid):
    task = first_task(mail_cid, "email")
    out = ai_search.submit_search_results(
        task,
        [page("https://northwind.example/contact", "Write to hr@northwind.example for careers.")],
        "web_search",
    )
    assert out["email_pattern"]["pattern"] is None and out["email_pattern"]["saved"] is False
    assert out["email_pattern"]["role_addresses"] == 1
    assert emails.known_pattern("northwind.example") is None


def test_email_submission_with_no_usable_url_is_not_verbatim(mail_cid):
    task = first_task(mail_cid, "email")
    with pytest.raises(AISearchError) as err:
        ai_search.submit_search_results(
            task, [{"title": "x", "url": "not a url", "snippet": "z" * 50}, 7], "web_search"
        )
    assert err.value.code == "not_verbatim"


def test_company_task_submission_is_noted_and_points_to_add_companies(cid):
    task = first_task(cid, "company")
    items = [
        {"title": "Top logistics firms in Singapore", "url": "https://example.com/list",
         "snippet": "A list of the largest logistics firms in Singapore."},
        {"title": ""},
    ]  # fmt: skip
    out = ai_search.submit_search_results(task, items, "web_search")
    assert [r["outcome"] for r in out["results"]] == ["noted", "rejected"]
    assert "add_companies" in out["next_step"]
    with pytest.raises(AISearchError) as err:
        ai_search.submit_search_results(
            first_task(cid, "company"), [{"title": ""}], "web_search"
        )
    assert err.value.code == "not_verbatim"


def test_guide_tells_the_ai_the_rules():
    text = ai_search.guide_text()
    for needle in (
        "batches of 10", "verbatim", "Do not open LinkedIn", "empty list", "get_spot_check",
        "record_spot_check", "web search tool in this conversation", "allow_guess_emails",
        "search key", "report progress",
    ):
        assert needle.lower() in text.lower(), needle
    for marker in ai_search.PARAPHRASE_MARKERS:
        assert marker in text.lower()
