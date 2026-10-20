"""In-memory search provider for tests and the end-to-end run. Never touches the network.

`fakes_enabled()` is the switch for running the real MCP server with canned search results
(the T19 transport test). It needs BOTH `LEADGEN_FAKE_PROVIDERS=1` and `LEADGEN_ALLOW_FAKES=1`,
so one stray variable can never make a real installation answer from canned data.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from leadgen.providers.base import ProviderError, ProviderInfo
from leadgen.providers.search import SearchResult

FAKE_ENV = "LEADGEN_FAKE_PROVIDERS"
ALLOW_ENV = "LEADGEN_ALLOW_FAKES"
DATA_ENV = "LEADGEN_FAKE_DATA"

Responses = dict[str, list[SearchResult]] | Callable[[str, str, int], list[SearchResult]]

FAKE_INFO = ProviderInfo(
    key="fake",
    name="Fake search (tests)",
    purpose="Canned results for tests",
    free_tier="n/a",
)


class FakeSearchProvider:
    """Returns canned results and records every call as `(query, country_gl, num)`.

    `responses` is either a dict keyed by the exact query string (unknown queries return `[]`)
    or a callable `(query, country_gl, num) -> list[SearchResult]`. Use `fail_with` to make every
    call raise a `ProviderError`, or `fail_after(n, error)` to fail from the n-th call on.
    """

    def __init__(
        self,
        responses: Responses | None = None,
        *,
        info: ProviderInfo = FAKE_INFO,
        fail_with: ProviderError | None = None,
    ) -> None:
        self.responses: Responses = responses if responses is not None else {}
        self.info = info
        self.calls: list[tuple[str, str, int]] = []
        self.fail_with = fail_with
        self._fail_from: int | None = None
        self._fail_error: ProviderError | None = None

    def fail_after(self, n_ok_calls: int, error: ProviderError) -> None:
        """Let `n_ok_calls` calls succeed, then raise `error` on every later call."""
        self._fail_from = n_ok_calls
        self._fail_error = error

    def configured(self) -> bool:
        """Always True."""
        return True

    def test(self) -> tuple[bool, str]:
        """Always OK unless `fail_with` is set."""
        if self.fail_with is not None:
            return False, self.fail_with.message
        return True, "Fake provider works."

    def search(self, query: str, country_gl: str = "", num: int = 10) -> list[SearchResult]:
        """Record the call, then raise the simulated error or return the canned results."""
        index = len(self.calls)
        self.calls.append((query, country_gl, num))
        if self.fail_with is not None:
            raise self.fail_with
        if self._fail_from is not None and index >= self._fail_from and self._fail_error:
            raise self._fail_error
        if callable(self.responses):
            found = self.responses(query, country_gl, num)
        else:
            found = self.responses.get(query, [])
        return list(found)[:num]


# --- canned search for the real server (tests only) ---------------------------------------------


def fakes_enabled() -> bool:
    """True only when both `LEADGEN_FAKE_PROVIDERS` and `LEADGEN_ALLOW_FAKES` are set to 1."""
    return os.environ.get(FAKE_ENV) == "1" and os.environ.get(ALLOW_ENV) == "1"


def rules_responder(rules: list[dict]) -> Callable[[str, str, int], list[SearchResult]]:
    """A responder from rules `{"contains": [text, ...], "results": [{title, link, snippet}]}`.

    The first rule whose every `contains` text occurs in the query answers it; no rule, no
    results.
    """

    def respond(query: str, country_gl: str, num: int) -> list[SearchResult]:
        for rule in rules:
            if all(text in query for text in rule.get("contains", [])):
                return [
                    SearchResult(
                        title=str(r.get("title", "")),
                        link=str(r.get("link", "")),
                        snippet=str(r.get("snippet", "")),
                        position=n,
                    )
                    for n, r in enumerate(rule.get("results", []), 1)
                ]
        return []

    return respond


def fake_search_from_env() -> FakeSearchProvider:
    """The canned search provider for the file named by `LEADGEN_FAKE_DATA` (a JSON object
    `{"rules": [...]}`, see `rules_responder`). Only call it when `fakes_enabled()` is true."""
    name = os.environ.get(DATA_ENV, "").strip()
    if not name:
        raise ProviderError("not_configured", f"{DATA_ENV} is not set (fake providers need it).")
    try:
        data = json.loads(Path(name).read_text(encoding="utf-8"))
        rules = data["rules"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ProviderError(
            "not_configured", f"The fake data file cannot be read ({type(exc).__name__})."
        ) from None
    if not isinstance(rules, list):
        raise ProviderError("not_configured", "The fake data file must hold a list of rules.")
    return FakeSearchProvider(rules_responder(rules))
