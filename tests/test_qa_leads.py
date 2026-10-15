"""Lead checks L01-L23: one pass and one fail case per code, plus loading a campaign.

All people and companies are invented. No network is used.
"""

from __future__ import annotations

import pytest
from qa_world import (
    COMPANY,
    add_company,
    add_email,
    add_person,
    campaign_dict,
    company_row,
    email_row,
    make_campaign,
    person_row,
    suppress,
)

from leadgen import db
from leadgen.services import campaign as camp
from leadgen.services import qa_leads
from leadgen.services.qa_leads import Issue, LeadContext, check_person


def run(person=None, company=None, campaign=None, email=None, context=None):
    return check_person(
        person or person_row(),
        company or company_row(),
        campaign or campaign_dict(),
        email,
        context,
    )


def codes(issues):
    return [i.code for i in issues]


def by_code(issues, code):
    return next(i for i in issues if i.code == code)


def title_snippet(headline, company=COMPANY, extra=" · Singapore"):
    return {
        "evidence_title": f"Aria Tan - {headline} at {company} | LinkedIn",
        "evidence_snippet": f"{headline} at {company}{extra} · Experience: {company}",
        "title": headline,
    }


def test_clean_lead_has_no_issues():
    assert run(email=email_row()) == []


def test_issue_as_dict_and_tables_cover_every_code():
    assert Issue("L04", "block", "x").as_dict() == {
        "code": "L04",
        "severity": "block",
        "detail": "x",
    }
    expected = {f"L{n:02d}" for n in range(1, 24)}
    assert set(qa_leads.LEAD_SEVERITY) == expected == set(qa_leads.LEAD_TITLES)


# --- L01 to L10, L13 to L16: the confirmation rules -------------------------------------------


def test_l01_valid_profile_url_passes_and_company_page_blocks():
    assert "L01" not in codes(run())
    issues = run(person_row(linkedin_url="https://www.linkedin.com/company/northwind"))
    assert by_code(issues, "L01").severity == "block"
    # a post, a bad host and an empty link are not profiles either
    for url in ("https://www.linkedin.com/posts/aria-tan", "https://example.com/in/aria", ""):
        assert "L01" in codes(run(person_row(linkedin_url=url)))


def test_l03_company_must_be_approved_and_not_excluded():
    assert "L03" not in codes(run())
    assert by_code(run(company=company_row(status="candidate")), "L03").severity == "block"
    assert "L03" in codes(run(company=company_row(status="excluded")))
    competitor = campaign_dict(exclusions={"competitors": [COMPANY], "companies": []})
    assert "competitor" in by_code(run(campaign=competitor), "L03").detail


def test_l04_current_employer_must_be_confirmed():
    assert "L04" not in codes(run())
    other = person_row(
        evidence_title="Aria Tan - Head of Learning & Development | LinkedIn",
        evidence_snippet="Singapore · Training leader. Previously at Northwind Logistics.",
    )
    issue = by_code(run(other), "L04")
    assert issue.severity == "block"


def test_l05_country_must_be_shown():
    assert "L05" not in codes(run())
    overseas = person_row(
        evidence_snippet=person_row()["evidence_snippet"].replace("Singapore", "London")
    )
    assert by_code(run(overseas), "L05").severity == "block"


def test_l06_designation_must_be_in_campaign_groups():
    assert "L06" not in codes(run())
    sales = person_row(**title_snippet("Head of Sales"))
    assert by_code(run(sales), "L06").severity == "block"


def test_l07_seniority_at_least_minimum():
    assert "L07" not in codes(run())
    junior = person_row(**title_snippet("Training Coordinator"))
    assert "L07" in codes(run(junior))
    assert by_code(run(junior), "L07").severity == "block"


def test_l08_former_role_blocks():
    assert "L08" not in codes(run())
    former = person_row(**title_snippet("Former Head of Learning & Development"))
    assert by_code(run(former), "L08").severity == "block"


def test_l09_headline_naming_another_employer_blocks():
    assert "L09" not in codes(run())
    other = person_row(
        evidence_title="Aria Tan - Head of Learning & Development at Contoso Foods | LinkedIn",
        evidence_snippet=f"Singapore · Experience: Contoso Foods · {COMPANY}",
    )
    assert by_code(run(other), "L09").severity == "block"


