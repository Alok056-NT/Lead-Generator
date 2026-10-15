"""Message checks M01-M16: one pass and one fail case per code, plus the similarity index.

All people and companies are invented. No network is used.
"""

from __future__ import annotations

import time

import pytest
from qa_world import (
    COMPANY,
    PROFILE,
    add_company,
    add_message,
    add_person,
    campaign_dict,
    company_row,
    email_message,
    make_campaign,
    message_row,
    person_row,
)

from leadgen.services import drafting, qa_messages
from leadgen.services.qa_messages import IndexEntry, check_message


def run(message=None, person=None, company=None, campaign=None, offer=None, index=None, others=()):
    return check_message(
        message or message_row(),
        person or person_row(),
        company or company_row(),
        campaign or campaign_dict(),
        offer or PROFILE,
        index,
        companies=list(others),
    )


def codes(issues):
    return [i.code for i in issues]


def by_code(issues, code):
    return next(i for i in issues if i.code == code)


def li(text):
    """A LinkedIn message that greets Aria, mentions the company and ends with a question."""
    return message_row(
        body=f"Hi Aria, I see you lead learning at {COMPANY}. {text} Would that be useful to you?"
    )


def test_clean_messages_have_no_issues():
    assert run() == []
    assert run(email_message()) == []


def test_tables_cover_every_code_and_the_channel_specs_are_reused():
    expected = {f"M{n:02d}" for n in range(1, 17)}
    assert set(qa_messages.MESSAGE_SEVERITY) == expected == set(qa_messages.MESSAGE_TITLES)
    assert qa_messages.drafting.CHANNEL_SPECS is drafting.CHANNEL_SPECS
    with pytest.raises(ValueError):
        run(message_row(channel="fax"))


# --- M01 greeting -----------------------------------------------------------------------------


def test_m01_greeting_uses_the_greeting_name():
    assert "M01" not in codes(run())
    wrong = message_row(body=message_row()["body"].replace("Hi Aria", "Hello Marcus"))
    assert by_code(run(wrong), "M01").severity == "block"
    assert "Aria" in by_code(run(wrong), "M01").detail
    whole_word = person_row(first_name="Al", greeting_name="Al")
    assert "M01" in codes(
        run(message_row(body=message_row()["body"].replace("Aria", "Alan")), whole_word)
    )
    no_name = person_row(first_name="", greeting_name="")
    assert "greeting_name" in by_code(run(person=no_name), "M01").detail


def test_m01_greeting_must_be_on_the_first_line():
    body = "Hi there,\n" + message_row()["body"].replace("Hi Aria,", "Aria, lovely to meet you.")
    assert "M01" in codes(run(message_row(body=body)))


# --- M02 company ------------------------------------------------------------------------------


def test_m02_names_the_company_but_no_other_campaign_company():
    others = [company_row(id=2, name="Contoso Foods", aliases=["Contoso"])]
    assert "M02" not in codes(run(others=others))
    missing = message_row(body=message_row()["body"].replace(COMPANY, "your firm"))
    issue = by_code(run(missing, others=others), "M02")
    assert issue.severity == "warn" and COMPANY in issue.detail
    mixed = message_row(body=message_row()["body"] + " We also help Contoso Foods.")
    blocked = by_code(run(mixed, others=others), "M02")
    assert blocked.severity == "block" and "Contoso Foods" in blocked.detail
    alias_hit = message_row(body=message_row()["body"] + " Like Contoso does.")
    assert by_code(run(alias_hit, others=others), "M02").severity == "block"


def test_m02_accepts_aliases_legal_endings_and_ampersands():
    company = company_row(name="Northwind Logistics Pte Ltd", aliases=["NWL Group"])
    body = message_row()["body"]
    assert "M02" not in codes(run(message_row(body=body), company=company))
    assert "M02" not in codes(
        run(message_row(body=body.replace(COMPANY, "NWL Group")), company=company)
    )
    two = company_row(name="Smith & Rao Freight")
    text = message_row(body=message_row()["body"].replace(COMPANY, "Smith and Rao Freight"))
    assert "M02" not in codes(run(text, company=two))


def test_m02_other_company_inside_own_name_is_not_flagged():
    others = [company_row(id=2, name="Northwind")]
    assert "M02" not in codes(run(others=others))


# --- M03 placeholders -------------------------------------------------------------------------


