"""Emailable email verification (250 free credits at sign-up). See DECISIONS (V13)."""

from __future__ import annotations

from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo, Verdict

VERIFY_URL = "https://api.emailable.com/v1/verify"
ACCOUNT_URL = "https://api.emailable.com/v1/account"
ENV_KEY = "EMAILABLE_API_KEY"
# Emailable answers 249 while it is still checking; we give up after 20 s and never retry.
VERIFY_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_PENDING = 249

INFO = ProviderInfo(
    key="emailable",
    name="Emailable",
    purpose="Checks that an email address exists before sending",
    env_keys=[ENV_KEY],
    free_tier="250 free checks when you sign up, no card",
    signup_url="https://app.emailable.com/signup",
    costs="1 credit per checked address",
    paid=False,
)

_limiter = base.RateLimiter(10)
_STATES = {
    "deliverable": "verified",
    "undeliverable": "invalid",
    "risky": "risky",
    "unknown": "unknown",
}


class EmailableProvider:
    """Emailable single verification and the free account call. The key goes in a header."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when EMAILABLE_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _get(
        self, url: str, params: dict[str, Any] | None = None, paid: bool = False
    ) -> httpx.Response:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        headers = {"Authorization": f"Bearer {key}"}
        extra: dict[str, Any] = {"timeout": VERIFY_TIMEOUT} if paid else {}

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.get(url, params=params, headers=headers, **extra)
            with http.client() as client:
                return client.get(url, params=params, headers=headers, **extra)

        return base.send_with_retry(send, retry_read_timeout=not paid)

    def verify(self, email: str) -> Verdict:
        """Check one address. One credit for any answer except "still checking" (249)."""
        response = self._get(VERIFY_URL, {"email": email, "timeout": 10}, paid=True)
        if response.status_code == _PENDING:
            base.record_usage(INFO.key, calls=1, credits=0)
            return Verdict("unknown", "pending", INFO.key)
        error = base.error_for_status(response, "Emailable")
        if error is not None:
            raise error
        data = base.json_object(response, "Emailable")
        raw = str(data.get("state") or "").lower()
        status = _STATES.get(raw, "unknown")
        catch_all = data.get("accept_all") is True
        if catch_all and status != "invalid":
            status = "risky"
        base.record_usage(INFO.key, calls=1, credits=0 if status == "unknown" else 1)
        return Verdict(status, raw, INFO.key, catch_all=catch_all)

    def test(self) -> tuple[bool, str]:
        """GET the account (free). Shows the credits left when Emailable reports them."""
        try:
            response = self._get(ACCOUNT_URL)
        except ProviderError as exc:
            return False, exc.message
        if response.status_code in (401, 403):
            return False, "Emailable did not accept the key."
        error = base.error_for_status(response, "Emailable")
        if error is not None:
            return False, error.message
        try:
            data = base.json_object(response, "Emailable")
        except ProviderError as exc:
            return False, exc.message
        credits = data.get("available_credits")
        if isinstance(credits, (int, float)):
            return True, f"Emailable key works ({credits:g} credits left)."
        return True, "Emailable key works."
