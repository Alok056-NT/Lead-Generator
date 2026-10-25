"""The Studio chat engine and its routes, with the fake claude (invented data only; no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from leadgen.core import secrets
from leadgen.dashboard import app as dash
from leadgen.services import ai_setup, chat, preferences, projects

BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}
KEY = "sk-ant-api03-" + "x" * 40


@pytest.fixture(autouse=True)
def _clear_turns():
    """Project ids repeat from 1 in every test database, so no turn may leak between tests."""
    chat._TURNS.clear()
    yield
    chat._TURNS.clear()


def parse(text: str) -> list[tuple[str, dict]]:
    """SSE text to `[(event, data)]`; keep-alive comments are skipped."""
    out = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        event = next((ln[len("event: "):] for ln in lines if ln.startswith("event: ")), None)
        data = next((ln[len("data: "):] for ln in lines if ln.startswith("data: ")), None)
        if event and data is not None:
            out.append((event, json.loads(data)))
    return out


def _project(name="Demo Co") -> int:
    return projects.create_project(name)["id"]


@pytest.fixture
def pid(ready_db, fake_claude):
    preferences.set_ai_mode("claude_plan")
    return _project()


def run(pid, text="hello", files=()):
    turn = chat.start_turn(pid, text, files)["turn_id"]
    assert chat.wait(pid, 30)
    return parse("".join(chat.stream_events(pid, turn)))


def _failure_codes(events):
    return [data["code"] for event, data in events if event == "error"]


def test_the_studio_tools_cover_every_mcp_tool():
    from leadgen import mcp_server

    names = {name for name, _ in mcp_server.tool_summaries()}
    assert set(chat.STUDIO_TOOLS).isdisjoint(chat.WITHHELD_TOOLS)
    assert set(chat.STUDIO_TOOLS) | chat.WITHHELD_TOOLS == names
    assert {"send_emails", "push_to_lemlist", "save_api_key", "create_project",
            "open_dashboard"} <= chat.WITHHELD_TOOLS
    assert len(chat.STUDIO_TOOLS) == 43


def test_build_command_is_exact():
    cmd = chat.build_command("claude", session_id="sid-1", resume=False,
                             mcp_config=Path("mcp.json"), prompt_file=Path("system.md"))
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert cmd == [
        "claude", "-p", "--output-format", "stream-json", "--verbose",
        "--include-partial-messages", "--mcp-config", "mcp.json", "--strict-mcp-config",
        "--tools", "", "--allowedTools", allowed, "--permission-mode", "dontAsk",
        "--append-system-prompt-file", "system.md", "--disable-slash-commands",
        "--max-turns", "80", "--session-id", "sid-1",
    ]
    assert "mcp__lead-generator__get_status" in allowed
    assert "send_emails" not in allowed
    resumed = chat.build_command("claude", session_id="sid-1", resume=True,
                                 mcp_config=Path("mcp.json"), prompt_file=Path("system.md"))
    assert resumed[-2:] == ["--resume", "sid-1"]


def test_build_prompt_has_the_context_block(tmp_path):
    project = {"id": 7, "name": "Demo Co", "sender_name": "", "sender_title": "",
               "sender_company": "", "offer_id": None}
    prompt = chat.build_prompt(project, "Hi there", [])
    assert prompt.startswith("[Studio context. Never repeat this block to the person.\n")
    assert "project_id: 7\n" in prompt and "project_name: Demo Co\n" in prompt
    assert "sender_name: (not set yet)\n" in prompt and "product_attached: no\n" in prompt
    assert "sender_title: (not set)\n" in prompt and "sender_company: (not set)\n" in prompt
    assert prompt.endswith("]\n\nHi there")


def test_chip_filter_takes_a_split_marker():
    flt = chat.ChipFilter()
    out = "".join(flt.feed(p) for p in ["Hi [", "[chips: A | B]", "] bye"])
    assert out + flt.finish() == "Hi  bye"
    assert flt.chips == ["A", "B"]


def test_chip_filter_go_buttons_and_unknown_targets():
    flt = chat.ChipFilter()
    assert flt.feed("Done [[go: keys]]") == "Done "
    assert flt.buttons == [{"label": "Open the Keys page", "href": "#/keys"}]
    assert chat.ChipFilter().feed("x [[go: nope]] y") == "x  y"
    assert chat.ChipFilter().feed("a [[x]] b") == "a [[x]] b"


def test_chip_filter_limits_chips():
    flt = chat.ChipFilter()
    flt.feed("[[chips: A | B | C | D | E]]")
    assert flt.chips == ["A", "B", "C", "D"]
    flt = chat.ChipFilter()
    flt.feed("[[chips: " + "x" * 40 + "]]")
    assert flt.chips == ["x" * 30]


def test_chip_filter_keeps_unfinished_text():
    flt = chat.ChipFilter()
    assert flt.feed("Hi [[chips: A") == "Hi "
    assert flt.finish() == "[[chips: A"
    lone = chat.ChipFilter()
    assert lone.feed("a [") == "a "
    assert lone.feed("b") == "[b"
    assert lone.finish() == ""


def test_classify_failure_table():
    base = {"text": "", "stderr": "", "assistant_error": "", "limited": False,
            "subtype": "success", "mode": "claude_plan"}
    cases = [
        ({"subtype": "error_max_turns"}, "too_long"),
        ({"text": "No conversation found with session ID: x"}, "session_lost"),
        ({"assistant_error": "billing_error"}, "no_credit"),
        ({"text": "Your credit balance is too low"}, "no_credit"),
        ({"assistant_error": "authentication_failed"}, "not_signed_in"),
        ({"text": "Not logged in · Please run /login"}, "not_signed_in"),
        ({"text": "Invalid API key"}, "not_signed_in"),
        ({"limited": True}, "usage_limit"),
        ({"assistant_error": "rate_limit"}, "usage_limit"),
        ({"text": "429 Too Many Requests"}, "usage_limit"),
        ({"stderr": "fetch failed"}, "network"),
        ({"text": "The model is overloaded"}, "network"),
        ({"text": "something odd"}, "ai_failed"),
    ]
    for change, code in cases:
        assert chat.classify_failure(**{**base, **change}) == code, change
    api = {**base, "mode": "api_key", "assistant_error": "authentication_failed"}
    assert chat.classify_failure(**api) == "bad_api_key"
    api_text = {**base, "mode": "api_key", "text": "Not logged in"}
    assert chat.classify_failure(**api_text) == "bad_api_key"


def test_a_normal_turn(pid, fake_claude):
    events = run(pid)
    names = [event for event, _ in events]
    assert names[0] == "turn"
    assert names[-1] == "done" and events[-1][1] == {"ok": True, "cancelled": False}
    texts = [d["delta"] for e, d in events if e == "text"]
    assert "".join(texts[:2]) == "Hello there! "
    assert "All set. " in texts and "[[" not in "".join(texts)
    order = [e for e in names if e in ("tool", "refresh", "chips")]
    assert order == ["tool", "tool", "refresh", "chips"]
    assert [d.get("state") for e, d in events if e == "tool"] == ["started", "finished"]

    rows = chat.history(pid)
    assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1]["text"] == "Hello there!"
    assert rows[2]["text"] == "Checking where things stand..." and rows[2]["tool"] == "get_status"
    assert rows[2]["ok"] is True
    assert rows[3]["text"] == "All set."
    assert rows[3]["chips"] == ["Looks good", "Change something"]

    call = fake_claude.calls()[0]
    assert call["argv"][call["argv"].index("--session-id") + 1] == projects.get_chat_session(pid)
    assert call["prompt"].startswith("[Studio context")
    assert f"project_id: {pid}" in call["prompt"]
    assert "sender_name: (not set yet)" in call["prompt"]
    assert call["tool_search"] == "false"
    assert Path(call["cwd"]).name == "studio"


def test_second_turn_resumes_the_session(pid, fake_claude):
    run(pid, "first")
    session = projects.get_chat_session(pid)
    run(pid, "second")
    second = fake_claude.calls()[1]["argv"]
    assert second[second.index("--resume") + 1] == session
    assert "--session-id" not in second


def test_api_key_mode_passes_the_key_only_to_the_child(pid, fake_claude, monkeypatch):
    secrets.set(ai_setup.API_KEY_NAME, KEY)
    preferences.set_ai_mode("api_key")
    run(pid)
    assert fake_claude.calls()[0]["api_key"] == KEY

    preferences.set_ai_mode("claude_plan")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    run(pid)
    assert fake_claude.calls()[1]["api_key"] == ""


def test_attachments_reach_the_prompt(pid, fake_claude):
    folder = chat.uploads_dir(pid)
    (folder / "brochure.pdf").write_bytes(b"%PDF-1.4 tiny")
    run(pid, "", ["brochure.pdf"])
    user = chat.history(pid)[0]
    assert user["text"] == "I attached a file."
    assert user["attachments"] == ["brochure.pdf"]
    assert f"- brochure.pdf: {folder / 'brochure.pdf'}" in fake_claude.calls()[0]["prompt"]
    for bad in ("../x.pdf", "x.exe", "missing.pdf"):
        with pytest.raises(chat.ChatError) as caught:
            chat.start_turn(pid, "hi", [bad])
        assert caught.value.code == "bad_attachment"


def test_input_checks(ready_db, fake_claude):
    pid = _project()
    with pytest.raises(chat.ChatError) as caught:
        chat.start_turn(pid, "   ")
    assert caught.value.code == "empty_message"
    with pytest.raises(chat.ChatError) as caught:
        chat.start_turn(pid, "x" * 8001)
    assert caught.value.code == "message_too_long"
    with pytest.raises(chat.ChatError) as caught:
        chat.start_turn(pid, "hello")
    assert caught.value.code == "ai_not_ready"
    preferences.set_ai_mode("claude_plan")
    with pytest.raises(projects.ProjectError) as caught:
        chat.start_turn(9999, "hello")
    assert caught.value.code == "not_found"


def test_one_turn_at_a_time_and_stop(pid, fake_claude):
    fake_claude.scenario("slow")
    turn = chat.start_turn(pid, "hello")["turn_id"]
    with pytest.raises(chat.ChatError) as caught:
        chat.start_turn(pid, "again")
    assert caught.value.code == "turn_running"
    assert chat.turn_state(pid) == {"running": True, "turn_id": turn}
    assert chat.cancel_turn(pid) == {"ok": True, "stopped": True}
    assert chat.wait(pid, 30)
    events = parse("".join(chat.stream_events(pid, turn)))
    assert events[-1] == ("done", {"ok": False, "cancelled": True})
    assert "error" not in [event for event, _ in events]
    assert chat.history(pid)[0]["role"] == "user"


@pytest.mark.parametrize("scenario, code", [
    ("auth", "not_signed_in"),
    ("limit", "usage_limit"),
    ("nomcp", "tools_unavailable"),
])
def test_failure_scenarios(pid, fake_claude, scenario, code):
    fake_claude.scenario(scenario)
    events = run(pid)
    assert _failure_codes(events) == [code]
    assert events[-1] == ("done", {"ok": False, "cancelled": False})
    assert any(row["text"] == "hello" for row in chat.history(pid) if row["role"] == "user")


def test_failure_message_wording(pid, fake_claude):
    fake_claude.scenario("limit")
    events = run(pid)
    assert events[-2][1]["message"] == chat.FAILURES[("usage_limit", "claude_plan")][0]


def test_api_key_auth_failure_says_bad_key(pid, fake_claude):
    secrets.set(ai_setup.API_KEY_NAME, KEY)
    preferences.set_ai_mode("api_key")
    fake_claude.scenario("auth")
    assert _failure_codes(run(pid)) == ["bad_api_key"]


def test_garbage_output_still_ends_ok(pid, fake_claude):
    fake_claude.scenario("garbage")
    events = run(pid)
    assert events[-1] == ("done", {"ok": True, "cancelled": False})


def test_missing_program(pid, monkeypatch):
    monkeypatch.setattr(ai_setup, "find_claude", lambda: None)
    ready = {"ready": True, "mode": "claude_plan"}
    monkeypatch.setattr(ai_setup, "get_status", lambda *a, **k: ready)
    events = run(pid)
    assert _failure_codes(events) == ["missing_cli"]
    assert events[-1] == ("done", {"ok": False, "cancelled": False})
    assert chat.history(pid)[0]["text"] == "hello"


def test_a_lost_session_is_cleared(pid, fake_claude):
    projects.set_chat_session(pid, "stale-id")
    fake_claude.scenario("badresume")
    assert _failure_codes(run(pid)) == ["session_lost"]
    assert projects.get_chat_session(pid) == ""
    fake_claude.scenario("chat")
    events = run(pid)
    assert events[-1] == ("done", {"ok": True, "cancelled": False})
    assert "--session-id" in fake_claude.calls()[-1]["argv"]


def test_idle_timeout(pid, fake_claude, monkeypatch):
    monkeypatch.setattr(chat, "IDLE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(chat, "WATCH_INTERVAL_S", 0.05)
    fake_claude.scenario("slow")
    assert _failure_codes(run(pid)) == ["timeout"]


def test_stream_events_resume_and_heartbeat(pid, fake_claude):
    turn = chat.start_turn(pid, "hello")["turn_id"]
    assert chat.wait(pid, 30)
    raw = "".join(chat.stream_events(pid, turn, after=3))
    assert raw.startswith("id: 4\n")
    assert all(not block.startswith("id: 1\n") for block in raw.split("\n\n"))

    held = chat.Turn(pid, "held")
    chat._TURNS[pid] = held
    gen = chat.stream_events(pid, "held", heartbeat_s=0.05)
    assert next(gen) == ": keep-alive\n\n"
    held.emit("turn", {"turn_id": "held", "project_id": pid})
    held.finish()
    assert "event: turn" in "".join(gen)

    with pytest.raises(chat.ChatError) as caught:
        chat.stream_events(pid, "unknown-turn")
    assert caught.value.code == "not_found"


def test_history_limit_and_shape(pid):
    rows = [{"role": "user", "text": f"m{i}", "meta": {}} for i in range(204)]
    rows.append({"role": "tool", "text": "Checking...", "meta": {"tool": "get_status", "ok": True}})
    chat._save_rows(pid, rows)
    history = chat.history(pid)
    assert len(history) == 200
    assert history[0]["text"] == "m5"
    assert history[-1]["tool"] == "get_status" and history[-1]["ok"] is True


@pytest.fixture
def client(pid):
    preferences.set_ai_mode("claude_plan")
    return TestClient(dash.create_app(), base_url=BASE)


def test_history_route_on_a_new_project(client, pid):
    body = client.get(f"/api/projects/{pid}/chat").json()
    assert body["messages"] == []
    assert body["turn"] == {"running": False, "turn_id": None}
    assert body["ai"]["ready"] is True


def test_send_needs_the_header_and_streams(client, pid, fake_claude):
    assert client.post(f"/api/projects/{pid}/chat", json={"message": "hi"}).status_code == 403
    sent = client.post(f"/api/projects/{pid}/chat", json={"message": "hi"}, headers=CSRF)
    assert sent.status_code == 200
    turn = sent.json()["turn_id"]
    assert chat.wait(pid, 30)
    reply = client.get(f"/api/projects/{pid}/chat/stream?turn={turn}")
    assert reply.headers["content-type"].startswith("text/event-stream")
    assert parse(reply.text) == parse("".join(chat.stream_events(pid, turn)))
    assert parse(reply.text)[-1][0] == "done"
    resumed = client.get(f"/api/projects/{pid}/chat/stream?turn={turn}",
                         headers={"Last-Event-ID": "3"})
    assert resumed.text.startswith("id: 4\n")


def test_second_send_while_running_is_409_and_stop(client, pid, fake_claude):
    fake_claude.scenario("slow")
    sent = client.post(f"/api/projects/{pid}/chat", json={"message": "hi"}, headers=CSRF)
    assert sent.status_code == 200
    busy = client.post(f"/api/projects/{pid}/chat", json={"message": "again"}, headers=CSRF)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "turn_running"
    stopped = client.post(f"/api/projects/{pid}/chat/stop", headers=CSRF)
    assert stopped.json() == {"ok": True, "stopped": True}
    assert chat.wait(pid, 30)


def test_unknown_turn_is_404_json(client, pid):
    reply = client.get(f"/api/projects/{pid}/chat/stream?turn=nope")
    assert reply.status_code == 404 and reply.json()["ok"] is False


def test_upload_route(client, pid):
    def upload(name, data):
        return client.post(f"/api/projects/{pid}/chat/upload", headers=CSRF,
                           files={"file": (name, data, "application/octet-stream")})

    contacts = upload("contacts.csv", b"name,email\n")
    assert contacts.json()["file"]["kind"] == "contacts"
    brochure = upload("brochure.pdf", b"%PDF-1.4 tiny")
    assert brochure.json()["file"]["kind"] == "product"
    refused = upload("x.exe", b"MZ")
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "unsupported_type"
    assert (chat.uploads_dir(pid) / "brochure.pdf").is_file()
    upload("../evil.pdf", b"%PDF-1.4 tiny")
    assert (chat.uploads_dir(pid) / "evil.pdf").is_file()