@pytest.mark.parametrize("needle", ["{{company}}", "[Name]", "<company>", "TODO", "{first}"])
def test_m03_placeholders_in_the_body_block(needle):
    body = message_row()["body"].replace("practical", f"practical {needle}")
    issue = by_code(run(message_row(body=body)), "M03")
    assert issue.severity == "block" and needle[:2] in issue.detail


def test_m03_subject_and_lowercase_todo():
    issue = by_code(run(email_message(subject="Hi [Name]")), "M03")
    assert "subject" in issue.detail
    spanish = message_row(body=message_row()["body"].replace("practical", "todo el"))
    assert "M03" not in codes(run(spanish))


# --- M04 length -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "needle"),
    [
        (message_row(body="Hi Aria, hello. Would that help?"), "Too short"),
        (message_row(body="Hi Aria, " + "x" * 800), "Too long"),
        (message_row(body=""), "empty"),
        (message_row(channel="linkedin_invite", body="Hi Aria, " + "x" * 200), "Too long"),
        (email_message(body="Hi Aria,\n\nShort note. Would that help?"), "Too short"),
        (email_message(body="Hi Aria, " + "word " * 170 + "Would that help?"), "Too long"),
    ],
)
def test_m04_length_limits(message, needle):
    issue = by_code(run(message), "M04")
    assert issue.severity == "block" and needle.lower() in issue.detail.lower()


def test_m04_limits_come_from_the_channel_specs():
    spec = drafting.CHANNEL_SPECS["linkedin_invite"]
    exact = message_row(channel="linkedin_invite", body=("Hi Aria, " + "x" * 400)[: spec.body_max])
    assert "M04" not in codes(run(exact))


# --- M05 forbidden claims ---------------------------------------------------------------------


def test_m05_offer_forbidden_claims_exact_and_shingled():
    assert "M05" not in codes(run())
    exact = li("We promise Guaranteed results every time.")
    assert by_code(run(exact), "M05").severity == "block"
    # three words of a longer forbidden claim in a row
    shingle = li("We cut accidents at all sites too.")
    issue = by_code(run(shingle), "M05")
    assert "cut accidents at" in issue.detail
    reworded = li("We help teams reduce accidents on site.")
    assert "M05" not in codes(run(reworded))


@pytest.mark.parametrize(
    "phrase",
    ["guarantee", "Guaranteed", "100%", "risk-free", "no-brainer", "Act now", "limited time"],
)
def test_m05_global_phrases(phrase):
    text = li(f"This is {phrase} for you.")
    issue = by_code(run(text), "M05")
    assert issue.severity == "block" and "banned phrase" in issue.detail


def test_m05_stopword_only_overlap_is_not_a_hit():
    offer = {**PROFILE, "forbidden_claims": ["We are the best in the world"]}
    assert "M05" not in codes(run(li("We are the people who run courses."), offer=offer))
    offer = {**PROFILE, "forbidden_claims": ["Our expert trainers deliver outstanding results"]}
    assert "M05" in codes(run(li("Our expert trainers will visit."), offer=offer))
    assert "M05" not in codes(run(li("Our new trainers will visit."), offer=offer))


# --- M06 numbers ------------------------------------------------------------------------------


def test_m06_numbers_must_be_in_proof_points():
    assert "M06" not in codes(run(li("We have trained 1,200 managers.")))
    assert "M06" not in codes(run(li("We have trained 1200 managers.")))
    issue = by_code(run(li("We cut costs by 43%.")), "M06")
    assert issue.severity == "block" and "43%" in issue.detail
    assert "M06" in codes(run(li("We have worked with 500 firms.")))


def test_m06_units_and_currency_are_compared():
    offer = {
        **PROFILE,
        "proof_points": [{"claim": "Saved clients S$50,000 and 30%", "source": "x"}],
    }
    assert "M06" not in codes(run(li("We saved S$50,000."), offer=offer))
    assert "M06" not in codes(run(li("We saved $50k."), offer=offer))
    assert "M06" not in codes(run(li("We saved 30 percent."), offer=offer))
    assert "M06" in codes(run(li("That is a 30x gain."), offer=offer))
    assert "M06" in codes(run(li("We have 30 clients."), offer=offer))


