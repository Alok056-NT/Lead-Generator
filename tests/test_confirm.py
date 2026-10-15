"""Confirmation engine: the synthetic snippet catalogue, names, urls and each rule (PLAN 11)."""

from __future__ import annotations

import re

import pytest
from fixtures.snippets import SNIPPET_CASES

from leadgen.providers.search import SearchResult
from leadgen.services import confirm
from leadgen.services.confirm import CampaignCtx, CompanyCtx
from leadgen.services.designations import resolve_groups

NORTHWIND = CompanyCtx(name="Northwind Health", aliases=("Northwind Health", "Northwind"))


def campaign(
    groups=("ld", "hr"), country="SG", seniority_min="manager", exclusions=None, suppressed=()
) -> CampaignCtx:
    return CampaignCtx(
        countries=(country,),
        groups=tuple(resolve_groups(list(groups))),
        seniority_min=seniority_min,
        exclusions=exclusions or {},
        suppressed=frozenset(suppressed),
    )


def result(title, link="https://www.linkedin.com/in/test-person", snippet="", position=1):
    return SearchResult(title=title, link=link, snippet=snippet, position=position)


def parse(title, snippet="", link="https://www.linkedin.com/in/test-person", company=NORTHWIND,
          camp=None):  # fmt: skip
    return confirm.parse_result(result(title, link, snippet), company, camp or campaign())


def codes(cand) -> list[str]:
    return cand.reason_codes


# --- the synthetic catalogue ------------------------------------------------------------------


def ctx_for(case: dict) -> tuple[CompanyCtx, CampaignCtx]:
    over = case.get("campaign", {})
    company = case["company"]
    return (
        CompanyCtx(
            name=company["name"], aliases=tuple(company["aliases"]), domain=company["domain"]
        ),
        campaign(
            groups=over.get("designation_groups", ("ld", "hr")),
            country=over.get("country_code", "SG"),
            seniority_min=over.get("seniority_min", "manager"),
            exclusions={"competitors": over.get("exclusions", [])},
            suppressed={confirm.url_key(u) for u in over.get("suppressed_urls", [])},
        ),
    )


@pytest.mark.parametrize("case", SNIPPET_CASES, ids=[c["case"] for c in SNIPPET_CASES])
def test_snippet_catalogue(case):
    company, camp = ctx_for(case)
    r = case["result"]
    cand = confirm.parse_result(
        SearchResult(r["title"], r["link"], r["snippet"], position=1), company, camp
    )
    expect = case["expect"]
    if cand is None:
        assert expect == {"confirmed": False, "reasons": [], "greeting_name": None}
        return
    assert cand.confirmed is expect["confirmed"], cand.reasons
    assert sorted(codes(cand)) == sorted(expect["reasons"]), cand.reasons
    assert cand.greeting_name == expect["greeting_name"]


def test_catalogue_is_large_and_covers_every_shape():
    assert len(SNIPPET_CASES) >= 58
    numbers = {int(c["case"].split("-")[0]) for c in SNIPPET_CASES}
    assert numbers == set(range(1, 25))
    ids = [c["case"] for c in SNIPPET_CASES]
    assert len(ids) == len(set(ids))


def test_catalogue_uses_only_invented_data():
    for case in SNIPPET_CASES:
        assert case["company"]["domain"].endswith((".example.com", ".example.org", ".example.net"))
        assert re.fullmatch(r"https://[a-z]{2,3}\.linkedin\.com/in/[^/]+|"
                            r"https://www\.linkedin\.com/(in|company)/[^/]+/?|"
                            r"https://[a-z]{2,3}\.linkedin\.com/company/[^/]+",
                            case["result"]["link"])  # fmt: skip


# --- urls -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://www.linkedin.com/in/mira-lee", "https://www.linkedin.com/in/mira-lee"),
        ("https://SG.LinkedIn.com/in/Mira-Lee/?trk=x#a", "https://sg.linkedin.com/in/mira-lee"),
        ("https://linkedin.com/in/mira-lee/", "https://linkedin.com/in/mira-lee"),
        ("https://www.linkedin.com/company/northwind", ""),
        ("https://www.linkedin.com/in/mira-lee/de", ""),
        ("https://www.linkedin.com/pub/mira-lee/1/2/3", ""),
        ("http://www.linkedin.com/in/mira-lee", ""),
        ("https://m.linkedin.com/in/mira-lee", ""),
        ("https://www.example.com/in/mira-lee", ""),
        ("", ""),
        ("not a url", ""),
        ("https://[bad/in/x", ""),
    ],
)
def test_canonical_url(link, expected):
    assert confirm.canonical_url(link) == expected


