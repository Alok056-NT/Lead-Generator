#!/usr/bin/env python
# ruff: noqa
"""A fake `claude` for tests (invented data only).

Run as `python fake_claude.py <the arguments Lead Generator would give claude>`.
Environment:
  FAKE_CLAUDE_SCENARIO  chat (default) | auth | limit | slow | badresume | garbage | nomcp
  FAKE_CLAUDE_LOG       file that gets one JSON line per -p call (argv, prompt, key, env flags)
  FAKE_CLAUDE_STATE     folder; `auth login` writes login_done there
  FAKE_CLAUDE_AUTH      in (default, signed in) | out (signed out until `auth login` ran)
  FAKE_CLAUDE_REAL_STATUS=1  the tool result is the real status.get_status() of LEADGEN_HOME
"""
import json
import os
import sys
import time
from pathlib import Path

SESSION = "11111111-2222-4333-8444-555555555555"
TOOL = "mcp__lead-generator__get_status"


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def log(entry):
    path = os.environ.get("FAKE_CLAUDE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def signed_in():
    if os.environ.get("FAKE_CLAUDE_AUTH", "in") == "in":
        return True
    state = os.environ.get("FAKE_CLAUDE_STATE")
    return bool(state) and (Path(state) / "login_done").exists()


def flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def stream_text(sid, parts):
    event = lambda e: out({"type": "stream_event", "event": e, "session_id": sid})  # noqa: E731
    event({"type": "message_start", "message": {"id": "m", "role": "assistant", "content": []}})
    event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    for part in parts:
        event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": part}})
    event({"type": "content_block_stop", "index": 0})


def tool_result():
    if os.environ.get("FAKE_CLAUDE_REAL_STATUS") == "1":
        from leadgen import db
        from leadgen.services import status

        db.init_db()
        return status.get_status()
    return {"ok": True, "next_step": {"step": "offer", "message": "Ask for the product."}}


def run_print(argv):
    prompt = sys.stdin.read()
    log({
        "argv": argv, "prompt": prompt, "cwd": os.getcwd(),
        "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "tool_search": os.environ.get("ENABLE_TOOL_SEARCH", ""),
    })
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "chat")
    resume = flag(argv, "--resume")
    sid = resume or flag(argv, "--session-id") or SESSION
    if scenario == "badresume" and resume:
        sys.stderr.write(f"No conversation found with session ID: {resume}\n")
        return 1
    out({"type": "system", "subtype": "init", "session_id": sid, "model": "fake",
         "tools": [TOOL], "mcp_servers": [{"name": "lead-generator",
         "status": "failed" if scenario == "nomcp" else "connected"}]})
    if scenario == "garbage":
        sys.stdout.write("this line is not json\n")
        sys.stdout.flush()
    if scenario == "auth":
        out({"type": "result", "subtype": "success", "is_error": True,
             "result": "Not logged in · Please run /login", "session_id": sid})
        return 1
    if scenario == "limit":
        out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}, "session_id": sid})
        out({"type": "result", "subtype": "success", "is_error": True,
             "result": "Claude usage limit reached. Your limit will reset at 3pm.", "session_id": sid})
        return 1
    stream_text(sid, ["Hello ", "there! "])
    if scenario == "slow":                        # a cancel test stops the program here
        for _ in range(600):
            time.sleep(0.1)
    out({"type": "stream_event", "session_id": sid, "event": {
        "type": "content_block_start", "index": 1,
        "content_block": {"type": "tool_use", "id": "toolu_01", "name": TOOL, "input": {}}}})
    out({"type": "assistant", "session_id": sid, "message": {"id": "msg_1", "role": "assistant",
         "content": [{"type": "text", "text": "Hello there! "},
                     {"type": "tool_use", "id": "toolu_01", "name": TOOL, "input": {}}]}})
    out({"type": "user", "session_id": sid, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01",
         "content": json.dumps(tool_result(), default=str), "is_error": False}]}})
    stream_text(sid, ["All set. ", "[[chips: Looks good | Change something]]"])
    out({"type": "assistant", "session_id": sid, "message": {"id": "msg_2", "role": "assistant",
         "content": [{"type": "text", "text": "All set. [[chips: Looks good | Change something]]"}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "All set.",
         "session_id": sid, "num_turns": 2, "total_cost_usd": 0.0})
    return 0


def main(argv):
    if argv[:1] == ["--version"]:
        print("2.1.310 (Claude Code)")
        return 0
    if argv[:2] == ["auth", "status"]:
        if os.environ.get("ANTHROPIC_API_KEY"):
            body, code = {"loggedIn": True, "authMethod": "api_key",
                          "apiKeySource": "ANTHROPIC_API_KEY"}, 0
        elif signed_in():
            body, code = {"loggedIn": True, "authMethod": "claude.ai", "email": "aria@example.com",
                          "subscriptionType": "pro"}, 0
        else:
            body, code = {"loggedIn": False, "authMethod": "none"}, 1
        print(json.dumps(body))
        return code
    if argv[:2] == ["auth", "login"]:
        print("Opening your browser: https://claude.ai/oauth/authorize?fake=1", flush=True)
        time.sleep(float(os.environ.get("FAKE_CLAUDE_LOGIN_SECONDS", "0.3")))
        state = os.environ.get("FAKE_CLAUDE_STATE")
        if state:
            (Path(state) / "login_done").write_text("1", encoding="utf-8")
        return 0
    if "-p" in argv or "--print" in argv:
        return run_print(argv)
    sys.stderr.write("unsupported\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
