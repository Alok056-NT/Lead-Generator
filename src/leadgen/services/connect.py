"""Connect Lead Generator to AI apps: Claude Desktop (config file) and Claude Code (CLI).

`connect_claude` adds one entry to Claude Desktop's `claude_desktop_config.json` and keeps every
other server and setting. The file is backed up first, and a file that is not valid JSON is never
overwritten. ChatGPT is not connected here: it needs the public tunnel (`services/tunnel.py`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from leadgen.core.fsutil import atomic_write_text

SERVER_NAME = "lead-generator"
CONFIG_NAME = "claude_desktop_config.json"
RESTART_NOTE = (
    "Now fully quit Claude Desktop (on Windows: right-click its icon in the system tray, "
    "then Quit; on macOS: Claude menu, then Quit) and open it again."
)


class ConnectError(Exception):
    """A connection step failed. The message is plain English and says what to do next."""


# --- finding things -----------------------------------------------------------------------------


def claude_desktop_config_path() -> Path:
    """Where Claude Desktop keeps its settings (Windows `%APPDATA%\\Claude`, macOS Library).

    Raises ConnectError on other systems: Claude Desktop's location there is not documented.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "Claude" / CONFIG_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / CONFIG_NAME
    raise ConnectError(
        "I don't know where Claude Desktop keeps its settings on this system. "
        "Use Claude Code instead (`leadgen connect claude-code`), or pass the file with --config."
    )


def leadgen_launcher() -> tuple[str, list[str]]:
    """The command and arguments that start the MCP server from this installation.

    Uses the `leadgen` program next to the running Python (inside the venv) when it exists,
    else `python -m leadgen`. The path is made absolute but never resolved: on macOS/Linux a venv's
    `bin/python` is a symlink, and following it would land outside the venv.
    """
    python = Path(sys.executable).absolute()
    scripts = python.parent
    exe = scripts / ("leadgen.exe" if os.name == "nt" else "leadgen")
    if exe.exists():
        return str(exe), ["mcp"]
    return str(python), ["-m", "leadgen", "mcp"]


def server_entry() -> dict[str, Any]:
    """The `mcpServers["lead-generator"]` value. `env` is added only if LEADGEN_HOME is set."""
    command, args = leadgen_launcher()
    entry: dict[str, Any] = {"command": command, "args": args}
    home = os.environ.get("LEADGEN_HOME")
    if home:
        entry["env"] = {"LEADGEN_HOME": home}
    return entry


# --- Claude Desktop -------------------------------------------------------------------------------


def _backup(path: Path, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    counter = 1
    while target.exists():
        counter += 1
        target = path.with_name(f"{path.stem}.backup-{stamp}-{counter}{path.suffix}")
    shutil.copy2(path, target)
    return target


def _load(path: Path, now: datetime) -> tuple[dict[str, Any], Path | None]:
    """Read the config: `(data, backup_path)`. A file that is not valid is only backed up."""
    if not path.exists():
        return {}, None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise ConnectError(f"I could not read {path}. Nothing was changed.") from None
    backup = _backup(path, now)
    if not text.strip():
        return {}, backup
    try:
        data = json.loads(text)
    except ValueError:
        raise ConnectError(
            f"Claude Desktop's settings file ({path}) is not valid JSON, so I left it alone. "
            f"A copy is saved as {backup.name}. Fix or delete the file, then run this again."
        ) from None
    if not isinstance(data, dict):
        raise ConnectError(
            f"Claude Desktop's settings file ({path}) is not in the expected form, so I left it "
            f"alone. A copy is saved as {backup.name}."
        )
    return data, backup


def _write(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    json.loads(text)  # must parse again before it replaces the old file
    atomic_write_text(path, text)
    json.loads(path.read_text(encoding="utf-8"))


def _servers(data: dict[str, Any], path: Path, backup: Path | None) -> dict[str, Any]:
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        saved = f" A copy is saved as {backup.name}." if backup else ""
        raise ConnectError(
            f"The 'mcpServers' part of {path} is not a list of servers, so I left it alone.{saved}"
        )
    return servers


def _drop_backup(backup: Path | None) -> None:
    """Remove a backup made for a run that changed nothing."""
    if backup is not None:
        try:
            backup.unlink()
        except OSError:
            pass


def connect_claude(
    config_path: Path | None = None,
    entry: dict[str, Any] | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, Any]:
    """Add or update the Lead Generator entry in Claude Desktop's settings file.

    Every other key and server is kept. The old file is copied to
    `claude_desktop_config.backup-<timestamp>.json` first. Returns `{path, backup, action,
    command, restart}` where `action` is `added`, `updated` or `unchanged`.
    """
    path = config_path or claude_desktop_config_path()
    new_entry = entry or server_entry()
    data, backup = _load(path, now())
    servers = _servers(data, path, backup)
    old = servers.get(SERVER_NAME)
    if old == new_entry:
        _drop_backup(backup)
        return {
            "path": str(path), "backup": None, "action": "unchanged",
            "command": new_entry["command"], "restart": RESTART_NOTE,
        }  # fmt: skip
    servers[SERVER_NAME] = new_entry
    _write(path, data)
    return {
        "path": str(path),
        "backup": str(backup) if backup else None,
        "action": "added" if old is None else "updated",
        "command": new_entry["command"],
        "restart": RESTART_NOTE,
    }


def disconnect_claude(
    config_path: Path | None = None, now: Callable[[], datetime] = datetime.now
) -> dict[str, Any]:
    """Remove only the Lead Generator entry from Claude Desktop's settings file."""
    path = config_path or claude_desktop_config_path()
    data, backup = _load(path, now())
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        _drop_backup(backup)
        return {"path": str(path), "backup": None, "action": "not_connected"}
    del servers[SERVER_NAME]
    _write(path, data)
    return {
        "path": str(path),
        "backup": str(backup) if backup else None,
        "action": "removed",
        "restart": RESTART_NOTE,
    }


# --- Claude Code ----------------------------------------------------------------------------------


def _quote(text: str) -> str:
    return f'"{text}"' if " " in text else text


def claude_code_command() -> list[str]:
    """`claude mcp add` as an argument list (user scope: available in every project)."""
    command, args = leadgen_launcher()
    cmd = ["claude", "mcp", "add", "--scope", "user"]
    home = os.environ.get("LEADGEN_HOME")
    if home:
        cmd += ["-e", f"LEADGEN_HOME={home}", "--scope", "user"]
    return [*cmd, SERVER_NAME, "--", command, *args]


def _result(ran: bool, command: str, message: str) -> dict[str, Any]:
    return {"ran": ran, "command": command, "message": message}


def connect_claude_code(
    run: Callable[..., Any] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Run `claude mcp add` when the `claude` program is installed, else return the command.

    Safe to repeat: an earlier entry with the same name is replaced.
    """
    run = run or subprocess.run
    cmd = claude_code_command()
    printable = " ".join(_quote(part) for part in cmd)
    claude = (which or shutil.which)("claude")
    if not claude:
        return _result(False, printable, "Claude Code is not installed here. Run this where it is:")
    cmd[0] = claude
    remove = [claude, "mcp", "remove", "--scope", "user", SERVER_NAME]
    options: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 60}
    options.update(encoding="utf-8", errors="replace")
    try:
        run(remove, **options)
        done = run(cmd, **options)
    except (OSError, subprocess.SubprocessError):
        return _result(False, printable, "I could not start Claude Code. Run this yourself:")
    if done.returncode != 0:
        return _result(False, printable, "Claude Code refused it. Run this yourself to see why:")
    return _result(True, printable, "Added to Claude Code. Start a new Claude Code session.")
