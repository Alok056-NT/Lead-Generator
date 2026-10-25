"""GetProspect people enrich by LinkedIn URL. See DECISIONS (V13)."""

from __future__ import annotations

from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import Found, ProviderError, ProviderInfo

ENRICH_URL = "https://api.getprospect.com/v2/people/enrich"
ENV_KEY = "GETPROSPECT_API_KEY"
# GetProspect may take over a minute; we stop at 45 s (a call must end inside 60 s).
READ_TIMEOUT_SECONDS = 45.0
TIMEOUT = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=10.0)
# Matching is free and reveals nothing, so the key check uses a profile that never matches.
_CHECK_URL = "https://www.linkedin.com/in/leadgen-key-check-0"

INFO = ProviderInfo(
    key="getprospect",
    name="GetProspect",
    purpose="Finds a work email (and, on paid plans, a phone number) from a LinkedIn profile",
    env_keys=[ENV_KEY],
    free_tier="50 emails and 100 checks a month, no card",
    signup_url="https://getprospect.com/signup",
    costs="1 email credit per new email found; 1 phone credit per phone (not on the free plan)",
    paid=False,
)

_limiter = base.RateLimiter(2)


class GetProspectProvider:
    """GetProspect enrich (`POST .../v2/people/enrich`). The key goes in the x-api-key header."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when GETPROSPECT_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        headers = {"x-api-key": key, "Content-Type": "application/json"}

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.post(ENRICH_URL, json=body, headers=headers, timeout=TIMEOUT)
            with http.client() as client:
                return client.post(ENRICH_URL, json=body, headers=headers, timeout=TIMEOUT)

        return base.send_with_retry(send, retry_read_timeout=False)

    def find(self, linkedin_url: str, want_phone: bool = False) -> Found:
        """Enrich one profile. Credits are an estimate: 1 per email, 1 per phone found."""
        body = {
            "data": {"linkedin_url": linkedin_url},
            "enrich_email": True,
            "enrich_phone": bool(want_phone),
        }
        response = self._post(body)
        if response.status_code == 408:
            raise ProviderError(
                "http", "GetProspect is still searching. Try again later (not charged)."
            )
        if response.status_code == 402:
            raise ProviderError("quota", "GetProspect has no credits left.")
        if response.status_code == 401:
            raise ProviderError("auth", "GetProspect did not accept the key.")
        error = base.error_for_status(response, "GetProspect")
        if error is not None:
            raise error
        data = base.json_object(response, "GetProspect")
        if data.get("success") is False:
            errors = [e for e in data.get("errors") or [] if isinstance(e, dict)]
            names = [str(e.get("name") or "") for e in errors]
            if "NOT_FOUND" not in names:
                raise ProviderError("http", "GetProspect reported a problem.")
            base.record_usage(INFO.key, calls=1, credits=0)
            return Found("getprospect")

        result = data.get("data") or {}
        email_info = result.get("email") or {}
        mobile_info = result.get("mobile_phone") or {}
        if email_info.get("status") == "searching":
            raise ProviderError("http", "GetProspect is still searching. Try again later.")
        address = str(email_info.get("address") or "").strip().lower()
        email = ""
        if address and "@" in address and email_info.get("status") not in ("invalid", "not_found"):
            email = address
        phone = ""
        if mobile_info.get("status") == "found" and mobile_info.get("number"):
            phone = str(mobile_info["number"])
        credits = float((1 if email else 0) + (1 if phone else 0))
        base.record_usage(INFO.key, calls=1, credits=credits)
        return Found(
            "getprospect",
            email=email,
            phone=phone,
            phone_kind="mobile" if phone else "",
            credits=credits,
        )

    def test(self) -> tuple[bool, str]:
        """There is no free balance endpoint, so a match on a profile that never exists is used
        (nothing is revealed and nothing is charged). 402 still means the key works."""
        body = {"data": {"linkedin_url": _CHECK_URL}, "enrich_email": False, "enrich_phone": False}
        try:
            response = self._post(body)
        except ProviderError as exc:
            return False, exc.message
        if response.status_code == 401:
            return False, "GetProspect did not accept the key."
        if response.status_code == 402 or response.is_success:
            return True, "GetProspect key works."
        error = base.error_for_status(response, "GetProspect")
        return False, error.message if error is not None else "GetProspect did not answer."