def test_m06_ignores_cta_numbers_names_titles_urls_and_ordinals():
    assert "M06" not in codes(run(li("Could we have a 15-minute call?")))
    person = person_row(title="Head of Learning, Region 3")
    assert "M06" not in codes(run(li("I saw you lead Region 3."), person=person))
    company = company_row(name="Route 66 Logistics")
    text = message_row(body=message_row()["body"].replace(COMPANY, "Route 66 Logistics"))
    assert "M06" not in codes(run(text, company=company))
    assert "M06" not in codes(run(li("See example.com/page2024 for the 1st look.")))
    assert "M06" not in codes(run(li("Write to team2@example.com anytime.")))


def test_m06_a_forbidden_claim_wins_over_a_proof_point():
    offer = {
        **PROFILE,
        "proof_points": [{"claim": "We cut accidents at all client sites by 40%", "source": "x"}],
    }
    issues = run(li("Accidents fall by 40% for us."), offer=offer)
    assert "M06" in codes(issues)


# --- M07 brand names --------------------------------------------------------------------------


def test_m07_unknown_capitalised_names_warn():
    assert "M07" not in codes(run())
    issue = by_code(run(li("We worked with Acme Holdings recently.")), "M07")
    assert issue.severity == "warn" and "Acme Holdings" in issue.detail
    # names in the offer, the evidence, the company or the greeting line are fine
    assert "M07" not in codes(run(li("Bright Harbour Training can help.")))
    assert "M07" not in codes(run(li("We work in Singapore and Hong Kong.")))
    assert "M07" not in codes(run(li("Thanks, Priya Nair")))


def test_m07_possessives_and_sign_offs():
    text = li(f"We follow {COMPANY}'s lead.")
    assert "M07" not in codes(run(text))
    assert "M07" not in codes(run(email_message()))


# --- M08 spam words ---------------------------------------------------------------------------


def test_m08_spam_words_warn_on_email_only():
    assert "M08" not in codes(run(email_message()))
    spammy = email_message(body=email_message()["body"].replace("practical", "free discount"))
    issue = by_code(run(spammy), "M08")
    assert issue.severity == "warn" and "free" in issue.detail
    linkedin = li("It is a free taster.")
    assert "M08" not in codes(run(linkedin))
    assert "M08" not in codes(
        run(email_message(body=email_message()["body"].replace("practical", "carefree")))
    )


# --- M09 subject ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "needle"),
    [("", "needs a subject"), ("x" * 61, "61"), ("Re: your note", "Re:"),
     ("FWD: hello", "Fwd"), ("SPECIAL OFFER INSIDE", "ALL CAPS")],
)  # fmt: skip
def test_m09_subject_rules_block(subject, needle):
    issue = by_code(run(email_message(subject=subject)), "M09")
    assert issue.severity == "block" and needle.lower() in issue.detail.lower()


def test_m09_good_subjects_and_other_channels():
    assert "M09" not in codes(run(email_message(subject=("Training " * 7)[:60])))
    assert "M09" not in codes(run(message_row(subject="RE: ignored on linkedin")))


# --- M10 ending -------------------------------------------------------------------------------


def test_m10_ends_with_a_question_or_clear_next_step():
    assert "M10" not in codes(run())
    flat = message_row(
        body=message_row()["body"].replace(
            "Would a short outline be useful to you?", "Thanks for reading."
        )
    )
    assert by_code(run(flat), "M10").severity == "warn"
    cta = message_row(body=flat["body"].replace("Thanks for reading.", "Happy to send an outline."))
    assert "M10" not in codes(run(cta))


def test_m10_ignores_the_sign_off():
    body = message_row()["body"] + "\n\nKind regards,\nPriya"
    assert "M10" not in codes(run(message_row(body=body)))
    flat = message_row(
        body=message_row()["body"].replace("Would a short outline be useful to you?", "Cheers.")
        + "\n\nBest,\nPriya"
    )
    assert "M10" in codes(run(flat))


# --- M11 links --------------------------------------------------------------------------------


@pytest.mark.parametrize("link", ["https://example.com/info", "www.example.com", "example.com/x"])
def test_m11_links_in_linkedin_messages_warn(link):
    issue = by_code(run(li(f"See {link} for more.")), "M11")
    assert issue.severity == "warn"
    invite = message_row(channel="linkedin_invite", body=f"Hi Aria, see {link}? Would that help?")
    assert "M11" in codes(run(invite))


def test_m11_emails_may_contain_links_and_plain_text_passes():
    body = email_message()["body"].replace("Kind regards", "See https://example.com. Kind regards")
    assert "M11" not in codes(run(email_message(body=body)))
    assert "M11" not in codes(run(li("We run L&D courses, e.g. for new managers.")))