def test_l10_owner_group_with_rank_only_title_blocks():
    owners = campaign_dict(designations=[*_owners()])
    rank = person_row(**title_snippet("Managing Director"))
    assert by_code(run(rank, campaign=owners), "L10").severity == "block"
    head = person_row(**title_snippet("Chief Executive Officer"))
    assert "L10" not in codes(run(head, campaign=owners))


def _owners():
    from qa_world import group_dicts

    return group_dicts("owners")


def test_l13_weak_fit_function_warns_with_l06():
    ops = campaign_dict(designations=_ops())
    weak = person_row(**title_snippet("Head of IT Operations"))
    issues = run(weak, campaign=ops)
    assert by_code(issues, "L13").severity == "warn"
    assert "L06" in codes(issues)
    assert "L13" not in codes(run(person_row(**title_snippet("Head of Operations")), campaign=ops))


def _ops():
    from qa_world import group_dicts

    return group_dicts("safety_ops")


def test_l14_junior_keyword_blocks():
    assert "L14" not in codes(run())
    junior = person_row(**title_snippet("Junior Learning and Development Manager"))
    assert by_code(run(junior), "L14").severity == "block"


def test_l15_job_seeking_hint_warns():
    assert "L15" not in codes(run())
    seeker = person_row(evidence_snippet=person_row()["evidence_snippet"] + " · Open to work")
    assert by_code(run(seeker), "L15").severity == "warn"


def test_l16_profile_name_or_email_on_the_suppression_list(ready_db):
    camp_dict = campaign_dict()
    suppress("linkedin", "https://www.linkedin.com/in/aria-tan-1a2b3c")
    suppress("email", "aria.tan@northwind.example")
    cid = make_campaign()["id"]
    co = add_company(cid)
    pid = add_person(cid, co, linkedin_url="https://www.linkedin.com/in/aria-tan-1a2b3c")
    add_email(pid, address="aria.tan@northwind.example")
    data = qa_leads.load_leads(camp.get_campaign(cid))
    issues = qa_leads.check_all(data)[pid]
    assert by_code(issues, "L16").severity == "block"
    # the email alone is enough
    other = add_person(cid, co, "Marcus", "Lim")
    add_email(other, address="aria.tan@northwind.example")
    data = qa_leads.load_leads(camp.get_campaign(cid))
    assert "L16" in codes(qa_leads.check_all(data)[other])
    assert camp_dict  # plain-dict context has no suppression list
    assert "L16" not in codes(run())


def test_l16_domain_suppression(ready_db):
    suppress("domain", "northwind.example")
    cid = make_campaign()["id"]
    pid = add_person(cid, add_company(cid))
    add_email(pid)
    data = qa_leads.load_leads(camp.get_campaign(cid))
    assert "L16" in codes(qa_leads.check_all(data)[pid])


def test_l16_company_domain_suppression_blocks_every_channel(ready_db):
    suppress("domain", "northwind.example")
    cid = make_campaign(channels=("linkedin",))["id"]
    pid = add_person(cid, add_company(cid))  # LinkedIn-only campaign, no email row
    data = qa_leads.load_leads(camp.get_campaign(cid))
    issue = by_code(qa_leads.check_all(data)[pid], "L16")
    assert issue.severity == "block" and "northwind.example" in issue.detail
    # a subdomain of a suppressed domain counts, a sibling domain does not
    ctx = LeadContext.basic(campaign_dict())
    ctx.suppressed_domains = frozenset({"northwind.example"})
    sub = company_row(domain="sg.northwind.example")
    assert "L16" in codes(run(company=sub, context=ctx))
    other = company_row(domain="notnorthwind.example")
    assert "L16" not in codes(run(company=other, context=ctx))


def test_l16_domain_suppression_in_an_email_campaign_without_an_email_row(ready_db):
    suppress("domain", "northwind.example")
    cid = make_campaign(channels=("email", "linkedin"))["id"]
    pid = add_person(cid, add_company(cid))
    data = qa_leads.load_leads(camp.get_campaign(cid))
    found = codes(qa_leads.check_all(data)[pid])
    assert "L16" in found and "L17" in found