def test_url_key_ignores_country_subdomain():
    a = confirm.url_key("https://sg.linkedin.com/in/Mira-Lee/")
    assert a == confirm.url_key("https://www.linkedin.com/in/mira-lee") == "in/mira-lee"
    assert confirm.url_key("https://www.linkedin.com/company/x") == ""


def test_company_page_is_not_a_person():
    assert parse("Northwind Health | LinkedIn", link="https://www.linkedin.com/company/nw") is None


# --- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "first", "last", "greeting"),
    [
        ("Mira Lee", "Mira", "Lee", "Mira"),
        ("Jerome Tan, MBA", "Jerome", "Tan", "Jerome"),
        ("Jerome Tan, MBA, CFA", "Jerome", "Tan", "Jerome"),
        ("Jerome Tan MBA", "Jerome", "Tan", "Jerome"),
        ("Aditi Rao PhD", "Aditi", "Rao", "Aditi"),
        ("Sarah Lopez (IHRP-CP)", "Sarah", "Lopez", "Sarah"),
        ("Sarah Lopez [GPHR]", "Sarah", "Lopez", "Sarah"),
        ("Lim Jun Hao (Max)", "Jun Hao", "Lim", "Max"),
        ("Anna Wu (MA)", "Anna", "Wu", "Anna"),
        ("Dr. Priya Raman", "Priya", "Raman", "Priya"),
        ("Prof Wei Ling Tan", "Wei Ling", "Tan", "Wei Ling"),
        ("R KUMARASAMY", "R", "Kumarasamy", "Kumarasamy"),
        ("R. Kumarasamy", "R", "Kumarasamy", "Kumarasamy"),
        ("J.R. Smith", "J.R", "Smith", "Smith"),
        ("ANITA PRASAD", "Anita", "Prasad", "Anita"),
        ("Maya O'NEIL", "Maya", "O'Neil", "Maya"),
        ("Mei Xin Tan", "Mei Xin", "Tan", "Mei Xin"),
        ("Lim Wei Jie", "Wei Jie", "Lim", "Wei Jie"),
        ("Chen Rachel", "Rachel", "Chen", "Rachel"),
        ("John Paul Lee", "John", "Paul Lee", "John"),
        ("Mei-Xin Tan", "Mei-Xin", "Tan", "Mei-Xin"),
        ("Aria Tan Wei Ling Koh", "Aria", "Tan Wei Ling Koh", "Aria"),
    ],
)
def test_parse_name(raw, first, last, greeting):
    parts = confirm.parse_name(raw)
    assert (parts.first, parts.last, parts.greeting) == (first, last, greeting)
    assert parts.full


@pytest.mark.parametrize("raw", ["Madonna", "", "   ", "Mira L.", "(Max)", "MBA"])
def test_parse_name_without_a_full_name(raw):
    assert not confirm.parse_name(raw).full


def test_nickname_must_look_like_a_name():
    assert confirm.parse_name("Anna Tan (HR)").greeting == "Anna"
    assert confirm.parse_name("Anna Tan (PhD)").greeting == "Anna"
    assert confirm.parse_name("Anna Tan (Annie)").greeting == "Annie"
    assert confirm.parse_name("Anna Tan (a nickname)").greeting == "Anna"


def test_name_html_entities_and_symbols():
    parts = confirm.parse_name("Maya O&#39;Neil \U0001f31f")
    assert parts.display == "Maya O'Neil"


def test_short_name_gets_l11():
    cand = parse("Madonna - Training Manager at Northwind Health | LinkedIn",
                 "Training Manager at Northwind Health · Singapore")  # fmt: skip
    assert "L11" in codes(cand)
    assert not cand.confirmed


# --- rules ------------------------------------------------------------------------------------

GOOD = "Training Manager at Northwind Health · Singapore"


def test_confirmed_person_has_all_fields():
    cand = parse("Mira Lee - Training Manager at Northwind Health | LinkedIn", GOOD)
    assert cand.confirmed and cand.reasons == []
    assert (cand.first_name, cand.last_name, cand.greeting_name) == ("Mira", "Lee", "Mira")
    assert cand.headline == "Training Manager at Northwind Health"
    assert cand.designation == "L&D / Training heads"
    assert cand.seniority == "manager"
    assert cand.country == "Singapore"
    assert cand.linkedin_url == "https://www.linkedin.com/in/test-person"
    assert cand.evidence_snippet == GOOD
    assert cand.evidence_title.startswith("Mira Lee")


