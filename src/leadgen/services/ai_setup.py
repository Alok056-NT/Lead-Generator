"""Connect your AI (v1.2): find Claude Code, sign in, API key, test. Raises `AiSetupError`.

Claude Code is the program that does the thinking; the person either signs in with their Claude
plan or gives an Anthropic API key. Nothing here sends chat messages (that is `chat.py`) and no
key is ever returned or logged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from leadgen.core import paths, secrets
from leadgen.providers import fake
from leadgen.services import preferences

CLAUDE_BIN_ENV = "LEADGEN_CLAUDE_BIN"
API_KEY_NAME = "ANTHROPIC_API_KEY"
MODELS_URL = "https://api.anthropic.com/v1/models"
MIN_VERSION = (2, 1, 0)
HELP_URL = "https://code.claude.com/docs/en/setup"
LOGIN_TIMEOUT_S = 600.0
CACHE_TTL_S = 15.0
ENV_EXTRA = {
    "ENABLE_TOOL_SEARCH": "false",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}
_INSTALL = {
    "windows": "irm https://claude.ai/install.ps1 | iex",
    "mac": "curl -fsSL https://claude.ai/install.sh | bash",
    "linux": "curl -fsSL https://claude.ai/install.sh | bash",
}
_KEY_SHAPE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$")
_URL = re.compile(r"https://[^\s\"'<>]+")
_CACHE: dict[str, Any] = {"at": 0.0, "key": "", "version": "", "plan": None}
_LOGIN: dict[str, Any] = {"proc": None, "started": 0.0, "last": ""}


class AiSetupError(Exception):
    """A step of connecting the AI failed. `code` is stable text; `hint` says what to do."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_response(self) -> dict[str, Any]:
        """The `{"ok": False, "error": {...}}` shape used by every service."""
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "hint": self.hint},
        }


# --- finding and running Claude Code ------------------------------------------------------------


def launcher(binary: str) -> list[str]:
    """The command that runs `binary`; a `.py` file (the fake used by tests) runs under this
    Python so the same test works on Windows, macOS and Linux."""
    return [sys.executable, binary] if binary.lower().endswith(".py") else [binary]


