"""Provider framework: error type, provider info, usage tracking, rate limiter, retry helper."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import httpx

from leadgen import db

ERROR_CODES = ("not_configured", "auth", "quota", "rate_limited", "http", "parse")
RETRIES = 2
BACKOFF_SECONDS = (1.0, 2.0)
MAX_RETRY_AFTER = 5.0

_sleep = time.sleep  # tests replace this so retries do not wait


class ProviderError(Exception):
    """A provider call failed. `code` is one of `ERROR_CODES`; the message never holds secrets."""

    def __init__(self, code: str, message: str = "") -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown provider error code {code!r}")
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class ProviderInfo:
    """Static description of a provider, shown to the user and used for 'needs key' replies.

    `paid` is True when using the provider needs a paid plan or a card on file (even if a free
    credit exists). `default` marks the provider the tool recommends for its purpose.
    """

    key: str
    name: str
    purpose: str
    env_keys: list[str] = field(default_factory=list)
    free_tier: str = ""
    signup_url: str = ""
    paid: bool = False
    default: bool = False


VERDICT_STATUSES = ("verified", "risky", "invalid", "unknown")


@dataclass(frozen=True)
class Verdict:
    """What an email verifier concluded about one address.

    `status` is verified / risky / invalid / unknown; `raw_status` is the provider's own word
    (kept as evidence); `catch_all` is True when the verifier says the whole domain accepts
    any address.
    """

    status: str
    raw_status: str
    provider: str
    catch_all: bool = False

    def __post_init__(self) -> None:
        if self.status not in VERDICT_STATUSES:
            raise ValueError(f"unknown verdict status {self.status!r}")


@runtime_checkable
class Provider(Protocol):
    """Small interface every provider implements."""

    info: ProviderInfo

    def configured(self) -> bool:
        """True when everything needed to call the provider is set."""

    def test(self) -> tuple[bool, str]:
        """Cheapest possible call (never more than 1 credit). Returns (ok, plain message)."""


# --- usage tracking -------------------------------------------------------------------------


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def record_usage(provider_key: str, calls: int = 1, credits: float = 1) -> None:
    """Add to today's (UTC) row in `api_usage` for `provider_key`."""
    with db.session() as conn:
        conn.execute(
            "INSERT INTO api_usage (provider, day, calls, credits, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (provider, day) DO UPDATE SET "
            "calls = calls + excluded.calls, credits = credits + excluded.credits, "
            "updated_at = excluded.updated_at",
            (provider_key, _today(), calls, credits, db.now()),
        )


def usage(provider_key: str, period: str = "month") -> dict[str, float]:
    """Total `{"calls", "credits"}` for the provider. `period`: "day", "month" (this UTC month)
    or "all"."""
    today = _today()
    if period == "day":
        where, params = "AND day = ?", [today]
    elif period == "month":
        where, params = "AND day >= ?", [today[:7] + "-01"]
    elif period == "all":
        where, params = "", []
    else:
        raise ValueError(f"unknown period {period!r}; use day, month or all")
    with db.session() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(calls), 0), COALESCE(SUM(credits), 0) FROM api_usage "
            f"WHERE provider = ? {where}",
            [provider_key, *params],
        ).fetchone()
    return {"calls": int(row[0]), "credits": float(row[1])}


# --- rate limiting --------------------------------------------------------------------------


class RateLimiter:
    """Thread-safe token bucket: at most `per_second` calls per second, bursts of up to one."""

    def __init__(self, per_second: float) -> None:
        if per_second <= 0:
            raise ValueError("per_second must be positive")
        self._interval = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a call is allowed.

        Uses `perf_counter` (not `monotonic`, which ticks every 15.6 ms on Windows).
        """
        with self._lock:
            now = time.perf_counter()
            start = max(now, self._next)
            self._next = start + self._interval
            wait = start - now
        if wait > 0:
            time.sleep(wait)


# --- HTTP retry and error mapping -----------------------------------------------------------


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "").strip()
    try:
        seconds = float(value)
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER) if math.isfinite(seconds) else None


def send_with_retry(
    send: Callable[[], httpx.Response], retry_read_timeout: bool = True
) -> httpx.Response:
    """Call `send()`; retry up to 2 times (1 s, 2 s backoff) on 429, 5xx or network errors.

    401/403 and other 4xx are returned at once (never retried). After the last retry the final
    response is returned (the caller maps it to an error); a network error becomes
    `ProviderError("http")`. With `retry_read_timeout=False` (paid calls) a read timeout is not
    retried, because the provider may already have done, and charged for, the work: it becomes
    `ProviderError("http")` at once.
    """
    for attempt in range(RETRIES + 1):
        last = attempt == RETRIES
        try:
            response = send()
        except httpx.ReadTimeout:
            if not retry_read_timeout:
                raise ProviderError(
                    "http", "no reply in time (not retried, the call may already be charged)"
                ) from None
            if last:
                raise ProviderError("http", "network error (ReadTimeout)") from None
            _sleep(BACKOFF_SECONDS[attempt])
            continue
        except httpx.TransportError as exc:
            if last:
                raise ProviderError("http", f"network error ({type(exc).__name__})") from None
            _sleep(BACKOFF_SECONDS[attempt])
            continue
        if response.status_code in (401, 403) or not (
            response.status_code == 429 or response.status_code >= 500
        ):
            return response
        if last:
            return response
        delay = _retry_after(response)
        _sleep(BACKOFF_SECONDS[attempt] if delay is None else delay)
    raise AssertionError("unreachable")  # pragma: no cover


def error_for_status(response: httpx.Response, provider_name: str) -> ProviderError | None:
    """Map a non-success response to a `ProviderError` (None when the status is 2xx).

    401/403 -> auth; 402, or a 400/403/429 whose body mentions credits -> quota; 429 ->
    rate_limited; anything else -> http. Only the status code is put in the message.
    """
    status = response.status_code
    if 200 <= status < 300:
        return None
    body = response.text[:500].lower() if status in (400, 402, 403, 429) else ""
    if status == 402 or (status in (400, 403, 429) and "credit" in body):
        return ProviderError("quota", f"{provider_name} has no credits left (HTTP {status}).")
    if status in (401, 403):
        return ProviderError("auth", f"{provider_name} rejected the API key (HTTP {status}).")
    if status == 429:
        return ProviderError("rate_limited", f"{provider_name} is rate limiting us (HTTP 429).")
    return ProviderError("http", f"{provider_name} returned HTTP {status}.")


def json_object(response: httpx.Response, provider_name: str) -> dict:
    """The response body as a JSON object, or `ProviderError("parse")` (message has no body)."""
    try:
        data = response.json()
    except ValueError:
        raise ProviderError(
            "parse", f"{provider_name} returned something that is not JSON."
        ) from None
    if not isinstance(data, dict):
        raise ProviderError("parse", f"{provider_name} returned an unexpected response.")
    return data
