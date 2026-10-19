"""Tests for connecting Claude Desktop and Claude Code (no real Claude is touched)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from leadgen import cli
from leadgen.services import connect

NOW = datetime(2026, 10, 20, 9, 30, 15)
ENTRY = {"command": "C:\\venv\\Scripts\\leadgen.exe", "args": ["mcp"]}


def backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob("claude_desktop_config.backup-*.json"))


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Never find (or run) the real `claude` program, whatever a test forgets to inject."""
    monkeypatch.setattr(connect.shutil, "which", lambda name: None)


@pytest.fixture
def cfg(tmp_path):
    return tmp_path / "Claude" / "claude_desktop_config.json"


def add(cfg, entry=ENTRY):
    return connect.connect_claude(cfg, entry, now=lambda: NOW)


def test_missing_file_is_created_without_backup(cfg):
    result = add(cfg)
    assert result["action"] == "added" and result["backup"] is None
    assert read(cfg) == {"mcpServers": {"lead-generator": ENTRY}}
    assert "fully quit" in result["restart"]


def test_empty_file_is_filled_and_backed_up(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("  \n", encoding="utf-8")
    result = add(cfg)
    assert result["action"] == "added"
    assert read(cfg)["mcpServers"]["lead-generator"] == ENTRY
    assert len(backups(cfg)) == 1


def test_other_servers_and_keys_are_kept(cfg):
    cfg.parent.mkdir(parents=True)
    original = {
        "mcpServers": {"files": {"command": "npx", "args": ["-y", "x"], "env": {"A": "1"}}},
        "theme": "dark",
        "nested": {"keep": [1, 2, {"deep": "ü"}]},
    }
    cfg.write_text(json.dumps(original), encoding="utf-8")
    result = add(cfg)
    data = read(cfg)
    assert data["mcpServers"]["files"] == original["mcpServers"]["files"]
    assert data["theme"] == "dark" and data["nested"] == original["nested"]
    assert data["mcpServers"]["lead-generator"] == ENTRY
    backup = Path(result["backup"])
    assert backup.name == "claude_desktop_config.backup-20261020-093015.json"
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_existing_entry_is_updated_and_nothing_else(cfg):
    cfg.parent.mkdir(parents=True)
    old = {"command": "old.exe", "args": ["mcp"], "env": {"X": "y"}}
    cfg.write_text(
        json.dumps({"mcpServers": {"lead-generator": old, "other": {"command": "o"}}}),
        encoding="utf-8",
    )
    result = add(cfg)
    assert result["action"] == "updated"
    data = read(cfg)
    assert data["mcpServers"]["lead-generator"] == ENTRY
    assert data["mcpServers"]["other"] == {"command": "o"}
    assert len(backups(cfg)) == 1


def test_repeat_run_changes_nothing_and_leaves_no_backup(cfg):
    add(cfg)
    before = cfg.read_bytes()
    result = add(cfg)
    assert result["action"] == "unchanged" and result["backup"] is None
    assert cfg.read_bytes() == before
    assert backups(cfg) == []


def test_invalid_json_is_backed_up_and_never_overwritten(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {', encoding="utf-8")
    with pytest.raises(connect.ConnectError) as err:
        add(cfg)
    assert "not valid JSON" in str(err.value) and "left it alone" in str(err.value)
    assert cfg.read_text(encoding="utf-8") == '{"mcpServers": {'
    [backup] = backups(cfg)
    assert backup.read_text(encoding="utf-8") == '{"mcpServers": {'
    assert backup.name in str(err.value)


@pytest.mark.parametrize("text", ["[1, 2]", '"text"', '{"mcpServers": []}', '{"mcpServers": 5}'])
def test_unexpected_shapes_are_refused(cfg, text):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(text, encoding="utf-8")
    with pytest.raises(connect.ConnectError):
        add(cfg)
    assert cfg.read_text(encoding="utf-8") == text
    assert len(backups(cfg)) == 1


def test_byte_order_mark_is_accepted(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode("utf-8"))
    add(cfg)
    assert read(cfg)["a"] == 1


def test_two_runs_in_one_second_do_not_overwrite_a_backup(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"a": 1}', encoding="utf-8")
    add(cfg, {"command": "one", "args": []})
    add(cfg, {"command": "two", "args": []})
    assert len(backups(cfg)) == 2


def test_disconnect_removes_only_our_entry(cfg):
    cfg.parent.mkdir(parents=True)
    other = {"command": "npx", "args": ["x"]}
    cfg.write_text(
        json.dumps({"mcpServers": {"lead-generator": ENTRY, "other": other}, "k": 1}),
        encoding="utf-8",
    )
    result = connect.disconnect_claude(cfg, now=lambda: NOW)
    assert result["action"] == "removed"
    assert read(cfg) == {"mcpServers": {"other": other}, "k": 1}
    assert len(backups(cfg)) == 1


def test_disconnect_when_not_connected_changes_nothing(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mcpServers": {"other": {}}}), encoding="utf-8")
    before = cfg.read_bytes()
    assert connect.disconnect_claude(cfg, now=lambda: NOW)["action"] == "not_connected"
    assert cfg.read_bytes() == before and backups(cfg) == []
    missing = cfg.parent / "none.json"
    assert connect.disconnect_claude(missing)["action"] == "not_connected"
    assert not missing.exists()


def test_disconnect_with_invalid_json_refuses(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{oops", encoding="utf-8")
    with pytest.raises(connect.ConnectError):
        connect.disconnect_claude(cfg, now=lambda: NOW)
    assert cfg.read_text(encoding="utf-8") == "{oops"


def test_config_path_windows_and_mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert connect.claude_desktop_config_path() == (
        tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "me")
    support = tmp_path / "me" / "Library" / "Application Support" / "Claude"
    assert connect.claude_desktop_config_path() == support / "claude_desktop_config.json"
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(connect.ConnectError):
        connect.claude_desktop_config_path()


def test_server_entry_uses_the_venv_program_and_workspace(monkeypatch, tmp_path, workspace):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    exe = scripts / ("leadgen.exe" if sys.platform == "win32" else "leadgen")
    exe.write_text("", encoding="utf-8")
    python = scripts / "python.exe"
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(python))
    entry = connect.server_entry()
    assert Path(entry["command"]) == exe and entry["args"] == ["mcp"]
    assert entry["env"] == {"LEADGEN_HOME": str(workspace)}  # the conftest sets it
    monkeypatch.delenv("LEADGEN_HOME")
    assert "env" not in connect.server_entry()


def test_launcher_stays_inside_the_venv_when_python_is_a_symlink(monkeypatch, tmp_path):
    """On macOS/Linux `venv/bin/python` is a symlink to the system Python: don't follow it."""
    scripts = tmp_path / "venv" / "bin"
    scripts.mkdir(parents=True)
    exe = scripts / ("leadgen.exe" if sys.platform == "win32" else "leadgen")
    exe.write_text("", encoding="utf-8")
    python = scripts / "python"
    python.write_text("", encoding="utf-8")
    system = tmp_path / "usr" / "bin" / "python3"
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):  # what a symlinked venv python would do
        return system if self == python else real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    monkeypatch.setattr(sys, "executable", str(python))
    command, args = connect.leadgen_launcher()
    assert Path(command) == exe and args == ["mcp"]
    exe.unlink()  # no leadgen program: the fallback is the venv python, not the system one
    command, args = connect.leadgen_launcher()
    assert Path(command) == python and args == ["-m", "leadgen", "mcp"]


def test_server_entry_falls_back_to_python_module(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(python))
    command, args = connect.leadgen_launcher()
    assert Path(command) == python and args == ["-m", "leadgen", "mcp"]


# --- Claude Code ------------------------------------------------------------------------------


class Runner:
    def __init__(self, codes=(0, 0)):
        self.calls: list[list[str]] = []
        self.codes = list(codes)

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.codes.pop(0), "", "")


def test_claude_code_command_shape(monkeypatch):
    monkeypatch.delenv("LEADGEN_HOME")
    cmd = connect.claude_code_command()
    assert cmd[:6] == ["claude", "mcp", "add", "--scope", "user", "lead-generator"]
    assert cmd[6] == "--" and cmd[-1] == "mcp"


def test_claude_code_command_passes_workspace(workspace):
    cmd = connect.claude_code_command()
    assert cmd.index("-e") < cmd.index("lead-generator")
    assert f"LEADGEN_HOME={workspace}" in cmd
    assert cmd[cmd.index("lead-generator") - 2] == "--scope"  # an option closes the -e list


def test_claude_code_runs_remove_then_add():
    run = Runner()
    result = connect.connect_claude_code(run, which=lambda name: "C:/bin/claude.exe")
    assert result["ran"] is True
    assert run.calls[0] == [
        "C:/bin/claude.exe", "mcp", "remove", "--scope", "user", "lead-generator",
    ]  # fmt: skip
    assert run.calls[1][:3] == ["C:/bin/claude.exe", "mcp", "add"]
    assert "--" in run.calls[1]


def test_claude_code_missing_prints_the_command():
    run = Runner()
    result = connect.connect_claude_code(run, which=lambda name: None)
    assert result["ran"] is False and run.calls == []
    assert result["command"].startswith("claude mcp add --scope user")


def test_claude_code_refusal_and_crash_print_the_command():
    refused = connect.connect_claude_code(Runner((1, 1)), which=lambda n: "claude")
    assert refused["ran"] is False and "refused" in refused["message"]

    def boom(cmd, **kwargs):
        raise OSError("nope")

    crashed = connect.connect_claude_code(boom, which=lambda n: "claude")
    assert crashed["ran"] is False and "could not start" in crashed["message"]


def test_printed_command_quotes_paths_with_spaces(monkeypatch):
    monkeypatch.setattr(connect, "leadgen_launcher", lambda: ("C:/My Apps/leadgen.exe", ["mcp"]))
    result = connect.connect_claude_code(Runner(), which=lambda n: None)
    assert '"C:/My Apps/leadgen.exe"' in result["command"]


# --- CLI --------------------------------------------------------------------------------------


def test_cli_connect_and_disconnect_claude(cfg, capsys):
    assert cli.main(["connect", "claude", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "Added Lead Generator" in out and "fully quit" in out and "status" in out
    assert "lead-generator" in read(cfg)["mcpServers"]
    assert cli.main(["connect", "claude", "--config", str(cfg)]) == 0
    assert "already set up" in capsys.readouterr().out
    assert cli.main(["disconnect", "claude", "--config", str(cfg)]) == 0
    assert "Removed" in capsys.readouterr().out
    assert read(cfg)["mcpServers"] == {}
    assert cli.main(["disconnect", "claude", "--config", str(cfg)]) == 0
    assert "was not in" in capsys.readouterr().out


def test_cli_connect_claude_invalid_json_exits_1(cfg, capsys):
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{", encoding="utf-8")
    assert cli.main(["connect", "claude", "--config", str(cfg)]) == 1
    assert "not valid JSON" in capsys.readouterr().out
    assert cfg.read_text(encoding="utf-8") == "{"


def test_cli_connect_claude_code(monkeypatch, capsys):
    monkeypatch.setattr(connect.shutil, "which", lambda name: None)
    assert cli.main(["connect", "claude-code"]) == 1
    assert "claude mcp add" in capsys.readouterr().out


def test_cli_connect_chatgpt_calls_the_tunnel(monkeypatch):
    from leadgen.services import tunnel

    seen = {}

    def fake(port=None, rotate_secret=False):
        seen.update(port=port, rotate=rotate_secret)
        return 0

    monkeypatch.setattr(tunnel, "connect_chatgpt", fake)
    assert cli.main(["connect", "chatgpt", "--port", "8777", "--rotate-secret"]) == 0
    assert seen == {"port": 8777, "rotate": True}
