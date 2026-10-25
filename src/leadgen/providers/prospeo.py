"""Prospeo Enrich Person by LinkedIn URL (synchronous). See DECISIONS (V13)."""

from __future__ import annotations

from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import Found, ProviderError, ProviderInfo

ENRICH_URL = "https://api.prospeo.io/enrich-person"
ACCOUNT_URL = "https://api.prospeo.io/account-information"
ENV_KEY = "PROSPEO_API_KEY"

INFO = ProviderInfo(
    key="prospeo",
    name="Prospeo",
    purpose="Finds a work email (and, if you ask, a mobile number) from a LinkedIn profile",
    env_keys=[ENV_KEY],
    free_tier="Free credits every month (amount set by Prospeo)",
    signup_url="https://app.prospeo.io/signup",
    costs="1 credit per email found; 10 per mobile number found (email included)",
    paid=False,
)

_limiter = base.RateLimiter(4)
_CODES = {"INSUFFICIENT_CREDITS": "quota", "INVALID_API_KEY": "auth"}


def _body_error(data: dict[str, Any]) -> ProviderError | None:
    """None for NO_MATCH (not found is not an error) and for success."""
    if data.get("error") is not True:
        return None
    code = str(data.get("error_code") or "")
    if code in ("NO_MATCH", "INVALID_DATAPOINTS"):
        return None
    kind = _CODES.get(code, "http")
    return ProviderError(kind, f"Prospeo said {code or 'error'}.")


class ProspeoProvider:
    """Prospeo enrich-person and the free account call. The key goes in the X-KEY header."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when PROSPEO_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _headers(self) -> dict[str, str]:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        return {"X-KEY": key, "Content-Type": "application/json"}

    def find(self, linkedin_url: str, want_phone: bool = False) -> Found:
        """Enrich one profile. Credits: 0 for a free re-reveal, 10 with a mobile, 1 for an email."""
        body = {
            "data": {"linkedin_url": linkedin_url},
            "only_verified_email": False,
            "enrich_mobile": bool(want_phone),
        }
        headers = self._headers()

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.post(ENRICH_URL, json=body, headers=headers)
            with http.client() as client:
                return client.post(ENRICH_URL, json=body, headers=headers)

        response = base.send_with_retry(send, retry_read_timeout=False)
        error = base.error_for_status(response, "Prospeo")
        if error is not None and response.status_code != 400:  # 400 carries Prospeo's codes
            raise error
        data = base.json_object(response, "Prospeo")
        body_error = _body_error(data)
        if body_error is not None:
            raise body_error
        if data.get("error") is True:  # NO_MATCH: nothing found, nothing charged
            base.record_usage(INFO.key, calls=1, credits=0)
            return Found("prospeo")

        person = data.get("person") or {}
        email_info = person.get("email") or {}
        mobile_info = person.get("mobile") or {}
        email = ""
        if email_info.get("revealed"):
            email = str(email_info.get("email") or "").strip().lower()
        if "@" not in email:
            email = ""
        phone = ""
        if mobile_info.get("revealed"):
            phone = str(mobile_info.get("mobile_international") or mobile_info.get("mobile") or "")
        if data.get("free_enrichment"):
            credits = 0.0
        else:
            credits = 10.0 if phone else (1.0 if email else 0.0)
        base.record_usage(INFO.key, calls=1, credits=credits)
        return Found(
            "prospeo",
            email=email,
            phone=phone,
            phone_kind="mobile" if phone else "",
            credits=credits,
        )

    def test(self) -> tuple[bool, str]:
        """GET the free account information (no credits used)."""
        try:
            headers = self._headers()

            def send() -> httpx.Response:
                _limiter.acquire()
                if self._client is not None:
                    return self._client.get(ACCOUNT_URL, headers=headers)
                with http.client() as client:
                    return client.get(ACCOUNT_URL, headers=headers)

            response = base.send_with_retry(send)
            if response.status_code in (401, 403):
                return False, "Prospeo did not accept the key."
            error = base.error_for_status(response, "Prospeo")
            if error is not None:
                raise error
            data = base.json_object(response, "Prospeo")
        except ProviderError as exc:
            return False, exc.message
        if data.get("error") is True:
            return False, "Prospeo did not accept the key."
        remaining = (data.get("response") or {}).get("remaining_credits")
        if isinstance(remaining, (int, float)):
            return True, f"Prospeo key works ({remaining:g} credits left)."
        return True, "Prospeo key works."
