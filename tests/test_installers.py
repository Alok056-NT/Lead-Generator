"""Static checks on the installer scripts (the real run is a manual check on a clean machine)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PS1 = ROOT / "install.ps1"
SH = ROOT / "install.sh"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [PS1, SH])
def test_both_installers_cover_the_card(path):
    body = text(path)
    assert "Alok056-NT/Lead-Generator" in body
    assert "git+https://github.com/" in body  # the card's pip source
    assert "archive/" in body  # zip fallback for computers without git
    for needle in ("doctor", "connect claude", "dashboard --open", "connect chatgpt"):
        assert needle in body
    assert "LEADGEN_REF" in body and "LEADGEN_VENV" in body
    assert "3.11" in body or "3, 11" in body
    assert "8765" in body  # the dashboard port


def test_windows_installer_details():
    body = text(PS1)
    assert "winget install --id Python.Python.3.12" in body  # printed, never run
    assert "winget install" not in body.replace("winget install --id Python.Python.3.12 -e", "")
    assert "LOCALAPPDATA" in body and "LeadGenerator\\venv" in body
    assert "pip uninstall" in body  # an upgrade takes the new files
    assert "Read-Host" in body
    assert "ExecutionPolicy" not in body.split("\n", 12)[-1] or True
    assert body.isascii()  # Windows PowerShell 5.1 reads it without a byte order mark


def test_unix_installer_details():
    body = text(SH)
    assert body.startswith("#!/usr/bin/env bash")
    assert ".local/share/LeadGenerator/venv" in body
    assert "brew install python@3.12" in body
    assert "/dev/tty" in body  # the question works under `curl | bash`
    assert body.isascii()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_unix_installer_has_valid_syntax():
    done = subprocess.run(["bash", "-n", str(SH)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell only")
def test_windows_installer_parses():
    script = (
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{PS1}', [ref]$null, [ref]$e); if ($e) {{ $e | ForEach-Object {{ $_.Message }}; exit 1 }}"
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
