"""Hunter.io: company email pattern (domain search), email finder and email verifier.

Endpoints, auth and credit rules are in DECISIONS.md (T09). The key is sent in the
`X-API-KEY` header, so it never appears in a URL.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo, Verdict

BASE_URL = "https://api.hunter.io/v2"
ENV_KEY = "HUNTER_API_KEY"
POLL_ATTEMPTS = 2  # extra tries when the verifier answers 202 (still running)

INFO = ProviderInfo(
    key="hunter",
    name="Hunter.io",
    purpose="Finds company email patterns and addresses",
    env_keys=[ENV_KEY],
    free_tier=(
        "50 credits per month, no card (a company pattern lookup or a found address costs "
        "1 credit, an email check 0.5)"
    ),
    signup_url="https://hunter.io/users/sign_up",
    paid=False,
    default=True,
)

_limiter = base.RateLimiter(5)

_TOKEN_RE = re.compile(r"\{(first|last|f|l)\}")
_PATTERN_RE = re.compile(r"^(?:\{(?:first|last|f|l)\}|[._-])+$")

_VERDICTS = {
    "valid": "verified",
    "invalid": "invalid",
    "accept_all": "risky",
    "webmail": "risky",
    "disposable": "invalid",
    "unknown": "unknown",
}


def normalize_pattern(text: object) -> str | None:
    """A Hunter pattern such as `{first}.{last}` in canonical form, or None when it is empty or
    uses tokens other than `{first} {last} {f} {l}` joined by `.`, `_` or `-`."""
    if not isinstance(text, str):
        return None
    pattern = text.strip().lower()
    if not pattern or not _PATTERN_RE.match(pattern) or not _TOKEN_RE.search(pattern):
        return None
    return pattern


def _error(response: httpx.Response) -> ProviderError | None:
    """Hunter's own meaning of the status codes: 401 bad key, 403 rate limit, 429 usage limit."""
    status = response.status_code
    if 200 <= status < 300:
        return None
    if status == 401:
        return ProviderError("auth", "Hunter rejected the API key (HTTP 401).")
    if status == 429:
        return ProviderError("quota", "Hunter has no credits left (HTTP 429).")
    if status == 403:
        return ProviderError("rate_limited", "Hunter is rate limiting us (HTTP 403).")
    return ProviderError("http", f"Hunter returned HTTP {status}.")


class HunterProvider:
    """Hunter.io API v2 (`GET https://api.hunter.io/v2/...`, see DECISIONS)."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when HUNTER_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        headers = {"X-API-KEY": key}
        url = f"{BASE_URL}/{path}"

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.get(url, params=params, headers=headers)
            with http.client() as client:
                return client.get(url, params=params, headers=headers)

        return base.send_with_retry(send)

    def _data(self, response: httpx.Response) -> dict[str, Any]:
        error = _error(response)
        if error is not None:
            raise error
        data = base.json_object(response, "Hunter").get("data")
        return data if isinstance(data, dict) else {}

    def domain_pattern(self, domain: str) -> tuple[str, float] | None:
        """The domain's dominant email pattern and a 0-1 confidence, or None when Hunter has no
        pattern for it. Costs 1 credit when Hunter returns at least one address."""
        response = self._get("domain-search", {"domain": domain, "limit": 1})
        data = self._data(response)
        emails = data.get("emails")
        found = len(emails) if isinstance(emails, list) else 0
        base.record_usage(INFO.key, calls=1, credits=1 if found else 0)
        pattern = normalize_pattern(data.get("pattern"))
        if pattern is None:
            return None
        meta = base.json_object(response, "Hunter").get("meta")
        results = meta.get("results") if isinstance(meta, dict) else None
        count = results if isinstance(results, int) and not isinstance(results, bool) else found
        return pattern, min(0.7 + 0.05 * max(count, 0), 0.95)

    def find(self, first: str, last: str, domain: str) -> tuple[str, float] | None:
        """Hunter's best address for the person and its 0-1 score, or None. 1 credit when found."""
        response = self._get(
            "email-finder", {"domain": domain, "first_name": first, "last_name": last}
        )
        if response.status_code == 404:
            base.record_usage(INFO.key, calls=1, credits=0)
            return None
        data = self._data(response)
        email = data.get("email")
        if not isinstance(email, str) or "@" not in email:
            base.record_usage(INFO.key, calls=1, credits=0)
            return None
        base.record_usage(INFO.key, calls=1, credits=1)
        score = data.get("score")
        value = float(score) / 100 if isinstance(score, (int, float)) else 0.0
        return email.strip().lower(), max(0.0, min(value, 1.0))

    def verify(self, email: str) -> Verdict:
        """Check one address (0.5 credit; an `unknown` answer is not charged)."""
        response = self._get("email-verifier", {"email": email})
        attempts = 0
        while response.status_code == 202 and attempts < POLL_ATTEMPTS:
            attempts += 1
            base._sleep(1.0)
            response = self._get("email-verifier", {"email": email})
        if response.status_code == 202:
            return Verdict("unknown", "pending", INFO.key)
        data = self._data(response)
        raw = str(data.get("status") or data.get("result") or "").lower()
        status = _VERDICTS.get(raw, "unknown")
        base.record_usage(INFO.key, calls=1, credits=0 if status == "unknown" else 0.5)
        return Verdict(status, raw, INFO.key, catch_all=raw == "accept_all")

    def test(self) -> tuple[bool, str]:
        """`GET /account` (free, uses no credit)."""
        if not self.configured():
            return False, f"{ENV_KEY} is not set."
        try:
            data = self._data(self._get("account", {}))
        except ProviderError as exc:
            return False, exc.message
        requests = data.get("requests")
        credits = requests.get("credits") if isinstance(requests, dict) else None
        left = credits.get("remaining") if isinstance(credits, dict) else None
        if isinstance(left, (int, float)) and not isinstance(left, bool):
            return True, f"Hunter key works ({left:g} credits left this period)."
        return True, "Hunter key works."
