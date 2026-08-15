"""Focused tests for the assistant-ui transport adapter.

The endpoint is intentionally tested with a tiny fake ``assistant_stream``
module.  The real dependency is optional at import time (the route imports it
lazily), so these tests exercise the command/event contract without requiring a
network or a running daemon.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import Iterable, Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from kollabor_engine.routes import messages


def _close_current_event_loop() -> None:
    """Close and clear the non-running loop left on the main-thread policy."""
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        return
    if not loop.is_running():
        loop.close()
        policy.set_event_loop(None)


@pytest.fixture(scope="module", autouse=True)
def _release_pytest_asyncio_replacement_loop() -> Iterator[None]:
    """Release the clean loop pytest-asyncio 0.21 leaves after async tests."""
    yield
    _close_current_event_loop()


def test_close_current_event_loop_releases_policy_resource() -> None:
    """Module cleanup closes and clears its pytest-asyncio replacement loop."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    policy.set_event_loop(loop)

    _close_current_event_loop()

    assert loop.is_closed()
    with pytest.raises(RuntimeError, match="no current event loop"):
        policy.get_event_loop()


class _FakeCancellationSignal:
    """Read-only cancellation surface exposed by assistant-stream 0.0.34."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> bool:
        return await self._event.wait()

    def set(self) -> None:
        self._event.set()


class _FakeToolController:
    def __init__(self, name: str, tool_call_id: str) -> None:
        self.name = name
        self.tool_call_id = tool_call_id
        self.args = ""
        self.responses: list[Any] = []

    def append_args_text(self, text: str) -> None:
        self.args += text

    def set_response(self, result: Any) -> None:
        self.responses.append(result)


class _FakeController:
    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = state
        self.cancelled_event = _FakeCancellationSignal()
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: list[_FakeToolController] = []
        self.tool_results: list[tuple[str, Any]] = []
        self.errors: list[str] = []

    def append_text(self, text: str) -> None:
        self.text.append(text)

    def append_reasoning(self, text: str) -> None:
        self.reasoning.append(text)

    async def add_tool_call(self, name: str, tool_call_id: str) -> _FakeToolController:
        tool = _FakeToolController(name, tool_call_id)
        self.tool_calls.append(tool)
        return tool

    def add_tool_result(self, tool_call_id: str, result: Any) -> None:
        self.tool_results.append((tool_call_id, result))

    def add_error(self, message: str) -> None:
        self.errors.append(message)


class _FakeSession:
    session_id = "session-test"
    alive = True

    def __init__(self, events: Iterable[dict[str, Any]]) -> None:
        self._events = asyncio.Queue()
        for event in events:
            self._events.put_nowait(event)
        self.send_message = AsyncMock(return_value={"accepted": True})
        self.resolve_permission = AsyncMock(return_value=True)
        self.state = types.SimpleNamespace(
            cancel_current_request=AsyncMock(),
        )
        self.subscriptions: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        self.subscriptions.append(self._events)
        return self._events

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscriptions:
            self.subscriptions.remove(queue)


class _FakeRegistry:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def get(self, session_id: str) -> _FakeSession | None:
        return self.session if session_id == self.session.session_id else None


def _install_fake_assistant_stream(monkeypatch: pytest.MonkeyPatch):
    """Install a fake assistant_stream package and capture its controller."""
    captured: dict[str, Any] = {}

    class RunController(_FakeController):
        pass

    async def create_run(callback, *, state=None):
        """Mirror assistant-stream 0.0.34's async-generator API."""
        controller = RunController(state)
        captured["callback"] = callback
        captured["controller"] = controller
        await callback(controller)
        # The real generator yields assistant-stream chunks. The route tests
        # inspect controller calls, so no chunks are needed here.
        if False:
            yield None

    async def drain_stream(stream):
        async for _chunk in stream:
            pass

    class AssistantTransportResponse:
        def __init__(self, stream):
            self.stream = stream

    package = types.ModuleType("assistant_stream")
    package.RunController = RunController
    package.create_run = create_run
    serialization = types.ModuleType("assistant_stream.serialization")
    serialization.AssistantTransportResponse = AssistantTransportResponse
    monkeypatch.setitem(sys.modules, "assistant_stream", package)
    monkeypatch.setitem(sys.modules, "assistant_stream.serialization", serialization)
    return captured, AssistantTransportResponse, drain_stream


