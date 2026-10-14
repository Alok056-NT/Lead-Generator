"""Tests for secret storage."""

from __future__ import annotations

import os
import stat

import pytest

from leadgen.core import paths, secrets

KEY = "sk-test-1234567890abcd"


def test_round_trip():
    assert secrets.get("SERPER_API_KEY") == ""
    secrets.set("SERPER_API_KEY", KEY)
    assert secrets.get("SERPER_API_KEY") == KEY
    assert secrets.delete("SERPER_API_KEY") is True
    assert secrets.get("SERPER_API_KEY") == ""
    assert secrets.delete("SERPER_API_KEY") is False


def test_other_keys_and_comments_preserved():
    paths.secrets_path().write_text(
        "# my keys\nHUNTER_API_KEY=hunter-value-1\n\n# serper below\nSERPER_API_KEY=old\n",
        encoding="utf-8",
    )
    secrets.set("SERPER_API_KEY", KEY)
    text = paths.secrets_path().read_text(encoding="utf-8")
    assert "# my keys" in text and "# serper below" in text
    assert "HUNTER_API_KEY=hunter-value-1" in text
    assert f"SERPER_API_KEY={KEY}" in text
    assert "old" not in text
    secrets.delete("SERPER_API_KEY")
    text = paths.secrets_path().read_text(encoding="utf-8")
    assert "SERPER_API_KEY" not in text
    assert "# my keys" in text and "HUNTER_API_KEY=hunter-value-1" in text


def test_new_key_appended_and_values_stripped_and_quotes_handled():
    paths.secrets_path().write_text('BRAVE_API_KEY = "quoted-value"  \n', encoding="utf-8")
    assert secrets.get("BRAVE_API_KEY") == "quoted-value"
    secrets.set("REOON_API_KEY", "  padded  ")
    assert secrets.get("REOON_API_KEY") == "padded"
    secrets.set("SMTP_PASSWORD", '"starts-with-quote"')
    assert secrets.get("SMTP_PASSWORD") == '"starts-with-quote"'


def test_env_fallback_and_file_wins(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "  from-env-value  ")
    assert secrets.get("HUNTER_API_KEY") == "from-env-value"
    secrets.set("HUNTER_API_KEY", "from-file-value")
    assert secrets.get("HUNTER_API_KEY") == "from-file-value"


def test_masked():
    assert secrets.masked("SERPER_API_KEY") == ""
    secrets.set("SERPER_API_KEY", KEY)
    assert secrets.masked("SERPER_API_KEY") == "…abcd"
    secrets.set("SMTP_PORT", "587")
    assert secrets.masked("SMTP_PORT") == "…"


def test_configured_lists_all_known_keys():
    secrets.set("SERPER_API_KEY", KEY)
    result = secrets.configured()
    assert set(result) == set(secrets.KNOWN_KEYS)
    assert result["SERPER_API_KEY"] is True
    assert result["HUNTER_API_KEY"] is False


def test_errors_never_contain_value():
    for value in ["line1\nline2-SECRETVALUE", "   "]:
        with pytest.raises(ValueError) as exc:
            secrets.set("SERPER_API_KEY", value)
        assert "SECRETVALUE" not in repr(exc.value)
    with pytest.raises(ValueError) as exc:
        secrets.set("bad name", "SECRETVALUE")
    assert "SECRETVALUE" not in repr(exc.value)
    with pytest.raises(ValueError):
        secrets.delete("bad name")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
def test_file_mode_is_private():
    secrets.set("SERPER_API_KEY", KEY)
    assert stat.S_IMODE(paths.secrets_path().stat().st_mode) == 0o600


def test_no_temp_files_left_behind(workspace):
    secrets.set("SERPER_API_KEY", KEY)
    secrets.set("HUNTER_API_KEY", KEY)
    assert sorted(p.name for p in workspace.glob("secrets.env*")) == ["secrets.env"]
