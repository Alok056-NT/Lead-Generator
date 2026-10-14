"""Reoon Email Verifier: checks one address with the single-verification endpoint (power mode).

Endpoint, parameters and status words are in DECISIONS.md (T09). Reoon only accepts the key as
a query parameter (`key`), so error messages and logs never include the URL.
"""

from __future__ import annotations

from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo, Verdict

VERIFY_URL = "https://emailverifier.reoon.com/api/v1/verify"
BALANCE_URL = "https://emailverifier.reoon.com/api/v1/check-account-balance/"
ENV_KEY = "REOON_API_KEY"
MODE = "power"  # quick mode does not detect catch-all domains
# Power mode can take a while (Reoon: "a few seconds to more than a minute"). We wait 25 s, so
# one check cannot eat a whole find_emails call (about 60 s); a slower check counts as a failure
# and the next verifier is tried. It is never retried: Reoon may charge each attempt.
READ_TIMEOUT_SECONDS = 25.0
VERIFY_TIMEOUT = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=10.0)

INFO = ProviderInfo(
    key="reoon",
    name="Reoon Email Verifier",
    purpose="Checks that an email address exists before sending",
    env_keys=[ENV_KEY],
    free_tier="20 free checks per day (about 600 a month) plus 100 at sign-up, no card",
    signup_url="https://emailverifier.reoon.com/register/",
    paid=False,
    default=True,
)

_limiter = base.RateLimiter(3)  # Reoon asks for no more than 5 parallel requests

_VERDICTS = {
    "safe": "verified",
    "valid": "verified",  # quick mode word
    "invalid": "invalid",
    "disabled": "invalid",
    "disposable": "invalid",
    "spamtrap": "invalid",
    "catch_all": "risky",
    "role_account": "risky",
    "inbox_full": "risky",
    "unknown": "unknown",
}


def _body_error(data: dict[str, Any]) -> ProviderError | None:
    """An error carried in the JSON body (`{"status": "error", "reason": "..."}`)."""
    if str(data.get("status") or "").lower() != "error":
        return None
    reason = str(data.get("reason") or "").lower()
    if "credit" in reason or "limit" in reason or "quota" in reason:
        return ProviderError("quota", "Reoon has no credits left.")
    if "key" in reason or "auth" in reason or "inactive" in reason:
        return ProviderError("auth", "Reoon rejected the API key.")
    return ProviderError("http", "Reoon reported an error.")


class ReoonProvider:
    """Reoon single verification (`GET .../api/v1/verify?email=&key=&mode=power`)."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when REOON_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _get(
        self, url: str, params: dict[str, Any], paid: bool = False
    ) -> dict[str, Any]:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        query = {**params, "key": key}
        extra: dict[str, Any] = {"timeout": VERIFY_TIMEOUT} if paid else {}

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.get(url, params=query, **extra)
            with http.client() as client:
                return client.get(url, params=query, **extra)

        response = base.send_with_retry(send, retry_read_timeout=not paid)
        error = base.error_for_status(response, "Reoon")
        if error is not None:
            raise error
        data = base.json_object(response, "Reoon")
        body_error = _body_error(data)
        if body_error is not None:
            raise body_error
        return data

    def verify(self, email: str) -> Verdict:
        """Check one address. One credit for any answer except `unknown`."""
        data = self._get(VERIFY_URL, {"email": email, "mode": MODE}, paid=True)
        raw = str(data.get("status") or "").lower()
        status = _VERDICTS.get(raw, "unknown")
        catch_all = raw == "catch_all" or data.get("is_catch_all") is True
        if catch_all and status != "invalid":
            status = "risky"
        base.record_usage(INFO.key, calls=1, credits=0 if status == "unknown" else 1)
        return Verdict(status, raw, INFO.key, catch_all=catch_all)

    def test(self) -> tuple[bool, str]:
        """The account-balance call (no verification is run)."""
        if not self.configured():
            return False, f"{ENV_KEY} is not set."
        try:
            data = self._get(BALANCE_URL, {})
        except ProviderError as exc:
            return False, exc.message
        if str(data.get("api_status") or "").lower() != "active":
            return False, "Reoon did not accept the key (the account is not active)."
        parts = [
            data.get("remaining_daily_credits"),
            data.get("remaining_instant_credits"),
        ]
        numbers = [p for p in parts if isinstance(p, (int, float)) and not isinstance(p, bool)]
        if numbers:
            return True, f"Reoon key works ({sum(numbers):g} credits left)."
        return True, "Reoon key works."