@pytest.mark.asyncio
async def test_assistant_transport_submits_add_message(monkeypatch):
    session = _FakeSession([{"type": "turn_complete", "input_tokens": 2}])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, response_type, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {"parts": [{"type": "text", "text": "hello"}]},
                }
            ],
            state={"client": "test"},
        ),
    )

    assert isinstance(response, response_type)
    async for _chunk in response.stream:
        pass
    session.send_message.assert_awaited_once_with("hello")
    assert captured["controller"].errors == []
    assert captured["controller"].state["usage"]["inputTokens"] == 2


@pytest.mark.asyncio
async def test_assistant_transport_maps_permission_result(monkeypatch):
    session = _FakeSession([{"type": "turn_complete"}])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-tool-result",
                    "toolCallId": "permission_shell-1",
                    "result": {"decision": "approve", "scope": "session"},
                }
            ]
        ),
    )
    async for _chunk in response.stream:
        pass

    session.resolve_permission.assert_awaited_once_with("shell-1", "approve", "session")


@pytest.mark.asyncio
async def test_assistant_transport_sets_usage_when_client_omits_state(monkeypatch):
    session = _FakeSession([{"type": "turn_complete", "output_tokens": 3}])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "hello"}]
        ),
    )
    async for _chunk in response.stream:
        pass

    assert captured["controller"].state == {
        "usage": {
            "inputTokens": 0,
            "outputTokens": 3,
            "toolCalls": 0,
            "stopReason": "end_turn",
        }
    }


@pytest.mark.asyncio
async def test_assistant_transport_preserves_state_through_real_state_proxy(
    monkeypatch,
):
    """Usage patches must not replace the assistant-ui client's state root."""
    import assistant_stream

    session = _FakeSession(
        [
            {"type": "thinking", "text": "thinking"},
            {"type": "token", "text": "hello"},
            {
                "type": "turn_complete",
                "input_tokens": 2,
                "output_tokens": 3,
            },
        ]
    )
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured: dict[str, Any] = {}
    real_create_run = assistant_stream.create_run

    async def create_run(callback, *, state=None):
        async def capture_controller(controller):
            captured["controller"] = controller
            await callback(controller)

        async for chunk in real_create_run(capture_controller, state=state):
            yield chunk

    monkeypatch.setattr(assistant_stream, "create_run", create_run)

    initial_state = {
        "sessionId": session.session_id,
        "messages": [{"id": "prior", "role": "user", "content": "previous"}],
        "sessions": [{"session_id": session.session_id}],
        "client": "test",
    }
    prior_messages = json.loads(json.dumps(initial_state["messages"]))
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "hello"}],
            state=initial_state,
        ),
    )

    chunks = [chunk async for chunk in response.body_iterator]

    assert chunks
    controller = captured["controller"]
    assert type(controller.state).__name__ == "StateProxy"
    state = controller._state_manager.state_data
    payload = "".join(
        chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks
    )
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in payload.splitlines()
        if line.startswith("data: {")
    ]
    assert [frame["type"] for frame in frames] == [
        "part-start",
        "text-delta",
        "part-finish",
        "part-start",
        "text-delta",
        "update-state",
        "part-finish",
        "message-finish",
    ]
    assert frames[1]["path"] == [0]
    assert frames[1]["textDelta"] == "thinking"
    assert frames[4]["path"] == [1]
    assert frames[4]["textDelta"] == "hello"
    assert frames[5]["operations"] == [
        {"type": "set", "path": ["messages"], "value": state["messages"]},
        {"type": "set", "path": ["usage"], "value": state["usage"]},
    ]
    assert frames[7]["finishReason"] == "stop"
    assert state["sessionId"] == initial_state["sessionId"]
    assert state["messages"] == [
        *prior_messages,
        {"id": "user-session-test-1", "role": "user", "content": "hello"},
        {
            "id": "assistant-session-test-2",
            "role": "assistant",
            "content": [
                {"type": "reasoning", "text": "thinking"},
                {"type": "text", "text": "hello"},
            ],
            "status": {"type": "complete", "reason": "stop"},
        },
    ]
    assert state["sessions"] == initial_state["sessions"]
    assert state["client"] == initial_state["client"]
    assert state["usage"] == {
        "inputTokens": 2,
        "outputTokens": 3,
        "toolCalls": 0,
        "stopReason": "end_turn",
    }


