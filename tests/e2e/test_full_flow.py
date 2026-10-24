"""End-to-end test (T19): the whole workflow as an AI would drive it, through the MCP tools.

The world is `universe.py`: one fictional VR training studio, 12 invented companies in
Singapore and the UK, 120 canned search results, canned Hunter and Reoon answers and a fake
mailbox. The real provider classes run against respx routes, so their request and reply
handling is exercised too; nothing touches the network (an unmocked request fails the test).

Two flows run on the same universe:
- `test_api_flow_end_to_end`: Serper-style search, emails, drafting (with planted defects the
  checks must catch), quality check, AI review, three exports, lemlist dry run, readiness,
  send dry run, a fake send through the worker, a fake mailbox sync and analytics.
- `test_ai_search_flow_matches_the_api_flow`: the same universe in AI search mode, including
  three fabricated results that must be blocked and the spot check; its confirmed set must be
  the API flow's confirmed set.
"""

from __future__ import annotations

import functools
import json
import random
from datetime import UTC, timedelta
from email.utils import format_datetime
from pathlib import Path

import anyio
import anyio.from_thread
import pytest
import respx
from mail_world import eml
from mcp import Client
from universe import COMPANIES, SEARCH_RESULTS
from world import (
    SECRETS,
    Clock,
    FakeMailbox,
    all_people,
    draft_channel,
    expected_outcomes,
    install_world,
    new_campaign,
    new_offer,
    read_csv,
    review_all,
    run_chunked,
)

from leadgen import db, mcp_server
from leadgen.core import paths
from leadgen.core.config import Settings, save_settings
from leadgen.send.smtp_imap import SmtpImapMailbox
from leadgen.services import people, sending

# The three exports, spelled out here so that a changed column name fails this test.
LEMLIST_COLUMNS = [
    "email", "firstName", "lastName", "companyName", "jobTitle", "linkedinUrl", "greetingName",
    "designation", "linkedinMessage", "linkedinInvite", "emailSubject", "emailBody",
    "companyDomain", "emailStatus", "evidenceOrigin",
]  # fmt: skip
GENERIC_COLUMNS = [
    "personId", "firstName", "lastName", "greetingName", "jobTitle", "designation", "seniority",
    "country", "companyName", "companyDomain", "website", "sector", "linkedinUrl", "email",
    "emailStatus", "evidenceOrigin", "evidenceSnippet", "linkedinInvite", "linkedinMessage",
    "emailSubject", "emailBody",
]  # fmt: skip
INSTANTLY_COLUMNS = [
    "email", "first_name", "last_name", "company_name", "personalization", "website",
    "job_title", "linkedin_url", "greeting", "designation", "follow_up_dm", "invite_note",
    "subject_line", "message_body", "address_status", "evidence_origin",
]  # fmt: skip
QA_REPORT_COLUMNS = [
    "person_id", "firstName", "lastName", "companyName", "jobTitle", "linkedinUrl",
    "evidence_origin", "spot_check", "person_status", "exported", "email", "email_status",
    "messages", "blocking_issues", "warnings", "ai_verdict_notes", "review_reasons",
]  # fmt: skip

CONFIRMED, REVIEW = 77, 40  # what the confirmation rules make of the 117 real-looking results
FABRICATED = [r for rows in SEARCH_RESULTS.values() for r in rows if r.get("fabricated")]


# --- fixtures -------------------------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch):
    """The keys, DNS, domain checks and the Serper/Hunter/Reoon routes of the universe."""
    with respx.mock(assert_all_called=False) as router:
        yield install_world(monkeypatch, router)


@pytest.fixture
def mcp(world):
    """`call(tool, **args)` runs a tool through the in-memory MCP client and returns its JSON.

    Every reply is kept in `call.transcript` so a test can search all of it for secrets.
    """
    server = mcp_server.build_server()
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(server)) as client:
            transcript: list[str] = []

            def call(tool, /, **args):
                result = portal.call(client.call_tool, tool, args)
                assert not result.is_error, result.content
                text = result.content[0].text
                transcript.append(text)
                return json.loads(text)

            call.transcript = transcript
            yield call