def test_bullet_and_dash_separators_and_entities():
    cand = parse(
        "Mira Lee - Training Manager | LinkedIn",
        "Experience • Northwind &amp; Co • Singapore • 500+ connections",
        company=CompanyCtx(name="Northwind & Co"),
    )
    assert cand.confirmed, cand.reasons
    cand = parse(
        "Mira Lee - Training Manager | LinkedIn",
        "Experience - Northwind Health - Singapore",
    )
    assert cand.confirmed, cand.reasons


def test_and_and_ampersand_are_interchangeable_in_company_names():
    company = CompanyCtx(name="Harbourview Housing and Development")
    cand = parse(
        "Eli Mensah - Training Manager at Harbourview Housing & Development | LinkedIn",
        "Singapore", company=company,
    )  # fmt: skip
    assert cand.confirmed, cand.reasons


def test_headline_naming_company_after_a_dash_counts():
    cand = parse("Mira Lee - Training Manager - Northwind Health | LinkedIn", "Singapore")
    assert cand.confirmed, cand.reasons
    assert cand.designation == "L&D / Training heads"


def test_company_word_in_headline_does_not_decide_the_designation():
    company = CompanyCtx(name="Safety First Training")
    cand = parse(
        "Mira Lee - Director - Safety First Training | LinkedIn", "Singapore", company=company,
        camp=campaign(groups=("hr",)),
    )  # fmt: skip
    assert "L06" in codes(cand)


def test_headline_from_snippet_when_google_truncated_the_title():
    cand = parse(
        "Mira Lee - Learning and Development Man… | LinkedIn",
        "Mira Lee. Learning and Development Manager at Northwind Health. Singapore.",
    )
    assert cand.headline == "Learning and Development Manager at Northwind Health"
    assert cand.confirmed, cand.reasons
    cand = parse(
        "Mira Lee - Learning and Development Man... | LinkedIn",
        "Learning and Development Manager at Northwind Health · Singapore",
    )
    assert cand.headline == "Learning and Development Manager at Northwind Health"
    assert cand.confirmed, cand.reasons


def test_headline_from_snippet_when_title_has_none():
    cand = parse(
        "Mira Lee | LinkedIn",
        "Mira Lee. Training Manager. Northwind Health Lakeshore University. Singapore.",
    )
    assert cand.headline == "Training Manager" and cand.confirmed
    cand = parse("Mira Lee | LinkedIn", "Training Manager at Northwind Health · Singapore")
    assert cand.headline == "Training Manager at Northwind Health" and cand.confirmed
    cand = parse("Mira Lee | LinkedIn", "Experience · Northwind Health · Singapore")
    assert cand.headline == "" and "L06" in codes(cand)


def test_title_with_other_dashes_and_noise():
    cand = parse("Mira Lee – Training Manager at Northwind Health | LinkedIn", GOOD)
    assert cand.confirmed and cand.raw_name == "Mira Lee"
    cand = parse("Mira Lee - Training Manager at Northwind Health - LinkedIn", GOOD)
    assert cand.confirmed


def test_truncated_employer_needs_an_ellipsis():
    cand = parse("Ben Adler - Training Manager at North… | LinkedIn", "Singapore")
    assert cand.confirmed, cand.reasons
    cand = parse("Ben Adler - Training Manager at North | LinkedIn", "Singapore")
    assert codes(cand) == ["L09"]
    cand = parse("Ben Adler - Training Manager at No… | LinkedIn", "Singapore")
    assert codes(cand) == ["L09"]  # too short to be a prefix of the name


def test_company_not_found_and_mentioned_only():
    cand = parse("Ben Adler - Training Manager | LinkedIn", "Singapore · Experience · Acme")
    assert cand.reasons[0] == "L04:company not found in profile"
    cand = parse("Ben Adler - Training Manager | LinkedIn", "Singapore · I worked with Northwind")
    assert cand.reasons[0].startswith("L04:company mentioned but not confirmed")


def test_present_must_follow_the_company_directly():
    snippet = (
        "Northwind Health · Jul 2011 - Dec 2015 · Blue Harbor · Jan 2016 - Present · Singapore"
    )
    cand = parse("Ben Adler - Training Manager | LinkedIn", snippet)
    assert codes(cand) == ["L04"]
    cand = parse("Ben Adler - Training Manager | LinkedIn",
                 "Northwind Health. Jul 2011 - Present · Singapore")  # fmt: skip
    assert cand.confirmed


