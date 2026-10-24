"""Start the dashboard as a background process and find out whether it is running.

`open_dashboard` (MCP) and `leadgen dashboard --open` use this. The dashboard writes a pid file
in the workspace while it runs; whether it is really up is decided by asking it (`/api/ping`),
not by the pid, because pids are reused.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from leadgen.core import config, paths

APP_ID = "leadgen-dashboard"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
START_WAIT_S = 12.0


def pid_file() -> Path:
    """Where the running dashboard records its process id and port."""
    return paths.home() / "dashboard.pid"


def url_for(host: str, port: int) -> str:
    """The address to open in a browser."""
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}/"


def _settings_address() -> tuple[str, int]:
    dash = config.load_settings().dashboard
    return dash.host or "127.0.0.1", int(dash.port)


def is_running(host: str, port: int) -> bool:
    """True when a Lead Generator dashboard answers on host:port."""
    try:
        reply = httpx.get(url_for(host, port) + "api/ping", timeout=1.5, trust_env=False)
        return bool(reply.status_code == 200 and reply.json().get("app") == APP_ID)
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


def _port_taken(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def write_pid(port: int) -> None:
    """Record this process as the running dashboard."""
    pid_file().write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")


def clear_pid() -> None:
    """Remove the pid file if it names this process."""
    try:
        data = json.loads(pid_file().read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            pid_file().unlink()
    except (OSError, ValueError, AttributeError):
        pass


def _console_python(executable: str | None = None, windows: bool | None = None) -> str:
    """The Python that runs the background dashboard: a desktop shortcut starts `pythonw.exe` (no
    console, no stdout), but the server wants the console build."""
    exe = Path(executable or sys.executable)
    nt = (os.name == "nt") if windows is None else windows
    if nt and exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return str(console)
    return str(exe)


def _spawn(port: int) -> subprocess.Popen[bytes]:
    cmd = [_console_python(), "-m", "leadgen", "dashboard", "--port", str(port)]
    common: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:  # leave the caller's job object so the page outlives a closing chat app
            return subprocess.Popen(
                cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **common
            )
        except OSError:
            return subprocess.Popen(cmd, creationflags=flags, **common)
    return subprocess.Popen(cmd, start_new_session=True, **common)


def _error(code: str, message: str, hint: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def ensure_running(wait_s: float = START_WAIT_S) -> dict[str, Any]:
    """Make sure the dashboard is up; start it in the background if not. Returns its `url`.

    The address comes from settings (`dashboard.host`, `dashboard.port`). Never raises for a
    start-up problem: the reply is `{"ok": False, "error": ...}` saying what to do.
    """
    host, port = _settings_address()
    if host not in LOOPBACK:
        return _error(
            "bad_host",
            "The dashboard only runs on this computer (127.0.0.1).",
            "Set dashboard.host to 127.0.0.1 in settings.yaml.",
        )
    url = url_for(host, port)
    if is_running(host, port):
        return {"ok": True, "url": url, "started": False}
    if _port_taken(host, port):
        return _error(
            "port_busy",
            f"Port {port} is used by another program.",
            "Change dashboard.port in settings.yaml (for example 8777) and try again.",
        )
    proc = _spawn(port)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_running(host, port):
            return {"ok": True, "url": url, "started": True}
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    return _error(
        "start_failed",
        "The dashboard did not start.",
        "Run `leadgen dashboard` in a terminal to see the problem, or check logs/leadgen.log.",
    )
