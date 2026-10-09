"""The MCP tool registry is built after startup, off the request path.

It is derived from the routes' OpenAPI document, which costs about a second
to generate.  Built on first use, that second was paid by the first reader in
every backend worker: an agent's first ``tools/list`` and the MCP reference
page (measured 1,009 ms with no database statement).
"""
import asyncio
import logging

from app import main
from app.api.v1.endpoints import mcp_tools


def test_warming_builds_the_registry_and_later_readers_do_not_rebuild_it(client, monkeypatch):
    registry = mcp_tools.TOOLS
    monkeypatch.setattr(registry, "_built", None)
    calls = []
    real = mcp_tools._api_schema
    monkeypatch.setattr(mcp_tools, "_api_schema", lambda: (calls.append(1), real())[1])

    assert registry.warm() == len(registry) > 0
    assert len(calls) == 1
    # Every read after it is served from what was built.
    assert registry["agent_identity"]["path"]
    assert list(registry.items())
    assert len(calls) == 1


class _Messages(logging.Handler):
    """The application logger does not propagate to pytest's capture."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _logged_while(run):
    handler, level = _Messages(), main.logger.level
    main.logger.addHandler(handler)
    main.logger.setLevel(logging.DEBUG)
    try:
        run()
    finally:
        main.logger.removeHandler(handler)
        main.logger.setLevel(level)
    return handler.messages


def test_startup_builds_it_and_says_how_many(client, monkeypatch):
    monkeypatch.setattr(mcp_tools.TOOLS, "_built", None)
    messages = _logged_while(lambda: asyncio.run(main._warm_mcp_registry()))
    assert mcp_tools.TOOLS._built is not None
    assert any("MCP tool registry built" in m for m in messages)


def test_a_registry_that_cannot_be_built_does_not_stop_startup(client, monkeypatch):
    def broken():
        raise RuntimeError("no routes yet")

    monkeypatch.setattr(mcp_tools.TOOLS, "warm", broken)
    # Must not raise.
    messages = _logged_while(lambda: asyncio.run(main._warm_mcp_registry()))
    assert any("could not be built ahead of use" in m for m in messages)
