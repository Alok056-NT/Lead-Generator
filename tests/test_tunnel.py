"""Tests for `leadgen serve --public` (no real processes, no network)."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from leadgen import cli, mcp_server
from leadgen.core import config
from leadgen.services import tunnel

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "S3cretS3cretS3cretS3cret"


def test_url_is_parsed_from_a_captured_log():
    lines = (FIXTURES / "cloudflared_quick.log").read_text(encoding="utf-8").splitlines()
    assert tunnel.find_tunnel_url(lines) == "https://example-one-two-three.trycloudflare.com"


def test_urls_that_are_not_the_tunnel_are_ignored():
    lines = (FIXTURES / "cloudflared_failed.log").read_text(encoding="utf-8").splitlines()
    assert tunnel.find_tunnel_url(lines) is None
    banner = "see https://www.cloudflare.com/website-terms/ and trycloudflare.com..."
    assert tunnel.parse_tunnel_url(banner) is None
    assert tunnel.parse_tunnel_url("|  HTTPS://Abc-Def.TryCloudflare.com  |") == (
        "https://abc-def.trycloudflare.com"
    )


def test_public_address_and_commands():
    assert tunnel.public_address("https://a-b.trycloudflare.com/", SECRET) == (
        f"https://a-b.trycloudflare.com/mcp/{SECRET}"
    )
    cmd = tunnel.tunnel_command("cf", 8766)
    assert cmd[:3] == ["cf", "tunnel", "--no-autoupdate"]
    assert cmd[cmd.index("--url") + 1] == "http://127.0.0.1:8766"
    assert cmd[cmd.index("--http-host-header") + 1] == "localhost:8766"
    server = tunnel.server_command(8766)
    assert server[1:] == ["-m", "leadgen", "mcp", "--http", "--port", "8766"]


def test_install_hints():
    assert tunnel.install_hint("win32") == "winget install --id Cloudflare.cloudflared"
    assert tunnel.install_hint("darwin") == "brew install cloudflared"
    assert "developers.cloudflare.com" in tunnel.install_hint("linux")


def test_find_cloudflared_path_then_common_folders(tmp_path):
    assert tunnel.find_cloudflared(lambda name: "/bin/cloudflared") == "/bin/cloudflared"
    pf = tmp_path / "pf" / "cloudflared"
    pf.mkdir(parents=True)
    env = {
        "ProgramFiles": str(tmp_path / "pf"),
        "ProgramFiles(x86)": str(tmp_path / "pf86"),
        "USERPROFILE": str(tmp_path),
    }
    assert tunnel.find_cloudflared(lambda n: None, env, "win32") is None
    (pf / "cloudflared.exe").write_text("", encoding="utf-8")
    assert tunnel.find_cloudflared(lambda n: None, env, "win32") == str(pf / "cloudflared.exe")
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "cloudflared").write_text("", encoding="utf-8")
    found = tunnel.find_cloudflared(lambda n: None, {"HOME": str(tmp_path)}, "linux")
    assert found == str(local / "cloudflared")


# --- run_public with fake child processes -------------------------------------------------------


class FakeProc:
    def __init__(self, lines=(), exit_code=None):
        self.stdout = iter(lines)
        self.code = exit_code
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.code = -9


@pytest.fixture
def fake_world(monkeypatch):
    world = {"procs": [], "commands": [], "listening": False}

    def popen(cmd):
        world["commands"].append(cmd)
        proc = world["procs"].pop(0)
        if len(world["commands"]) == 1:
            world["listening"] = proc.code is None  # the server is "up" unless it already exited
        return proc

    monkeypatch.setattr(tunnel, "_popen", popen)
    monkeypatch.setattr(tunnel, "port_in_use", lambda port, host="127.0.0.1": world["listening"])
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: "cf")
    monkeypatch.setattr(tunnel, "SERVER_WAIT_S", 0.5)
    monkeypatch.setattr(tunnel, "TUNNEL_WAIT_S", 0.5)
    monkeypatch.setattr(tunnel.time, "sleep", lambda s: None)
    return world


def run(world, **kwargs):
    out: list[str] = []
    code = tunnel.run_public(out=out.append, wait=kwargs.pop("wait", lambda: None), **kwargs)
    return code, "\n".join(out)


def test_run_prints_address_once_and_stops_both(fake_world):
    secret = config.ensure_mcp_secret()
    server = FakeProc(["Lead Generator MCP server: http://localhost:8766/mcp/" + secret])
    cloud = FakeProc((FIXTURES / "cloudflared_quick.log").read_text(encoding="utf-8").splitlines())
    fake_world["procs"] = [server, cloud]
    code, out = run(fake_world, port=8766)
    assert code == 0
    address = f"https://example-one-two-three.trycloudflare.com/mcp/{secret}"
    assert out.count(address) == 1
    for phrase in (
        "Add custom MCP server",
        "chatgpt.com/plugins",
        "anyone who has this address",
        "Ctrl+C",
        "No authentication",
    ):
        assert phrase.lower() in out.lower()
    assert server.terminated and cloud.terminated
    assert fake_world["commands"][0][1:4] == ["-m", "leadgen", "mcp"]
    assert fake_world["commands"][1][0] == "cf"
    assert "Stopped" in out


def test_cloudflared_missing_prints_install_command(monkeypatch, fake_world):
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: None)
    code, out = run(fake_world)
    assert code == 1 and tunnel.install_hint() in out
    assert fake_world["commands"] == []


def test_port_busy_stops_before_starting(monkeypatch, fake_world):
    monkeypatch.setattr(tunnel, "port_in_use", lambda port, host="127.0.0.1": True)
    code, out = run(fake_world, port=8766)
    assert code == 1 and "already in use" in out and fake_world["commands"] == []


def test_server_that_does_not_start_is_reported_without_the_secret(fake_world):
    secret = config.ensure_mcp_secret()
    crashed = FakeProc(
        [f"Lead Generator MCP server: http://localhost:8766/mcp/{secret}", "Traceback boom"],
        exit_code=1,
    )
    fake_world["procs"] = [crashed]
    code, out = run(fake_world, port=8766)
    assert code == 1 and "did not start" in out and "Traceback boom" in out
    assert secret not in out


def test_tunnel_that_gives_no_address_is_reported_and_cleaned_up(fake_world):
    server = FakeProc()
    cloud = FakeProc(["something else"], exit_code=1)
    fake_world["procs"] = [server, cloud]
    code, out = run(fake_world, port=8766)
    assert code == 1 and "secure link did not start" in out and "something else" in out
    assert server.terminated


def test_ctrl_c_switches_everything_off(fake_world):
    server, cloud = FakeProc(), FakeProc(["https://abc-def.trycloudflare.com"])
    fake_world["procs"] = [server, cloud]

    def interrupt():
        raise KeyboardInterrupt

    code, _ = run(fake_world, port=8766, wait=interrupt)
    assert code == 0 and server.terminated and cloud.terminated


def test_one_child_dying_ends_the_session_with_an_error(fake_world):
    server, cloud = FakeProc(), FakeProc(["https://abc-def.trycloudflare.com"])
    fake_world["procs"] = [server, cloud]
    code, out = run(fake_world, port=8766, wait=lambda: setattr(server, "code", 3))
    assert code == 1 and "stopped" in out.lower() and cloud.terminated


def test_rotate_secret_makes_a_new_one(fake_world):
    old = config.ensure_mcp_secret()
    fake_world["procs"] = [FakeProc(), FakeProc(["https://abc-def.trycloudflare.com"])]
    code, out = run(fake_world, port=8766, rotate_secret=True)
    new = config.load_settings().mcp_http.secret
    assert code == 0 and new != old and len(new) >= 32
    assert f"/mcp/{new}" in out and old not in out
    assert "no longer works" in out


def test_stop_escalates_to_kill():
    class Stubborn(FakeProc):
        def wait(self, timeout=None):
            if self.code != -9:
                raise subprocess.TimeoutExpired("x", timeout)
            return self.code

    proc = Stubborn()
    tunnel.stop(proc)
    assert proc.code == -9
    tunnel.stop(None)
    tunnel.stop(FakeProc(exit_code=0))


def test_port_in_use_sees_a_listener():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        assert tunnel.port_in_use(sock.getsockname()[1])
    assert not tunnel.port_in_use(1)


# --- CLI and the HTTP transport ---------------------------------------------------------------


def test_serve_without_flags_explains(capsys):
    assert cli.main(["serve"]) == 1
    assert "--public" in capsys.readouterr().out


def test_serve_rotate_secret_only(capsys):
    old = config.ensure_mcp_secret()
    assert cli.main(["serve", "--rotate-secret"]) == 0
    assert config.load_settings().mcp_http.secret != old
    assert old not in capsys.readouterr().out


def test_serve_public_passes_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(tunnel, "run_public", lambda **kw: seen.update(kw) or 0)
    assert cli.main(["serve", "--public", "--port", "8777", "--rotate-secret"]) == 0
    assert seen == {"port": 8777, "rotate_secret": True}


def test_mcp_http_json_mode_answers_plain_json():
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    app = mcp_server.http_app(mcp_server.build_server(remote=True), SECRET, json_response=True)
    with TestClient(app, base_url="http://localhost:8766") as client:
        reply = client.post(f"/mcp/{SECRET}", json=init, headers=headers)
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("application/json")


def test_run_http_asks_for_json_responses(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(mcp_server, "http_app", lambda *a, **kw: seen.update(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda cfg: type("S", (), {"run": lambda self: None})())
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **kw: object())
    mcp_server.run_http()
    assert seen.get("json_response") is True