def test_l16_email_suppression_is_checked_whatever_the_channels(ready_db):
    suppress("email", "aria.tan@northwind.example")
    cid = make_campaign(channels=("linkedin",))["id"]
    pid = add_person(cid, add_company(cid))
    add_email(pid, address="aria.tan@northwind.example")
    data = qa_leads.load_leads(camp.get_campaign(cid))
    found = codes(qa_leads.check_all(data)[pid])
    assert "L16" in found and "L17" not in found  # email-only codes stay off


# --- L02 duplicates ---------------------------------------------------------------------------


def test_l02_duplicates_by_url_email_and_name_and_company(ready_db):
    cid = make_campaign()["id"]
    co = add_company(cid)
    first = add_person(cid, co, linkedin_url="https://www.linkedin.com/in/aria-tan-0001ab")
    same_url = add_person(
        cid, co, "Aria", "Tan-Lim", linkedin_url="https://sg.linkedin.com/in/aria-tan-0001ab/"
    )
    same_name = add_person(cid, co, linkedin_url="https://www.linkedin.com/in/aria-tan-9999ab")
    sharing = add_person(cid, co, "Marcus", "Lim")
    third = add_person(cid, co, "Devika", "Rao")
    add_email(first, address="shared@northwind.example")
    add_email(sharing, address="Shared@Northwind.example")
    add_email(third, address="devika.rao@northwind.example")
    data = qa_leads.load_leads(camp.get_campaign(cid))
    found = qa_leads.check_all(data)
    assert "L02" not in codes(found[first]) and "L02" not in codes(found[third])
    assert by_code(found[same_url], "L02").severity == "block"
    assert "LinkedIn address" in by_code(found[same_url], "L02").detail
    assert "name and company" in by_code(found[same_name], "L02").detail
    assert "email address" in by_code(found[sharing], "L02").detail


def test_l02_name_case_and_other_company_do_not_clash(ready_db):
    cid = make_campaign()["id"]
    co, other_co = add_company(cid), add_company(cid, "Contoso Foods", "contoso.example")
    a = add_person(cid, co)
    b = add_person(cid, other_co, company="Contoso Foods")
    found = qa_leads.check_all(qa_leads.load_leads(camp.get_campaign(cid)))
    assert "L02" not in codes(found[a]) and "L02" not in codes(found[b])
    c = add_person(cid, co, "ARIA", "TAN")
    found = qa_leads.check_all(qa_leads.load_leads(camp.get_campaign(cid)))
    assert "L02" in codes(found[c])


# --- L11, L12 ---------------------------------------------------------------------------------


def test_l11_name_quality_needs_a_greeting_name():
    assert "L11" not in codes(run())
    no_greeting = person_row(greeting_name="")
    assert by_code(run(no_greeting), "L11").severity == "warn"
    initial = person_row(
        first_name="R", greeting_name="R", raw_name="R Kumarasamy",
        evidence_title=f"R. Kumarasamy - Head of Learning & Development at {COMPANY} | LinkedIn",
    )  # fmt: skip
    assert "L11" in codes(run(initial))
    fixed = person_row(first_name="R", greeting_name="Kumarasamy", raw_name="R Kumarasamy")
    assert "L11" not in codes(run(fixed))
    nameless = person_row(
        first_name="", last_name="", raw_name="", greeting_name="",
        evidence_title=f"LinkedIn Member - Head of Learning at {COMPANY}",
    )  # fmt: skip
    assert "L11" in codes(run(nameless))


def test_l12_title_garbled_or_cut_off_warns():
    assert "L12" not in codes(run())
    for title in ("", "Head of Learning at", "Head of Learning and Devel…"):
        assert by_code(run(person_row(title=title)), "L12").severity == "warn"
    garbled = person_row(
        evidence_title="Aria Tan - View mutual connections | LinkedIn",
        evidence_snippet=f"View mutual connections with Aria · Singapore · {COMPANY}",
    )
    assert "L12" in codes(run(garbled))


# --- L17 to L19, L23: email -------------------------------------------------------------------


def test_l17_email_channel_needs_a_usable_email():
    assert "L17" not in codes(run(email=email_row("verified")))
    assert by_code(run(email=None), "L17").severity == "block"
    for status in ("invalid", "unknown"):
        assert "L17" in codes(run(email=email_row(status)))
    # a LinkedIn-only campaign does not need an email
    assert run(campaign=campaign_dict(channels=["linkedin"])) == []