# --- M12 near duplicates ----------------------------------------------------------------------


def entry(mid, text, channel="linkedin_message"):
    return IndexEntry(id=mid, channel=channel, text=text.casefold())


def test_m12_near_duplicate_reports_the_most_similar_message():
    message = message_row(id=7)
    same = qa_messages.similarity_text(
        "", message["body"].replace("Aria", "Marcus").replace(COMPANY, "Contoso Foods"),
        "Marcus", company_row(name="Contoso Foods"), "Marcus Lim",
    )  # fmt: skip
    index = {
        1: entry(1, "something completely different " * 10),
        2: entry(2, same),
        7: entry(7, same),  # the message itself is ignored
        3: entry(3, same, channel="email"),  # other channel
    }
    issue = by_code(run(message, index=index), "M12")
    assert issue.severity == "warn" and "message 2" in issue.detail
    assert "M12" not in codes(run(message, index={1: index[1], 3: index[3], 7: index[7]}))


def test_m12_names_do_not_make_messages_different():
    a = qa_messages.similarity_text(
        "", "Hi Aria, hello from Northwind.", "Aria", company_row(name="Northwind"), "Aria Tan"
    )
    b = qa_messages.similarity_text(
        "", "Hi Marcus, hello from Contoso.", "Marcus", company_row(name="Contoso"), "Marcus Lim"
    )
    assert a == b
    c = qa_messages.similarity_text(
        "",
        "Hi Marcus, a very different note about courses.",
        "Marcus",
        company_row(name="Contoso"),
        "",
    )
    assert a != c


def test_m12_threshold_is_092():
    base = ("alpha beta gamma delta " * 12).strip()
    near = base[:-5] + "zeta"  # one short edit
    far = ("alpha beta gamma delta " * 6 + "something else entirely " * 6).strip()
    score, other = qa_messages._most_similar(near, "email", None, {1: entry(1, base, "email")})
    assert other == 1 and score >= qa_messages.SIMILARITY_LIMIT
    score, _ = qa_messages._most_similar(far, "email", None, {1: entry(1, base, "email")})
    assert score < qa_messages.SIMILARITY_LIMIT
    index = {1: entry(1, far, "email")}
    assert "M12" not in codes(run(email_message(id=9), index=index))


def test_m12_is_fast_for_a_big_campaign():
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()

    def note(i):
        return " ".join(words[(i * 7 + j * 3) % 12] + str((i * j) % 17) for j in range(40))

    texts = {i: entry(i, note(i)) for i in range(400)}
    start = time.perf_counter()
    score, other = qa_messages._most_similar(note(5) + " extra", "linkedin_message", None, texts)
    assert other == 5 and score >= 0.92
    # worst case: every message is a copy of the others
    same = {i: entry(i, "Hi NAME, I saw COMPANY lead learning. " * 8 + f"v{i}") for i in range(400)}
    qa_messages._most_similar(
        "Hi NAME, I saw COMPANY lead learning. " * 8, "linkedin_message", None, same
    )
    assert time.perf_counter() - start < 3.0


def test_build_index_reads_messages_and_skips_rejected(ready_db):
    cid = make_campaign()["id"]
    co = add_company(cid)
    a, b, c = (add_person(cid, co, n, "Tan") for n in ("Aria", "Marcus", "Lena"))
    m1 = add_message(a)
    m2 = add_message(b, status="rejected")
    m3 = add_message(c, channel="email")
    index = qa_messages.build_index(cid)
    assert set(index) == {m1, m3} and m2 not in index
    assert index[m3].channel == "email" and "aria" not in index[m1].text


# --- M13 locale -------------------------------------------------------------------------------


def test_m13_locale_spelling():
    assert "M13" not in codes(run())
    us = li("We organize workshops with great color and a new program.")
    issue = by_code(run(us), "M13")
    assert issue.severity == "warn" and "organize" in issue.detail and "organise" in issue.detail
    assert "program" in issue.detail
    gb = li("We organise workshops with great colour and a new programme.")
    assert "M13" not in codes(run(gb))
    assert "M13" in codes(run(gb, campaign=campaign_dict(locale="en-US")))
    assert "M13" not in codes(run(us, campaign=campaign_dict(locale="en-US")))


