"""`leadgen doctor --live` (T19): the real-provider smoke test, tested with mocked HTTP only.

No test here spends a credit: every request goes to a respx route, and anything unmocked fails.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from leadgen.cli import main
from leadgen.core import secrets
from leadgen.services import live_check

SERPER = "https://google.serper.dev/search"
HUNTER_ACCOUNT = "https://api.hunter.io/v2/account"
KEYS = {
    "SERPER_API_KEY": "serper-live-key-1111",
    "REOON_API_KEY": "reoon-live-key-2222",
    "HUNTER_API_KEY": "hunter-live-key-3333",
}


class Routes:
    """The four mocked endpoints a live check can reach."""

    def __init__(self, router: respx.MockRouter) -> None:
        organic = [{"title": "t", "link": "https://example.com", "snippet": "s"}]
        self.serper = router.post(SERPER).mock(
            return_value=httpx.Response(200, json={"organic": organic})
        )
        self.reoon = router.get(
            url__regex=r"https://emailverifier\.reoon\.com/api/v1/verify.*"
        ).mock(return_value=httpx.Response(200, json={"status": "safe", "is_catch_all": False}))
        self.hunter = router.get(HUNTER_ACCOUNT).mock(
            return_value=httpx.Response(
                200, json={"data": {"requests": {"credits": {"remaining": 38}}}}
            )
        )
        self.zerobounce = router.get(
            url__regex=r"https://api\.zerobounce\.net/v2/validate.*"
        ).mock(return_value=httpx.Response(200, json={"status": "valid", "sub_status": ""}))

    def any_called(self) -> bool:
        return any(r.called for r in (self.serper, self.reoon, self.hunter, self.zerobounce))


@pytest.fixture
def routes(sleeps, ready_db):
    """No waits, a database, and every request mocked (an unmocked one fails the test)."""
    with respx.mock(assert_all_called=False) as router:
        yield Routes(router)


def set_keys(*names):
    for name in names:
        secrets.set(name, KEYS.get(name, "other-live-key-4444"))


def test_no_keys_means_every_step_is_skipped_and_no_call_is_made(routes):
    steps = live_check.plan()
    assert [s["provider"] for s in steps] == ["", "", ""]
    results = live_check.run()
    assert [r["status"] for r in results] == ["skipped"] * 3
    assert not routes.any_called()


def test_all_keys_one_call_each_and_at_most_three_credits(routes):
    set_keys(*KEYS)
    steps = live_check.plan()
    assert [s["provider"] for s in steps] == ["serper", "reoon", "hunter"]
    assert sum(s["credits"] for s in steps) == 2 <= live_check.MAX_CREDITS
    results = live_check.run(steps)
    assert [r["status"] for r in results] == ["ok", "ok", "ok"]
    calls = (routes.serper.call_count, routes.reoon.call_count, routes.hunter.call_count)
    assert calls == (1, 1, 1)
    assert "38 credits left" in results[2]["message"]
    assert sum(r["credits"] for r in results) <= live_check.MAX_CREDITS


def test_zerobounce_is_used_when_reoon_has_no_key(routes):
    set_keys("SERPER_API_KEY", "ZEROBOUNCE_API_KEY")
    steps = live_check.plan()
    assert steps[1]["provider"] == "zerobounce"
    results = live_check.run(steps)
    assert results[1]["status"] == "ok" and routes.zerobounce.call_count == 1
    assert not routes.reoon.called


def test_a_provider_error_is_reported_without_the_key(routes):
    set_keys(*KEYS)
    routes.serper.mock(return_value=httpx.Response(403, json={"message": "Unauthorized"}))
    results = live_check.run()
    assert results[0]["status"] == "failed" and results[0]["credits"] == 0
    assert KEYS["SERPER_API_KEY"] not in repr(results)
    assert [r["status"] for r in results[1:]] == ["ok", "ok"]


def test_cli_asks_first_and_a_no_spends_nothing(routes, capsys, monkeypatch):
    set_keys(*KEYS)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert main(["doctor", "--live"]) == 0
    out = capsys.readouterr().out
    assert "at most 2 credit(s)" in out and "Nothing was spent" in out
    assert not routes.any_called()


def test_cli_live_with_yes_prints_the_answers_and_no_key(routes, capsys):
    set_keys(*KEYS)
    assert main(["doctor", "--live", "--yes"]) == 0
    out = capsys.readouterr().out
    assert out.count("OK ") >= 3 and "All good." in out
    for value in KEYS.values():
        assert value not in out
    assert routes.serper.call_count == 1 and routes.reoon.call_count == 1


def test_cli_live_failure_gives_a_non_zero_exit(routes, capsys):
    set_keys(*KEYS)
    routes.hunter.mock(return_value=httpx.Response(401, json={}))
    assert main(["doctor", "--live", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "Problems found" in out


def test_plain_doctor_never_makes_a_live_call(routes):
    set_keys(*KEYS)
    assert main(["doctor"]) == 0
    assert not routes.any_called()
