"""Provider registry, structured replies, key saving and the status service."""

from __future__ import annotations

import httpx
import pytest
import respx

from leadgen import cli
from leadgen.core import config, paths, secrets
from leadgen.providers import base, brave, ddg, registry, serper
from leadgen.providers.base import ProviderError
from leadgen.services import status

SERPER_URL = "https://google.serper.dev/search"
OK_BODY = {"organic": [{"title": "T", "link": "https://example.com/", "position": 1}]}


def test_all_providers_listed():
    assert list(registry.ALL_PROVIDERS) == [
        "serper",
        "brave",
        "ddg",
        "hunter",
        "reoon",
        "zerobounce",
        "lemlist",
        "smtp",
    ]
    assert registry.ALL_PROVIDERS["serper"].env_keys == ["SERPER_API_KEY"]
    assert registry.ALL_PROVIDERS["serper"].default
    assert not registry.ALL_PROVIDERS["brave"].default and registry.ALL_PROVIDERS["brave"].paid


def test_search_provider_fallback_order():
    with pytest.raises(ProviderError) as info:
        registry.get_search_provider()
    assert info.value.code == "not_configured"

    secrets.set("BRAVE_API_KEY", "brave-key-0001")
    assert isinstance(registry.get_search_provider(), brave.BraveProvider)

    secrets.set("SERPER_API_KEY", "serper-key-0001")
    assert isinstance(registry.get_search_provider(), serper.SerperProvider)

    secrets.delete("SERPER_API_KEY")
    secrets.delete("BRAVE_API_KEY")
    settings = config.load_settings()
    settings.allow_ddg_fallback = True
    config.save_settings(settings)
    assert isinstance(registry.get_search_provider(), ddg.DuckDuckGoProvider)


def test_needs_key_response_shape():
    reply = registry.needs_key_response("serper", "To find people")
    assert reply == {
        "ok": False,
        "needs_key": {
            "provider": "serper",
            "name": "Serper.dev (Google search)",
            "why": "To find people",
            "free_tier": "2,500 free searches, no credit card",
            "signup_url": "https://serper.dev",
            "how_to_add": ["Dashboard → Keys (recommended)", "Tell the AI to call save_api_key"],
        },
        "fallback_available": "ai_web_search",
        "fallback_hint": (
            "No key or credits? The AI can run these searches with its own web search instead "
            "(slower, free)."
        ),
    }
    with pytest.raises(ValueError):
        registry.needs_key_response("nope", "x")


def test_quota_and_budget_responses():
    quota = registry.quota_response("serper")
    assert quota["ok"] is False and quota["quota_exhausted"]["provider"] == "serper"
    assert quota["fallback_available"] == "ai_web_search" and quota["fallback_hint"]
    budget = registry.budget_response("search", 300, 300)
    assert budget["budget_reached"] == {
        "kind": "search",
        "used": 300,
        "limit": 300,
        "how_to_continue": "Ask the user, then repeat the call with confirm=true.",
    }
    assert budget["fallback_available"] == "ai_web_search" and budget["fallback_hint"]


def test_error_response_routes_codes():
    assert "needs_key" in registry.error_response("serper", ProviderError("not_configured"), "why")
    assert "quota_exhausted" in registry.error_response("hunter", ProviderError("quota"))
    other = registry.error_response("serper", ProviderError("http", "HTTP 500"))
    assert other["error"]["code"] == "http" and other["ok"] is False


