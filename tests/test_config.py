"""Tests for settings loading and saving."""

from __future__ import annotations

import pytest

from leadgen.core import config, paths


def test_defaults_when_file_absent():
    s = config.load_settings()
    assert s.locale == "en-GB"
    assert s.defaults.channels == ["linkedin"]
    assert s.budgets.search == 300
    assert s.sending.max_bounce_rate == 0.05
    assert s.strict_review is True
    assert s.allow_ddg_fallback is False
    assert s.mcp_http.port == 8766 and s.mcp_http.secret == ""
    assert s.dashboard.port == 8765


def test_round_trip():
    s = config.load_settings()
    s.sender.name = "Aria Tan"
    s.sender.company = "Northwind Logistics"
    s.sending.daily_cap = 30
    s.defaults.channels = ["linkedin", "email"]
    config.save_settings(s)
    again = config.load_settings()
    assert again == s
    assert again.sender.name == "Aria Tan"


def test_unknown_keys_preserved():
    paths.settings_path().write_text(
        "locale: en-US\nfuture_flag: true\nsender:\n  name: Aria\n  nickname: Ari\n",
        encoding="utf-8",
    )
    s = config.load_settings()
    assert s.locale == "en-US"
    config.save_settings(s)
    text = paths.settings_path().read_text(encoding="utf-8")
    assert "future_flag: true" in text
    assert "nickname: Ari" in text
    assert config.load_settings().sender.model_extra == {"nickname": "Ari"}


def test_partial_file_fills_defaults_and_empty_file_ok():
    paths.settings_path().write_text("budgets:\n  hunter: 10\n", encoding="utf-8")
    s = config.load_settings()
    assert s.budgets.hunter == 10 and s.budgets.search == 300
    paths.settings_path().write_text("", encoding="utf-8")
    assert config.load_settings().locale == "en-GB"


def test_invalid_values_raise_without_leaking_input():
    paths.settings_path().write_text("mcp_http:\n  port: SECRETVALUE\n", encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc:
        config.load_settings()
    assert "mcp_http.port" in str(exc.value)
    assert "SECRETVALUE" not in str(exc.value)


def test_broken_yaml_and_non_mapping():
    paths.settings_path().write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_settings()
    paths.settings_path().write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_settings()
