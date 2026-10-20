"""Real-provider smoke test for `leadgen doctor --live` (T19).

It asks each configured provider one cheap question, so the owner can see that the keys and the
code agree with the real services. It spends at most `MAX_CREDITS` credits in all:

1. one search with the search provider (1 credit),
2. one email check with Reoon, or ZeroBounce when Reoon has no key (1 credit),
3. Hunter's account call (free: it uses no credit).

A provider with no key is skipped. Nothing here is used by the normal workflow, and nothing is
returned that holds a key (provider error messages never include one).
"""

from __future__ import annotations

from typing import Any

from leadgen.core import secrets
from leadgen.providers import hunter, reoon, zerobounce
from leadgen.providers.base import ProviderError
from leadgen.providers.registry import get_search_provider

MAX_CREDITS = 3
SEARCH_QUERY = "lead generator connection test"
VERIFY_ADDRESS = "doctor.check@example.com"  # example.com: a made-up mailbox on a reserved domain


def _search_provider() -> Any | None:
    try:
        return get_search_provider()
    except ProviderError:
        return None


def _verifier() -> Any | None:
    for module, env in ((reoon, reoon.ENV_KEY), (zerobounce, zerobounce.ENV_KEY)):
        if secrets.get(env):
            return reoon.ReoonProvider() if module is reoon else zerobounce.ZeroBounceProvider()
    return None


def plan() -> list[dict[str, Any]]:
    """What a live check would do with the keys that are set now (no calls are made)."""
    searcher, verifier = _search_provider(), _verifier()
    steps: list[dict[str, Any]] = [
        {
            "id": "search",
            "provider": searcher.info.key if searcher else "",
            "what": "1 search",
            "credits": 1 if searcher and searcher.info.key != "ddg" else 0,
        },
        {
            "id": "verify",
            "provider": verifier.info.key if verifier else "",
            "what": "1 email check",
            "credits": 1 if verifier else 0,
        },
        {
            "id": "hunter",
            "provider": "hunter" if secrets.get(hunter.ENV_KEY) else "",
            "what": "account call (free)",
            "credits": 0,
        },
    ]
    assert sum(s["credits"] for s in steps) <= MAX_CREDITS
    return steps


def _skipped(step: dict[str, Any], why: str) -> dict[str, Any]:
    return {**step, "status": "skipped", "credits": 0, "message": why}


def run(steps: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Run the planned steps; each result has `status` ok / failed / skipped and a message."""
    steps = steps if steps is not None else plan()
    results: list[dict[str, Any]] = []
    for step in steps:
        if not step["provider"]:
            results.append(_skipped(step, "No key is set for this step."))
            continue
        try:
            if step["id"] == "search":
                provider = get_search_provider()
                found = provider.search(SEARCH_QUERY, "", 1)
                message = f"{provider.info.name} answered ({len(found)} result(s))."
            elif step["id"] == "verify":
                verdict = (_verifier() or reoon.ReoonProvider()).verify(VERIFY_ADDRESS)
                message = f"{verdict.provider} answered '{verdict.raw_status}'."
            else:
                ok, message = hunter.HunterProvider().test()
                if not ok:
                    results.append({**step, "status": "failed", "credits": 0, "message": message})
                    continue
        except ProviderError as exc:
            results.append({**step, "status": "failed", "credits": 0, "message": exc.message})
            continue
        results.append({**step, "status": "ok", "message": message})
    spent = sum(r["credits"] for r in results if r["status"] == "ok")
    assert spent <= MAX_CREDITS
    return results
