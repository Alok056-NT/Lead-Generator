"""Fixtures shared by the end-to-end tests: the invented world and an in-memory MCP client."""

from __future__ import annotations

import json

import anyio
import anyio.from_thread
import pytest
import respx
from mcp import Client
from world import install_world

from leadgen import mcp_server


@pytest.fixture
def world(monkeypatch):
    """The keys, DNS, domain checks and the Serper/Hunter/Reoon routes of the universe."""
    with respx.mock(assert_all_called=False) as router:
        yield install_world(monkeypatch, router)


@pytest.fixture
def mcp(world):
    """`call(tool, **args)` runs a tool through the in-memory MCP client and returns its JSON.

    Every reply is kept in `call.transcript` so a test can search all of it for secrets.
    """
    server = mcp_server.build_server()
    with anyio.from_thread.start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(Client(server)) as client:
            transcript: list[str] = []

            def call(tool, /, **args):
                result = portal.call(client.call_tool, tool, args)
                assert not result.is_error, result.content
                text = result.content[0].text
                transcript.append(text)
                return json.loads(text)

            call.transcript = transcript
            yield call
