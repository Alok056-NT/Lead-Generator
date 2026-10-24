"""Starting the dashboard in the background and knowing whether it is running."""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn

from leadgen import cli, db
from leadgen.core import config
from leadgen.dashboard import app as dash
from leadgen.services import dashboard_ctl as ctl


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def set_address(host="127.0.0.1", port=8765):
    settings = config.load_settings()
    settings.dashboard.host, settings.dashboard.port = host, port
    config.save_settings(settings)


class FakeProc:
    def __init__(self, exit_code=None):
        self._code = exit_code

    def poll(self):
        return self._code


@pytest.fixture
def running_server():
    """A real dashboard on a free loopback port (loopback only, no outside network)."""
    db.init_db()
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(dash.create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield port
    server.should_exit = True
    thread.join(timeout=10)


def test_url_for_brackets_ipv6():
    assert ctl.url_for("127.0.0.1", 8765) == "http://127.0.0.1:8765/"
    assert ctl.url_for("::1", 8765) == "http://[::1]:8765/"


def test_is_running_recognises_only_the_dashboard(running_server):
    assert ctl.is_running("127.0.0.1", running_server) is True
    assert ctl.is_running("127.0.0.1", free_port()) is False


def test_is_running_ignores_other_web_servers(monkeypatch):
    class Other:
        status_code = 200

        def json(self):
            return {"hello": "world"}

    monkeypatch.setattr(ctl.httpx, "get", lambda *a, **k: Other())
    assert ctl.is_running("127.0.0.1", 1) is False

    class Html:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(ctl.httpx, "get", lambda *a, **k: Html())
    assert ctl.is_running("127.0.0.1", 1) is False


def test_ensure_running_reuses_a_running_dashboard(running_server, monkeypatch):
    set_address(port=running_server)
    monkeypatch.setattr(ctl, "_spawn", lambda port: pytest.fail("must not start another"))
    assert ctl.ensure_running() == {
        "ok": True,
        "url": f"http://127.0.0.1:{running_server}/",
        "started": False,
    }


def test_ensure_running_starts_a_process_and_waits_for_it(monkeypatch):
    set_address(port=8790)
    started = []
    answers = iter([False, False, False, True])
    monkeypatch.setattr(ctl, "is_running", lambda host, port: next(answers))
    monkeypatch.setattr(ctl, "_port_taken", lambda host, port: False)
    monkeypatch.setattr(ctl, "_spawn", lambda port: started.append(port) or FakeProc())
    monkeypatch.setattr(ctl.time, "sleep", lambda s: None)
    reply = ctl.ensure_running()
    assert reply == {"ok": True, "url": "http://127.0.0.1:8790/", "started": True}
    assert started == [8790]


def test_ensure_running_reports_a_busy_port(monkeypatch):
    set_address(port=8790)
    monkeypatch.setattr(ctl, "is_running", lambda host, port: False)
    monkeypatch.setattr(ctl, "_port_taken", lambda host, port: True)
    monkeypatch.setattr(ctl, "_spawn", lambda port: pytest.fail("must not start"))
    reply = ctl.ensure_running()
    assert reply["ok"] is False and reply["error"]["code"] == "port_busy"
    assert "8790" in reply["error"]["message"] and reply["error"]["hint"]


def test_ensure_running_reports_a_process_that_dies_at_once(monkeypatch):
    set_address()
    monkeypatch.setattr(ctl, "is_running", lambda host, port: False)
    monkeypatch.setattr(ctl, "_port_taken", lambda host, port: False)
    monkeypatch.setattr(ctl, "_spawn", lambda port: FakeProc(exit_code=1))
    reply = ctl.ensure_running()
    assert reply["ok"] is False and reply["error"]["code"] == "start_failed"


def test_ensure_running_times_out(monkeypatch):
    set_address()
    monkeypatch.setattr(ctl, "is_running", lambda host, port: False)
    monkeypatch.setattr(ctl, "_port_taken", lambda host, port: False)
    monkeypatch.setattr(ctl, "_spawn", lambda port: FakeProc())
    monkeypatch.setattr(ctl.time, "sleep", lambda s: None)
    clock = iter([0.0, 0.0, 1.0, 99.0, 99.0])
    monkeypatch.setattr(ctl.time, "monotonic", lambda: next(clock))
    assert ctl.ensure_running(wait_s=5)["error"]["code"] == "start_failed"


def test_ensure_running_refuses_a_public_host():
    set_address(host="0.0.0.0")
    reply = ctl.ensure_running()
    assert reply["ok"] is False and reply["error"]["code"] == "bad_host"


def test_spawn_command_is_detached_and_uses_this_python(monkeypatch):
    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            seen["cmd"], seen["kwargs"] = cmd, kwargs

    monkeypatch.setattr(ctl.subprocess, "Popen", FakePopen)
    ctl._spawn(8801)
    assert seen["cmd"][1:] == ["-m", "leadgen", "dashboard", "--port", "8801"]
    assert seen["cmd"][0] == ctl.sys.executable
    assert seen["kwargs"]["stdin"] == ctl.subprocess.DEVNULL
    detached = "creationflags" in seen["kwargs"] or "start_new_session" in seen["kwargs"]
    assert detached


def test_spawn_falls_back_when_the_job_does_not_allow_breakaway(monkeypatch):
    if ctl.os.name != "nt":
        pytest.skip("Windows job objects")
    calls = []

    def popen(cmd, **kwargs):
        calls.append(kwargs["creationflags"])
        if len(calls) == 1:
            raise OSError("access denied")
        return FakeProc()

    monkeypatch.setattr(ctl.subprocess, "Popen", popen)
    ctl._spawn(8801)
    assert len(calls) == 2 and calls[0] & ctl.subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert not calls[1] & ctl.subprocess.CREATE_BREAKAWAY_FROM_JOB


def test_pid_file_is_written_and_only_removed_by_its_owner():
    ctl.write_pid(8765)
    data = json.loads(ctl.pid_file().read_text(encoding="utf-8"))
    assert data["port"] == 8765 and data["pid"] == ctl.os.getpid()
    ctl.pid_file().write_text(json.dumps({"pid": -5, "port": 1}), encoding="utf-8")
    ctl.clear_pid()
    assert ctl.pid_file().exists()  # someone else's file stays
    ctl.write_pid(8765)
    ctl.clear_pid()
    assert not ctl.pid_file().exists()
    ctl.clear_pid()  # nothing there: no error
    ctl.pid_file().write_text("not json", encoding="utf-8")
    ctl.clear_pid()


def test_serve_writes_and_clears_the_pid_file(monkeypatch, capsys):
    set_address(port=8799)
    seen = {}

    def fake_run(app, **kwargs):
        seen.update(kwargs)
        seen["pid_file_during_run"] = ctl.pid_file().exists()

    monkeypatch.setattr(uvicorn, "run", fake_run)
    assert dash.serve() == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8799 and seen["access_log"] is False
    assert seen["pid_file_during_run"] is True and not ctl.pid_file().exists()
    assert "http://127.0.0.1:8799/" in capsys.readouterr().out
    assert dash.serve(8800) == 0 and seen["port"] == 8800


# --- command line --------------------------------------------------------------------------------


def test_cli_dashboard_runs_the_app(monkeypatch):
    seen = []
    monkeypatch.setattr(dash, "serve", lambda port=None: seen.append(port) or 0)
    assert cli.main(["dashboard", "--port", "8811"]) == 0
    assert cli.main(["dashboard"]) == 0
    assert seen == [8811, None]


def test_cli_dashboard_open_flag_opens_the_browser(monkeypatch):
    import threading as th
    import webbrowser

    timers = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            timers.append((delay, fn, args))

        def start(self):
            pass

    monkeypatch.setattr(th, "Timer", FakeTimer)
    monkeypatch.setattr(dash, "serve", lambda port=None: 0)
    assert cli.main(["dashboard", "--open", "--port", "8812"]) == 0
    assert timers[0][1] is webbrowser.open and timers[0][2] == ("http://127.0.0.1:8812/",)


def test_console_python_prefers_the_console_build_on_windows(tmp_path):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    gui, console = scripts / "pythonw.exe", scripts / "python.exe"
    gui.write_bytes(b"")
    console.write_bytes(b"")
    assert ctl._console_python(str(gui), windows=True) == str(console)
    assert ctl._console_python(str(gui), windows=False) == str(gui)
    assert ctl._console_python(str(console), windows=True) == str(console)
    console.unlink()
    assert ctl._console_python(str(gui), windows=True) == str(gui)
