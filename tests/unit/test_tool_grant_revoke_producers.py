"""Integration test: real LLMService.inject_tool_grant / inject_tool_revoke
actually call push_env and produce events in an EnvQueue.

test_tool_grant_revoke.py uses a FakeCoordinator that re-implements the
method body without the push_env wiring — so it doesn't prove the
producer path works end-to-end in production. This file instantiates a
real LLMService and exercises the producer bridge.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from kollabor_ai.notifications import SYMBOLS, EnvKind, EnvQueue


class _FakeBus:
    """Minimal event bus: just enough for push_env to find the queue."""

    def __init__(self, queue: Optional[EnvQueue]):
        self._services: Dict[str, Any] = {}
        if queue is not None:
            self._services["env_queue"] = queue

    def get_service(self, name: str) -> Any:
        return self._services.get(name)


def _ensure_registry():
    """Re-initialize tool registry if a prior test nuked it."""
    from kollabor_agent.tool_registry import ToolRegistry

    registry = ToolRegistry.get_global()
    if len(registry.list()) >= 10:
        return
    ToolRegistry._instance = None
    new_registry = ToolRegistry()
    ToolRegistry._instance = new_registry
    from kollabor_agent.tool_definitions.file_ops import file_read

    new_registry.register(file_read)


def _build_service(queue: Optional[EnvQueue]):
    """Build a real LLMService with a fake bus carrying the env queue.

    We stub the heavy deps so __init__ doesn't do real API setup — we
    only exercise the inject_tool_grant / inject_tool_revoke methods,
    which don't touch the API or profile manager.
    """
    from kollabor.llm.llm_coordinator import LLMService

    bus = _FakeBus(queue)

    service = LLMService.__new__(LLMService)
    service.event_bus = bus
    service.conversation_logger = None
    service.current_parent_uuid = None

    from kollabor_agent.tool_executor import ToolExecutor

    service.tool_executor = ToolExecutor(
        mcp_integration=None,
        event_bus=None,
        terminal_timeout=30,
        mcp_timeout=30,
    )
    # start with a small scope so grant/revoke has work to do
    service.tool_executor.set_bundle_scope([])

    # inject_tool_grant calls inject_system_message — stub it to a no-op
    # so we don't need conversation_logger or hooks wired up. The bit
    # under test is the push_env call that follows.
    service.inject_system_message = MagicMock(
        side_effect=_async_noop
    )

    return service


async def _async_noop(*args, **kwargs):
    return None


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(scope="module", autouse=True)
def _registry():
    _ensure_registry()


def test_inject_tool_grant_pushes_capability_event_to_env_queue():
    queue = EnvQueue()
    service = _build_service(queue)

    _run(service.inject_tool_grant("file-read", reason="mcp connect"))

    events = queue.drain()
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == EnvKind.TOOL_GRANT
    assert evt.symbol == SYMBOLS["capability"]
    assert evt.message == "+tool:file-read"


def test_inject_tool_revoke_pushes_capability_event_to_env_queue():
    queue = EnvQueue()
    service = _build_service(queue)
    service.tool_executor.set_bundle_scope(["file-read"])

    _run(service.inject_tool_revoke("file-read", reason="plugin shutdown"))

    events = queue.drain()
    assert len(events) == 1
    evt = events[0]
    assert evt.kind == EnvKind.TOOL_REVOKE
    assert evt.symbol == SYMBOLS["capability"]
    assert evt.message == "-tool:file-read"


def test_inject_tool_grant_silent_when_no_env_queue_on_bus():
    """Missing queue must not break the grant flow — producer is fire-and-forget."""
    service = _build_service(queue=None)

    # should not raise
    _run(service.inject_tool_grant("file-read"))


def test_inject_tool_grant_unknown_tool_does_not_push():
    """Unknown tool early-returns BEFORE push_env — no phantom events."""
    queue = EnvQueue()
    service = _build_service(queue)

    _run(service.inject_tool_grant("this-tool-does-not-exist"))

    assert queue.size() == 0


def test_grant_then_revoke_produces_two_distinct_events():
    queue = EnvQueue()
    service = _build_service(queue)

    _run(service.inject_tool_grant("file-read"))
    _run(service.inject_tool_revoke("file-read"))

    events = queue.drain()
    assert len(events) == 2
    assert events[0].kind == EnvKind.TOOL_GRANT
    assert events[0].message == "+tool:file-read"
    assert events[1].kind == EnvKind.TOOL_REVOKE
    assert events[1].message == "-tool:file-read"