def test_at_company_in_snippet_counts():
    cand = parse("Ben Adler - Training Manager | LinkedIn",
                 "Training Manager at Northwind Health · Singapore")  # fmt: skip
    assert cand.confirmed
    cand = parse(
        "Ben Adler - Training Manager | LinkedIn", "Training Manager @Northwind · Singapore"
    )
    assert cand.confirmed


@pytest.mark.parametrize(
    "snippet",
    [
        "Training Manager · Singapore · Previously Training Manager at Northwind Health",
        "Singapore · Formerly HR lead at Northwind Health.",
        "Singapore · Ex-HR lead @ Northwind Health",
        "Singapore · Retired HR lead at Northwind Health",
        "Singapore · Until 2019 HR lead at Northwind Health",
        "Singapore · Worked in the past at Northwind Health",
    ],
)
def test_at_company_after_a_former_word_is_not_current(snippet):
    cand = parse("Ben Adler - HR Manager | LinkedIn", snippet)
    assert codes(cand) == ["L04"]


@pytest.mark.parametrize(
    "snippet",
    [
        "Singapore · Previously Sr. HR Manager at Northwind Health",
        "Singapore · Formerly Asst. Director, HR at Northwind Health",
        "Singapore · Ex-Snr. Manager @ Northwind Health",
        "Singapore · Previously HR Mgr. at Northwind Health",
    ],
)
def test_abbreviation_dot_does_not_end_the_former_role_lookback(snippet):
    cand = parse("Ben Adler - Senior HR Manager | LinkedIn", snippet)
    assert codes(cand) == ["L04"]


def test_abbreviation_in_layout_a_headline_does_not_split_it():
    name, snippet = "Ben Adler", "Ben Adler. Sr. HR Manager. Northwind Health School. Singapore"
    lead, after, _ = confirm._snippet_layouts(name, snippet)
    assert lead == "Sr. HR Manager"
    assert after.startswith("Northwind Health")


_STALE_TAIL = " Northwind Health National University of Singapore. Singapore."


@pytest.mark.parametrize(
    ("title", "headline"),
    [
        ("Aria Tan - Northwind Health | LinkedIn", "HR Manager at Acme Pte. Ltd."),
        ("Aria Tan - HR Manager at Acme Pte... | LinkedIn", "HR Manager at Acme Pte. Ltd."),
        ("Aria Tan - HR Manager at Acme Co... | LinkedIn", "HR Manager at Acme Co."),
        ("Aria Tan - Northwind Health | LinkedIn", "HR Manager at Acme Logistics."),
        ("Aria Tan - Northwind Health | LinkedIn", "HR Manager at Acme Sdn. Bhd."),
        ("Aria Tan - Northwind Health | LinkedIn", "Sr. HR Manager at Acme Co. Ltd."),
        ("Aria Tan - Northwind Health | LinkedIn", "HR Manager at Acme Intl. Pte. Ltd."),
    ],
)
def test_stale_company_suffix_headline_is_a_different_employer(title, headline):
    cand = parse(title, f"Aria Tan. {headline}{_STALE_TAIL}")
    assert "L09" in codes(cand), cand.reasons
    assert cand.headline and "University" not in cand.headline and "Singapore" not in cand.headline


def test_company_suffix_headline_naming_the_company_is_confirmed():
    snippet = (
        "Aria Tan. HR Manager at Northwind Health Pte. Ltd. Northwind Health "
        "National University of Singapore. Singapore."
    )
    cand = parse("Aria Tan - Northwind Health | LinkedIn", snippet)
    assert cand.confirmed, cand.reasons
    assert cand.headline == "HR Manager at Northwind Health Pte. Ltd"
    assert "University" not in cand.headline and "Singapore" not in cand.headline


@pytest.mark.parametrize(
    ("headline", "lead"),
    [
        ("HR Manager at Acme Pte. Ltd.", "HR Manager at Acme Pte. Ltd"),
        ("HR Manager at Acme Sdn. Bhd.", "HR Manager at Acme Sdn. Bhd"),
        ("HR Manager at Acme Co. Ltd.", "HR Manager at Acme Co. Ltd"),
        ("HR Manager at Acme Corp.", "HR Manager at Acme Corp"),
        ("Asst. Mgr. HR at Acme.", "Asst. Mgr. HR at Acme"),
    ],
)
def test_layout_a_split_keeps_suffix_pairs_together(headline, lead):
    snippet = f"Ben Adler. {headline} Northwind Health. SG"
    got_lead, got_after, _ = confirm._snippet_layouts("Ben Adler", snippet)
    assert got_lead == lead
    assert got_after.startswith("Northwind")