@respx.mock
def test_save_api_key_success_masks_key(sleeps, ready_db):
    respx.post(SERPER_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    result = registry.save_api_key("serper", "  abcd-efgh-1234-WXYZ  ")
    assert result["ok"] is True and result["masked_key"] == "…WXYZ"
    assert "abcd-efgh" not in repr(result)
    assert secrets.get("SERPER_API_KEY") == "abcd-efgh-1234-WXYZ"
    assert base.usage("serper")["credits"] == 1.0


@respx.mock
def test_save_api_key_deletes_key_when_test_fails(sleeps, ready_db):
    respx.post(SERPER_URL).mock(return_value=httpx.Response(403))
    result = registry.save_api_key("serper", "wrong-key-value-9999")
    assert result["ok"] is False and "not saved" in result["message"]
    assert "wrong-key" not in repr(result)
    assert secrets.get("SERPER_API_KEY") == ""
    assert "SERPER_API_KEY" not in paths.secrets_path().read_text(encoding="utf-8")


@respx.mock
def test_failed_replacement_restores_previous_key(sleeps, ready_db):
    secrets.set("SERPER_API_KEY", "old-good-key-0001")
    respx.post(SERPER_URL).mock(return_value=httpx.Response(401))
    assert registry.save_api_key("serper", "new-bad-key-0002")["ok"] is False
    assert secrets.get("SERPER_API_KEY") == "old-good-key-0001"


@pytest.mark.parametrize(
    ("provider", "key"), [("smtp", "x"), ("hunter", "x"), ("serper", "  "), ("serper", "a\nb")]
)
def test_save_api_key_refuses_unusable_input(provider, key):
    assert registry.save_api_key(provider, key)["ok"] is False
    assert secrets.get("SERPER_API_KEY") == ""
    assert secrets.get("HUNTER_API_KEY") == ""


def test_check_provider_unbuilt_and_unknown():
    assert registry.check_provider("hunter")["ok"] is False
    with pytest.raises(ValueError):
        registry.check_provider("nope")


# --- status service -------------------------------------------------------------------------


def test_list_providers(ready_db):
    secrets.set("SERPER_API_KEY", "serper-key-WXYZ")
    secrets.set("HUNTER_API_KEY", "hunter-key-ABCD")
    base.record_usage("serper", credits=7)
    base.record_usage("hunter", credits=4)
    items = {p["key"]: p for p in status.list_providers()}
    assert list(items) == list(registry.ALL_PROVIDERS)
    serper_row = items["serper"]
    assert serper_row["configured"] and serper_row["masked_key"] == "…WXYZ"
    assert serper_row["usage_month"] == {"calls": 1, "credits": 7.0}
    assert serper_row["remaining_free"] == 2493.0
    assert serper_row["implemented"] is True
    assert not items["brave"]["configured"] and items["brave"]["masked_key"] == ""
    assert items["brave"]["remaining_free"] == 1000.0
    assert items["hunter"]["configured"] and items["hunter"]["implemented"] is False
    assert items["hunter"]["remaining_free"] == 46.0
    assert items["ddg"]["configured"] is False and items["ddg"]["env_keys"] == []
    assert items["smtp"]["masked_key"] == "" and items["smtp"]["remaining_free"] is None
    assert "serper-key" not in repr(items)


# --- CLI ------------------------------------------------------------------------------------


def test_cli_keys_list(capsys, ready_db):
    secrets.set("SERPER_API_KEY", "serper-key-WXYZ")
    assert cli.main(["keys", "list"]) == 0
    out = capsys.readouterr().out
    assert "serper" in out and "…WXYZ" in out and "serper-key" not in out
    assert "hunter" in out and "not built yet" in out


@respx.mock
def test_cli_keys_set_prompts_without_echo_and_tests(capsys, monkeypatch, sleeps):
    prompts = []
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda prompt: prompts.append(prompt) or "k-1234567890"
    )
    respx.post(SERPER_URL).mock(return_value=httpx.Response(200, json=OK_BODY))
    assert cli.main(["keys", "set", "serper"]) == 0
    out = capsys.readouterr().out
    assert prompts and "k-1234567890" not in out and "…7890" in out
    assert secrets.get("SERPER_API_KEY") == "k-1234567890"


@respx.mock
def test_cli_keys_set_failure_and_test(capsys, monkeypatch, sleeps):
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "bad-key-1234567")
    respx.post(SERPER_URL).mock(return_value=httpx.Response(401))
    assert cli.main(["keys", "set", "serper"]) == 1
    assert "not saved" in capsys.readouterr().out
    assert cli.main(["keys", "test", "serper"]) == 1
    assert "not set" in capsys.readouterr().out


def test_cli_keys_unknown_provider_exits():
    with pytest.raises(SystemExit):
        cli.main(["keys", "set", "nope"])
