"""Provider framework: usage tracking, rate limiter, retry helper, error mapping."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import httpx
import pytest

from leadgen import db
from leadgen.providers import base
from leadgen.providers.base import ProviderError


def test_provider_error_codes():
    err = ProviderError("auth", "nope")
    assert err.code == "auth" and str(err) == "nope"
    with pytest.raises(ValueError):
        ProviderError("bogus")


def test_record_and_read_usage(ready_db):
    base.record_usage("serper")
    base.record_usage("serper", calls=2, credits=3)
    base.record_usage("brave")
    assert base.usage("serper", "day") == {"calls": 3, "credits": 4.0}
    assert base.usage("serper", "month") == {"calls": 3, "credits": 4.0}
    assert base.usage("nothing") == {"calls": 0, "credits": 0.0}
    with db.session() as conn:
        rows = db.query(conn, "SELECT day FROM api_usage WHERE provider = 'serper'")
    assert rows == [{"day": datetime.now(UTC).strftime("%Y-%m-%d")}]


def test_usage_aggregates_by_month(ready_db):
    today = datetime.now(UTC)
    this_month = today.strftime("%Y-%m")
    with db.session() as conn:
        for day, calls, credits in (
            ("2020-01-31", 5, 5),  # old month
            (f"{this_month}-01", 2, 2),
            (f"{this_month}-15", 3, 6),
        ):
            db.insert(conn, "api_usage", provider="serper", day=day, calls=calls, credits=credits)
        db.insert(conn, "api_usage", provider="brave", day=f"{this_month}-02", calls=9, credits=9)
    assert base.usage("serper", "month") == {"calls": 5, "credits": 8.0}
    assert base.usage("serper", "all") == {"calls": 10, "credits": 13.0}
    assert base.usage("brave", "month")["calls"] == 9
    with pytest.raises(ValueError):
        base.usage("serper", "week")


def test_rate_limiter_spaces_calls():
    limiter = base.RateLimiter(20)  # 50 ms apart
    start = time.perf_counter()
    for _ in range(4):
        limiter.acquire()
    assert time.perf_counter() - start >= 0.14
    with pytest.raises(ValueError):
        base.RateLimiter(0)


def test_rate_limiter_is_thread_safe():
    limiter = base.RateLimiter(50)
    stamps: list[float] = []

    def work():
        limiter.acquire()
        stamps.append(time.perf_counter())

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert all(g >= 0.012 for g in gaps)  # 20 ms nominal, allow timer jitter


def _sender(*statuses, headers=None):
    codes = list(statuses)
    calls: list[int] = []

    def send() -> httpx.Response:
        code = codes[len(calls)] if len(calls) < len(codes) else codes[-1]
        calls.append(code)
        return httpx.Response(code, headers=headers or {})

    return send, calls


def test_retry_on_429_then_success(sleeps):
    send, calls = _sender(429, 200)
    assert base.send_with_retry(send).status_code == 200
    assert calls == [429, 200] and sleeps == [1.0]


def test_retry_gives_up_after_two_retries(sleeps):
    send, calls = _sender(503)
    assert base.send_with_retry(send).status_code == 503
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


@pytest.mark.parametrize("status", [401, 403, 400, 404])
def test_no_retry_on_client_errors(status, sleeps):
    send, calls = _sender(status)
    assert base.send_with_retry(send).status_code == status
    assert len(calls) == 1 and sleeps == []


def test_retry_after_header_is_honoured_and_capped(sleeps):
    send, _ = _sender(429, 429, 200, headers={"retry-after": "30"})
    base.send_with_retry(send)
    assert sleeps == [base.MAX_RETRY_AFTER, base.MAX_RETRY_AFTER]


def test_network_errors_retry_then_raise_http(sleeps):
    attempts = []

    def send():
        attempts.append(1)
        raise httpx.ConnectError("boom", request=httpx.Request("GET", "https://example.com"))

    with pytest.raises(ProviderError) as info:
        base.send_with_retry(send)
    assert info.value.code == "http" and len(attempts) == 3 and sleeps == [1.0, 2.0]


def test_error_for_status_mapping():
    def err(status, text=""):
        return base.error_for_status(httpx.Response(status, text=text), "X")

    assert err(200) is None and err(204) is None
    assert err(401).code == "auth" and err(403, "Unauthorized.").code == "auth"
    assert err(429).code == "rate_limited"
    assert err(402).code == "quota"
    assert err(400, '{"message":"Not enough credits"}').code == "quota"
    assert err(400, "bad request").code == "http"
    assert err(500).code == "http" and err(404).code == "http"


def test_read_timeout_retried_by_default(sleeps):
    attempts = []

    def send():
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=httpx.Request("GET", "https://example.com"))

    with pytest.raises(ProviderError) as info:
        base.send_with_retry(send)
    assert info.value.code == "http" and len(attempts) == 3


def test_read_timeout_not_retried_for_paid_calls(sleeps):
    attempts = []

    def send():
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=httpx.Request("GET", "https://example.com"))

    with pytest.raises(ProviderError) as info:
        base.send_with_retry(send, retry_read_timeout=False)
    assert info.value.code == "http" and len(attempts) == 1 and sleeps == []


def test_connect_errors_still_retried_for_paid_calls(sleeps):
    attempts = []

    def send():
        attempts.append(1)  # the request never left: safe to retry
        raise httpx.ConnectError("boom", request=httpx.Request("GET", "https://example.com"))

    with pytest.raises(ProviderError):
        base.send_with_retry(send, retry_read_timeout=False)
    assert len(attempts) == 3