def test_real_sentence_end_still_limits_the_former_word():
    cand = parse(
        "Ben Adler - HR Manager | LinkedIn",
        "Singapore · Formerly HR lead at Acme Health. HR Manager at Northwind Health",
    )
    assert cand.confirmed, cand.reasons


def test_former_word_in_another_segment_does_not_block_at_company():
    cand = parse(
        "Ben Adler - HR Manager | LinkedIn",
        "Singapore · Previously at Acme · HR Manager at Northwind Health",
    )
    assert cand.confirmed, cand.reasons
    # a second, current `at` match still counts
    cand = parse(
        "Ben Adler - HR Manager | LinkedIn",
        "Singapore · Formerly HR lead at Northwind Health. HR Manager at Northwind Health",
    )
    assert cand.confirmed, cand.reasons


def test_experience_field_naming_another_company_blocks_loose_at_match():
    title = "Ben Adler - HR Manager | LinkedIn"
    cand = parse(title, "Experience: Acme Corp · Singapore · HR Manager at Northwind Health")
    assert codes(cand) == ["L04"]
    # a present-tense date range, or an Experience field that names the company, still counts
    cand = parse(title, "Experience: Acme Corp · Northwind Health. Mar 2018 - Present · Singapore")
    assert cand.confirmed, cand.reasons
    cand = parse(title, "Experience: Northwind Health · Singapore · HR Manager at Northwind Health")
    assert cand.confirmed, cand.reasons
    # the headline naming the company is enough whatever the Experience field says
    cand = parse(
        "Ben Adler - HR Manager at Northwind Health | LinkedIn", "Experience: Acme Corp · Singapore"
    )
    assert cand.confirmed, cand.reasons


def test_title_with_only_a_company_after_the_name_uses_the_snippet():
    title = "Aria Tan - Northwind Health | LinkedIn"
    cand = parse(
        title,
        "Training Manager at Northwind Health · Experience: Northwind Health · "
        "Location: Singapore",
    )
    assert cand.confirmed, cand.reasons
    assert cand.headline == "Training Manager at Northwind Health"
    cand = parse(title, "Experience: Northwind Health · Location: Singapore")
    assert "L06" in codes(cand)
    assert cand.headline == ""
    cand = parse(
        "Aria Tan - Northwind | LinkedIn",
        "Aria Tan. HR Manager. Northwind Health School. Singapore.",
    )
    assert cand.confirmed and cand.headline == "HR Manager"
    # a role that merely contains the company name is still a headline
    cand = parse("Aria Tan - Northwind Health HR Manager | LinkedIn", "Singapore")
    assert cand.headline == "Northwind Health HR Manager"


def test_short_caps_alias_is_case_sensitive_and_whole_word():
    company = CompanyCtx(name="Example Holdings", aliases=("EH",))
    for snippet in ("Experience · Eh Bakery · Singapore", "Experience · REHAB Centre · Singapore"):
        cand = parse("Ben Adler - Training Manager | LinkedIn", snippet, company=company)
        assert codes(cand) == ["L04"], snippet
    cand = parse("Ben Adler - Training Manager | LinkedIn", "Experience · EH · Singapore",
                 company=company)  # fmt: skip
    assert cand.confirmed
    cand = parse("Ben Adler - Training Manager at EH | LinkedIn", "Singapore", company=company)
    assert cand.confirmed


def test_long_alias_is_case_insensitive():
    cand = parse(
        "Ben Adler - Training Manager | LinkedIn", "experience · NORTHWIND HEALTH · Singapore"
    )
    assert cand.confirmed


def test_country_uses_snippet_not_company_name():
    company = CompanyCtx(name="Bank of Singapore")
    cand = parse("Ben Adler - Training Manager at Bank of Singapore | LinkedIn",
                 "Training Manager at Bank of Singapore · London", company=company)  # fmt: skip
    assert codes(cand) == ["L05"]


def test_company_country_narrows_campaign_countries():
    company = CompanyCtx(name="Northwind Health", countries=("MY",))
    camp = campaign(country="SG")
    cand = parse("Ben Adler - Training Manager at Northwind Health | LinkedIn",
                 "Kuala Lumpur, Malaysia", company=company, camp=camp)  # fmt: skip
    assert cand.confirmed and cand.country == "Malaysia"
    cand = parse("Ben Adler - Training Manager at Northwind Health | LinkedIn",
                 "Singapore", company=company, camp=camp)  # fmt: skip
    assert codes(cand) == ["L05"]


