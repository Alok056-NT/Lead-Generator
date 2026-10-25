"""Apollo people match by LinkedIn profile URL; phone numbers arrive later and are polled
(poll_only, no webhook: this app has no public address). See DECISIONS (V13)."""

from __future__ import annotations

from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import Found, ProviderError, ProviderInfo

MATCH_URL = "https://api.apollo.io/api/v1/people/match"
RESULT_URL = "https://api.apollo.io/api/v1/webhook_result/{request_id}"
ENV_KEY = "APOLLO_API_KEY"
PHONE_CREDITS = 8

INFO = ProviderInfo(
    key="apollo",
    name="Apollo",
    purpose="Finds a work email (and, if you ask, a mobile number) from a LinkedIn profile",
    env_keys=[ENV_KEY],
    free_tier=(
        "Free plan with a small monthly credit allowance (amount not published; the API "
        "may need a paid plan)"
    ),
    signup_url="https://app.apollo.io/#/settings/integrations/api",
    costs="1 credit per person found; 8 more if a mobile number is found",
    paid=False,
)

_limiter = base.RateLimiter(0.8)  # free plan: 50 per minute
_LOCKED = "email_not_unlocked"  # Apollo's placeholder for an email the plan cannot show


class ApolloProvider:
    """Apollo people match and webhook-result poll. The key goes in a header, never in a URL."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when APOLLO_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _headers(self) -> dict[str, str]:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        return {"x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"}

    def _send(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        paid: bool = False,
    ) -> httpx.Response:
        headers = self._headers()

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.request(method, url, params=params, headers=headers)
            with http.client() as client:
                return client.request(method, url, params=params, headers=headers)

        return base.send_with_retry(send, retry_read_timeout=not paid)

    def find(self, linkedin_url: str, want_phone: bool = False) -> Found:
        """Match one profile. Charges 1 credit when Apollo matches a person; a phone request
        (poll_only) is answered later by `collect_phone`."""
        params: dict[str, Any] = {"linkedin_url": linkedin_url, "reveal_personal_emails": "false"}
        if want_phone:
            params |= {"reveal_phone_number": "true", "poll_only": "true"}
        response = self._send("POST", MATCH_URL, params=params, paid=True)
        if response.status_code == 403:
            raise ProviderError(
                "auth",
                "Apollo refused the key for this lookup (HTTP 403). Make a key that may call "
                "People Enrichment and Webhook Result, or a master key.",
            )
        error = base.error_for_status(response, "Apollo")
        if error is not None:
            raise error
        data = base.json_object(response, "Apollo")
        person = data.get("person") or {}
        email = str(person.get("email") or "").strip().lower()
        if email.startswith(_LOCKED) or "@" not in email:
            email = ""
        matched = bool(person) and person.get("match_confidence") != "none"
        request_id = str(data.get("request_id") or "") if want_phone and matched else ""
        credits = 1.0 if matched else 0.0
        base.record_usage(INFO.key, calls=1, credits=credits)
        return Found("apollo", email=email, phone_request=request_id, credits=credits)

    def collect_phone(self, request_id: str) -> tuple[str, str, str]:
        """Poll one phone request. Returns (state, number, kind): state "ready" (number may be ""
        when none was found), "pending" or "gone" (unknown or older than 30 days). Charges 8
        credits only when a number is found."""
        response = self._send("GET", RESULT_URL.format(request_id=request_id))
        if response.status_code == 200:
            data = base.json_object(response, "Apollo")
            if data.get("webhook_status") == "in_progress":
                return "pending", "", ""
            people = (data.get("webhook_result") or {}).get("people") or []
            person = people[0] if people and isinstance(people[0], dict) else {}
            phones = person.get("phone_numbers") or []
            first = phones[0] if phones and isinstance(phones[0], dict) else {}
            number = str(first.get("sanitized_number") or first.get("raw_number") or "")
            kind = str(first.get("type_cd") or "")
            base.record_usage(INFO.key, calls=1, credits=PHONE_CREDITS if number else 0)
            return "ready", number, kind
        if response.status_code == 404 and "result_pending" in response.text:
            return "pending", "", ""
        if response.status_code in (400, 404, 410):
            return "gone", "", ""
        error = base.error_for_status(response, "Apollo")
        if error is not None:
            raise error
        return "gone", "", ""  # pragma: no cover - 2xx other than 200 is not expected

    def test(self) -> tuple[bool, str]:
        """GET the webhook result for request "1" (free). 401/403 means the key is refused."""
        try:
            response = self._send("GET", RESULT_URL.format(request_id="1"))
        except ProviderError as exc:
            return False, exc.message
        if response.status_code in (401, 403):
            return False, (
                f"Apollo did not accept the key (HTTP {response.status_code}). Make a key that "
                "may call People Enrichment and Webhook Result, or a master key."
            )
        if response.status_code in (400, 404, 410) or response.is_success:
            return True, "Apollo key works."
        error = base.error_for_status(response, "Apollo")
        return False, error.message if error is not None else "Apollo did not answer."