# --- small readers --------------------------------------------------------------------------------


def sql(query: str, params: tuple = ()) -> list[dict]:
    with db.session() as conn:
        return db.query(conn, query, params)


def country_of(address: str) -> str:
    domain = address.split("@", 1)[1]
    return next(c["country_code"] for c in COMPANIES if c["domain"] == domain)


def assert_no_secrets(*texts: str) -> None:
    for text in texts:
        for value in SECRETS.values():
            assert value not in text


# --- the API flow ---------------------------------------------------------------------------------


def test_api_flow_end_to_end(mcp, world, monkeypatch):
    # 0. status: a new workspace points at the offer; the three keys are set and masked
    status = mcp("get_status")
    assert status["next_step"]["tool"] == "create_project"  # work starts with a project
    keys = {k["provider"]: k for k in status["keys"]}
    assert all(keys[p]["configured"] for p in ("serper", "hunter", "reoon"))
    assert not keys["lemlist"]["configured"]
    assert keys["serper"]["masked_key"] == "…" + SECRETS["SERPER_API_KEY"][-4:]

    # 1. offer: the two [CONFIRM ...] lines are reported, the profile is stored
    offer_id, ingested = new_offer(mcp)
    assert ingested["unconfirmed_count"] == 2 and ingested["next_cursor"] is None
    assert all("[CONFIRM" in line for line in ingested["unconfirmed_snippets"])
    assert mcp("get_offer", offer_id=offer_id)["profile"]["name"] == "Lanternfish VR"

    # 2. campaign (email + linkedin), companies added and approved
    cid = new_campaign(mcp, offer_id, "Lanternfish SG+UK", channels=("email", "linkedin"))
    campaign = mcp("get_campaign", campaign_id=cid)
    assert campaign["channels"] == ["email", "linkedin"]
    assert campaign["counts"]["companies"] == {"candidate": 0, "approved": 12, "excluded": 0}
    listed = mcp("list_companies", campaign_id=cid, status="approved")
    assert listed["total"] == 12 and all(c["domain_ok"] for c in listed["companies"])

    # 3. people: the estimate asks for a yes, nothing is spent until it is given
    estimate = mcp("estimate_people_search", campaign_id=cid)
    assert (estimate["searches"], estimate["credits_needed"]) == (24, 24)
    assert estimate["needs_confirmation"] is True
    held = mcp("find_people", campaign_id=cid)
    assert held.get("needs_confirmation") is True and world.calls["serper"] == 0
    # force small chunks (24 searches would fit one call): several calls chained by next_cursor
    monkeypatch.setattr(
        people, "find_people", functools.partial(people.find_people, max_searches_per_call=10)
    )
    found = run_chunked(mcp, "find_people", cid, confirm=True)
    assert [r["searches_done"] for r in found] == [10, 10, 4]
    assert [r["next_cursor"] for r in found] == ["10", "20", None]
    assert [r["done"] for r in found] == [False, False, True]
    assert found[-1]["campaign_people"] == {
        "confirmed": CONFIRMED, "review": REVIEW, "rejected": 0, "removed": 0, "reserve": 0,
    }  # fmt: skip
    assert world.calls["serper"] == estimate["searches"]
    confirmed = all_people(mcp, cid, "confirmed")
    review = all_people(mcp, cid, "review")
    assert (len(confirmed), len(review)) == (CONFIRMED, REVIEW)
    by_name = {p["raw_name"]: p for p in confirmed + review}
    assert by_name["Mira Tan"]["status"] == "confirmed"
    assert set(by_name["Ravi Menon"]["reasons"]) >= {
        "L07:below manager level", "L14:junior keyword in the title",
    }  # fmt: skip
    assert by_name["Joy Lim"]["status"] == "review"
    actual = {p["linkedin_url"]: "confirm" for p in confirmed}
    for person in review:
        codes = sorted(reason.split(":")[0] for reason in person["reasons"])
        actual[person["linkedin_url"]] = " ".join(["review", *codes])
    assert actual == expected_outcomes()  # every result got the outcome the universe expects
    stored_urls = {p["linkedin_url"] for p in confirmed + review}
    assert not stored_urls & {r["link"] for r in FABRICATED}  # Google never returns them
    again = run_chunked(mcp, "find_people", cid, confirm=True)  # the cache answers this one
    assert sum(r["cache_hits"] for r in again) == 24 and world.calls["serper"] == 24

    # 4. emails: estimate, a yes, chunks of 25 with a cursor
    email_estimate = mcp("estimate_email_search", campaign_id=cid)
    assert email_estimate["people"] == CONFIRMED and email_estimate["domains"] == 12
    assert email_estimate["needs_confirmation"] is True
    assert mcp("find_emails", campaign_id=cid).get("needs_confirmation") is True
    chunks = run_chunked(mcp, "find_emails", cid, confirm=True)
    assert [c["people_done"] for c in chunks] == [25, 25, 25, 2]
    assert chunks[-1]["campaign_emails"] == {
        "verified": 56, "risky": 12, "guess": 4, "invalid": 5, "unknown": 0,
    }  # fmt: skip
    assert world.unexpected == []  # the universe had an answer for every question
    assert world.calls["hunter:domain-search"] == 12  # one pattern lookup per domain
    verifications = world.calls["reoon"] + world.calls["hunter:email-verifier"]
    assert verifications <= email_estimate["verify_credits"]

    # 5. drafting with planted defects: the instant checks must catch each one
    planted_li = {0: "no_greeting", 1: "placeholder", 2: "forbidden", 3: "number", 4: "too_long"}
    planted_li |= {5: "hype", 6: "hype", 7: "hype"}  # only the AI review can catch these
    linkedin = draft_channel(mcp, cid, "linkedin_message", planted_li)
    assert sorted(code for _, codes in linkedin["failed"] for code in codes) == [
        "M01", "M03", "M04", "M05", "M06",
    ]  # fmt: skip
    assert linkedin["saved"] == CONFIRMED
    planted_em = {0: "bad_subject", 1: "forbidden", 2: "hype", 3: "hype"}
    email = draft_channel(mcp, cid, "email", planted_em)
    assert sorted(code for _, codes in email["failed"] for code in codes) == ["M05", "M09"]
    assert email["saved"] == 56  # only people with a verified address get an email
    counts = mcp("get_campaign", campaign_id=cid)["counts"]["messages"]
    assert counts["qa_pass"] == CONFIRMED + 56 and counts["qa_fail"] == 0

    # 6. quality check: no blocks left but the missing AI review; nothing may leave yet
    qa = mcp("run_qa", campaign_id=cid)
    assert qa["people"]["with_blocks"] == 0
    assert qa["messages"]["awaiting_ai_review"] == CONFIRMED + 56
    assert {c for c, n in qa["counts"].items() if n["block"]} == {"L17", "L23", "M16"}
    assert qa["counts"]["L17"]["block"] == 17 and qa["counts"]["L23"]["block"] == 4
    assert mcp("export_gate", campaign_id=cid)["messages"]["exportable"] == 0

    # 7. the AI review loop: pass, fix (hype, planted) and one reject (a recruiter)
    verdicts = review_all(mcp, cid)
    assert verdicts["reject"] == 1 and verdicts["fix"] == 5
    qa = mcp("run_qa", campaign_id=cid)
    assert qa["messages"]["awaiting_ai_review"] == 0
    assert qa["messages"]["approved"] == CONFIRMED + 56 - 2
    assert qa["people"]["checked"] == CONFIRMED - 1  # the rejected person left the campaign
    assert {c for c, n in qa["counts"].items() if n["block"]} == {"L17", "L23"}
    gate = mcp("export_gate", campaign_id=cid)
    assert gate["leads"]["exportable"] == CONFIRMED - 1 and gate["messages"]["blocked"] == 1

    # 8. the three exports: exact columns, nothing blocked, every message has an AI pass
    passed = {m["body"] for m in sql("SELECT body FROM messages WHERE ai_review = 'pass'")}
    removed_people = sql("SELECT linkedin_url FROM people WHERE status != 'confirmed'")
    forbidden_urls = {p["linkedin_url"] for p in removed_people} | {r["link"] for r in FABRICATED}
    export_files: list[Path] = []
    lemlist_rows: list[dict[str, str]] = []
    for fmt, columns, expected_rows in (
        ("lemlist", LEMLIST_COLUMNS, CONFIRMED - 1),
        ("generic", GENERIC_COLUMNS, CONFIRMED - 1),
        ("instantly", INSTANTLY_COLUMNS, 59),  # Instantly needs an address: 17 people have none
    ):
        exported = mcp("export_campaign", campaign_id=cid, format=fmt)
        assert exported["exported"] == expected_rows and exported["not_reviewed"] == 0
        assert exported["evidence_origin_counts"] == {"api": expected_rows}
        header, rows = read_csv(exported["path"])
        assert header == columns
        assert len(rows) == expected_rows
        export_files.append(Path(exported["path"]))
        url_key = "linkedin_url" if fmt == "instantly" else "linkedinUrl"
        assert not {r[url_key] for r in rows} & forbidden_urls  # no review/removed/fake person
        text_keys = ("emailBody", "linkedinMessage", "message_body", "follow_up_dm")
        for row in rows:
            for key in text_keys:
                if row.get(key):
                    assert row[key] in passed, f"{fmt}: a message without an AI pass was exported"
        if fmt == "lemlist":
            lemlist_rows = rows
            report = read_csv(exported["qa_report_path"])
            assert report[0] == QA_REPORT_COLUMNS
            assert len(report[1]) == CONFIRMED + REVIEW  # every person is accounted for
            export_files += [Path(exported["qa_report_path"]), Path(exported["removed_path"])]
    assert "recruit" not in " ".join(r["jobTitle"].lower() for r in lemlist_rows)
    assert {r["emailStatus"] for r in lemlist_rows} == {"verified", "guess", ""}  # no risky/invalid
    assert sum(1 for r in lemlist_rows if not r["email"]) == 17
    guess_rows = [r for r in lemlist_rows if r["emailStatus"] == "guess"]
    assert len(guess_rows) == 4 and all(not r["emailBody"] for r in guess_rows)
    removed_csv = read_csv(Path(exported["removed_path"]))[1]
    assert len(removed_csv) == 1  # the one rejected person

    # 9. lemlist: a dry run only (no key, no HTTP), a preview of three payloads
    dry = mcp("push_to_lemlist", campaign_id=cid, lemlist_campaign_id="cam_e2e000001")
    assert dry["dry_run"] is True and dry["ready"] == CONFIRMED - 1
    assert dry["to_push"] == 55 and dry["no_email_for_lemlist_api"] == 17
    assert len(dry["preview"]) == 3
    assert not [c for c in world.router.calls if "lemlist" in str(c.request.url)]

    # 10. readiness: compliance first, then ready
    box = FakeMailbox()
    monkeypatch.setattr(SmtpImapMailbox, "from_config", classmethod(lambda cls: box))
    clock = Clock()
    monkeypatch.setattr(sending, "_utcnow", clock)
    monkeypatch.setattr(sending, "_local_tz", lambda: UTC)
    monkeypatch.setattr(sending, "_spawn_worker", lambda: True)
    settings = Settings()
    settings.sender.name, settings.sender.role = "Noor Haddad", "Studio Lead"
    settings.sender.company = "Lanternfish VR"
    settings.postal_address = "1 Example Road, Singapore 000000"
    save_settings(settings)
    readiness = mcp("check_send_readiness", campaign_id=cid)
    assert readiness["ready"] is False
    assert readiness["missing"] == ["compliance:SG", "compliance:GB"]
    assert mcp("send_emails", campaign_id=cid, limit=5, dry_run=False, confirm=True)["ok"] is False
    for country in ("SG", "GB"):
        assert mcp("acknowledge_compliance", campaign_id=cid, country=country)["ok"]
    assert mcp("check_send_readiness", campaign_id=cid)["ready"] is True

    # 11. send: a dry run touches no mailbox; a confirmed run only queues; the worker sends
    preview = mcp("send_emails", campaign_id=cid, limit=12)
    assert preview["dry_run"] is True and preview["count"] == 12 and box.sent == []
    for item in preview["emails"]:
        assert item["email_status"] == "verified"
        assert item["subject"].startswith("<ADV> ") == (country_of(item["to"]) == "SG")
        assert "1 Example Road, Singapore 000000" in item["body"]
        assert "reply 'unsubscribe'" in item["body"]
    queued = mcp("send_emails", campaign_id=cid, limit=12, dry_run=False, confirm=True)
    assert queued["queued"] == 12 and queued["worker_started"] is True and box.sent == []
    outcome = sending.run_worker(mailbox=box, sleep=clock.sleep, rng=random.Random(5))
    assert outcome == {"started": True, "sent": 12, "reason": "queue_empty"}
    assert len(box.sent) == 12 and len({m["to"] for m in box.sent}) == 12
    verified = {e["email"] for e in sql("SELECT email FROM emails WHERE status = 'verified'")}
    assert {m["to"] for m in box.sent} <= verified
    elapsed = (clock.now - clock.start).total_seconds()
    assert 11 * 60 <= elapsed <= 11 * 180 + 1  # a random 60-180 second gap between sends
    assert sql("SELECT COUNT(*) AS n FROM messages WHERE status = 'sent'")[0]["n"] == 12

    # 12. the fake mailbox: a reply, an unsubscribe, an out-of-office and a bounce
    sent = box.sent
    stamp = format_datetime(clock.now + timedelta(hours=1))  # an hour after the last send
    box.add_mail(
        eml(
            sent[0]["to"], "Re: " + sent[0]["subject"],
            "Thanks, this looks interesting. Can you send an example?",
            in_reply_to=sent[0]["message_id"], date=stamp,
        ).decode()
    )  # fmt: skip
    box.add_mail(
        eml(
            sent[1]["to"], "Re: " + sent[1]["subject"],
            "Please unsubscribe me from these emails.",
            in_reply_to=sent[1]["message_id"], date=stamp,
        ).decode()
    )  # fmt: skip
    box.add_mail(
        eml(
            sent[2]["to"], "Out of office: " + sent[2]["subject"],
            "I am out of the office until Monday.", in_reply_to=sent[2]["message_id"],
            extra_headers="Auto-Submitted: auto-replied", date=stamp,
        ).decode()
    )  # fmt: skip
    box.add_mail("\n".join([
        "From: Mail Delivery System <MAILER-DAEMON@example.org>",
        f"To: {box.user}",
        f"Date: {stamp}",
        f"Subject: Undeliverable: {sent[3]['subject']}",
        "Message-ID: <bounce-e2e@example.org>",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "",
        "The recipient was:",
        "",
        f"    <{sent[3]['to']}>: host mx.example.org said:",
        "    550 5.1.1 User unknown (in reply to RCPT TO command)",
        "",
        "Your original message follows:",
        f"Message-ID: {sent[3]['message_id']}",
        "",
    ]))  # fmt: skip
    synced = mcp("sync_mailbox")
    assert synced["done"] is True and synced["scanned"] == 4 and synced["unmatched"] == 0
    assert (synced["replies"], synced["auto_replies"]) == (1, 1)
    assert (synced["bounces"], synced["unsubscribes"]) == (1, 1)
    assert set(box.imap.commands) == {"EXAMINE", "SEARCH", "FETCH"}  # read-only
    do_not_contact = {r["value"] for r in sql("SELECT value FROM suppression WHERE kind = 'email'")}
    assert do_not_contact == {sent[1]["to"], sent[3]["to"]}
    bounced = sql("SELECT status FROM emails WHERE email = ?", (sent[3]["to"],))
    assert bounced[0]["status"] == "invalid"
    blocked = mcp("export_gate", campaign_id=cid, purpose="send")["blocked_by"]
    assert blocked.get("L16", 0) >= 2  # both addresses are now do-not-contact

    # 13. analytics
    totals = mcp("get_analytics", campaign_id=cid)["totals"]
    assert (totals["sent"], totals["bounced"], totals["delivered"]) == (12, 1, 11)
    assert (totals["replied"], totals["unsubscribed"], totals["auto_replied"]) == (1, 1, 1)
    assert totals["small_sample"] is True
    grouped = mcp("get_analytics", campaign_id=cid, group_by="designation")
    assert sum(g["sent"] for g in grouped["groups"]) == 12

    # provider budgets: every call was estimated, asked for and counted
    usage = {p["key"]: p["usage_month"]["credits"] for p in mcp("list_providers")["providers"]}
    assert usage["serper"] == 24 <= 300  # the default search budget of a campaign
    assert usage["hunter"] <= 25 and usage["reoon"] <= 200  # the default hunter / verify budgets
    assert world.unexpected == []

    # no secret anywhere: replies, exports, the log file
    log_text = ""
    log_dir = paths.logs_dir()
    for log in log_dir.glob("*.log*"):
        log_text += log.read_text(encoding="utf-8", errors="replace")
    exported_text = [f.read_text(encoding="utf-8-sig") for f in export_files]
    assert_no_secrets("\n".join(mcp.transcript), log_text, *exported_text)
    assert mcp("get_status")["campaigns"][0]["next_step"]["tool"]

    # 14. the project remembers everyone found, exported and emailed ...
    project_id = campaign["project_id"]
    channels = {
        r["channel"]: r["n"]
        for r in sql(
            "SELECT channel, COUNT(*) AS n FROM contact_log WHERE project_id = ? GROUP BY channel",
            (project_id,),
        )
    }
    assert channels["email_sent"] == 12 and channels["exported_csv"] > 0 and channels["found"] > 0
    project = mcp("get_project", project_id=project_id)
    assert project["counts"]["contacted_in_cooldown"] > 0
    # ... so a second campaign of the same project (its searches are cached, so they are free)
    # is not offered the same people again: they wait in the review list with L24 or L25.
    # (Someone the first campaign rejected is not remembered, so may be found again.)
    serper_calls = world.calls["serper"]
    again = new_campaign(
        mcp, offer_id, "Lanternfish second wave", channels=("linkedin",), project_id=project_id
    )
    run_chunked(mcp, "find_people", again, confirm=True)
    assert world.calls["serper"] == serper_calls
    now_confirmed = {
        r["raw_name"]
        for r in sql("SELECT raw_name FROM people WHERE campaign_id = ? AND status = 'confirmed'",
                     (again,))  # fmt: skip
    }
    first_confirmed = {
        r["raw_name"]
        for r in sql("SELECT raw_name FROM people WHERE campaign_id = ? AND status = 'confirmed'",
                     (cid,))  # fmt: skip
    }
    assert now_confirmed.isdisjoint(first_confirmed)
    held = sql(
        "SELECT reasons_json FROM people WHERE campaign_id = ? AND status = 'review'", (again,)
    )
    codes = [r["reasons_json"][0].split(":")[0] for r in held]
    assert codes.count("L25") > 0  # in another active campaign of the project
    assert mcp("get_status")["projects"][0]["campaigns"] == 2


