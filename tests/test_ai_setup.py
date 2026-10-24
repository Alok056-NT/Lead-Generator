"""Connect your AI: finding Claude Code, sign-in, API key and the check, with the fake claude
(invented data only; the Anthropic call is mocked with respx)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from leadgen.core import paths, secrets
from leadgen.dashboard import app as dash
from leadgen.services import ai_setup, preferences

SECRET = "sk-ant-api03-" + "x" * 40
MASKED = "…" + "x" * 4
FAKE = Path(__file__).parent / "fake_claude.py"
BASE = "http://127.0.0.1:8765"
CSRF = {"X-LeadGen": "1"}


@pytest.fixture(autouse=True)
def _reset_login():
    """Sign-in state never leaks from one test to the next."""
    ai_setup._LOGIN.update(proc=None, started=0.0, last="")
    yield
    ai_setup._LOGIN.update(proc=None, started=0.0, last="")


def _no_program(monkeypatch):
    monkeypatch.delenv(ai_setup.CLAUDE_BIN_ENV, raising=False)
    monkeypatch.setattr(ai_setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(ai_setup, "_candidates", lambda: [])


# --- finding and running Claude Code ------------------------------------------------------------


def test_find_claude_prefers_the_environment_variable(monkeypatch):
    monkeypatch.setenv(ai_setup.CLAUDE_BIN_ENV, str(FAKE))
    assert ai_setup.find_claude() == str(FAKE)
    _no_program(monkeypatch)
    assert ai_setup.find_claude() is None


def test_launcher_runs_a_py_file_with_this_python():
    assert ai_setup.launcher(str(FAKE)) == [sys.executable, str(FAKE)]
    assert ai_setup.launcher("claude") == ["claude"]


def test_status_with_the_fake(ready_db, fake_claude):
    status = ai_setup.get_status(True)
    assert status["mode"] == "" and status["ready"] is False
    assert status["claude"] == {"installed": True, "version": "2.1.310", "outdated": False}
    assert status["plan"]["signed_in"] is True and status["plan"]["email"] == "aria@example.com"

    preferences.set_ai_mode("claude_plan")
    status = ai_setup.get_status(True)
    assert status["mode"] == "claude_plan" and status["ready"] is True
    assert status["plan"]["email"] == "aria@example.com"

    preferences.set_ai_mode("api_key")
    assert ai_setup.get_status(True)["ready"] is False
    secrets.set("ANTHROPIC_API_KEY", SECRET)
    status = ai_setup.get_status(True)
    assert status["ready"] is True
    assert status["api_key"] == {"configured": True, "masked": MASKED}
    assert SECRET not in json.dumps(status)


def test_plan_check_ignores_an_api_key_in_the_environment(ready_db, fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    plan = ai_setup.get_status(True)["plan"]
    assert plan["signed_in"] is True and plan["email"] == "aria@example.com"


def test_child_env(ready_db, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "invented-token")
    plan_env = ai_setup.child_env("claude_plan")
    assert "ANTHROPIC_API_KEY" not in plan_env and "ANTHROPIC_AUTH_TOKEN" not in plan_env
    secrets.set("ANTHROPIC_API_KEY", SECRET)
    key_env = ai_setup.child_env("api_key")
    assert key_env["ANTHROPIC_API_KEY"] == SECRET
    for env in (plan_env, key_env):
        assert env["ENABLE_TOOL_SEARCH"] == "false"
        assert env["LEADGEN_HOME"] == str(paths.home())


def test_outdated_version(ready_db, fake_claude, monkeypatch):
    monkeypatch.setattr(ai_setup, "cli_version", lambda binary: "2.0.5")
    assert ai_setup.get_status(True)["claude"]["outdated"] is True
    assert ai_setup._outdated("2.1.0") is False


def test_install_help_by_platform(monkeypatch):
    expected = {
        "win32": ("windows", "irm https://claude.ai/install.ps1 | iex"),
        "darwin": ("mac", "curl -fsSL https://claude.ai/install.sh | bash"),
        "linux": ("linux", "curl -fsSL https://claude.ai/install.sh | bash"),
    }
    for platform, (name, command) in expected.items():
        monkeypatch.setattr(ai_setup.sys, "platform", platform)
        help_ = ai_setup.install_help()
        assert help_["platform"] == name and help_["command"] == command


# --- sign in with a Claude plan -----------------------------------------------------------------


def _wait(predicate, limit: float = 5.0) -> dict:
    """Poll `login_state()` until `predicate` holds (at most `limit` seconds)."""
    end = time.monotonic() + limit
    state = ai_setup.login_state()
    while not predicate(state) and time.monotonic() < end:
        time.sleep(0.1)
        state = ai_setup.login_state()
    return state


def test_login_flow(ready_db, fake_claude, monkeypatch):
    fake_claude.auth("out")
    monkeypatch.setenv("FAKE_CLAUDE_LOGIN_SECONDS", "2")
    assert ai_setup.get_status(True)["plan"]["signed_in"] is False

    assert ai_setup.start_login() == {"ok": True, "started": True}
    assert ai_setup.start_login() == {"ok": True, "started": False}
    waiting = _wait(lambda s: s["url"] != "")
    assert waiting["state"] == "waiting" and waiting["url"].startswith("https://claude.ai/")

    assert _wait(lambda s: s["state"] == "done")["state"] == "done"
    assert ai_setup.get_status(True)["plan"]["signed_in"] is True


def test_login_needs_the_program(monkeypatch):
    _no_program(monkeypatch)
    with pytest.raises(ai_setup.AiSetupError) as err:
        ai_setup.start_login()
    assert err.value.code == "claude_missing"


# --- API key -------------------------------------------------------------------------------------


def test_save_api_key(ready_db, monkeypatch):
    with pytest.raises(ai_setup.AiSetupError) as err:
        ai_setup.save_api_key("not-a-key")
    assert err.value.code == "invalid_key"

    with respx.mock() as mock:
        mock.get(ai_setup.MODELS_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(ai_setup.AiSetupError) as err:
            ai_setup.save_api_key(SECRET)
        assert err.value.code == "key_rejected"
    assert secrets.stored("ANTHROPIC_API_KEY") == ""

    with respx.mock() as mock:
        mock.get(ai_setup.MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        assert ai_setup.save_api_key(SECRET) == {"ok": True, "masked": MASKED}
    assert secrets.stored("ANTHROPIC_API_KEY") == SECRET

    with respx.mock() as mock:
        mock.get(ai_setup.MODELS_URL).mock(return_value=httpx.Response(429))
        assert ai_setup.save_api_key(SECRET)["ok"] is True

    with respx.mock() as mock:
        mock.get(ai_setup.MODELS_URL).mock(side_effect=httpx.ConnectError("no route"))
        with pytest.raises(ai_setup.AiSetupError) as err:
            ai_setup.save_api_key(SECRET)
        assert err.value.code == "network"

    with respx.mock() as mock:
        mock.get(ai_setup.MODELS_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(ai_setup.AiSetupError) as err:
            ai_setup.save_api_key(SECRET)
        assert err.value.code == "unavailable"


def test_save_api_key_makes_no_request_with_fakes(ready_db, monkeypatch):
    monkeypatch.setenv("LEADGEN_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("LEADGEN_ALLOW_FAKES", "1")
    with respx.mock(assert_all_mocked=True) as mock:
        assert ai_setup.save_api_key(SECRET)["ok"] is True
        assert mock.calls.call_count == 0


def test_check_connection_without_the_program(ready_db, monkeypatch):
    _no_program(monkeypatch)
    with pytest.raises(ai_setup.AiSetupError) as err:
        ai_setup.check_connection()
    assert err.value.code == "claude_missing"


def test_check_connection_branches(ready_db, fake_claude):
    with pytest.raises(ai_setup.AiSetupError) as err:
        ai_setup.check_connection()
    assert err.value.code == "no_mode"

    preferences.set_ai_mode("claude_plan")
    fake_claude.auth("out")
    with pytest.raises(ai_setup.AiSetupError) as err:
        ai_setup.check_connection()
    assert err.value.code == "not_signed_in"
    fake_claude.auth("in")
    assert ai_setup.check_connection() == {"ok": True, "connected": True, "message": "Connected"}

    preferences.set_ai_mode("api_key")
    with pytest.raises(ai_setup.AiSetupError) as err:
        ai_setup.check_connection()
    assert err.value.code == "no_api_key"

    secrets.set("ANTHROPIC_API_KEY", SECRET)
    with respx.mock() as mock:
        mock.get(ai_setup.MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        assert ai_setup.check_connection()["connected"] is True
        assert mock.calls.call_count == 1


# --- the routes ----------------------------------------------------------------------------------


@pytest.fixture
def client(ready_db, fake_claude):
    return TestClient(dash.create_app(), base_url=BASE)


def test_get_ai_shape(client):
    reply = client.get("/api/ai")
    assert reply.status_code == 200
    body = reply.json()
    assert set(body) == {"ok", "mode", "claude", "plan", "api_key", "login", "ready", "install"}
    assert body["ok"] is True and body["claude"]["installed"] is True


def test_post_ai_mode_routes(client):
    assert client.post("/api/ai/mode", json={"mode": "api_key"}).status_code == 403
    bad = client.post("/api/ai/mode", json={"mode": "other"}, headers=CSRF)
    assert bad.status_code == 422 and bad.json()["ok"] is False
    good = client.post("/api/ai/mode", json={"mode": "claude_plan"}, headers=CSRF)
    assert good.status_code == 200
    assert good.json()["mode"] == "claude_plan" and good.json()["ready"] is True


def test_post_ai_api_key_route_hides_the_key(client):
    with respx.mock() as mock:
        mock.get(ai_setup.MODELS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
        reply = client.post("/api/ai/api-key", json={"key": SECRET}, headers=CSRF)
    assert reply.status_code == 200 and reply.json() == {"ok": True, "masked": MASKED}
    assert SECRET not in reply.text


def test_post_ai_login_without_the_program(client, monkeypatch):
    _no_program(monkeypatch)
    reply = client.post("/api/ai/login", headers=CSRF)
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "claude_missing"


# --- the fake itself -----------------------------------------------------------------------------


def test_fake_claude_script_itself(ready_db, fake_claude):
    fake_claude.auth("out")

    def run(*args, stdin=None):
        return subprocess.run(
            [sys.executable, str(FAKE), *args], input=stdin, capture_output=True, text=True,
            encoding="utf-8", env=ai_setup.child_env("claude_plan"), timeout=30,
        )  # fmt: skip

    assert run("--version").stdout.startswith("2.1.310")
    assert run("auth", "status", "--json").returncode == 1
    fake_claude.auth("in")
    signed = run("auth", "status", "--json")
    assert signed.returncode == 0 and json.loads(signed.stdout)["loggedIn"] is True

    done = run("-p", "--session-id", "11111111-2222-4333-8444-555555555555", stdin="hello")
    assert done.returncode == 0
    events = [json.loads(line) for line in done.stdout.splitlines()]
    assert events[-1]["type"] == "result"
    assert fake_claude.calls()[-1]["prompt"] == "hello"
