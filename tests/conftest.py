"""Shared fixtures: every test gets its own throwaway workspace."""

from __future__ import annotations

import pytest

from leadgen.core.logging import shutdown_logging
from leadgen.core.secrets import KNOWN_KEYS


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    """Point LEADGEN_HOME at a temp folder so tests never touch the real workspace."""
    home = tmp_path / "LeadGenerator"
    monkeypatch.setenv("LEADGEN_HOME", str(home))
    for name in KNOWN_KEYS:
        monkeypatch.delenv(name, raising=False)
    yield home
    shutdown_logging()


@pytest.fixture(autouse=True)
def fast_sqlite(monkeypatch):
    """Skip the disk flush on every commit of a throwaway test database (about 10x faster on
    Windows). Only the durability promise changes; every query and constraint behaves the same."""
    from leadgen import db

    real_connect = db.connect

    def connect(path=None):
        conn = real_connect(path)
        conn.execute("PRAGMA synchronous=OFF")
        return conn

    monkeypatch.setattr(db, "connect", connect)


_SSL_CONTEXT: list = []


@pytest.fixture(autouse=True)
def shared_ssl_context(monkeypatch):
    """Build httpx's default TLS context once per process instead of for every client.

    Loading the CA bundle takes about 0.4 s on Windows and nearly every provider test makes a
    client (all HTTP in tests is mocked, so the context is never used to connect).
    """
    from httpx._transports import default

    real = default.create_ssl_context

    def cached(verify=True, cert=None, trust_env=True):
        if verify is not True or cert:
            return real(verify=verify, cert=cert, trust_env=trust_env)
        if not _SSL_CONTEXT:
            _SSL_CONTEXT.append(real(verify=verify, cert=cert, trust_env=trust_env))
        return _SSL_CONTEXT[0]

    monkeypatch.setattr(default, "create_ssl_context", cached)


@pytest.fixture
def sleeps(monkeypatch):
    """Replace retry sleeps and provider rate limiters so provider tests never wait."""
    from leadgen.providers import base, brave, ddg, hunter, lemlist, reoon, serper, zerobounce

    recorded: list[float] = []
    monkeypatch.setattr(base, "_sleep", recorded.append)
    for module in (serper, brave, ddg, hunter, lemlist, reoon, zerobounce):
        monkeypatch.setattr(module, "_limiter", base.RateLimiter(10_000))
    return recorded


@pytest.fixture
def ready_db():
    """A freshly created database in the temp workspace."""
    from leadgen import db

    db.init_db()


# The two longest tests (full campaign runs, 30-90 s alone) go first so that, with `-n auto`, they
# start at once on their own workers instead of finishing last and stretching the whole run.
_RUN_FIRST = ("test_api_flow_end_to_end", "test_ai_search_flow_matches_the_api_flow")


def pytest_collection_modifyitems(items):
    """Move the known-slowest tests to the front (same tests, different order)."""
    items.sort(key=lambda item: 0 if item.name in _RUN_FIRST else 1)
