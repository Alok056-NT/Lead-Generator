"""MCP server (T13): tool catalogue, error shapes, a mini flow, secrets, transports.

Everything is invented and nothing touches the network: search uses `FakeSearchProvider` and
HTTP calls are blocked by respx. Tools are called through the SDK's in-memory client.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

import anyio
import anyio.from_thread
import pytest
import respx
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from qa_world import PROFILE
from starlette.testclient import TestClient

from leadgen import cli, mcp_server
from leadgen.core import config, secrets
from leadgen.providers import registry
from leadgen.providers.base import ProviderError
from leadgen.providers.fake import FakeSearchProvider
from leadgen.providers.search import SearchResult
from leadgen.services import companies as companies_svc
from leadgen.services import guide
from leadgen.services import lemlist as lemlist_svc

# Tools in PLAN section 10 (1-40), plus the extra tools the follow-ups asked for.
PLAN_TOOLS = {
    "get_status", "get_workflow_guide", "list_providers", "save_api_key", "ingest_offer_source",
    "save_offer_profile", "get_offer", "list_designation_templates", "create_campaign",
    "update_campaign", "get_campaign", "list_campaigns", "search_web", "add_companies",
    "list_companies", "set_company_status", "estimate_people_search", "find_people",
    "list_people", "set_person_status", "estimate_email_search", "find_emails",
    "get_drafting_batch", "save_drafts", "run_qa", "list_qa_issues", "get_review_batch",
    "record_review", "export_campaign", "push_to_lemlist", "check_send_readiness",
    "send_emails", "sync_mailbox", "get_analytics", "add_suppression", "open_dashboard",
    "get_search_tasks", "submit_search_results", "get_spot_check", "record_spot_check",
}  # fmt: skip
EXTRA_TOOLS = {
    "approve_companies",
    "recheck_people",
    "export_gate",
    "list_lemlist_campaigns",
    "skip_task",
    "acknowledge_compliance",
}
STUBS = {
    "sync_mailbox": {},
    "get_analytics": {"campaign_id": 1},
    "add_suppression": {"values": ["x@example.com"], "kind": "email"},
    "open_dashboard": {},
}
COMPANY = "Northwind Logistics"


@pytest.fixture(autouse=True)
def no_real_http():
    """Any HTTP request a test did not mock fails, so tests never use the network."""
    with respx.mock:
        yield


@pytest.fixture
def server():
    return mcp_server.build_server()


@pytest.fixture
def mcp(server):
    """`call(name, **args)` runs a tool through an in-memory client and returns its JSON."""
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(server)) as client:

            def call(tool, /, **args):
                result = portal.call(client.call_tool, tool, args)
                assert not result.is_error, result.content
                return json.loads(result.content[0].text)

            call.client = client
            call.portal = portal
            yield call


def error_code(reply):
    assert reply["ok"] is False
    return reply["error"]["code"]


# --- catalogue, descriptions, annotations -------------------------------------------------------


def tools_by_name(mcp):
    return {t.name: t for t in mcp.portal.call(mcp.client.list_tools).tools}


def test_tool_names_match_the_plan(mcp):
    names = set(tools_by_name(mcp))
    assert PLAN_TOOLS <= names
    assert names == PLAN_TOOLS | EXTRA_TOOLS


def test_every_tool_is_described_and_annotated(mcp):
    for name, tool in tools_by_name(mcp).items():
        assert len(tool.description) > 80, name
        assert tool.annotations is not None and tool.annotations.title, name
        assert tool.annotations.read_only_hint is not None, name


def test_annotations_mark_reading_spending_and_sending(mcp):
    tools = tools_by_name(mcp)
    for name in ("get_status", "list_people", "estimate_people_search", "get_review_batch"):
        assert tools[name].annotations.read_only_hint is True, name
    for name in ("find_people", "find_emails", "search_web", "save_api_key", "push_to_lemlist"):
        note = tools[name].annotations
        assert note.read_only_hint is False and note.open_world_hint is True, name
    assert tools["push_to_lemlist"].annotations.destructive_hint is True
    assert tools["send_emails"].annotations.destructive_hint is True
    assert tools["find_people"].annotations.destructive_hint is False


def test_tool_schemas_expose_the_follow_up_arguments(mcp):
    tools = tools_by_name(mcp)
    push = tools["push_to_lemlist"].input_schema["properties"]
    assert "allow_incomplete" in push and "dry_run" in push and "confirm" in push
    # The AI cannot switch off the failed AI-search spot check through update_campaign.
    update = tools["update_campaign"].input_schema["properties"]
    assert "ai_search_needs_recheck" not in update and "options" not in update
    assert "search_mode" in update


def test_confirm_defaults_to_false(mcp):
    tools = tools_by_name(mcp)
    for name in ("find_people", "find_emails", "push_to_lemlist", "search_web", "send_emails"):
        assert tools[name].input_schema["properties"]["confirm"]["default"] is False, name


def test_tool_summaries_and_cli_tools(capsys):
    summaries = mcp_server.tool_summaries()
    assert {name for name, _ in summaries} == PLAN_TOOLS | EXTRA_TOOLS
    assert all(text.endswith(".") and len(text) < 300 for _, text in summaries)
    assert cli.main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "get_status" in out and "push_to_lemlist" in out


# --- prompt and guide ---------------------------------------------------------------------------


def test_prompt_is_the_workflow_guide(mcp):
    prompts = mcp.portal.call(mcp.client.list_prompts).prompts
    assert [p.name for p in prompts] == ["lead_generation"]
    got = mcp.portal.call(mcp.client.get_prompt, "lead_generation")
    assert got.messages[0].content.text == guide.workflow_text()


def test_workflow_guide_covers_the_card():
    text = guide.workflow_text()
    for needle in (
        "get_status", "confirm=true", "20 rows", "Never fabricate", "next_step",
        "leadgen keys set", "allow_incomplete", "messages_incomplete_hint", "recheck_people",
        "get_spot_check", "section=\"ai_search\"", "not_available_yet",
    ):  # fmt: skip
        assert needle in text, needle


def test_get_workflow_guide_sections(mcp):
    default = mcp("get_workflow_guide")
    assert default["text"] == guide.workflow_text()
    assert {s["name"] for s in default["available_sections"]} == set(guide.SECTIONS)
    assert "AI search mode" in mcp("get_workflow_guide", section="ai_search")["text"]
    assert (
        "How to write the messages" in mcp("get_workflow_guide", section="messaging_style")["text"]
    )
    everything = mcp("get_workflow_guide", section="all")
    assert everything["sections_included"] == list(guide.SECTIONS)
    assert len(everything["text"]) > 20000  # whole guides are never shortened
    bad = mcp("get_workflow_guide", section="nope")
    assert error_code(bad) == "unknown_section"


# --- status -------------------------------------------------------------------------------------


def test_get_status_on_a_new_workspace(mcp):
    secrets.set("SERPER_API_KEY", "sk-serper-secret-0123456789")
    reply = mcp("get_status")
    assert reply["ok"] and reply["campaigns"] == [] and reply["offers"] == []
    assert reply["next_step"]["tool"] == "ingest_offer_source"
    serper = next(k for k in reply["keys"] if k["provider"] == "serper")
    assert serper["configured"] is True and serper["masked_key"].endswith("6789")
    assert "sk-serper-secret-0123456789" not in json.dumps(reply)
    assert reply["workspace"] and reply["version"]


def test_next_step_follows_the_work(mcp):
    ingested = mcp("ingest_offer_source", text="We run practical training courses for managers.")
    assert mcp("get_status")["next_step"]["tool"] == "save_offer_profile"
    mcp("save_offer_profile", offer_id=ingested["offer_id"], profile=PROFILE)
    assert mcp("get_status")["next_step"]["tool"] == "create_campaign"
    campaign = make_campaign(mcp, ingested["offer_id"])
    step = mcp("get_status")["next_step"]
    assert step["tool"] == "add_companies" and step["campaign_id"] == campaign["id"]


# --- error shapes -------------------------------------------------------------------------------


def make_campaign(mcp, offer_id, **over):
    args = {
        "name": "SG logistics", "offer_id": offer_id, "countries": ["SG"],
        "sectors": ["logistics"], "company_size": "200-1000", "channels": ["linkedin"],
        "designations": ["ld", "hr"],
    }  # fmt: skip
    args.update(over)
    reply = mcp("create_campaign", **args)
    assert reply["ok"], reply
    return reply


def stored_offer(mcp):
    reply = mcp("ingest_offer_source", text="We run practical training courses for managers.")
    mcp("save_offer_profile", offer_id=reply["offer_id"], profile=PROFILE)
    return reply["offer_id"]


def test_missing_key_gives_the_needs_key_shape(mcp):
    offer_id = stored_offer(mcp)
    cid = make_campaign(mcp, offer_id)["id"]
    mcp(
        "add_companies", campaign_id=cid,
        companies=[{"name": COMPANY, "domain": "northwind.example"}],
    )  # fmt: skip
    mcp("approve_companies", campaign_id=cid)
    reply = mcp("find_people", campaign_id=cid, confirm=True)
    assert reply["ok"] is False
    assert reply["error"]["code"] == "needs_key"
    assert reply["error"]["message"] and reply["error"]["hint"]
    assert reply["needs_key"]["provider"] == "serper"
    assert reply["fallback_available"] == "ai_web_search"


def test_search_web_without_a_key_is_needs_key(mcp):
    reply = mcp("search_web", query="largest logistics companies in Singapore")
    assert reply["ok"] is False and "needs_key" in reply
    assert reply["error"]["code"] == "needs_key"
    assert reply["error"]["message"] and reply["error"]["hint"]


def test_budget_reached_gives_the_error_shape(mcp, fake_search):
    offer_id = stored_offer(mcp)
    cid = make_campaign(mcp, offer_id, budget={"search": 0})["id"]
    mcp(
        "add_companies", campaign_id=cid,
        companies=[{"name": COMPANY, "domain": "northwind.example"}],
    )  # fmt: skip
    mcp("approve_companies", campaign_id=cid)
    reply = mcp("find_people", campaign_id=cid, confirm=False)
    assert error_code(reply) == "budget_reached"
    assert reply["error"]["message"] and "confirm=true" in reply["error"]["hint"]
    assert reply["budget_reached"]["kind"] == "search"
    assert not fake_search.calls


def test_validation_error_has_the_error_shape(mcp):
    offer_id = stored_offer(mcp)
    reply = mcp(
        "create_campaign", name="Bad", offer_id=offer_id, countries=["SG"], sectors=["x"],
        company_size="small", channels=["carrier pigeon"], designations=["ld"],
    )  # fmt: skip
    assert reply["ok"] is False
    assert set(reply["error"]) == {"code", "message", "hint"}
    assert reply["error"]["code"] == "invalid_channel"


def test_unknown_ids_are_not_found(mcp):
    assert error_code(mcp("get_campaign", campaign_id=999)) == "not_found"
    assert error_code(mcp("get_offer", offer_id=999)) == "not_found"
    assert error_code(mcp("run_qa", campaign_id=999)) == "not_found"


def test_bad_tool_arguments_are_an_sdk_error_not_a_crash(mcp):
    result = mcp.portal.call(mcp.client.call_tool, "get_campaign", {"campaign_id": "abc"})
    assert result.is_error


def test_stubs_say_not_available_yet(mcp):
    for name, args in STUBS.items():
        reply = mcp(name, **args)
        assert error_code(reply) == "not_available_yet", name
        assert reply["error"]["hint"]


def test_error_reply_mapping(caplog):
    caplog.set_level(logging.ERROR, logger="leadgen.mcp")
    fail = mcp_server._error_reply
    assert fail("t", ProviderError("auth", "Key rejected"))["error"]["code"] == "provider_auth"
    assert fail("t", config.ConfigError("bad yaml"))["error"]["code"] == "settings_problem"
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        n: int

    with pytest.raises(ValidationError) as caught:
        M(n="secret-value")
    reply = fail("t", caught.value)
    assert reply["error"]["code"] == "invalid_value" and "secret-value" not in json.dumps(reply)
    reply = fail("t", RuntimeError("https://api.example.com/?key=sk-abc123 failed"))
    assert reply["error"]["code"] == "internal_error"
    assert "sk-abc123" not in json.dumps(reply)
    assert "sk-abc123" not in caplog.text and "RuntimeError" in caplog.text


# --- shortening and the reply wrapper ----------------------------------------------------------


def test_long_strings_are_shortened_except_listed_keys():
    long = "x" * 5000
    data = {"a": long, "text": long, "nested": [{"b": long}], "short": "ok"}
    out = mcp_server._finish(data, frozenset({"text"}))
    assert out["text"] == long and out["short"] == "ok"
    assert len(out["a"]) < 2100 and out["a"].endswith("characters in all]")
    assert len(out["nested"][0]["b"]) < 2100


def test_finish_adds_ok_wraps_lists_and_bounds_the_size():
    assert mcp_server._finish({"n": 1}, frozenset()) == {"ok": True, "n": 1}
    assert mcp_server._finish([1, 2], frozenset()) == {"ok": True, "items": [1, 2]}
    big = {"rows": [{"v": "y" * 1500} for _ in range(100)]}
    assert error_code(mcp_server._finish(big, frozenset())) == "result_too_large"


def test_guarded_tool_catches_service_errors():
    def boom():
        raise ProviderError("quota", "no credits")

    reply = mcp_server._guarded(boom, frozenset())()
    assert reply["ok"] is False and reply["error"]["code"] == "provider_quota"


# --- a mini flow through the tools --------------------------------------------------------------


def linkedin_hit(name, company=COMPANY):
    slug = name.lower().replace(" ", "-")
    return SearchResult(
        title=f"{name} - Head of Learning and Development at {company} | LinkedIn",
        link=f"https://www.linkedin.com/in/{slug}",
        snippet=f"Head of Learning and Development at {company} · Singapore",
        position=1,
    )


@pytest.fixture
def fake_search(monkeypatch):
    def respond(query, gl, num):
        if f'"{COMPANY}"' in query and "learning" in query:
            return [linkedin_hit("Aria Tan")]
        return []

    provider = FakeSearchProvider(respond)
    monkeypatch.setattr(registry, "get_search_provider", lambda: provider)
    monkeypatch.setattr(
        companies_svc,
        "check_domain",
        lambda host: companies_svc.DomainCheck(True, "Domain check ok"),
    )
    return provider


GOOD_MESSAGE = (
    f"Hi Aria, I see you lead learning and development at {COMPANY}. We run practical "
    "programmes for new managers, and our trainers are working practitioners. "
    "Would a short outline be useful to you?"
)


def test_mini_flow_from_offer_to_export(mcp, fake_search, workspace):
    # offer and campaign
    ingested = mcp("ingest_offer_source", text="We run practical training courses for managers.")
    assert ingested["text"].startswith("We run") and ingested["next_cursor"] is None
    saved = mcp("save_offer_profile", offer_id=ingested["offer_id"], profile=PROFILE)
    assert saved["profile"]["name"] == "Bright Harbour Training"
    assert mcp("get_offer", offer_id=ingested["offer_id"])["profile"]["name"]
    assert any(t["key"] == "ld" for t in mcp("list_designation_templates")["templates"])
    cid = make_campaign(mcp, ingested["offer_id"])["id"]
    assert mcp("get_campaign", campaign_id=cid)["counts"]["companies"]["approved"] == 0
    assert [c["id"] for c in mcp("list_campaigns")["campaigns"]] == [cid]

    # companies
    added = mcp(
        "add_companies", campaign_id=cid,
        companies=[{"name": COMPANY, "domain": "northwind.example", "sector": "logistics"}],
    )  # fmt: skip
    assert added["added"] == 1
    assert mcp("approve_companies", campaign_id=cid)["approved"]
    listed = mcp("list_companies", campaign_id=cid, status="approved")
    assert listed["total"] == 1

    # people (the fake search provider stands in for Serper)
    estimate = mcp("estimate_people_search", campaign_id=cid)
    assert estimate["searches"] == 2
    held = mcp("find_people", campaign_id=cid, confirm=False)
    assert held["ok"] is True or "needs_confirmation" in held  # small run: no confirmation needed
    found = held if held.get("done") else mcp("find_people", campaign_id=cid, confirm=True)
    assert found["done"] is True
    people = mcp("list_people", campaign_id=cid, status="confirmed")
    assert people["total"] == 1
    person = people["people"][0]
    assert person["linkedin_url"] == "https://www.linkedin.com/in/aria-tan"

    # drafting
    batch = mcp("get_drafting_batch", campaign_id=cid, channel="linkedin_message")
    assert [p["person_id"] for p in batch["people"]] == [person["id"]]
    assert len(batch["guide"]) > 5000  # the style guide is not shortened
    drafts = mcp(
        "save_drafts", campaign_id=cid,
        drafts=[{"person_id": person["id"], "channel": "linkedin_message", "body": GOOD_MESSAGE}],
    )  # fmt: skip
    assert drafts["results"][0]["status"] == "qa_pass"

    # QA, AI review, gate, export
    qa = mcp("run_qa", campaign_id=cid)
    assert qa["ok"]
    review_batch = mcp("get_review_batch", campaign_id=cid)
    assert len(review_batch["items"]) == 1
    message_id = review_batch["items"][0]["message_id"]
    recorded = mcp(
        "record_review", campaign_id=cid,
        results=[{"message_id": message_id, "verdict": "pass", "notes": "Accurate and polite."}],
    )  # fmt: skip
    assert recorded["ok"]
    gate = mcp("export_gate", campaign_id=cid)
    assert gate["ok"]
    exported = mcp("export_campaign", campaign_id=cid, format="lemlist")
    assert exported["exported"] == 1
    assert exported["path"].endswith(".csv") and "evidence_origin_counts" in exported
    assert mcp("get_status")["campaigns"][0]["next_step"]["tool"] == "export_campaign"
    assert fake_search.calls  # the fake provider answered, not the network
    assert mcp("list_qa_issues", campaign_id=cid)["ok"]
    assert mcp("recheck_people", campaign_id=cid)["checked"] == 1
    set_status = mcp(
        "set_person_status", campaign_id=cid, ids=[person["id"]], status="review", reason="test"
    )
    assert set_status["updated"] == 1


def test_company_status_tool(mcp, fake_search):
    offer_id = stored_offer(mcp)
    cid = make_campaign(mcp, offer_id)["id"]
    mcp(
        "add_companies",
        campaign_id=cid,
        companies=[{"name": COMPANY, "domain": "northwind.example"}],
    )
    company_id = mcp("list_companies", campaign_id=cid)["companies"][0]["id"]
    out = mcp(
        "set_company_status", campaign_id=cid, ids=[company_id], status="excluded", reason="no"
    )
    assert out["updated"] == 1


def test_email_tools_refuse_a_linkedin_only_campaign(mcp):
    offer_id = stored_offer(mcp)
    cid = make_campaign(mcp, offer_id)["id"]
    assert error_code(mcp("find_emails", campaign_id=cid, confirm=True)) == "no_email_channel"


# --- campaign options ---------------------------------------------------------------------------


def test_update_campaign_changes_only_what_is_given(mcp):
    offer_id = stored_offer(mcp)
    cid = make_campaign(mcp, offer_id, search_mode="api")["id"]
    assert mcp("get_campaign", campaign_id=cid)["options"]["search_mode"] == "api"
    updated = mcp("update_campaign", campaign_id=cid, search_mode="ai", allow_guess_emails=True)
    assert updated["options"]["search_mode"] == "ai"
    assert updated["options"]["allow_guess_emails"] is True
    assert updated["countries"] == ["SG"]
    assert error_code(mcp("update_campaign", campaign_id=cid, seniority_min="emperor")) == (
        "invalid_seniority"
    )


# --- lemlist ------------------------------------------------------------------------------------


def test_push_to_lemlist_passes_every_argument_on(mcp, monkeypatch):
    seen = {}

    def fake_push(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return {"ok": True, "dry_run": True, "messages_incomplete_hint": "hint", "guess_emails": 2}

    monkeypatch.setattr(lemlist_svc, "push_to_lemlist", fake_push)
    reply = mcp(
        "push_to_lemlist", campaign_id=4, lemlist_campaign_id="cam_abc12345",
        allow_incomplete=True,
    )  # fmt: skip
    assert seen["args"] == (4, "cam_abc12345", True, False, None)
    assert seen["kwargs"] == {"allow_incomplete": True}
    assert reply["messages_incomplete_hint"] == "hint" and reply["guess_emails"] == 2


def test_list_lemlist_campaigns_without_a_key(mcp):
    reply = mcp("list_lemlist_campaigns")
    assert error_code(reply) == "needs_key"


def test_lemlist_listing_is_wrapped(mcp, monkeypatch):
    monkeypatch.setattr(
        lemlist_svc, "list_lemlist_campaigns", lambda: [{"id": "cam_1", "name": "N"}]
    )
    assert mcp("list_lemlist_campaigns")["campaigns"] == [{"id": "cam_1", "name": "N"}]


# --- offers: chunks and files -------------------------------------------------------------------


def test_ingest_in_chunks_and_source_rules(mcp):
    text = "\n".join(f"Line {i}: " + "word " * 40 for i in range(400))  # about 90,000 characters
    first = mcp("ingest_offer_source", text=text)
    assert first["next_cursor"] and len(first["text"]) <= 40000
    second = mcp(
        "ingest_offer_source", offer_id=first["offer_id"], cursor=first["next_cursor"]
    )  # fmt: skip
    assert second["text"] and second["offer_id"] == first["offer_id"]
    assert error_code(mcp("ingest_offer_source")) == "one_source_needed"
    both = mcp("ingest_offer_source", url="https://example.com", text="x")
    assert error_code(both) == "one_source_needed"
    assert error_code(mcp("ingest_offer_source", offer_id=first["offer_id"], text="x")) == (
        "conflicting_input"
    )
    assert error_code(mcp("ingest_offer_source", offer_id=999)) == "not_found"
    assert error_code(mcp("ingest_offer_source", offer_id=first["offer_id"], cursor="x")) == (
        "invalid_cursor"
    )


def test_local_file_is_read(mcp, tmp_path):
    path = tmp_path / "offer.txt"
    path.write_text("Our courses are practical and short.", encoding="utf-8")
    reply = mcp("ingest_offer_source", file_path=str(path))
    assert reply["ok"] and "practical" in reply["text"]


def test_remote_mode_reads_files_only_inside_the_workspace(tmp_path, workspace):
    from leadgen.core import paths

    paths.ensure_workspace()
    outside = tmp_path / "outside.txt"
    outside.write_text("Secret notes", encoding="utf-8")
    inside = workspace / "offer.txt"
    inside.write_text("Our courses are practical and short.", encoding="utf-8")
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(mcp_server.build_server(remote=True))) as c:

            def call(**args):
                res = portal.call(c.call_tool, "ingest_offer_source", args)
                return json.loads(res.content[0].text)

            assert error_code(call(file_path=str(outside))) == "file_outside_workspace"
            assert "Secret" not in json.dumps(call(file_path=str(outside)))
            assert call(file_path=str(inside))["ok"] is True


# --- keys and secrets ---------------------------------------------------------------------------


def test_save_api_key_unknown_provider_and_bad_key(mcp):
    assert error_code(mcp("save_api_key", provider="nope", key="abc")) == "unknown_provider"
    respx.post("https://google.serper.dev/search").respond(403, json={"message": "Unauthorized"})
    reply = mcp("save_api_key", provider="serper", key="sk-bad-key-1234567890")
    assert error_code(reply) == "key_not_saved"
    assert "sk-bad-key-1234567890" not in json.dumps(reply)
    assert secrets.stored("SERPER_API_KEY") == ""


def test_save_api_key_success_returns_only_the_masked_key(mcp):
    respx.post("https://google.serper.dev/search").respond(200, json={"organic": []})
    reply = mcp("save_api_key", provider="serper", key="sk-good-key-1234567890")
    assert reply["ok"] and reply["masked_key"].endswith("7890")
    assert "sk-good-key-1234567890" not in json.dumps(reply)
    assert secrets.stored("SERPER_API_KEY") == "sk-good-key-1234567890"


FAKE_KEYS = {
    "SERPER_API_KEY": "sk-serper-AAAA1111BBBB2222",
    "HUNTER_API_KEY": "sk-hunter-CCCC3333DDDD4444",
    "REOON_API_KEY": "sk-reoon-EEEE5555FFFF6666",
    "LEMLIST_API_KEY": "sk-lemlist-GGGG7777HHHH8888",
}


def test_no_tool_output_contains_a_secret(mcp, fake_search):
    for name, value in FAKE_KEYS.items():
        secrets.set(name, value)
    offer_id = stored_offer(mcp)
    cid = make_campaign(mcp, offer_id, channels=["linkedin", "email"])["id"]
    mcp(
        "add_companies",
        campaign_id=cid,
        companies=[{"name": COMPANY, "domain": "northwind.example"}],
    )
    mcp("approve_companies", campaign_id=cid)
    mcp("find_people", campaign_id=cid, confirm=True)
    respx.get(url__regex=r"https://api\.lemlist\.com/.*").respond(401, json={})
    respx.get(url__regex=r"https://api\.hunter\.io/.*").respond(500, json={})
    calls = [
        ("get_status", {}), ("list_providers", {}), ("get_workflow_guide", {"section": "all"}),
        ("get_campaign", {"campaign_id": cid}), ("list_people", {"campaign_id": cid}),
        ("estimate_email_search", {"campaign_id": cid}),
        ("find_emails", {"campaign_id": cid, "confirm": True}),
        ("list_lemlist_campaigns", {}),
        ("save_api_key", {"provider": "serper", "key": "  "}),
        ("search_web", {"query": "logistics", "campaign_id": cid}),
        ("export_gate", {"campaign_id": cid}), ("run_qa", {"campaign_id": cid}),
    ]  # fmt: skip
    for name, args in calls:
        text = json.dumps(mcp(name, **args))
        for value in FAKE_KEYS.values():
            assert value not in text, name


# --- AI search mode -----------------------------------------------------------------------------


def test_ai_search_tools_and_spot_check(mcp, monkeypatch):
    monkeypatch.setattr(
        companies_svc,
        "check_domain",
        lambda host: companies_svc.DomainCheck(True, "Domain check ok"),
    )
    offer_id = stored_offer(mcp)
    cid = make_campaign(mcp, offer_id, search_mode="ai")["id"]
    mcp(
        "add_companies",
        campaign_id=cid,
        companies=[{"name": COMPANY, "domain": "northwind.example"}],
    )
    mcp("approve_companies", campaign_id=cid)
    assert mcp("get_status")["campaigns"][0]["next_step"]["tool"] == "get_search_tasks"

    api = mcp("find_people", campaign_id=cid, confirm=True)
    assert api["ok"] is False and api["use_ai_search"] is True

    tasks = mcp("get_search_tasks", campaign_id=cid, kind="people", batch_size=10)
    assert tasks["tasks"] and tasks["instructions"]
    first = tasks["tasks"][0]
    fabricated = mcp(
        "submit_search_results", task_id=first["task_id"], tool_name="web_search",
        results=[{"title": "Aria Tan", "url": "linkedin.com/in/aria", "snippet": "x"}],
    )  # fmt: skip
    assert error_code(fabricated) == "not_verbatim"
    good = mcp(
        "submit_search_results", task_id=first["task_id"], tool_name="web_search",
        results=[{
            "title": f"Aria Tan - Head of Learning and Development at {COMPANY} | LinkedIn",
            "url": "https://www.linkedin.com/in/aria-tan",
            "snippet": f"Head of Learning and Development at {COMPANY} · Singapore · "
            f"Experience: {COMPANY}",
        }],
    )  # fmt: skip
    assert good["ok"] and good["results"][0]["outcome"] == "confirmed"
    skipped = mcp("skip_task", task_id=tasks["tasks"][1]["task_id"], reason="user said skip")
    assert skipped["ok"]
    assert error_code(mcp("skip_task", task_id=first["task_id"], reason="")) in {
        "reason_required", "task_not_open",
    }  # fmt: skip

    people = mcp("list_people", campaign_id=cid, status="confirmed")
    assert people["people"][0]["evidence_origin"] == "ai_web_search"
    spot = mcp("get_spot_check", campaign_id=cid)
    assert spot["ok"] and spot["people"][0]["name"] == "Aria Tan"
    recorded = mcp(
        "record_spot_check", campaign_id=cid,
        results=[{"person_id": spot["people"][0]["person_id"], "verdict": "ok", "note": ""}],
    )  # fmt: skip
    assert recorded["ok"]
    assert error_code(mcp("get_search_tasks", campaign_id=999)) == "not_found"


# --- transports ---------------------------------------------------------------------------------

SECRET = "s3cretPathValue-0123456789"
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}  # fmt: skip
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_http_only_the_secret_path_answers():
    app = mcp_server.http_app(mcp_server.build_server(remote=True), SECRET)
    with TestClient(app, base_url="http://127.0.0.1:8766") as client:
        assert client.post("/mcp/wrong", json=INIT, headers=HEADERS).status_code == 404
        assert client.post("/mcp", json=INIT, headers=HEADERS).status_code == 404
        assert client.get("/", headers=HEADERS).status_code == 404
        assert client.post(f"/mcp/{SECRET}x", json=INIT, headers=HEADERS).status_code == 404
        ok = client.post(f"/mcp/{SECRET}", json=INIT, headers=HEADERS)
        assert ok.status_code == 200
        assert '"serverInfo"' in ok.text or "lead-generator" in ok.text


def test_http_refuses_foreign_host_headers_unless_allowed():
    server = mcp_server.build_server(remote=True)
    app = mcp_server.http_app(server, SECRET)
    with TestClient(app, base_url="http://tunnel.example.com") as client:
        denied = client.post(f"/mcp/{SECRET}", json=INIT, headers=HEADERS)
        assert denied.status_code in (400, 403, 421)
    app = mcp_server.http_app(
        mcp_server.build_server(remote=True), SECRET, allowed_hosts=["tunnel.example.com"]
    )
    with TestClient(app, base_url="http://tunnel.example.com") as client:
        assert client.post(f"/mcp/{SECRET}", json=INIT, headers=HEADERS).status_code == 200


def test_http_secret_must_be_long_enough():
    with pytest.raises(ValueError):
        mcp_server.http_app(mcp_server.build_server(init_db=False), "short")
    with pytest.raises(ValueError):
        mcp_server.http_app(mcp_server.build_server(init_db=False), "has/slash/in/it/0123456789")


def test_secret_is_generated_once_and_kept():
    first = config.ensure_mcp_secret()
    assert len(first) >= 32 and mcp_server._SECRET_RE.fullmatch(first)
    assert config.ensure_mcp_secret() == first
    assert config.load_settings().mcp_http.secret == first


def test_run_http_uses_localhost_without_access_log(monkeypatch, capsys):
    import uvicorn

    seen = {}

    class FakeServer:
        def __init__(self, cfg):
            seen["config"] = cfg

        def run(self):
            seen["ran"] = True

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    mcp_server.run_http()
    cfg = seen["config"]
    assert seen["ran"] and cfg.host == "127.0.0.1" and cfg.port == 8766
    assert cfg.access_log is False
    secret = config.load_settings().mcp_http.secret
    assert f"/mcp/{secret}" in capsys.readouterr().err  # shown once to the user, not logged


def test_run_http_warns_when_bound_to_other_addresses(monkeypatch, capsys):
    import uvicorn

    class FakeServer:
        def __init__(self, cfg):
            self.cfg = cfg

        def run(self):
            pass

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    mcp_server.run_http("0.0.0.0", 9000)
    assert "reachable from other computers" in capsys.readouterr().err


def test_cli_mcp_parses_http_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        mcp_server,
        "run_http",
        lambda host, port, allowed: seen.update(host=host, port=port, a=allowed),
    )
    assert cli.main(["mcp", "--http", "--port", "9100", "--allowed-host", "x.example"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 9100, "a": ["x.example"]}
    monkeypatch.setattr(mcp_server, "run_stdio", lambda: seen.update(stdio=True))
    assert cli.main(["mcp"]) == 0 and seen["stdio"] is True


def test_http_logging_never_records_request_urls():
    mcp_server.build_server(init_db=False)
    for name in ("httpx", "httpcore", "uvicorn.access"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_stdio_server_speaks_mcp_and_keeps_stdout_clean(workspace):
    """Start `leadgen mcp` for real and call get_status over stdin/stdout."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "leadgen", "mcp"],
        env={"LEADGEN_HOME": str(workspace)},
    )

    async def main():
        async with Client(params) as client:
            tools = await client.list_tools()
            status = await client.call_tool("get_status", {})
            return tools, status

    tools, status = anyio.run(main)
    assert len(tools.tools) == len(PLAN_TOOLS | EXTRA_TOOLS)
    assert json.loads(status.content[0].text)["next_step"]["tool"] == "ingest_offer_source"


def test_module_runs_as_a_script_help():
    out = subprocess.run(
        [sys.executable, "-m", "leadgen", "mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0 and "--http" in out.stdout