def test_l17_risky_follows_the_campaign_option():
    assert "L17" in codes(run(email=email_row("risky")))
    allowed = campaign_dict(options={"allow_risky_emails": True, "allow_guess_emails": False})
    assert "L17" not in codes(run(campaign=allowed, email=email_row("risky")))


def test_l23_guess_blocks_sending_unless_allowed():
    off = run(email=email_row("guess"))
    assert by_code(off, "L23").severity == "block"
    assert "L17" not in codes(off)
    allowed = campaign_dict(options={"allow_risky_emails": False, "allow_guess_emails": True})
    on = run(campaign=allowed, email=email_row("guess"))
    assert by_code(on, "L23").severity == "warn" and "L17" not in codes(on)
    assert "L23" not in codes(run(email=email_row("verified")))
    assert "L23" in qa_leads.EMAIL_ONLY_CODES and "L23" in qa_leads.SEND_ONLY_BLOCKS


def test_l18_email_domain_must_match_company_domain_or_alias():
    assert "L18" not in codes(run(email=email_row()))
    assert "L18" not in codes(run(email=email_row(address="aria@mail.northwind.example")))
    other = run(email=email_row(address="aria.tan@gmail.example"))
    assert by_code(other, "L18").severity == "warn"
    alias = company_row(aliases=["northwindgroup.example", "Northwind"])
    assert "L18" not in codes(
        run(company=alias, email=email_row(address="aria@northwindgroup.example"))
    )


def test_l19_role_address_blocks():
    assert "L19" not in codes(run(email=email_row()))
    issue = by_code(run(email=email_row(address="hr@northwind.example")), "L19")
    assert issue.severity == "block"


def test_pick_email_prefers_verified_then_newest():
    rows = [
        {"id": 1, "status": "guess", "email": "a@x.example"},
        {"id": 2, "status": "verified", "email": "b@x.example"},
        {"id": 3, "status": "verified", "email": "c@x.example"},
        {"id": 4, "status": "invalid", "email": "d@x.example"},
    ]
    assert qa_leads.pick_email(rows)["email"] == "c@x.example"
    assert qa_leads.pick_email([]) is None


# --- L20 --------------------------------------------------------------------------------------


def test_l20_language_hint_warns():
    assert "L20" not in codes(run())
    spanish = person_row(
        evidence_snippet=(
            f"Gerente de Recursos Humanos at {COMPANY} · Singapore · Experience: {COMPANY}"
        )
    )
    assert by_code(run(spanish), "L20").severity == "warn"


def test_l20_text_placing_the_person_elsewhere_warns():
    # the country is only confirmed by the link (sg. subdomain), the text says Sydney
    person = person_row(
        linkedin_url="https://sg.linkedin.com/in/aria-tan-1a2b3c",
        evidence_snippet=(
            f"Head of Learning & Development at {COMPANY} · Sydney · Experience: {COMPANY}"
        ),
    )
    issue = by_code(run(person), "L20")
    assert "Australia" in issue.detail and issue.severity == "warn"


# --- L21 and L22 ------------------------------------------------------------------------------


def ai_person(**over):
    return person_row(evidence_origin="ai_web_search", **over)


def test_l21_ai_search_lead_blocks_until_the_spot_check_passes():
    assert "L21" not in codes(run())
    campaign = campaign_dict()
    base = LeadContext.basic(campaign)
    for state, severity in (("incomplete", "block"), ("failed", "block"), ("ok", "warn")):
        base.ai_spot_check = state
        issue = by_code(run(ai_person(), campaign=campaign, context=base), "L21")
        assert issue.severity == severity, state
    base.ai_spot_check = "ok"
    wrong = by_code(run(ai_person(spot_check="wrong"), context=base), "L21")
    assert wrong.severity == "block" and "wrong" in wrong.detail


