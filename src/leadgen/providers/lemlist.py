"""lemlist API: list campaigns and add a lead (with custom variables) to an existing campaign.

Base URL, auth, paths, parameters and error texts are in DECISIONS.md (T12). Auth is HTTP Basic
with an empty user name and the API key as the password; the key never appears in a URL, a log
or an error message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from leadgen.core import http, secrets
from leadgen.providers import base
from leadgen.providers.base import ProviderError, ProviderInfo

BASE_URL = "https://api.lemlist.com/api"
ENV_KEY = "LEMLIST_API_KEY"
PAGE_SIZE = 100  # documented default and maximum for GET /campaigns
MAX_PAGES = 10  # at most 1,000 campaigns are listed

INFO = ProviderInfo(
    key="lemlist",
    name="lemlist",
    purpose="Pushes LinkedIn campaign leads into lemlist",
    env_keys=[ENV_KEY],
    free_tier="Needs a lemlist plan (the API is used on your own lemlist account)",
    signup_url="https://www.lemlist.com/",
    costs="",
    paid=True,
    default=False,
)

# 20 requests per 2 seconds per API key (lemlist "Rate Limits"); stay below at 8 per second.
_limiter = base.RateLimiter(8)


@dataclass(frozen=True)
class LeadResult:
    """What lemlist said about one lead.

    `status`: `created` (200), `duplicate` (the contact already has a lead in this campaign) or
    `rejected` (lemlist refused this lead only; `detail` says why in a few words).
    """

    status: str
    lead_id: str = ""
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


def _short(text: str, limit: int = 160) -> str:
    return " ".join(text.split())[:limit]


class LemlistProvider:
    """lemlist API (`https://api.lemlist.com/api`)."""

    info = INFO

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def configured(self) -> bool:
        """True when LEMLIST_API_KEY is set."""
        return bool(secrets.get(ENV_KEY))

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        key = secrets.get(ENV_KEY)
        if not key:
            raise ProviderError("not_configured", f"{ENV_KEY} is not set.")
        url = f"{BASE_URL}{path}"
        auth = httpx.BasicAuth("", key)

        def send() -> httpx.Response:
            _limiter.acquire()
            if self._client is not None:
                return self._client.request(method, url, params=params, json=json, auth=auth)
            with http.client() as client:
                return client.request(method, url, params=params, json=json, auth=auth)

        return base.send_with_retry(send)

    def list_campaigns(self) -> list[dict[str, str]]:
        """Every campaign as `{id, name, status}` (`GET /campaigns?version=v2`, 100 a page)."""
        out: list[dict[str, str]] = []
        for page in range(MAX_PAGES):
            response = self._request(
                "GET",
                "/campaigns",
                params={"version": "v2", "limit": PAGE_SIZE, "offset": page * PAGE_SIZE},
            )
            base.record_usage(INFO.key, calls=1, credits=0)
            error = base.error_for_status(response, "lemlist")
            if error is not None:
                raise error
            try:
                data = response.json()
            except ValueError:
                raise ProviderError(
                    "parse", "lemlist returned something that is not JSON."
                ) from None
            if not isinstance(data, list):
                raise ProviderError("parse", "lemlist returned an unexpected campaign list.")
            for item in data:
                if isinstance(item, dict) and item.get("_id"):
                    out.append(
                        {
                            "id": str(item["_id"]),
                            "name": str(item.get("name") or ""),
                            "status": str(item.get("status") or ""),
                        }
                    )
            if len(data) < PAGE_SIZE:
                break
        return out

    def create_lead(self, campaign_id: str, payload: dict[str, Any]) -> LeadResult:
        """Add one lead (`POST /campaigns/{id}/leads/`). Extra keys become custom variables.

        A problem with this one lead (invalid email, already in the campaign, the contact
        belongs to another lead) comes back as a `LeadResult`; a problem with the key, the
        campaign or lemlist itself raises `ProviderError`.
        """
        response = self._request("POST", f"/campaigns/{campaign_id}/leads/", json=payload)
        base.record_usage(INFO.key, calls=1, credits=0)
        status = response.status_code
        if 200 <= status < 300:
            try:
                data = response.json()
            except ValueError:
                data = {}
            data = data if isinstance(data, dict) else {}
            warnings = [
                str(w.get("code") or "")
                for w in (data.get("warnings") or [])
                if isinstance(w, dict)
            ]
            return LeadResult("created", lead_id=str(data.get("_id") or ""), warnings=warnings)
        text = _short(response.text)
        lowered = text.lower()
        if status == 404 and "api key" in lowered:
            raise ProviderError("auth", "lemlist does not know this API key (HTTP 404).")
        if status == 404 and "campaign" in lowered:
            raise ProviderError("http", "lemlist says this campaign was not found.")
        if status in (400, 405, 409):
            if status == 400 and (
                "already in the campaign" in lowered or "already_in_campaign" in lowered
            ):
                return LeadResult("duplicate", detail=text)
            return LeadResult("rejected", detail=text or f"HTTP {status}")
        error = base.error_for_status(response, "lemlist")
        raise error or ProviderError("http", f"lemlist returned HTTP {status}.")

    def test(self) -> tuple[bool, str]:
        """List one campaign (no lead is touched)."""
        if not self.configured():
            return False, f"{ENV_KEY} is not set."
        try:
            response = self._request(
                "GET", "/campaigns", params={"version": "v2", "limit": 1, "offset": 0}
            )
            error = base.error_for_status(response, "lemlist")
        except ProviderError as exc:
            return False, exc.message
        if error is not None:
            return False, error.message
        return True, "lemlist key works."
