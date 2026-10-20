"""Provider registry: info for every provider, search-provider choice and 'needs a key' replies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from leadgen.core import secrets
from leadgen.providers import brave, ddg, fake, hunter, lemlist, reoon, serper, zerobounce
from leadgen.providers.base import Provider, ProviderError, ProviderInfo
from leadgen.providers.search import SearchProvider
from leadgen.send import smtp_imap

FALLBACK_HINT = (
    "No key or credits? The AI can run these searches with its own web search instead "
    "(slower, free)."
)
QUOTA_FALLBACK_HINT = (
    "Credits are used up. The AI can run these searches with its own web search instead "
    "(slower, free)."
)
BUDGET_FALLBACK_HINT = (
    "Budget reached. The AI can run these searches with its own web search instead "
    "(slower, free), or you can raise the budget."
)

ALL_PROVIDERS: dict[str, ProviderInfo] = {
    info.key: info
    for info in (
        serper.INFO,
        brave.INFO,
        ddg.INFO,
        hunter.INFO,
        reoon.INFO,
        zerobounce.INFO,
        lemlist.INFO,
        smtp_imap.INFO,
    )
}

# Provider classes that exist so far. Later tasks add their entries here.
_FACTORIES: dict[str, Callable[[], Provider]] = {
    "serper": serper.SerperProvider,
    "brave": brave.BraveProvider,
    "ddg": ddg.DuckDuckGoProvider,
    "hunter": hunter.HunterProvider,
    "reoon": reoon.ReoonProvider,
    "zerobounce": zerobounce.ZeroBounceProvider,
    "lemlist": lemlist.LemlistProvider,
    "smtp": smtp_imap.SmtpImapMailbox.from_config,
}


def get_provider(provider_key: str) -> Provider | None:
    """A provider object, or None when its class is not built yet (or the key is unknown)."""
    factory = _FACTORIES.get(provider_key)
    return factory() if factory else None


def get_search_provider() -> SearchProvider:
    """First configured of Serper, Brave; else DuckDuckGo if allowed; else not_configured.

    The canned test provider is returned instead only when `fake.fakes_enabled()` (T19).
    """
    if fake.fakes_enabled():
        return fake.fake_search_from_env()
    for provider in (serper.SerperProvider(), brave.BraveProvider()):
        if provider.configured():
            return provider
    fallback = ddg.DuckDuckGoProvider()
    if fallback.configured():
        return fallback
    raise ProviderError(
        "not_configured", "No search key is set. Add a Serper key (free) to start searching."
    )


# --- structured replies (PLAN section 7 and 7a) ---------------------------------------------


def _info_or_raise(provider_key: str) -> ProviderInfo:
    try:
        return ALL_PROVIDERS[provider_key]
    except KeyError:
        raise ValueError(f"unknown provider {provider_key!r}") from None


def needs_key_response(provider_key: str, why: str) -> dict[str, Any]:
    """Reply for a missing key: what it is for, the free allowance, where to sign up, how to add."""
    info = _info_or_raise(provider_key)
    return {
        "ok": False,
        "error": {
            "code": "needs_key",
            "message": f"No {info.name} key is set.",
            "hint": (
                "Ask the user to add the key (dashboard, Keys page, or save_api_key with "
                f"provider '{info.key}'), or use the AI's own web search instead."
            ),
        },
        "needs_key": {
            "provider": info.key,
            "name": info.name,
            "why": why,
            "free_tier": info.free_tier,
            "signup_url": info.signup_url,
            "how_to_add": [
                "Dashboard → Keys (recommended)",
                "Tell the AI to call save_api_key",
            ],
        },
        "fallback_available": "ai_web_search",
        "fallback_hint": FALLBACK_HINT,
    }


def quota_response(provider_key: str) -> dict[str, Any]:
    """Reply when a provider has no credits left."""
    info = _info_or_raise(provider_key)
    return {
        "ok": False,
        "error": {
            "code": "quota_exhausted",
            "message": f"{info.name} has no credits left.",
            "hint": (
                "Ask the user to add credits or a new key (dashboard, Keys page, or "
                f"save_api_key with provider '{info.key}'), or use the AI's own web search."
            ),
        },
        "quota_exhausted": {
            "provider": info.key,
            "name": info.name,
            "message": f"{info.name} has no credits left.",
            "free_tier": info.free_tier,
            "signup_url": info.signup_url,
        },
        "fallback_available": "ai_web_search",
        "fallback_hint": QUOTA_FALLBACK_HINT,
    }


def budget_response(kind: str, used: float, limit: float) -> dict[str, Any]:
    """Reply when a campaign budget (`search`, `hunter`, `verify`) is reached."""
    return {
        "ok": False,
        "error": {
            "code": "budget_reached",
            "message": f"The {kind} budget is reached ({used:g} of {limit:g} used).",
            "hint": (
                "Ask the user to raise the budget (update_campaign) or approve more spending, "
                "then repeat the call with confirm=true; or use the AI's own web search."
            ),
        },
        "budget_reached": {
            "kind": kind,
            "used": used,
            "limit": limit,
            "how_to_continue": "Ask the user, then repeat the call with confirm=true.",
        },
        "fallback_available": "ai_web_search",
        "fallback_hint": BUDGET_FALLBACK_HINT,
    }


def error_response(provider_key: str, exc: ProviderError, why: str = "") -> dict[str, Any]:
    """Turn a `ProviderError` into the right reply for the AI (services use this).

    not_configured -> needs_key; quota -> quota; everything else -> a plain error with a hint.
    """
    if exc.code == "not_configured":
        return needs_key_response(provider_key, why or exc.message)
    if exc.code == "quota":
        return quota_response(provider_key)
    info = _info_or_raise(provider_key)
    return {
        "ok": False,
        "error": {
            "provider": info.key,
            "name": info.name,
            "code": exc.code,
            "message": exc.message,
            "hint": "Try again; if it repeats, tell the user.",
        },
    }


# --- keys ------------------------------------------------------------------------------------


def check_provider(provider_key: str) -> dict[str, Any]:
    """Run the provider's own `test()`; returns `{"ok", "message"}` (no secrets)."""
    info = _info_or_raise(provider_key)
    provider = get_provider(provider_key)
    if provider is None:
        return {"ok": False, "message": f"{info.name} is not available in this version yet."}
    ok, message = provider.test()
    return {"ok": ok, "message": message}


def save_api_key(provider_key: str, key: str) -> dict[str, Any]:
    """Store a provider's key, then test it. A key that fails the test is not kept.

    On failure the previous key (if any) is put back, otherwise the new one is deleted again.
    The reply holds only a masked key.
    """
    info = _info_or_raise(provider_key)
    if len(info.env_keys) != 1:
        return {"ok": False, "message": f"{info.name} does not use a single API key."}
    if get_provider(provider_key) is None:
        return {"ok": False, "message": f"{info.name} is not available in this version yet."}
    key = key.strip()
    if not key:
        return {"ok": False, "message": "The key is empty."}
    if "\n" in key or "\r" in key:
        return {"ok": False, "message": "The key must be a single line."}
    env = info.env_keys[0]
    previous = secrets.stored(env)
    secrets.set(env, key)
    result = check_provider(provider_key)
    if not result["ok"]:
        if previous:
            secrets.set(env, previous)
        else:
            secrets.delete(env)
        return {
            "ok": False,
            "provider": info.key,
            "message": f"The key was not saved. {result['message']}",
        }
    return {
        "ok": True,
        "provider": info.key,
        "masked_key": secrets.masked(env),
        "message": result["message"],
    }
