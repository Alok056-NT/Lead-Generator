"""The settings the dashboard may change."""

from __future__ import annotations

import pytest

from leadgen.core import config, paths
from leadgen.services import preferences
from leadgen.services.preferences import PreferencesError


def test_defaults_are_returned():
    reply = preferences.get_preferences()
    assert reply["ok"] and reply["locale"] == "en-GB" and reply["strict_review"] is True
    assert reply["budgets"] == {"search": 300, "hunter": 25, "verify": 200, "finder": 50}
    assert set(reply["sender"]) == set(preferences.SENDER_FIELDS)


def test_update_changes_only_what_was_sent_and_keeps_other_settings():
    settings = config.load_settings()
    settings.sending.daily_cap = 33
    settings.sender.company = "Bright Harbour Training"
    config.save_settings(settings)
    path = paths.settings_path()
    path.write_text(path.read_text(encoding="utf-8") + "my_extra_key: kept\n", encoding="utf-8")

    reply = preferences.update_preferences(
        {
            "sender": {"name": " Priya Nair ", "email": "priya@example.com"},
            "postal_address": "1 Example Road\nSingapore 000000",
            "locale": "en-US",
            "budgets": {"search": 120},
            "strict_review": False,
        }
    )
    assert reply["sender"]["name"] == "Priya Nair"
    assert reply["sender"]["company"] == "Bright Harbour Training"  # not sent: unchanged
    assert reply["budgets"] == {"search": 120, "hunter": 25, "verify": 200, "finder": 50}
    after = config.load_settings()
    assert after.sending.daily_cap == 33 and after.strict_review is False
    assert after.postal_address.startswith("1 Example Road\nSingapore")
    assert "my_extra_key: kept" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "changes",
    [
        {"sending": {"paused": True}},
        {"sender": {"phone": "123"}},
        {"sender": "Priya"},
        {"sender": {"name": 5}},
        {"sender": {"name": "Line\nbreak"}},
        {"sender": {"name": "x" * 201}},
        {"sender": {"email": "not an email"}},
        {"postal_address": 5},
        {"postal_address": "x" * 501},
        {"locale": "english"},
        {"locale": "en_GB"},
        {"budgets": {"search": -1}},
        {"budgets": {"search": 1.5}},
        {"budgets": {"search": True}},
        {"budgets": {"search": 2_000_000}},
        {"budgets": {"coffee": 1}},
        {"budgets": [1]},
        {"strict_review": "yes"},
    ],
)
def test_bad_changes_are_refused_and_nothing_is_saved(changes):
    before = preferences.get_preferences()
    with pytest.raises(PreferencesError) as err:
        preferences.update_preferences(changes)
    assert err.value.as_response()["error"]["code"] == "invalid_value"
    assert preferences.get_preferences() == before


def test_empty_sender_email_is_allowed_and_clears_it():
    preferences.update_preferences({"sender": {"email": "a@example.com"}})
    assert preferences.update_preferences({"sender": {"email": ""}})["sender"]["email"] == ""


def test_broken_settings_file_raises_config_error():
    paths.settings_path().write_text("budgets:\n  search: lots\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        preferences.get_preferences()
