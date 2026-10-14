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


@pytest.fixture
def sleeps(monkeypatch):
    """Replace retry sleeps and provider rate limiters so provider tests never wait."""
    from leadgen.providers import base, brave, ddg, serper

    recorded: list[float] = []
    monkeypatch.setattr(base, "_sleep", recorded.append)
    for module in (serper, brave, ddg):
        monkeypatch.setattr(module, "_limiter", base.RateLimiter(10_000))
    return recorded


@pytest.fixture
def ready_db():
    """A freshly created database in the temp workspace."""
    from leadgen import db

    db.init_db()