def test_any_target_country_will_do_and_none_means_no_check():
    camp = CampaignCtx(countries=("SG", "MY"), groups=tuple(resolve_groups(["ld"])))
    cand = parse("Ben Adler - Training Manager at Northwind Health | LinkedIn",
                 "Johor Bahru, Malaysia", camp=camp)  # fmt: skip
    assert cand.confirmed and cand.country == "Malaysia"
    camp = CampaignCtx(countries=("SG", "MY"), groups=tuple(resolve_groups(["ld"])))
    cand = parse("Ben Adler - Training Manager at Northwind Health | LinkedIn", "Paris", camp=camp)
    assert cand.reasons == ["L05:not shown as based in Singapore or Malaysia"]
    camp = CampaignCtx(countries=(), groups=tuple(resolve_groups(["ld"])))
    cand = parse("Ben Adler - Training Manager at Northwind Health | LinkedIn", "Paris", camp=camp)
    assert cand.confirmed
    camp = CampaignCtx(countries=("Narnia",), groups=tuple(resolve_groups(["ld"])))
    cand = parse("Ben Adler - Training Manager at Northwind Health | LinkedIn", "Paris", camp=camp)
    assert cand.confirmed  # an unknown country cannot be checked


@pytest.mark.parametrize(
    "headline",
    [
        "Former Training Manager at Northwind Health",
        "Training Manager, formerly at Northwind Health",
        "Retired Training Manager at Northwind Health",
        "Ex-Training Manager at Northwind Health",
        "Previously Training Manager at Northwind Health",
    ],
)
def test_former_role(headline):
    cand = parse(f"Ben Adler - {headline} | LinkedIn", "Singapore")
    assert "L08" in codes(cand)


def test_former_in_the_snippets_lead():
    cand = parse("Ben Adler - Training Manager | LinkedIn",
                 "Former Training Manager at Northwind Health · Singapore")  # fmt: skip
    assert "L08" in codes(cand)


@pytest.mark.parametrize(
    "headline",
    [
        "Training Manager at",
        "Training Manager @",
        "View mutual connections",
        "Training Manager - can introduce you to Mira",
    ],
)
def test_garbled_headline(headline):
    cand = parse(f"Ben Adler - {headline} | LinkedIn", "Experience · Northwind Health · Singapore")
    assert "L12" in codes(cand)
    assert not cand.confirmed


@pytest.mark.parametrize(
    "hint",
    ["Looking for new opportunities", "#OpenToWork", "Seeking a new role", "Job seeker"],
)
def test_job_seeker_hints(hint):
    cand = parse(
        "Ben Adler - Training Manager at Northwind Health | LinkedIn", f"{hint} · Singapore"
    )
    assert codes(cand) == ["L15"]


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("Training Specialist at Northwind Health", {"L07", "L14"}),
        ("Training Coordinator at Northwind Health", {"L07", "L14"}),
        ("Junior Training Manager at Northwind Health", {"L07", "L14"}),
        ("Training Intern at Northwind Health", {"L07", "L14"}),
        ("Associate Training Manager at Northwind Health", set()),  # manager word wins
        ("Associate Director, Training at Northwind Health", set()),
        ("Executive Director, Training at Northwind Health", set()),
        ("Head of Executive Learning at Northwind Health", set()),
        ("Trainee Training Manager at Northwind Health", {"L07", "L14"}),
    ],
)
def test_junior_keywords(headline, expected):
    cand = parse(f"Ben Adler - {headline} | LinkedIn", "Singapore")
    assert set(codes(cand)) == expected, cand.reasons


def test_seniority_minimum_levels():
    title = "Ben Adler - Training Manager at Northwind Health | LinkedIn"
    assert parse(title, "Singapore", camp=campaign(seniority_min="individual")).confirmed
    cand = parse(title, "Singapore", camp=campaign(seniority_min="director"))
    assert cand.reasons == ["L07:below director level"]
    cand = parse(title, "Singapore", camp=campaign(seniority_min="chief"))
    assert cand.reasons[0].startswith("L07:unknown minimum")