def test_l21_state_comes_from_the_campaigns_spot_checks(ready_db):
    cid = make_campaign()["id"]
    co = add_company(cid)
    ids = [
        add_person(cid, co, f"Name{i}", f"Lastname{i}", evidence_origin="ai_web_search")
        for i in range(3)
    ]
    state = lambda: qa_leads.load_leads(camp.get_campaign(cid)).context.ai_spot_check  # noqa: E731
    assert state() == "incomplete"
    with db.session() as conn:
        db.update(conn, "people", ids[0], spot_check="pending")
    assert state() == "incomplete"
    with db.session() as conn:
        db.update(conn, "people", ids[0], spot_check="ok")
        db.update(conn, "people", ids[1], spot_check="ok")
    assert state() == "ok"
    with db.session() as conn:
        db.update(conn, "people", ids[1], spot_check="wrong")
    assert state() == "failed"
    api = make_campaign()["id"]
    add_person(api, add_company(api))
    assert qa_leads.load_leads(camp.get_campaign(api)).context.ai_spot_check == ""


def test_l22_slug_must_contain_part_of_the_name():
    assert "L22" not in codes(run())
    wrong = person_row(linkedin_url="https://www.linkedin.com/in/john-smith-77aa11")
    assert by_code(run(wrong), "L22").severity == "warn"  # API lead
    assert (
        by_code(run(wrong.copy() | {"evidence_origin": "ai_web_search"}), "L22").severity == "block"
    )


def test_l22_id_slugs_are_allowed_but_flagged():
    for slug in ("ACoAAB1x2y3z4", "q8w7e6r5t4y3u2i1"):
        person = ai_person(linkedin_url=f"https://www.linkedin.com/in/{slug}")
        issue = by_code(run(person), "L22")
        assert issue.severity == "warn" and "ID" in issue.detail


def test_l22_accents_initials_and_unreadable_names():
    accented = person_row(
        first_name="Zoë", last_name="Müller", greeting_name="Zoë", raw_name="Zoë Müller",
        linkedin_url="https://www.linkedin.com/in/zoe-muller-12ab34",
    )  # fmt: skip
    assert "L22" not in codes(run(accented))
    chinese = person_row(
        first_name="伟", last_name="王", greeting_name="伟", raw_name="王 伟",
        linkedin_url="https://www.linkedin.com/in/wei-wang-5566ab",
    )  # fmt: skip
    assert "L22" not in codes(run(chinese))  # nothing to compare


# --- hand promotion, extension point, context -------------------------------------------------


def test_hand_promoted_person_keeps_blocks_as_warnings():
    weak = person_row(
        **title_snippet("Head of Sales"),
        reasons_json=[
            "L06:role not in the campaign's designations",
            "MANUAL:confirmed: checked page",
        ],
    )
    issue = by_code(run(weak), "L06")
    assert issue.severity == "warn" and "checked page" in issue.detail
    # L03 and L01 are never overridden
    closed = run(weak, company=company_row(status="excluded"))
    assert by_code(closed, "L03").severity == "block"


def test_extra_checks_can_be_registered():
    seen = []

    def extra(person, company, campaign, email, context):
        seen.append(person["id"])
        return [Issue("L99", "warn", "extension")]

    qa_leads.EXTRA_LEAD_CHECKS.append(extra)
    try:
        assert "L99" in codes(run())
    finally:
        qa_leads.EXTRA_LEAD_CHECKS.remove(extra)
    assert seen == [1] and qa_leads.EXTRA_LEAD_CHECKS == []


def test_first_finding_of_a_code_wins_and_blocks_beat_warnings():
    issues = run(person_row(title=""), email=None)
    assert len(codes(issues)) == len(set(codes(issues)))
    assert codes(issues) == sorted(codes(issues))


def test_load_leads_reads_only_confirmed_people_and_their_best_email(ready_db):
    cid = make_campaign()["id"]
    co = add_company(cid)
    good = add_person(cid, co)
    add_email(good, "guess", "g@northwind.example")
    add_email(good, "verified", "v@northwind.example")
    add_person(cid, co, "Marcus", "Lim", status="review")
    data = qa_leads.load_leads(camp.get_campaign(cid))
    assert [p["id"] for p in data.people] == [good]
    assert data.emails[good]["email"] == "v@northwind.example"
    assert qa_leads.check_all(data) == {good: []}


@pytest.mark.parametrize("channels", [["linkedin"], ["email"]])
def test_email_checks_only_run_for_email_campaigns(channels):
    issues = run(campaign=campaign_dict(channels=channels), email=email_row("guess"))
    assert ("L23" in codes(issues)) == ("email" in channels)