def test_m13_practise_analyse_centre_and_other_locales():
    us = li("We organize workshops with great color.")
    gb = li("We analyse results and practise with your centre team.")
    us_issue = by_code(run(gb, campaign=campaign_dict(locale="en-US")), "M13")
    assert all(w in us_issue.detail for w in ("analyse", "practise", "centre"))
    verb = li("Managers need to practice listening.")
    assert "M13" in codes(run(verb))
    assert "M13" not in codes(run(li("Good practice matters.")))
    for locale in ("es-MX", "en", "fr-FR", ""):
        assert "M13" not in codes(run(us, campaign=campaign_dict(locale=locale)))
    assert "M13" not in codes(
        run(li("We organize workshops."), campaign=campaign_dict(locale="en-CA"))
    )


def test_m13_words_in_names_are_not_flagged():
    us = campaign_dict(locale="en-US")
    offer = {**PROFILE, "name": "Colour Works Ltd"}
    assert "M13" not in codes(run(li("Colour Works can help."), campaign=us, offer=offer))
    assert "M13" in codes(run(li("Colour Works can help."), campaign=us))


# --- M14 titles -------------------------------------------------------------------------------


def test_m14_role_named_in_the_message_must_match_the_title():
    assert "M14" not in codes(run(li("As Head of Learning and Development you decide courses.")))
    assert "M14" not in codes(run(li("As head of L&D you decide courses.")))
    issue = by_code(run(li("As Head of Operations you decide courses.")), "M14")
    assert issue.severity == "warn" and "Head of Operations" in issue.detail
    assert "M14" in codes(run(li("As Manager of Learning you decide courses.")))
    assert "M14" not in codes(run(li("As Director of Learning you decide courses.")))


def test_m14_ignores_the_senders_own_role_and_chief_titles():
    assert "M14" not in codes(run(li("I am a Partner of Bright Harbour Training.")))
    hr = person_row(title="HR Director")
    assert "M14" not in codes(run(li("As Director of HR you decide hiring."), person=hr))
    ceo = person_row(title="Chief Executive Officer")
    assert "M14" in codes(run(li("As Head of Finance you decide hiring."), person=ceo))


# --- M15 tone ---------------------------------------------------------------------------------


def test_m15_exclamation_emoji_and_shouting():
    assert "M15" not in codes(run(li("We love this!")))
    issue = by_code(run(li("We love this!! Truly!")), "M15")
    assert issue.severity == "warn" and "exclamation" in issue.detail
    assert "emoji" in by_code(run(li("We love this 🚀")), "M15").detail
    assert "ALL CAPS" in by_code(run(li("BIG SAVINGS and GREAT RESULTS today.")), "M15").detail


# --- M16 --------------------------------------------------------------------------------------


def test_m16_review_pending_is_a_block_in_strict_mode_only():
    msg = message_row()
    strict = qa_messages.review_pending_issue(msg, True)
    assert [(i.code, i.severity) for i in strict] == [("M16", "block")]
    assert qa_messages.review_pending_issue(msg, False)[0].severity == "warn"
    assert qa_messages.review_pending_issue({**msg, "ai_review": "pass"}, True) == []
    assert "M16" not in codes(run(msg))  # not part of check_message


# --- drafting integration ---------------------------------------------------------------------


def test_save_drafts_uses_the_full_checks(ready_db):
    cid = make_campaign()["id"]
    co = add_company(cid)
    a, b = add_person(cid, co), add_person(cid, co, "Marcus", "Lim")
    good = message_row()["body"]
    reply = drafting.save_drafts(
        cid,
        [
            {"person_id": a, "channel": "linkedin_message", "body": good},
            {
                "person_id": b,
                "channel": "linkedin_message",
                "body": good.replace("Aria", "Marcus") + " 43%",
            },
        ],
    )
    first, second = reply["results"]
    assert first["status"] == "qa_pass" and second["status"] == "qa_fail"
    assert "M06" in [i["code"] for i in second["issues"]]
    # the second draft is also a near-duplicate of the first, caught in the same call
    assert "M12" in [i["code"] for i in second["issues"]]
    assert drafting.CHECKER == "qa_messages"
    batch = drafting.get_drafting_batch(cid, "linkedin_message")
    assert batch["campaign"]["sectors"] == ["logistics"]
    assert batch["campaign"]["target_designations"]


def test_guide_mentions_the_new_wording():
    guide = " ".join(drafting.guide_text().split())
    assert "unless the campaign sells to that kind of firm" in guide
    assert "strategic partner" in guide and "business partner programme" in guide
