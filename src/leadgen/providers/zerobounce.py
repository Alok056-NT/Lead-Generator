"""ZeroBounce: checks one address with the v2 validate endpoint (optional second verifier).

Endpoint, parameters and status words are in DECISIONS.md (T09).
"""

from __future__ import annotations

from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo, Verdict

VALIDATE_URL = "https://api.zerobounce.net/v2/validate"
CREDITS_URL = "https://api.zerobounce.net/v2/getcredits"
ENV_KEY = "ZEROBOUNCE_API_KEY"
TIMEOUT_SECONDS = 15  # the API accepts 3 to 60; we stay under our own HTTP timeout

INFO = ProviderInfo(
    key="zerobounce",
    name="ZeroBounce",
    purpose="Optional second email verifier",
    env_keys=[ENV_KEY],
    free_tier="100 free checks per month (sign up with a business email address)",
    signup_url="https://www.zerobounce.net/members/signin/register",
    paid=False,
    default=False,
)

_limiter = base.RateLimiter(5)

_CATCH_ALL_SUB = ("accept_all", "role_based_catch_all")
_ROLE_SUB = ("role_based", "role_based_catch_all")


class ZeroBounceProvider:
    """ZeroBounce v2 (`GET https://api.zerobounce.net/v2/validate`, see DECISIONS)."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when ZEROBOUNCE_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        query = {**params, "api_key": key}

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.get(url, params=query)
            with http.client() as client:
                return client.get(url, params=query)

        response = base.send_with_retry(send)
        error = base.error_for_status(response, "ZeroBounce")
        if error is not None:
            raise error
        data = base.json_object(response, "ZeroBounce")
        if "error" in data and "status" not in data:
            # The documented error is {"error": "Invalid API Key or your account ran out of
            # credits"}: the two causes cannot be told apart from this reply.
            raise ProviderError(
                "auth", "ZeroBounce says the API key is invalid or the account is out of credits."
            )
        return data

    def verify(self, email: str) -> Verdict:
        """Check one address (1 credit; `unknown` answers are not counted here)."""
        data = self._get(VALIDATE_URL, {"email": email, "timeout": TIMEOUT_SECONDS})
        raw = str(data.get("status") or "").lower()
        sub = str(data.get("sub_status") or "").lower()
        catch_all = raw == "catch-all" or sub in _CATCH_ALL_SUB
        if raw == "valid":
            status = "verified"
        elif raw == "invalid":
            status = "invalid"
        elif raw == "catch-all":
            status = "risky"
        elif raw == "do_not_mail" and sub in _ROLE_SUB:
            status = "risky"
        elif raw in ("do_not_mail", "spamtrap", "abuse"):
            status = "invalid"  # never send to these
        else:
            status = "unknown"
        base.record_usage(INFO.key, calls=1, credits=0 if status == "unknown" else 1)
        label = f"{raw}/{sub}" if sub else raw
        return Verdict(status, label, INFO.key, catch_all=catch_all)

    def test(self) -> tuple[bool, str]:
        """`GET /v2/getcredits` (no verification is run)."""
        if not self.configured():
            return False, f"{ENV_KEY} is not set."
        try:
            data = self._get(CREDITS_URL, {})
        except ProviderError as exc:
            return False, exc.message
        credits = data.get("Credits")
        if isinstance(credits, bool) or not isinstance(credits, (int, float)):
            return False, "ZeroBounce returned an unexpected reply."
        if credits < 0:
            return False, "ZeroBounce rejected the API key (it reports -1 credits)."
        return True, f"ZeroBounce key works ({credits:g} credits left)."