def test_owner_group_rules():
    owners = campaign(groups=("owners",))
    base = "Singapore"
    for headline, ok in [
        ("Deputy CEO at Northwind Health", True),
        ("Chief Executive Officer at Northwind Health", False),  # not a keyword of the template
        ("CEO and Founder at Northwind Health", True),
        ("Country Manager at Northwind Health", True),
        ("General Manager at Northwind Health", True),
        ("Managing Partner at Northwind Health", True),
        ("Managing Director at Northwind Health", False),
        ("Principal at Northwind Health", False),
        ("Executive Director at Northwind Health", False),
        ("Vice President at Northwind Health", False),
    ]:
        cand = parse(f"Ben Adler - {headline} | LinkedIn", base, camp=owners)
        assert cand.confirmed is ok, (headline, cand.reasons)
    cand = parse("Ben Adler - Managing Director at Northwind Health | LinkedIn", base, camp=owners)
    assert codes(cand) == ["L10"]


def test_general_manager_counts_as_senior_in_the_owner_group():
    owners = campaign(groups=("owners",), seniority_min="director")
    cand = parse("Ben Adler - General Manager at Northwind Health | LinkedIn", "Singapore",
                 camp=owners)  # fmt: skip
    assert cand.confirmed, cand.reasons
    cand = parse("Ben Adler - General Manager at Northwind Health | LinkedIn", "Singapore",
                 camp=campaign(groups=("hr",), seniority_min="director"))  # fmt: skip
    assert "L06" in codes(cand)


@pytest.mark.parametrize(
    "role",
    ["Head of IT Operations", "Treasury Operations Manager", "Sales Operations Director",
     "Data Centre Operations Manager", "Commercial Operations Manager"],
)  # fmt: skip
def test_weak_fit_operations_titles(role):
    camp = campaign(groups=("safety_ops",))
    cand = parse(f"Ben Adler - {role} at Northwind Health | LinkedIn", "Singapore", camp=camp)
    assert set(codes(cand)) == {"L06", "L13"}, cand.reasons


@pytest.mark.parametrize(
    "role",
    ["Operations Manager", "Head of Facilities Operations", "Health and Safety Operations Lead",
     "IT Safety Operations Manager", "Head of Plant Operations"],
)  # fmt: skip
def test_real_operations_titles_stay_confirmed(role):
    camp = campaign(groups=("safety_ops",))
    cand = parse(f"Ben Adler - {role} at Northwind Health | LinkedIn", "Singapore", camp=camp)
    assert cand.confirmed or codes(cand) == ["L07"], cand.reasons
    assert "L13" not in codes(cand)


def test_weak_fit_title_may_still_match_another_group():
    camp = campaign(groups=("safety_ops", "sales"))
    cand = parse("Ben Adler - Sales Operations Manager at Northwind Health | LinkedIn", "Singapore",
                 camp=camp)  # fmt: skip
    assert cand.confirmed and cand.designation == "Sales"
    # An IT group is not hurt by the rule either ("IT Operations" is IT).
    camp = campaign(groups=("it", "safety_ops"))
    cand = parse("Ben Adler - Head of IT Operations at Northwind Health | LinkedIn", "Singapore",
                 camp=camp)  # fmt: skip
    assert cand.confirmed and cand.designation == "IT / Technology leaders"


def test_excluded_company_competitor_and_domain():
    base = "Ben Adler - Training Manager at Northwind Health | LinkedIn"
    for exclusions in (
        {"companies": ["Northwind Health"]},
        {"competitors": ["northwind  health"]},
        {"companies": ["Northwind"]},
        {"domains": ["northwindhealth.example.com"]},
        {"competitors": ["northwindhealth.example.com"]},
    ):
        company = CompanyCtx(name="Northwind Health", aliases=("Northwind",),
                             domain="www.northwindhealth.example.com")  # fmt: skip
        cand = parse(base, "Singapore", company=company, camp=campaign(exclusions=exclusions))
        assert codes(cand) == ["L03"], exclusions
    cand = parse(base, "Singapore", camp=campaign(exclusions={"companies": ["Other Co"]}))
    assert cand.confirmed
    for status in ("excluded", "candidate"):
        company = CompanyCtx(name="Northwind Health", status=status)
        cand = parse(base, "Singapore", company=company)
        assert codes(cand) == ["L03"] and status in cand.reasons[0]


def test_suppressed_profile_and_excluded_person():
    base = "Ben Adler - Training Manager at Northwind Health | LinkedIn"
    ctx = CampaignCtx.from_campaign(
        {
            "countries": ["SG"],
            "designations": [{"label": "L&D", "keywords": ["training"], "search_terms": ["x"]}],
            "exclusions": {"people": ["https://sg.linkedin.com/in/ben-adler", "Mira Lee"]},
        },
        suppressed=["https://www.linkedin.com/in/someone-else"],
    )
    cand = confirm.parse_result(
        result(base, "https://www.linkedin.com/in/BEN-ADLER/", "Singapore"), NORTHWIND, ctx
    )
    assert codes(cand) == ["L16"]
    cand = confirm.parse_result(
        result("Mira Lee - Training Manager at Northwind Health", "https://www.linkedin.com/in/x1",
               "Singapore"), NORTHWIND, ctx)  # fmt: skip
    assert codes(cand) == ["L16"]  # excluded by name
    cand = confirm.parse_result(
        result(base, "https://www.linkedin.com/in/someone-else", "Singapore"), NORTHWIND, ctx
    )
    assert codes(cand) == ["L16"]
    assert "blocked" not in ctx.suppressed