@pytest.mark.asyncio
async def test_assistant_transport_cancellation_cancels_daemon(monkeypatch):
    session = _FakeSession([])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, drain_stream = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "cancel me"}]
        ),
    )
    stream_task = asyncio.create_task(drain_stream(response.stream))
    for _ in range(3):
        await asyncio.sleep(0)
        if "controller" in captured:
            break
    captured["controller"].cancelled_event.set()
    await stream_task

    session.state.cancel_current_request.assert_awaited_once_with()
    assert captured["controller"].errors == []


@pytest.mark.asyncio
async def test_assistant_transport_exposes_and_resolves_permission_prompt(monkeypatch):
    session = _FakeSession(
        [
            {
                "type": "permission_request",
                "tool_id": "shell-2",
                "tool_name": "terminal",
                "tool_type": "terminal",
                "risk_level": "high",
                "risk_reason": "runs a shell command",
                "input": {"command": "echo hi"},
            }
        ]
    )

    async def resolve_permission(tool_id: str, decision: str, scope: str) -> bool:
        session._events.put_nowait({"type": "permission_denied", "tool_id": tool_id})
        session._events.put_nowait({"type": "turn_complete"})
        return True

    session.resolve_permission = AsyncMock(side_effect=resolve_permission)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    first = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "run command"}]
        ),
    )
    async for _chunk in first.stream:
        pass
    first_controller = captured["controller"]
    assert [(tool.name, tool.tool_call_id) for tool in first_controller.tool_calls] == [
        ("request_permission", "permission_shell-2")
    ]
    assert '"tool_id": "shell-2"' in first_controller.tool_calls[0].args

    second = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-tool-result",
                    "toolCallId": "permission_shell-2",
                    "result": {"decision": "deny", "scope": "once"},
                }
            ]
        ),
    )
    async for _chunk in second.stream:
        pass
    session.resolve_permission.assert_awaited_once_with("shell-2", "deny", "once")
    assert captured["controller"].errors == []


@pytest.mark.asyncio
async def test_assistant_transport_maps_daemon_events_to_stream(monkeypatch):
    session = _FakeSession(
        [
            {"type": "token", "text": "answer"},
            {"type": "thinking", "text": "reason"},
            {
                "type": "tool_start",
                "tool_id": "call-1",
                "tool_name": "read_file",
                "input": {"path": "README.md"},
            },
            {
                "type": "tool_result",
                "tool_id": "call-1",
                "success": True,
                "output": "contents",
            },
            {
                "type": "turn_complete",
                "input_tokens": 10,
                "output_tokens": 4,
                "tool_calls": 1,
                "stop_reason": "tool_use",
            },
        ]
    )
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "inspect README"}]
        ),
    )
    async for _chunk in response.stream:
        pass

    controller = captured["controller"]
    assert controller.text == ["answer"]
    assert controller.reasoning == ["reason"]
    assert [(t.name, t.tool_call_id) for t in controller.tool_calls] == [
        ("read_file", "call-1")
    ]
    assert controller.tool_calls[0].responses == [
        {
            "success": True,
            "output": "contents",
            "error": "",
            "metadata": {},
        }
    ]
    assert controller.state["usage"] == {
        "inputTokens": 10,
        "outputTokens": 4,
        "toolCalls": 1,
        "stopReason": "tool_use",
    }
