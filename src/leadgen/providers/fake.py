"""In-memory search provider for tests and the end-to-end run. Never touches the network."""

from __future__ import annotations

from collections.abc import Callable

from leadgen.providers.base import ProviderError, ProviderInfo
from leadgen.providers.search import SearchResult

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
