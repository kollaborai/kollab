"""Unit tests for auto_grant_mcp_tools (kollab-p8f).

Exercises MCPIntegration._auto_grant_mcp_tools directly. The golden
path — a mid-session MCP connect firing one inject_tool_grant per
discovered tool — is the core behavior we need to defend.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from kollabor_agent.mcp_integration import MCPIntegration


class _FakeBus:
    def __init__(self, services: Optional[Dict[str, Any]] = None):
        self._services: Dict[str, Any] = services or {}

    def get_service(self, name: str) -> Any:
        return self._services.get(name)


class _FakeConfig:
    def __init__(self, values: Dict[str, Any]):
        self._values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _make_llm_service(
    first_turn_complete: bool = True,
    auto_grant: bool = True,
) -> Any:
    svc = type("FakeLLMService", (), {})()
    svc._first_turn_complete = first_turn_complete
    svc.config = _FakeConfig(
        {"plugins.mcp.auto_grant_mcp_tools": auto_grant}
    )
    svc.inject_tool_grant = AsyncMock()
    return svc


def _make_mcp(llm_service: Optional[Any]) -> MCPIntegration:
    services: Dict[str, Any] = {}
    if llm_service is not None:
        services["llm_service"] = llm_service
    bus = _FakeBus(services)
    return MCPIntegration(event_bus=bus)


@pytest.mark.asyncio
async def test_fires_grant_per_tool_when_enabled_and_turn_complete():
    llm = _make_llm_service(first_turn_complete=True, auto_grant=True)
    mcp = _make_mcp(llm)

    tools: List[Dict[str, Any]] = [
        {"name": "github-search"},
        {"name": "github-create-issue"},
        {"name": "github-comment"},
    ]
    await mcp._auto_grant_mcp_tools("github", tools)

    assert llm.inject_tool_grant.await_count == 3
    calls = [c.args[0] for c in llm.inject_tool_grant.await_args_list]
    assert calls == ["github-search", "github-create-issue", "github-comment"]
    reasons = [
        c.kwargs.get("reason") for c in llm.inject_tool_grant.await_args_list
    ]
    assert all("github" in (r or "") for r in reasons)


@pytest.mark.asyncio
async def test_skips_when_first_turn_not_complete():
    """Boot-time connects: tools are already in initial prompt. No grants."""
    llm = _make_llm_service(first_turn_complete=False, auto_grant=True)
    mcp = _make_mcp(llm)

    await mcp._auto_grant_mcp_tools("github", [{"name": "foo"}])

    llm.inject_tool_grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_config_flag_false():
    llm = _make_llm_service(first_turn_complete=True, auto_grant=False)
    mcp = _make_mcp(llm)

    await mcp._auto_grant_mcp_tools("github", [{"name": "foo"}])

    llm.inject_tool_grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_llm_service_not_registered():
    mcp = _make_mcp(llm_service=None)

    # must not raise
    await mcp._auto_grant_mcp_tools("github", [{"name": "foo"}])


@pytest.mark.asyncio
async def test_skips_when_event_bus_missing():
    mcp = MCPIntegration(event_bus=None)

    await mcp._auto_grant_mcp_tools("github", [{"name": "foo"}])


@pytest.mark.asyncio
async def test_ignores_tools_without_name():
    llm = _make_llm_service()
    mcp = _make_mcp(llm)

    tools: List[Dict[str, Any]] = [
        {"name": "has-name"},
        {},
        {"name": ""},
        {"description": "no name"},
    ]
    await mcp._auto_grant_mcp_tools("server", tools)

    assert llm.inject_tool_grant.await_count == 1


@pytest.mark.asyncio
async def test_survives_inject_tool_grant_raising():
    """Grant failures must not stop the loop — each tool is independent."""
    llm = _make_llm_service()
    llm.inject_tool_grant = AsyncMock(
        side_effect=[RuntimeError("boom"), None, None]
    )
    mcp = _make_mcp(llm)

    tools = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    await mcp._auto_grant_mcp_tools("server", tools)

    assert llm.inject_tool_grant.await_count == 3


@pytest.mark.asyncio
async def test_auto_grant_default_is_true_when_config_key_missing():
    """If the config key isn't set, default is to grant (spec at
    RFC-2026-04-11-unified-tool-loading.md:1432)."""
    svc = type("FakeLLMService", (), {})()
    svc._first_turn_complete = True
    svc.config = _FakeConfig({})  # no key set
    svc.inject_tool_grant = AsyncMock()
    mcp = _make_mcp(svc)

    await mcp._auto_grant_mcp_tools("server", [{"name": "foo"}])

    svc.inject_tool_grant.assert_awaited_once_with(
        "foo", reason="mcp server server connected"
    )


def test_turn_completed_latches_first_turn_complete():
    """The setter on LLMService.turn_completed should latch
    _first_turn_complete to True the first time a turn completes."""
    from kollabor.llm.llm_coordinator import LLMService

    svc = LLMService.__new__(LLMService)
    svc._first_turn_complete = False

    class _FakeQP:
        turn_completed = False

    svc._queue_processor = _FakeQP()

    assert svc._first_turn_complete is False
    svc.turn_completed = True
    assert svc._first_turn_complete is True

    # Resetting turn_completed to False (next turn starting) must NOT
    # unlatch — once a turn has ever completed, we stay "first turn
    # complete" for the rest of the process lifetime.
    svc.turn_completed = False
    assert svc._first_turn_complete is True