# --- the AI search flow ---------------------------------------------------------------------------


def test_ai_search_flow_matches_the_api_flow(mcp, world):
    offer_id, _ = new_offer(mcp)

    # AI mode on a fresh cache: every pair becomes a task the fake AI answers
    ai = new_campaign(mcp, offer_id, "Lanternfish AI", channels=("linkedin",), search_mode="ai")
    refused = mcp("find_people", campaign_id=ai, confirm=True)
    assert refused["ok"] is False and refused["use_ai_search"] is True
    assert world.calls["serper"] == 0

    submitted = 0
    blocked_fabricated = []
    cursor = None
    while True:
        batch = mcp(
            "get_search_tasks", campaign_id=ai, kind="people",
            **({} if cursor is None else {"cursor": cursor}),
        )  # fmt: skip
        assert len(batch["tasks"]) <= 10
        for task in batch["tasks"]:
            rows = SEARCH_RESULTS[f"{task['company']}|{task['designation_group']}"]
            results = [
                {"title": r["title"], "url": r["link"], "snippet": r["snippet"]} for r in rows
            ]
            reply = mcp(
                "submit_search_results", task_id=task["task_id"], results=results,
                tool_name="web_search",
            )  # fmt: skip
            assert reply["ok"] and reply["result_count"] == len(rows)
            submitted += 1
            for row, outcome in zip(rows, reply["results"], strict=True):
                if row.get("fabricated"):
                    blocked_fabricated.append(outcome)
        if not batch.get("next_cursor"):
            break
        cursor = batch["next_cursor"]
    assert submitted == 24
    assert len(blocked_fabricated) == 3
    for outcome in blocked_fabricated:
        assert outcome["outcome"] != "confirmed"
        assert any(reason.startswith("L22") for reason in outcome["reasons"])

    ai_confirmed = all_people(mcp, ai, "confirmed")
    ai_review = all_people(mcp, ai, "review")
    assert len(ai_confirmed) == CONFIRMED and len(ai_review) == REVIEW + 3
    assert {p["linkedin_url"] for p in ai_review} >= {r["link"] for r in FABRICATED}
    assert {p["evidence_origin"] for p in ai_confirmed} == {"ai_web_search"}

    # the same universe through the paid-search path gives the same confirmed people
    api = new_campaign(mcp, offer_id, "Lanternfish API", channels=("linkedin",))
    run_chunked(mcp, "find_people", api, confirm=True)
    api_confirmed = all_people(mcp, api, "confirmed")
    assert {p["linkedin_url"] for p in ai_confirmed} == {p["linkedin_url"] for p in api_confirmed}
    assert {p["linkedin_url"] for p in ai_review} - {p["linkedin_url"] for p in
        all_people(mcp, api, "review")} == {r["link"] for r in FABRICATED}  # fmt: skip

    # an AI-mode campaign created AFTER the API run finds its searches in the shared cache: the
    # cached results are loaded for free into the new campaign (evidence origin api), not hidden
    late = new_campaign(mcp, offer_id, "Lanternfish AI late", channels=("linkedin",),
                        search_mode="ai")  # fmt: skip
    batch = mcp("get_search_tasks", campaign_id=late, kind="people")
    assert batch["filled_from_cache"] == 24 and batch["tasks_created"] == 0
    assert batch["tasks"] == [] and batch["open_total"] == 0
    late_confirmed = all_people(mcp, late, "confirmed")
    assert len(late_confirmed) == CONFIRMED
    assert {p["linkedin_url"] for p in late_confirmed} == {p["linkedin_url"] for p in api_confirmed}
    assert {p["evidence_origin"] for p in late_confirmed} == {"api"}
    assert len(all_people(mcp, late, "review")) == REVIEW
    assert world.calls["serper"] == 24  # the cache answered; no credit was spent

    # until the spot check is done, no AI-search lead may leave
    qa = mcp("run_qa", campaign_id=ai)
    assert qa["counts"]["L21"]["block"] == CONFIRMED
    assert mcp("export_gate", campaign_id=ai)["leads"]["exportable"] == 0

    sample = mcp("get_spot_check", campaign_id=ai)
    assert sample["needed"] is True and sample["sample_size"] == 5
    answers = [{"person_id": p["person_id"], "verdict": "ok", "note": ""} for p in sample["people"]]
    recorded = mcp("record_spot_check", campaign_id=ai, results=answers)
    assert recorded["ok"]
    qa = mcp("run_qa", campaign_id=ai)
    assert qa["counts"]["L21"]["block"] == 0 and qa["counts"]["L21"]["warn"] == CONFIRMED

    # the rest of the workflow works on AI-search leads and labels where they came from
    drafted = draft_channel(mcp, ai, "linkedin_message")
    assert drafted["saved"] == CONFIRMED and drafted["failed"] == []
    review_all(mcp, ai)
    exported = mcp("export_campaign", campaign_id=ai, format="lemlist")
    assert exported["exported"] == CONFIRMED - 1  # the recruiter was rejected in review
    assert exported["evidence_origin_counts"] == {"ai_web_search": CONFIRMED - 1}
    header, rows = read_csv(exported["path"])
    assert {r["evidenceOrigin"] for r in rows} == {"ai_web_search"}
    assert not {r["linkedinUrl"] for r in rows} & {r["link"] for r in FABRICATED}
    assert_no_secrets("\n".join(mcp.transcript))
    assert world.calls["serper"] == 24  # only the API campaign searched