def _candidates() -> list[Path]:
    home = Path.home()
    return [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude"]


def find_claude() -> str | None:
    """`LEADGEN_CLAUDE_BIN` if that file exists, else `claude` on the PATH, else the usual
    install place under the home folder, else None."""
    override = os.environ.get(CLAUDE_BIN_ENV, "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("claude")
    if found:
        return found
    for path in _candidates():
        if path.is_file():
            return str(path)
    return None


def child_env(mode: str) -> dict[str, str]:
    """The environment Claude Code runs in. Plan mode never carries an API key (a key set for the
    whole computer must not bill the person); API-key mode carries the saved key."""
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(name, None)
    if mode == "api_key":
        env[API_KEY_NAME] = secrets.get(API_KEY_NAME)
    env.update(ENV_EXTRA)
    env["LEADGEN_HOME"] = str(paths.home())
    return env


def _hidden() -> dict[str, Any]:
    """Popen options that keep a console window from flashing on Windows."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _run(
    binary: str, args: list[str], timeout: float = 20.0
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [*launcher(binary), *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
            env=child_env("claude_plan"), **_hidden(),
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None


def clear_cache() -> None:
    """Forget the cached program checks (tests, and the end of a sign-in)."""
    _CACHE.update(at=0.0, key="", version="", plan=None)


def cli_version(binary: str) -> str:
    """`claude --version` as "2.1.310", or "" when it cannot be read."""
    done = _run(binary, ["--version"], 10.0)
    words = done.stdout.split() if done and done.returncode == 0 else []
    return words[0] if words else ""


def _signed_out() -> dict[str, Any]:
    return {"signed_in": False, "email": "", "plan": ""}


def auth_status(binary: str) -> dict[str, Any]:
    """Whether the person is signed in with a plan: `{"signed_in", "email", "plan"}`. A signed-out
    program answers with exit code 1 and JSON `loggedIn: false`; anything unreadable counts as
    signed out."""
    out = _signed_out()
    done = _run(binary, ["auth", "status", "--json"])
    if done is None:
        return out
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return out
    if isinstance(data, dict) and data.get("loggedIn"):
        out.update(
            signed_in=True, email=str(data.get("email") or ""),
            plan=str(data.get("subscriptionType") or ""),
        )
    return out


def _checks(binary: str, refresh: bool) -> tuple[str, dict[str, Any]]:
    """`(version, plan)` with a short cache so the page can poll."""
    fresh = (time.monotonic() - _CACHE["at"]) < CACHE_TTL_S and _CACHE["key"] == binary
    if not refresh and fresh:
        return _CACHE["version"], _CACHE["plan"]
    version, plan = cli_version(binary), auth_status(binary)
    _CACHE.update(at=time.monotonic(), key=binary, version=version, plan=plan)
    return version, plan


def _outdated(version: str) -> bool:
    try:
        return tuple(int(part) for part in version.split(".")[:3]) < MIN_VERSION
    except ValueError:
        return False


def install_help() -> dict[str, str]:
    """The platform name, the one-line install command and the install page."""
    if sys.platform == "win32":
        platform = "windows"
    elif sys.platform == "darwin":
        platform = "mac"
    else:
        platform = "linux"
    return {"platform": platform, "command": _INSTALL[platform], "help_url": HELP_URL}


# --- sign in with a Claude plan -----------------------------------------------------------------


def _login_file() -> Path:
    return paths.cache_dir() / "claude-login.txt"


def start_login() -> dict[str, Any]:
    """Start `claude auth login --claudeai` (it opens the browser and waits). Returns at once."""
    binary = find_claude()
    if binary is None:
        raise AiSetupError(
            "claude_missing", "Claude Code is not installed yet.",
            "Install it first, then press Check again.",
        )  # fmt: skip
    proc = _LOGIN["proc"]
    if proc is not None and proc.poll() is None:
        return {"ok": True, "started": False}
    with _login_file().open("wb") as out:
        _LOGIN["proc"] = subprocess.Popen(
            [*launcher(binary), "auth", "login", "--claudeai"], stdin=subprocess.DEVNULL,
            stdout=out, stderr=subprocess.STDOUT, env=child_env("claude_plan"), **_hidden(),
        )  # fmt: skip
    _LOGIN.update(started=time.monotonic(), last="")
    return {"ok": True, "started": True}


def login_state() -> dict[str, str]:
    """`{"state": idle|waiting|done|failed, "url": ...}`; `url` is the sign-in address the program
    printed, if any (never logged)."""
    proc = _LOGIN["proc"]
    if proc is None:
        return {"state": _LOGIN["last"] or "idle", "url": ""}
    code = proc.poll()
    if code is None and time.monotonic() - _LOGIN["started"] > LOGIN_TIMEOUT_S:
        proc.kill()
        code = proc.wait()
    if code is None:
        try:
            found = _URL.search(_login_file().read_text(encoding="utf-8", errors="replace"))
        except OSError:
            found = None
        return {"state": "waiting", "url": found.group(0) if found else ""}
    _LOGIN.update(proc=None, last="done" if code == 0 else "failed")
    clear_cache()
    try:
        _login_file().unlink()
    except OSError:
        pass
    return {"state": _LOGIN["last"], "url": ""}


# --- API key -------------------------------------------------------------------------------------


def check_api_key(key: str) -> None:
    """Ask Anthropic (a free call) whether the key works. Raises `AiSetupError`."""
    if fake.fakes_enabled():  # tests and the click-through test: no outside call
        return
    try:
        reply = httpx.get(
            MODELS_URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=15.0
        )
    except httpx.HTTPError:
        raise AiSetupError(
            "network", "I could not reach Anthropic.",
            "Check your internet connection and try again.",
        ) from None  # fmt: skip
    if reply.status_code in (401, 403):
        raise AiSetupError(
            "key_rejected", "Anthropic did not accept that key.",
            "Copy it again from console.anthropic.com, or make a new one.",
        )  # fmt: skip
    if not (reply.is_success or reply.status_code == 429):
        raise AiSetupError(
            "unavailable", "Anthropic did not answer properly just now.", "Try again in a minute."
        )  # fmt: skip


def save_api_key(key: str) -> dict[str, Any]:
    """Check the key and save it in `secrets.env`; returns only the masked form."""
    key = (key or "").strip()
    if not _KEY_SHAPE.match(key):
        raise AiSetupError(
            "invalid_key", "That does not look like an Anthropic API key.",
            "It starts with sk-ant- and is shown once on console.anthropic.com.",
        )  # fmt: skip
    check_api_key(key)
    secrets.set(API_KEY_NAME, key)
    return {"ok": True, "masked": secrets.masked(API_KEY_NAME)}


# --- the whole picture ---------------------------------------------------------------------------


def get_status(refresh: bool = False) -> dict[str, Any]:
    """Everything the Connect screen and the chat need to know (never a key)."""
    binary = find_claude()
    version, plan = _checks(binary, refresh) if binary else ("", _signed_out())
    mode = preferences.get_ai_mode()
    configured = bool(secrets.get(API_KEY_NAME))
    ready = bool(binary) and (
        (mode == "claude_plan" and plan["signed_in"]) or (mode == "api_key" and configured)
    )
    return {
        "ok": True, "mode": mode,
        "claude": {"installed": bool(binary), "version": version, "outdated": _outdated(version)},
        "plan": plan,
        "api_key": {"configured": configured, "masked": secrets.masked(API_KEY_NAME)},
        "login": login_state(), "ready": ready, "install": install_help(),
    }  # fmt: skip


def check_connection() -> dict[str, Any]:
    """Check that the chosen way of connecting works right now. Raises `AiSetupError`."""
    status = get_status(refresh=True)
    if not status["claude"]["installed"]:
        raise AiSetupError(
            "claude_missing", "Claude Code is not installed yet.",
            "Install it first, then press Check again.",
        )  # fmt: skip
    if status["mode"] == "claude_plan":
        if not status["plan"]["signed_in"]:
            raise AiSetupError(
                "not_signed_in", "You are not signed in to Claude yet.", "Press Sign in to Claude."
            )  # fmt: skip
    elif status["mode"] == "api_key":
        key = secrets.get(API_KEY_NAME)
        if not key:
            raise AiSetupError(
                "no_api_key", "There is no API key saved yet.",
                "Paste your key and press Save and check.",
            )  # fmt: skip
        check_api_key(key)
    else:
        raise AiSetupError(
            "no_mode", "Choose how to connect first.", "Pick Use my Claude plan or Use an API key."
        )  # fmt: skip
    return {"ok": True, "connected": True, "message": "Connected"}
