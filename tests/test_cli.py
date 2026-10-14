"""Tests for the command line."""

from __future__ import annotations

import pytest

from leadgen import __version__, cli
from leadgen.cli import main
from leadgen.core import paths, secrets


def test_doctor_healthy_in_fresh_workspace(capsys, workspace):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert str(workspace) in out
    assert "Settings      : valid" in out
    assert "not installed yet" in out


def test_doctor_masks_keys(capsys):
    secrets.set("SERPER_API_KEY", "sk-test-1234567890abcd")
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "…abcd" in out
    assert "sk-test-1234567890abcd" not in out


def test_doctor_fails_on_bad_settings(capsys):
    paths.settings_path().write_text("budgets:\n  search: lots\n", encoding="utf-8")
    assert main(["doctor"]) == 1
    assert "PROBLEM" in capsys.readouterr().out


def test_doctor_reports_unusable_workspace(capsys, tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LEADGEN_HOME", str(blocker))
    assert main(["doctor"]) == 1
    assert "cannot be created" in capsys.readouterr().out


def test_doctor_reports_unwritable_workspace(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_workspace_writable", lambda _root: False)
    assert main(["doctor"]) == 1
    assert "NOT writable" in capsys.readouterr().out


def test_version_and_paths(capsys, workspace):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
    assert main(["paths"]) == 0
    assert str(workspace) in capsys.readouterr().out


def test_unknown_command_exits():
    with pytest.raises(SystemExit):
        main(["nope"])


def test_python_dash_m_runs(workspace):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "leadgen", "version"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0
    assert result.stdout.strip() == __version__