def test_campaign_ctx_from_campaign_defaults():
    ctx = CampaignCtx.from_campaign({"designations": [], "countries": None})
    assert ctx.countries == () and ctx.groups == () and ctx.seniority_min == "manager"
    assert ctx.suppressed == frozenset()


def test_company_ctx_aliases_are_clean_and_unique():
    company = CompanyCtx(name="Northwind  Health", aliases=("Northwind Health", "", "NW"))
    assert company.all_aliases() == ("Northwind Health", "NW")


def test_candidate_properties():
    cand = parse("Ben Adler - Training Manager | LinkedIn", "Singapore")
    assert not cand.confirmed
    assert cand.reason_codes == ["L04"]


def test_different_employer_wins_over_snippet_mention():
    cand = parse("Ben Adler - Training Manager at The Acme Group | LinkedIn",
                 "Experience · Northwind Health · Singapore")  # fmt: skip
    assert codes(cand) == ["L09"]
    assert "Acme" in cand.reasons[0]


def test_two_at_phrases_use_the_first_employer():
    cand = parse("Ben Adler - Training Manager at Northwind Health | Speaker at Conf | LinkedIn",
                 "Singapore")  # fmt: skip
    assert cand.confirmed, cand.reasons


# --- slug against name (T21, L22) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "first", "last", "expected"),
    [
        ("aria-tan", "Aria", "Tan", "match"),
        ("aria-tan-4b7a2c1", "Aria", "Tan", "match"),  # trailing hex ID
        ("aria-tan-123456", "Aria", "Tan", "match"),  # trailing number
        ("ariatan123", "Aria", "Tan", "match"),  # number glued to the name
        ("atan", "Aria", "Tan", "match"),  # first initial + surname
        ("ariat", "Aria", "Tan", "match"),  # first name + last initial
        ("a-tan", "Aria", "Tan", "match"),
        ("tan-aria", "Aria", "Tan", "match"),
        ("rob-smith", "Robert", "Smith", "match"),  # shortened first name
        ("zoe-muller-12ab34", "Zoë", "Müller", "match"),  # accents folded
        ("jose-garcia", "José", "García", "match"),
        ("S%C3%B8ren-hansen".lower(), "Søren", "Hansen", "match"),
        ("mary-jane-o-brien", "Mary Jane", "O'Brien", "match"),
        ("anna-lee-wong", "Anna", "Lee Wong", "match"),  # one name token is enough
        ("john-smith-77aa11", "Aria", "Tan", "mismatch"),
        ("natanael-xyz", "Aria", "Tan", "mismatch"),  # "atan" inside another name
        ("bad", "Aria", "Tan", "mismatch"),  # hex-looking word without digits is kept
        ("ACoAAB1x2y3z4", "Aria", "Tan", "no_tokens"),  # machine ID
        ("q8w7e6r5t4y3u2i1", "Aria", "Tan", "no_tokens"),
        ("wei-wang-5566ab", "伟", "王", "no_tokens"),  # nothing to compare
        ("aria-tan", "", "", "no_tokens"),
        ("12345", "Aria", "Tan", "no_tokens"),
    ],
)
def test_slug_matches_name_table(slug, first, last, expected):
    url = f"https://www.linkedin.com/in/{slug}"
    assert confirm.slug_matches_name(url, first, last) == expected


def test_slug_matches_name_needs_a_profile_link():
    check = confirm.slug_matches_name
    assert check("https://example.com/in/aria-tan", "Aria", "Tan") == "no_tokens"
    assert check("", "Aria", "Tan") == "no_tokens"
    assert check("https://uk.linkedin.com/in/aria-tan/", "Aria", "Tan") == "match"


def test_looks_like_id():
    assert confirm.looks_like_id("ACoAAB1x2y3z4") and confirm.looks_like_id("q8w7e6r5t4y3u2i1")
    assert not confirm.looks_like_id("aria-tan-1a2b3c") and not confirm.looks_like_id("arianatalia")
