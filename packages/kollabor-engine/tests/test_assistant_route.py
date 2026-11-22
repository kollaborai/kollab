"""Focused tests for the assistant-ui transport adapter.

The endpoint is intentionally tested with a tiny fake ``assistant_stream``
module.  The real dependency is optional at import time (the route imports it
lazily), so these tests exercise the command/event contract without requiring a
network or a running daemon.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
import types
from collections.abc import Iterable, Iterator
from typing import Any
from unittest.mock import ANY, AsyncMock

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
    session.send_message.assert_awaited_once_with("hello", request_id=ANY)
    # Always an id, so a daemon that echoes it can stamp the turn (#551).
    assert re.fullmatch(r"[0-9a-f]{32}", session.send_message.await_args.kwargs["request_id"])
    assert captured["controller"].errors == []
    assert captured["controller"].state["usage"]["inputTokens"] == 2


@pytest.mark.asyncio
async def test_assistant_transport_preserves_image_part_in_submitted_message(
    monkeypatch,
):
    session = _FakeSession([{"type": "turn_complete"}])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "parts": [
                            {"type": "text", "text": "inspect this"},
                            {
                                "type": "image",
                                "image": "data:image/png;base64,iVBORw0KGgo=",
                            },
                        ]
                    },
                }
            ],
            state={"messages": []},
        ),
    )
    async for _chunk in response.stream:
        pass

    session.send_message.assert_awaited_once_with(
        [
            {"type": "text", "text": "inspect this"},
            {"type": "image", "image": "data:image/png;base64,iVBORw0KGgo="},
        ],
        request_id=ANY,
    )
    assert captured["controller"].state["messages"][0]["content"] == (
        "inspect this\n[image1]"
    )


@pytest.mark.asyncio
async def test_assistant_transport_maps_permission_result(monkeypatch):
    permission_id = "owner-shell-1"
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
                    "toolCallId": f"permission_shell-1_{permission_id}",
                    "result": {"decision": "approve", "scope": "session", "permission_id": permission_id},
                }
            ],
            state={"messages": [{"role": "assistant", "content": [{
                "type": "tool-call", "toolCallId": f"permission_shell-1_{permission_id}",
                "toolName": "request_permission", "args": {"tool_id": "shell-1", "permission_id": permission_id},
            }]}]},
        ),
    )
    async for _chunk in response.stream:
        pass

    session.resolve_permission.assert_awaited_once_with("shell-1", "approve", "session", permission_id=permission_id)


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
        "update-state",
        "part-start",
        "text-delta",
        "update-state",
        "part-finish",
        "part-start",
        "text-delta",
        "update-state",
        "part-finish",
        "message-finish",
    ]
    # The admitted send shows at once, before the daemon says anything (#1359).
    admitted = [(op["path"], op["value"]) for op in frames[0]["operations"]]
    assert [path for path, _ in admitted] == [["messages", "1"]]
    assert admitted[0][1]["content"] == "hello"
    assert frames[2]["path"] == [0]
    assert frames[2]["textDelta"] == "thinking"
    # The first streamed event publishes at once; "hello" lands inside the
    # throttle window and rides the final publish.
    mid_turn = [(op["path"], op["value"]) for op in frames[3]["operations"]]
    assert [path for path, _ in mid_turn] == [["messages", "2"]]
    assert mid_turn[0][1]["content"] == [{"type": "reasoning", "text": "thinking"}]
    assert frames[6]["path"] == [1]
    assert frames[6]["textDelta"] == "hello"
    assert frames[7]["operations"] == [
        {"type": "set", "path": ["messages"], "value": state["messages"]},
        {"type": "set", "path": ["synced"], "value": None},
        {"type": "set", "path": ["usage"], "value": state["usage"]},
    ]
    assert frames[9]["finishReason"] == "stop"
    assert state["sessionId"] == initial_state["sessionId"]
    assert state["messages"] == [
        *prior_messages,
        # The send's request id rides on its row (#1359).
        {"id": "user-session-test-1", "role": "user", "content": "hello",
         "metadata": {"custom": {"requestId": ANY}}},
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
async def test_assistant_stream_close_leaves_daemon_turn_running(monkeypatch):
    # Opening another chat unmounts this thread and closes its stream; the
    # agent's turn must keep going (Stop cancels via POST /cancel instead).
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

    session.state.cancel_current_request.assert_not_awaited()
    assert captured["controller"].errors == []


@pytest.mark.asyncio
async def test_assistant_transport_exposes_and_resolves_permission_prompt(monkeypatch):
    permission_id = "owner-shell-2"
    session = _FakeSession(
        [
            {
                "type": "permission_request",
                "request_id": "parent-turn-shell-2",
                "tool_id": "shell-2",
                "permission_id": permission_id,
                "tool_name": "terminal",
                "tool_type": "terminal",
                "risk_level": "high",
                "risk_reason": "runs a shell command",
                "input": {"command": "echo hi"},
            }
        ]
    )

    async def resolve_permission(tool_id: str, decision: str, scope: str, *, permission_id: str) -> bool:
        session._events.put_nowait(
            {
                "type": "permission_denied",
                "tool_id": tool_id,
                "permission_id": permission_id,
                "option_id": "vendor-denied",
                "vendor_saved": True,
                "warning": "Saved by the ACP provider; Kollab cannot undo it.",
            }
        )
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
            commands=[{"type": "add-message", "content": "run command"}],
            state={"messages": []},
        ),
    )
    async for _chunk in first.stream:
        pass
    first_controller = captured["controller"]
    assert [(tool.name, tool.tool_call_id) for tool in first_controller.tool_calls] == [
        ("request_permission", f"permission_shell-2_{permission_id}")
    ]
    assert '"tool_id": "shell-2"' in first_controller.tool_calls[0].args
    assert '"request_id": "parent-turn-shell-2"' in first_controller.tool_calls[0].args

    second = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-tool-result",
                    "toolCallId": f"permission_shell-2_{permission_id}",
                    "result": {"decision": "deny", "scope": "once", "permission_id": permission_id},
                }
            ],
            state=first_controller.state,
        ),
    )
    async for _chunk in second.stream:
        pass
    session.resolve_permission.assert_awaited_once_with("shell-2", "deny", "once", permission_id=permission_id)
    assert captured["controller"].errors == []


async def _answer_parked_prompt(monkeypatch, decision, events):
    """Answer a prompt a Stop left open; the daemon answers with ``events``."""
    permission_id = "owner-shell-3"
    session = _FakeSession([])

    async def resolve_permission(tool_id: str, decision: str, scope: str, *, permission_id: str) -> bool:
        for event in events:
            session._events.put_nowait({**event, "permission_id": permission_id})
        return True

    session.resolve_permission = AsyncMock(side_effect=resolve_permission)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)
    answer = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-tool-result",
                    "toolCallId": f"permission_shell-3_{permission_id}",
                    "result": {"decision": decision, "scope": "once", "permission_id": permission_id},
                }
            ],
            state={"messages": [{"role": "assistant", "content": [{
                "type": "tool-call", "toolCallId": f"permission_shell-3_{permission_id}",
                "toolName": "request_permission", "args": {"tool_id": "shell-3", "permission_id": permission_id},
            }], "status": {"type": "requires-action", "reason": "tool-calls"}}]},
        ),
    )

    async def drain():
        async for _chunk in answer.stream:
            pass

    await asyncio.wait_for(drain(), 2)
    return captured["controller"]


@pytest.mark.asyncio
async def test_a_parked_prompts_deny_ends_its_answer_without_a_turn(monkeypatch):
    # Deny on a card its turn left open runs nothing: no turn_complete follows.
    controller = await _answer_parked_prompt(
        monkeypatch,
        "deny",
        [{"type": "permission_denied", "tool_id": "shell-3", "parked": True}],
    )
    assert controller.errors == []


@pytest.mark.asyncio
async def test_a_parked_prompts_allow_streams_the_turn_it_goes_on_with(monkeypatch):
    controller = await _answer_parked_prompt(
        monkeypatch,
        "approve",
        [
            {"type": "permission_granted", "tool_id": "shell-3", "scope": "once", "parked": True},
            {"type": "token", "text": "Two PRs are open."},
            {"type": "turn_complete"},
        ],
    )
    assert controller.text == ["Two PRs are open."]


@pytest.mark.parametrize(
    ("event", "decision"),
    [
        ({"type": "permission_granted", "scope": "once", "permission_id": "owner-shell-3"}, "approve"),
        ({"type": "permission_denied", "permission_id": "owner-shell-3"}, "deny"),
    ],
)
@pytest.mark.asyncio
async def test_a_message_that_answers_a_shown_card_closes_it(monkeypatch, event, decision):
    """A typed yes or no answers a parked card an earlier run showed: it closes at once."""
    session = _FakeSession(
        [
            {**event, "tool_id": "shell-3", "parked": True},
            {"type": "token", "text": "Done."},
            {"type": "turn_complete"},
        ]
    )
    card = {
        "type": "tool-call",
        "toolCallId": "permission_shell-3_owner-shell-3",
        "toolName": "request_permission",
        "args": {"tool_id": "shell-3", "permission_id": "owner-shell-3", "details": {"parked": True}},
        "argsText": "{}",
    }
    state = {
        "messages": [
            {"id": "u", "role": "user", "content": "make a notes file"},
            {
                "id": "permission-shell-3-0",
                "role": "assistant",
                "content": [card],
                "status": {"type": "requires-action", "reason": "tool-calls"},
            },
        ]
    }
    sets = await _state_ops(
        monkeypatch,
        session,
        [{"type": "add-message", "message": {"parts": [{"type": "text", "text": "yes"}]}}],
        state,
    )

    # The answering message shows at once (#1359); the card closes as soon as
    # its answer lands, before the turn goes on.
    assert sets[0][0] == ["messages", "2"] and sets[0][1]["content"] == "yes"
    path, message = sets[1]
    assert path == ["messages", "1"]
    assert message["content"][0]["result"] == {
        "decision": decision, "scope": "once", "permission_id": "owner-shell-3",
    }
    assert message["content"][0]["isError"] is (decision == "deny")
    # Turn end: the card stays answered above the message that answered it.
    final = sets[-3][1]
    assert [m["role"] for m in final] == ["user", "assistant", "user", "assistant"]
    assert final[1]["content"][0]["result"]["decision"] == decision
    assert final[3]["content"] == [{"type": "text", "text": "Done."}]


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


async def _state_ops(monkeypatch, session, commands, state):
    """Run the route through the real StateProxy; return its update-state ops."""
    import assistant_stream

    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    real_create_run = assistant_stream.create_run

    async def create_run(callback, *, state=None):
        async for chunk in real_create_run(callback, state=state):
            yield chunk

    monkeypatch.setattr(assistant_stream, "create_run", create_run)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=commands, state=state),
    )
    chunks = [chunk async for chunk in response.body_iterator]
    payload = "".join(
        chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks
    )
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in payload.splitlines()
        if line.startswith("data: {")
    ]
    return [
        (op["path"], op["value"])
        for frame in frames
        if frame["type"] == "update-state"
        for op in frame["operations"]
        if op["type"] == "set"
    ]


def test_tool_completed_ms_prefers_measured_run_time():
    assert messages._tool_completed_ms(1000, {"execution_time": 1.25}) == 2250
    # A missing or zero run time must not render as "0ms"; never before the start.
    for event in ({}, {"execution_time": 0}, {"execution_time": None}):
        assert messages._tool_completed_ms(10**15, event) == 10**15


@pytest.mark.asyncio
async def test_assistant_transport_publishes_running_tool_with_timing(monkeypatch):
    """A running tool part reaches the client mid-turn, then finishes timed."""
    session = _FakeSession(
        [
            {
                "type": "tool_start",
                "tool_id": "call-1",
                "tool_name": "terminal: ls",
                "input": {"command": "ls"},
            },
            {
                "type": "tool_result",
                "tool_id": "call-1",
                "success": True,
                "output": "a",
                "execution_time": 1.25,
            },
            {"type": "turn_complete"},
        ]
    )
    prior = [{"id": "prior", "role": "user", "content": "previous"}]
    sets = await _state_ops(
        monkeypatch,
        session,
        [{"type": "add-message", "content": "run ls"}],
        {"messages": prior},
    )

    assert [path for path, _ in sets] == [
        ["messages", "1"],
        ["messages", "2"],
        ["messages", "2"],
        ["messages"],
        ["synced"],
        ["usage"],
    ]
    # Admission: the user message, before the daemon says anything (#1359).
    # tool_start: an in-flight assistant message, no result and no explicit
    # status (the client shows it running while the run is open).
    assert sets[0][1]["role"] == "user"
    running = sets[1][1]
    part = running["content"][0]
    assert "status" not in running and "result" not in part
    assert isinstance(part["timing"]["startedAt"], int)
    assert "completedAt" not in part["timing"]
    # tool_result: same message index, now with its result and measured run time.
    done = sets[2][1]["content"][0]
    assert done["result"]["output"] == "a" and "status" not in sets[2][1]
    assert done["timing"]["completedAt"] - done["timing"]["startedAt"] == 1250
    # Turn end: the authoritative list, nothing duplicated, timing kept.
    final = sets[3][1]
    assert [m["role"] for m in final] == ["user", "user", "assistant"]
    assert final[2]["status"] == {"type": "complete", "reason": "stop"}
    assert final[2]["content"][0]["timing"] == done["timing"]


@pytest.mark.asyncio
async def test_assistant_transport_progress_extends_continued_assistant_message(
    monkeypatch,
):
    """A run that answers a permission prompt updates that message in place."""
    session = _FakeSession(
        [
            {
                "type": "tool_start",
                "tool_id": "call-2",
                "tool_name": "terminal: pwd",
                "input": {"command": "pwd"},
            },
            {"type": "turn_complete"},
        ]
    )
    earlier = {"type": "tool-call", "toolCallId": "call-1", "toolName": "terminal"}
    permission_part = {
        "type": "tool-call", "toolCallId": "permission_call-1_owner-call-1",
        "toolName": "request_permission",
        "args": {"tool_id": "call-1", "permission_id": "owner-call-1"},
    }
    state = {
        "messages": [
            {"id": "u", "role": "user", "content": "go"},
            {
                "id": "a",
                "role": "assistant",
                "content": [earlier, permission_part],
                "status": {"type": "requires-action", "reason": "tool-calls"},
            },
        ]
    }
    sets = await _state_ops(
        monkeypatch,
        session,
        [
            {
                "type": "add-tool-result",
                "toolCallId": "permission_call-1_owner-call-1",
                "result": {"decision": "approve", "permission_id": "owner-call-1"},
            }
        ],
        state,
    )

    path, message = sets[0]
    assert path == ["messages", "1"]
    assert [part["toolCallId"] for part in message["content"]] == [
        "call-1", "permission_call-1_owner-call-1", "call-2",
    ]
    assert "status" not in message
    # The final publish keeps one assistant message with an explicit status.
    assert [m["role"] for m in sets[-3][1]] == ["user", "assistant"]
    assert sets[-3][1][1]["status"] == {"type": "complete", "reason": "stop"}


@pytest.mark.asyncio
async def test_assistant_transport_finishes_gated_tool_in_place(monkeypatch):
    """A tool gated by a prompt gets its result and duration in the answering run."""
    permission_id = "owner-call-1"
    session = _FakeSession(
        [
            {"type": "permission_granted", "tool_id": "call-1", "permission_id": permission_id, "scope": "once"},
            {
                "type": "tool_result",
                "tool_id": "call-1",
                "success": True,
                "output": "a",
                "execution_time": 2.0,
            },
            {"type": "token", "text": "done"},
            {"type": "turn_complete"},
        ]
    )
    state = {
        "messages": [
            {"id": "u", "role": "user", "content": "go"},
            {
                "id": "a",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool-call",
                        "toolCallId": "call-1",
                        "toolName": "terminal",
                        "args": {"command": "ls"},
                        "argsText": '{"command": "ls"}',
                        "timing": {"startedAt": 1000},
                    },
                    {
                        "type": "tool-call",
                        "toolCallId": f"permission_call-1_{permission_id}",
                        "toolName": "request_permission",
                        "args": {"tool_id": "call-1", "permission_id": permission_id},
                        "argsText": json.dumps({"tool_id": "call-1", "permission_id": permission_id}),
                    },
                ],
                "status": {"type": "requires-action", "reason": "tool-calls"},
            },
        ]
    }
    sets = await _state_ops(
        monkeypatch,
        session,
        [
            {
                "type": "add-tool-result",
                "toolCallId": f"permission_call-1_{permission_id}",
                "result": {"decision": "approve", "scope": "once", "permission_id": permission_id},
            }
        ],
        state,
    )

    # permission_granted: the prompt card is answered and the message runs again.
    path, message = sets[0]
    assert path == ["messages", "1"] and "status" not in message
    assert message["content"][1]["result"] == {"decision": "approve", "scope": "once", "permission_id": permission_id}
    # tool_result: the same row finishes with its output and a measured duration.
    path, message = sets[1]
    row = message["content"][0]
    assert path == ["messages", "1"] and row["toolCallId"] == "call-1"
    assert row["result"]["output"] == "a" and row["isError"] is False
    assert row["timing"] == {"startedAt": 1000, "completedAt": 3000}
    # Turn end: one assistant message, each tool call once, the row still finished.
    final = sets[-3][1]
    assert [m["role"] for m in final] == ["user", "assistant"]
    tools = [p for p in final[1]["content"] if p["type"] == "tool-call"]
    assert [p["toolCallId"] for p in tools] == ["call-1", "permission_call-1_owner-call-1"]
    assert tools[0]["timing"] == {"startedAt": 1000, "completedAt": 3000}
    # The continued run's text lands after the finished rows, not above them.
    assert final[1]["content"][-1] == {"type": "text", "text": "done"}
    assert final[1]["status"] == {"type": "complete", "reason": "stop"}


def _tokens(*texts: str) -> list[dict[str, Any]]:
    return [{"type": "token", "text": text} for text in texts]


@pytest.mark.asyncio
async def test_assistant_transport_publishes_streamed_text_as_it_grows(monkeypatch):
    """With no throttle every token publishes the whole text so far."""
    monkeypatch.setattr(messages, "STREAM_PUBLISH_INTERVAL", 0)
    session = _FakeSession([*_tokens("a", "b", "c"), {"type": "turn_complete"}])
    sets = await _state_ops(
        monkeypatch,
        session,
        [{"type": "add-message", "content": "count"}],
        {"messages": []},
    )

    assert [path for path, _ in sets] == [
        ["messages", "0"],
        ["messages", "1"],
        ["messages", "1"],
        ["messages", "1"],
        ["messages"],
        ["synced"],
        ["usage"],
    ]
    growing = [value["content"] for _, value in sets[1:4]]
    assert growing == [[{"type": "text", "text": text}] for text in ("a", "ab", "abc")]
    # Mid-turn publishes leave the status to the client; only the end sets it.
    assert all("status" not in value for _, value in sets[1:4])
    assert sets[4][1][1]["status"] == {"type": "complete", "reason": "stop"}


@pytest.mark.asyncio
async def test_assistant_transport_throttles_streamed_text_publishes(monkeypatch):
    """Rapid tokens publish once mid-turn; the final publish carries the tail."""
    session = _FakeSession([*_tokens("a", "b", "c"), {"type": "turn_complete"}])
    sets = await _state_ops(
        monkeypatch,
        session,
        [{"type": "add-message", "content": "count"}],
        {"messages": []},
    )

    assert [path for path, _ in sets] == [
        ["messages", "0"],
        ["messages", "1"],
        ["messages"],
        ["synced"],
        ["usage"],
    ]
    # The admitted send's publish does not start the throttle: "a" still
    # publishes at once.
    assert sets[1][1]["content"] == [{"type": "text", "text": "a"}]
    assert sets[2][1][1]["content"] == [{"type": "text", "text": "abc"}]


@pytest.mark.asyncio
async def test_assistant_transport_flushes_held_text_when_the_stream_goes_quiet(
    monkeypatch,
):
    """Text the throttle held publishes once the daemon goes quiet, not only at
    turn_complete (the last words of a reply used to lag by seconds)."""
    monkeypatch.setattr(messages, "STREAM_PUBLISH_INTERVAL", 0.05)
    session = _FakeSession(_tokens("a", "b", "c"))
    asyncio.get_running_loop().call_later(
        0.3, session._events.put_nowait, {"type": "turn_complete"}
    )
    sets = await _state_ops(
        monkeypatch,
        session,
        [{"type": "add-message", "content": "count"}],
        {"messages": []},
    )

    assert [path for path, _ in sets] == [
        ["messages", "0"],
        ["messages", "1"],
        ["messages", "1"],
        ["messages"],
        ["synced"],
        ["usage"],
    ]
    assert sets[1][1]["content"] == [{"type": "text", "text": "a"}]
    assert sets[2][1]["content"] == [{"type": "text", "text": "abc"}]


@pytest.mark.asyncio
async def test_assistant_transport_orders_text_and_tools_chronologically(monkeypatch):
    """A text -> tool -> text turn keeps each text on its own side of the row."""
    session = _FakeSession(
        [
            *_tokens("Checking. ", "now"),
            {
                "type": "tool_start",
                "tool_id": "call-1",
                "tool_name": "terminal: ls",
                "input": {"command": "ls"},
            },
            {
                "type": "tool_result",
                "tool_id": "call-1",
                "success": True,
                "output": "a",
            },
            *_tokens("All ", "done"),
            {"type": "turn_complete"},
        ]
    )
    sets = await _state_ops(
        monkeypatch,
        session,
        [{"type": "add-message", "content": "run ls"}],
        {"messages": []},
    )

    def shape(content):
        return [part.get("text") or part["toolCallId"] for part in content]

    # tool_start flushes the throttled tail ahead of the row it starts.
    assert shape(sets[2][1]["content"]) == ["Checking. now", "call-1"]
    final = sets[-3][1][-1]
    assert [part["type"] for part in final["content"]] == [
        "text",
        "tool-call",
        "text",
    ]
    assert shape(final["content"]) == ["Checking. now", "call-1", "All done"]
    assert final["content"][1]["result"]["output"] == "a"


@pytest.mark.asyncio
async def test_assistant_transport_continued_run_keeps_text_order(monkeypatch):
    """Answering a prompt finishes its row in place; later text and tools follow it."""
    permission_id = "owner-call-1"
    session = _FakeSession(
        [
            {"type": "permission_granted", "tool_id": "call-1", "permission_id": permission_id, "scope": "once"},
            {
                "type": "tool_result",
                "tool_id": "call-1",
                "success": True,
                "output": "a",
            },
            *_tokens("x"),
            {
                "type": "tool_start",
                "tool_id": "call-2",
                "tool_name": "terminal: pwd",
                "input": {"command": "pwd"},
            },
            {
                "type": "tool_result",
                "tool_id": "call-2",
                "success": True,
                "output": "/",
            },
            *_tokens("y"),
            {"type": "turn_complete"},
        ]
    )
    state = {
        "messages": [
            {"id": "u", "role": "user", "content": "go"},
            {
                "id": "a",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "before"},
                    {
                        "type": "tool-call",
                        "toolCallId": "call-1",
                        "toolName": "terminal",
                    },
                    {
                        "type": "tool-call",
                        "toolCallId": f"permission_call-1_{permission_id}",
                        "toolName": "request_permission",
                        "args": {"tool_id": "call-1", "permission_id": permission_id},
                    },
                ],
                "status": {"type": "requires-action", "reason": "tool-calls"},
            },
        ]
    }
    sets = await _state_ops(
        monkeypatch,
        session,
        [
            {
                "type": "add-tool-result",
                "toolCallId": f"permission_call-1_{permission_id}",
                "result": {"decision": "approve", "permission_id": permission_id},
            }
        ],
        state,
    )

    final = sets[-3][1]
    assert [m["role"] for m in final] == ["user", "assistant"]
    assert [part.get("text") or part["toolCallId"] for part in final[1]["content"]] == [
        "before",
        "call-1",
        f"permission_call-1_{permission_id}",
        "x",
        "call-2",
        "y",
    ]
    assert final[1]["content"][1]["result"]["output"] == "a"


@pytest.mark.asyncio
async def test_assistant_transport_ends_a_failed_turn_with_its_error(monkeypatch):
    """The daemon's error becomes the reply's error status, which the page shows
    in its error box, instead of a turn that ends blank."""
    session = _FakeSession(
        [
            {"type": "error", "message": "LLM provider not available"},
            {"type": "turn_complete", "stop_reason": "end_turn"},
        ]
    )
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "hello"}],
            state={"messages": []},
        ),
    )
    async for _chunk in response.stream:
        pass

    controller = captured["controller"]
    assert controller.errors == []
    user, reply = controller.state["messages"]
    assert user["content"] == "hello"
    assert reply["status"] == {
        "type": "incomplete",
        "reason": "error",
        "error": "LLM provider not available",
    }
    assert controller.state["usage"]["stopReason"] == "error"


@pytest.mark.asyncio
async def test_assistant_transport_streams_its_own_reply_behind_a_running_turn(monkeypatch):
    """#551: a message sent while another turn ran ended on that turn's
    untagged completion, and its own reply never streamed."""
    session = _FakeSession([])

    def admit(content, request_id=None):
        for event in (
            {"type": "turn_complete", "stop_reason": "end_turn"},  # the turn already running
            {"type": "token", "text": "B-OK", "request_ids": [request_id]},
            {"type": "turn_complete", "stop_reason": "end_turn", "request_ids": [request_id]},
        ):
            session._events.put_nowait(event)
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=admit)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "hello"}],
            state={"messages": []},
        ),
    )
    async for _chunk in response.stream:
        pass

    _user, reply = captured["controller"].state["messages"]
    assert "B-OK" in json.dumps(reply)


@pytest.mark.asyncio
async def test_assistant_transport_shows_none_of_a_running_turn_typed_in_the_terminal(monkeypatch):
    """Chalcedony on #554 round 5: a turn typed in the terminal names no
    request, and its text and permission prompt became this message's."""
    session = _FakeSession([])

    def admit(content, request_id=None):
        for event in (
            {"type": "token", "text": "THEIRS"},
            {"type": "permission_request", "tool_id": "their-tool", "permission_id": "owner-their-tool", "tool_name": "terminal",
             "input": {"command": "echo hi"}},
            {"type": "turn_complete", "stop_reason": "end_turn"},
            {"type": "token", "text": "B-OK", "request_ids": [request_id]},
            {"type": "turn_complete", "stop_reason": "end_turn", "request_ids": [request_id]},
        ):
            session._events.put_nowait(event)
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=admit)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "hello"}],
            state={"messages": []},
        ),
    )
    async for _chunk in response.stream:
        pass

    controller = captured["controller"]
    assert controller.text == ["B-OK"] and controller.tool_calls == []


@pytest.mark.asyncio
async def test_assistant_transport_fails_the_run_when_no_state_carries_the_error(
    monkeypatch,
):
    session = _FakeSession(
        [{"type": "error", "message": "boom"}, {"type": "turn_complete"}]
    )
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

    assert captured["controller"].errors == ["boom"]


@pytest.mark.asyncio
async def test_assistant_transport_says_how_much_history_the_messages_show(
    monkeypatch,
):
    """The page appends later history past this point instead of swapping in
    reloaded copies of the turn it just ran (assistant-ui would keep both)."""
    session = _FakeSession([*_tokens("hi"), {"type": "turn_complete"}])
    session.refresh_history = AsyncMock(return_value=[
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "hello"}],
            state={"messages": [{"id": "history-1", "role": "user", "content": "a"}]},
        ),
    )
    async for _chunk in response.stream:
        pass

    state = captured["controller"].state
    assert len(state["messages"]) == 3
    assert state["synced"] == {"history": 4, "messages": 3}


@pytest.mark.asyncio
async def test_stale_unrelated_history_never_claims_sync(monkeypatch):
    """A refresh that missed this turn must not be reconciled by role alone.

    Garnet's round-3 probe: client state with only the provisional rows and
    a stale history holding an unrelated [user, assistant] pair. Matching
    by role sequence alone rewrote the new rows to history-0/history-1,
    colliding with canonical rows the page would later load.
    """
    session = _FakeSession([*_tokens("brand new reply"), {"type": "turn_complete"}])
    session.refresh_history = AsyncMock(return_value=[
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "real question"}],
            state={"messages": []},
        ),
    )
    async for _chunk in response.stream:
        pass

    state = captured["controller"].state
    assert state["synced"] is None
    ids = [m["id"] for m in state["messages"]]
    assert not any(i.startswith("history-") for i in ids), ids


@pytest.mark.asyncio
async def test_failed_history_refresh_publishes_no_watermark(monkeypatch):
    """A refresh failure must not leave the seeded sync point standing (#666).

    The turn's transient rows publish before the watermark advances, so a
    stale watermark replays the turn as history next to those rows and the
    live window draws it twice until a manual reload. ``None`` tells the page
    its whole sync point is gone, so its next refresh reloads whole."""
    session = _FakeSession([*_tokens("hi"), {"type": "turn_complete"}])
    session.refresh_history = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "hello"}],
            state={"messages": [{"id": "history-1", "role": "user", "content": "a"}],
                   "synced": {"history": 1, "messages": 1}},
        ),
    )
    async for _chunk in response.stream:
        pass

    state = captured["controller"].state
    assert len(state["messages"]) == 3
    assert state["synced"] is None


@pytest.mark.asyncio
async def test_partial_client_state_with_stale_rows_never_claims_sync(monkeypatch):
    """Partial prior state (one shown row) plus stale history: the unshown
    stale assistant row must not be paired with this turn's fresh reply."""
    session = _FakeSession([*_tokens("brand new reply"), {"type": "turn_complete"}])
    session.refresh_history = AsyncMock(return_value=[
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "real question"}],
            state={"messages": [
                {"id": "history-0", "role": "user", "content": "old question"},
            ]},
        ),
    )
    async for _chunk in response.stream:
        pass

    state = captured["controller"].state
    assert state["synced"] is None
    ids = [m["id"] for m in state["messages"]]
    # The pre-existing row keeps its id; this turn's rows keep provisional ids.
    assert ids[0] == "history-0"
    assert not any(i.startswith("history-") for i in ids[1:]), ids


@pytest.mark.asyncio
async def test_align_requires_content_identity_for_paired_rows(monkeypatch):
    """Same-role, different-content stored rows must not pair: content, not
    role order, is the evidence a row was streamed by this turn."""
    session = _FakeSession([*_tokens("the real answer"), {"type": "turn_complete"}])
    session.refresh_history = AsyncMock(return_value=[
        {"role": "user", "content": "real question"},
        {"role": "assistant", "content": "different answer entirely"},
    ])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "real question"}],
            state={"messages": []},
        ),
    )
    async for _chunk in response.stream:
        pass

    assert captured["controller"].state["synced"] is None


def test_align_helper_preserves_streamed_ids_for_matching_content():
    """Exact-content alignment records history positions without replacing row ids."""
    fresh = [
        {"id": "user-x", "role": "user", "content": "q"},
        {
            "id": "assistant-x",
            "role": "assistant",
            "content": [{"type": "text", "text": "final answer"}],
        },
    ]
    good = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "final answer"},
    ]
    assert messages._assistant_align_streamed_ids(fresh, good) is True
    assert [m["id"] for m in fresh] == ["user-x", "assistant-x"]
    assert [m["metadata"]["custom"]["historySourceIndex"] for m in fresh] == [0, 1]

    stale = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a different old answer"},
    ]
    other = [
        {"id": "user-y", "role": "user", "content": "q"},
        {
            "id": "assistant-y",
            "role": "assistant",
            "content": [{"type": "text", "text": "final answer"}],
        },
    ]
    assert messages._assistant_align_streamed_ids(other, stale) is False
    assert [m["id"] for m in other] == ["user-y", "assistant-y"]


def test_a_sent_message_takes_the_row_id_the_page_names():
    """A message the page already shows keeps its row when it is sent (#1102).

    A queued message is drawn before it is sent, and rows can join the chat
    while it waits. Numbered by the rows at hand it would get a new id, and
    the chat takes a row whose id changed down and builds it again.
    """
    state = [{"id": "history-0", "role": "user", "content": "q"}, {"id": "user-s1-9", "role": "user", "content": "x"}]

    def rows(row_ids):
        built = messages._assistant_update_message_state(
            [dict(row) for row in state], "s1", ["first", "second"], [], None, None, ["r1", "r2"], row_ids
        )
        return [row["id"] for row in built[2:4]]

    assert rows(["user-s1-1", "user-s1-7"]) == ["user-s1-1", "user-s1-7"]
    # No name, a row the chat already has, another session's row, another kind
    # of row and a name used twice: the message is numbered by the rows at hand.
    assert rows(None) == ["user-s1-2", "user-s1-3"]
    assert rows(["user-s1-9", "user-s2-4"]) == ["user-s1-2", "user-s1-3"]
    assert rows(["history-0", "assistant-s1-4"]) == ["user-s1-2", "user-s1-3"]
    assert rows(["user-s1-5", "user-s1-5"]) == ["user-s1-5", "user-s1-3"]
    assert rows(["user-s1-5"]) == ["user-s1-5", "user-s1-3"]


def test_align_helper_keeps_a_row_shown_for_a_saved_row_of_tool_calls_alone():
    """A reply that starts with a tool call keeps its row in the chat (#1359).

    The page shows the reply's first saved row, tool calls with no words, under
    the id it drew the reply with. That row has no words to compare, so its
    call ids are its identity: the next turn still lines up with history.
    """
    def shown() -> list[dict]:
        return [
            {"id": "user-s-0", "role": "user", "content": "q1",
             "metadata": {"custom": {"historySourceId": "history-0", "historySourceIndex": 0}}},
            {"id": "assistant-s-1", "role": "assistant",
             "content": [{"type": "tool-call", "toolCallId": "c1", "toolName": "terminal", "result": "ok"}],
             "metadata": {"custom": {"historySourceId": "history-1", "historySourceIndex": 1}}},
            {"id": "history-3", "role": "assistant", "content": [{"type": "text", "text": "first answer"}]},
            {"id": "user-s-4", "role": "user", "content": "q2"},
            {"id": "assistant-s-5", "role": "assistant", "content": [{"type": "text", "text": "second answer"}]},
        ]

    stored = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"id": "c1"}]}},
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "second answer"},
    ]
    rows = shown()
    assert messages._assistant_align_streamed_ids(rows, stored) is True
    assert [m["id"] for m in rows] == ["user-s-0", "assistant-s-1", "history-3", "user-s-4", "assistant-s-5"]
    assert [m["metadata"]["custom"]["historySourceIndex"] for m in rows[3:]] == [4, 5]

    # The position now holds other calls: the row names an obsolete place.
    moved = [dict(row) for row in stored]
    moved[1] = {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"id": "c9"}]}}
    assert messages._assistant_align_streamed_ids(shown(), moved) is False


def test_archive_and_synthetic_alias_cannot_claim_the_new_turns_history_row():
    previous = [
        {
            "id": "history-1", "role": "assistant", "content": "old reply",
            "metadata": {"custom": {"compactionArchiveOrder": 0}},
        },
        {
            "id": "history-view-history-1-error-1", "role": "assistant", "content": "same",
            "metadata": {"custom": {"historySourceId": "history-1-error", "historySourceIndex": 1}},
        },
    ]
    turn = [
        {"id": "user-new", "role": "user", "content": "question"},
        {"id": "assistant-new", "role": "assistant", "content": "same"},
    ]
    stored = [{"role": row["role"], "content": row["content"]} for row in turn]
    assert messages._assistant_align_streamed_ids([*previous, *turn], stored)
    assert [row["metadata"]["custom"]["historySourceIndex"] for row in turn] == [0, 1]


def test_align_helper_tolerates_split_tool_round_with_call_identity():
    """Stored [user, assistant(calls), tool, assistant(final)] still aligns:
    the calls row is proven rendered inside the streamed row by call id."""
    streamed = [
        {"id": "user-x", "role": "user", "content": "run it"},
        {
            "id": "assistant-x",
            "role": "assistant",
            "content": [
                {"type": "tool-call", "toolCallId": "call-1"},
                {"type": "text", "text": "done"},
            ],
        },
    ]
    stored = [
        {"role": "system", "content": "init"},
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": "",
            "metadata": {"tool_calls": [{"id": "call-1", "name": "f"}]},
        },
        {"role": "tool", "content": "out"},
        {"role": "assistant", "content": "done"},
    ]
    assert messages._assistant_align_streamed_ids(streamed, stored) is True
    assert [m["id"] for m in streamed] == ["user-x", "assistant-x"]
    assert [m["metadata"]["custom"]["historySourceIndex"] for m in streamed] == [1, 4]


def test_align_helper_keeps_prior_indexes_when_a_new_turn_arrives():
    """Already aligned rows stay stable while a later streamed turn is aligned."""
    streamed = [
        {"id": "user-first", "role": "user", "content": "first"},
        {"id": "assistant-first", "role": "assistant", "content": "first answer"},
    ]
    first_history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first answer"},
    ]
    assert messages._assistant_align_streamed_ids(streamed, first_history) is True

    streamed.extend([
        {"id": "user-second", "role": "user", "content": "second"},
        {"id": "assistant-second", "role": "assistant", "content": "second answer"},
    ])
    full_history = [
        *first_history,
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "second answer"},
    ]
    assert messages._assistant_align_streamed_ids(streamed, full_history) is True
    assert [row["id"] for row in streamed] == [
        "user-first", "assistant-first", "user-second", "assistant-second",
    ]
    assert [row["metadata"]["custom"]["historySourceIndex"] for row in streamed] == [0, 1, 2, 3]


def test_align_helper_rejects_calls_row_without_call_identity():
    """An unexplained calls row (ids absent from the streamed row) is not
    skippable: the walk must refuse instead of reaching past it."""
    streamed = [
        {"id": "user-x", "role": "user", "content": "run it"},
        {
            "id": "assistant-x",
            "role": "assistant",
            "content": [
                {"type": "tool-call", "toolCallId": "call-1"},
                {"type": "text", "text": "done"},
            ],
        },
    ]
    stored = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": "",
            "metadata": {"tool_calls": [{"id": "call-9", "name": "f"}]},
        },
        {"role": "tool", "content": "out"},
        {"role": "assistant", "content": "done"},
    ]
    assert messages._assistant_align_streamed_ids(streamed, stored) is False
    assert [m["id"] for m in streamed] == ["user-x", "assistant-x"]


@pytest.mark.asyncio
async def test_new_admitted_turn_clears_previous_cancel_error(monkeypatch):
    session = _FakeSession([*_tokens("new answer"), {"type": "turn_complete"}])
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)
    cancelled = {"id": "old", "role": "assistant", "content": "interrupted",
                 "status": {"type": "incomplete", "reason": "cancelled"}}
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "try again"}],
            state={"messages": [cancelled], "error": "Run cancelled"},
        ),
    )
    async for _chunk in response.stream:
        pass
    state = captured["controller"].state
    assert state["error"] == ""
    assert state["messages"][0] == cancelled
    assert state["messages"][-1]["status"] == {"type": "complete", "reason": "stop"}
    assert captured["controller"].errors == []


@pytest.mark.asyncio
async def test_admitted_send_reaches_the_page_before_the_daemon_speaks(monkeypatch):
    """The page drops its pending copy of a send at the first state it receives.

    A daemon busy with another turn stays quiet long after it admits the send,
    and clearing the last turn's error is already a state: without the send's
    row in it, the message is gone from the chat until the daemon speaks (#1359).
    """
    session = _FakeSession([])
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain_stream = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{
                "type": "add-message",
                "message": {"id": "send-1", "parts": [{"type": "text", "text": "hello"}]},
            }],
            state={
                "messages": [{"id": "history-0", "role": "user", "content": "earlier"}],
                "error": "Run cancelled",
            },
        ),
    )
    run = asyncio.create_task(drain_stream(response.stream))
    try:
        for _ in range(50):
            await asyncio.sleep(0)
            if session.send_message.await_count:
                break
        await asyncio.sleep(0.01)
        state = captured["controller"].state
        assert state["error"] == ""
        # The reply's row comes with its first output: until then the page
        # keeps the placeholder it already shows.
        assert [row["id"] for row in state["messages"]] == ["history-0", "user-session-test-1"]
        assert state["messages"][1]["content"] == "hello"
        assert state["messages"][1]["metadata"] == {"custom": {"requestId": "send-1"}}
    finally:
        session._events.put_nowait({"type": "turn_complete"})
        await run
    final = captured["controller"].state["messages"]
    assert [row["id"] for row in final] == ["history-0", "user-session-test-1", "assistant-session-test-2"]
    assert final[-1]["status"] == {"type": "complete", "reason": "stop"}


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_first_observed_harness_completion_has_no_invented_duration(status):
    part = messages._assistant_harness_tool_part({
        "sessionUpdate": "tool_call_update", "toolCallId": "reconnected-card",
        "title": "Saved result", "kind": "edit", "status": status,
        "rawOutput": 0,
    })
    assert "timing" not in part
    assert part["result"]["output"] == 0
    assert part["result"]["metadata"]["acp"]["status"] == status
    assert part["isPreliminary"] is False


def test_harness_completion_keeps_observed_start_time(monkeypatch):
    times = iter([1000, 2500])
    monkeypatch.setattr(messages, "_now_ms", lambda: next(times))
    part = messages._assistant_harness_tool_part({
        "sessionUpdate": "tool_call", "toolCallId": "observed-card", "status": "pending",
    })
    messages._assistant_harness_tool_part({
        "sessionUpdate": "tool_call_update", "toolCallId": "observed-card", "status": "completed",
    }, part)
    assert part["timing"] == {"startedAt": 1000, "completedAt": 2500}


def test_permission_result_preserves_vendor_metadata_for_grant_and_denial():
    granted = messages._permission_result_from_event(
        {
            "type": "permission_granted",
            "option_id": "allow-always",
            "vendor_saved": True,
            "warning": "Saved by [harness]; Kollab cannot undo it.",
        }
    )
    denied = messages._permission_result_from_event(
        {
            "type": "permission_denied",
            "option_id": "reject-always",
            "vendor_saved": True,
            "warning": "Saved by [harness]; Kollab cannot undo it.",
        }
    )
    native = messages._permission_result_from_event(
        {"type": "permission_granted"}
    )

    assert granted == {
        "decision": "approve",
        "option_id": "allow-always",
        "vendor_saved": True,
        "warning": "Saved by [harness]; Kollab cannot undo it.",
    }
    assert denied == {
        "decision": "deny",
        "option_id": "reject-always",
        "vendor_saved": True,
        "warning": "Saved by [harness]; Kollab cannot undo it.",
    }
    assert native == {"decision": "approve", "scope": "once"}


@pytest.mark.asyncio
async def test_private_goal_assistant_stream_ignores_foreign_turn(monkeypatch):
    session = _FakeSession([{"type": "token", "text": "foreign turn"}, {"type": "turn_complete"}])
    session.send_message = AsyncMock(return_value={"accepted": True, "goal_reply_id": "a" * 32})
    session.poll_goal_reply = AsyncMock(side_effect=[{"status": "pending"}, {"status": "complete", "success": True, "conversation_uid": "a", "message": "own goal result"}])
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{"type": "add-message", "content": "/goal show"}], state={"messages": [], "conversationUid": "a"}),
    )
    async for _chunk in response.stream:
        pass
    controller = captured["controller"]
    assert controller.text == ["own goal result"]
    assert "foreign turn" not in repr(controller.state)
    assert not session.subscriptions
    session.send_message.assert_awaited_once_with(
        "/goal show", request_id=ANY, private_goal_reply=True, goal_conversation_uid="a",
    )


@pytest.mark.asyncio
async def test_goal_stream_does_not_claim_concurrent_history_as_rendered(monkeypatch):
    session = _FakeSession([])
    session.send_message = AsyncMock(return_value={"accepted": True, "goal_reply_id": "a" * 32})
    session.poll_goal_reply = AsyncMock(return_value={
        "status": "complete", "success": True, "conversation_uid": "a",
        "message": "own goal result", "history_recorded": True,
    })
    session.refresh_history = AsyncMock(return_value=[{}, {}, {}, {}])
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{"type": "add-message", "content": "/goal show"}],
                                  state={"messages": [], "conversationUid": "a", "synced": {"history": 0, "messages": 0}}),
    )
    async for _chunk in response.stream:
        pass
    assert captured["controller"].state["synced"] is None
    assert captured["controller"].state["goalHistoryReloadRequired"] is True
    session.refresh_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_goal_history_reload_signal_is_sent_through_real_state_proxy(monkeypatch):
    session = _FakeSession([])
    session.send_message = AsyncMock(return_value={"accepted": True, "goal_reply_id": "a" * 32})
    session.poll_goal_reply = AsyncMock(return_value={
        "status": "complete", "success": True, "conversation_uid": "a",
        "message": "own goal result", "history_recorded": True,
    })
    session.refresh_history = AsyncMock(return_value=[{}, {}, {}, {}])
    ops = await _state_ops(monkeypatch, session,
        [{"type": "add-message", "content": "/goal show"}],
        {"messages": [], "conversationUid": "a", "synced": {"history": 0, "messages": 0}})
    assert (["synced"], None) in ops
    assert (["goalHistoryReloadRequired"], True) in ops
    session.refresh_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_goal_legacy_stream_ignores_foreign_turn(monkeypatch):
    session = _FakeSession([{"type": "token", "text": "foreign turn"}, {"type": "turn_complete"}])
    session.send_message = AsyncMock(return_value={"accepted": True, "goal_reply_id": "b" * 32})
    session.poll_goal_reply = AsyncMock(return_value={"status": "complete", "success": True, "conversation_uid": "a", "message": "own goal result"})
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    response = await messages.send_message(session.session_id, messages.MessageRequest(content="/goal show", conversation_uid="a"))
    events = [item async for item in response.body_iterator]
    assert "own goal result" in repr(events) and "foreign turn" not in repr(events)
    assert len(events) == 2 and not session.subscriptions


@pytest.mark.asyncio
async def test_private_goal_rejects_unupgraded_daemon_before_submission():
    from kollabor_engine.session import EngineSession

    session = EngineSession.__new__(EngineSession)
    state = types.SimpleNamespace(send_message=AsyncMock())
    session.daemon = types.SimpleNamespace(state=state, rpc=types.SimpleNamespace(call=AsyncMock(side_effect=RuntimeError("not found"))))
    result = await session.send_message("/goal show", private_goal_reply=True)
    assert not result["accepted"] and "does not support" in result["reason"]
    state.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_goal_cancel_never_falls_back_to_current_model_turn():
    from kollabor_engine.session import EngineSession

    session = EngineSession.__new__(EngineSession)
    rpc = types.SimpleNamespace(call=AsyncMock(return_value={"cancelled": True, "queued": True}))
    session.daemon = types.SimpleNamespace(rpc=rpc)
    reply_id = "goal-" + "c" * 32
    result = await session.cancel_owned_turn(reply_id)
    assert result["cancelled"] and result["queued"]
    rpc.call.assert_awaited_once_with("state.cancel_goal_reply", {"reply_id": reply_id})


class _PromptRpc:
    def register(self, method, handler):
        self.handler = handler


@pytest.mark.asyncio
async def test_an_answer_to_a_parked_card_reaches_the_message_that_gives_it(monkeypatch):
    """Danburite on #554 round 6: a parked native prompt keeps the ids of the
    turn that showed it, so its answer, given by a later message, was dropped
    as that turn's output, and the card stayed open after the answer took
    effect. Real stamp, permission bridge, display tap and event normalizer."""
    from kollabor_engine.daemon_pool import _normalize_event

    from kollabor.llm.permissions.attach_bridge import AttachPermissionBridge
    from kollabor_agent.queue_processor import QueueProcessor
    from kollabor_tui.display_tap import DisplayTap

    session = _FakeSession([])
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)
    tap = DisplayTap()
    events = tap.subscribe("parked-answer")
    bridge, rpc = AttachPermissionBridge(), _PromptRpc()
    turn = QueueProcessor.__new__(QueueProcessor)
    turn._turn_token = None
    turn._stamp_turn(None, ("request-A",))
    try:
        prompt = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=tap,
                rpc_server=rpc,
                details={"tool_id": "shell-3", "permission_id": "owner-shell-3", "tool_type": "shell"},
                timeout=None,
            )
        )
    finally:
        turn._stamp_turn(None)  # turn A ends; its parked prompt keeps A's context
    await asyncio.sleep(0)
    permission_event = events.get_nowait()
    assert permission_event["request_ids"] == ["request-A"]
    permission_id = permission_event["details"]["permission_id"]
    bridge.park("shell-3", permission_id)
    assert events.get_nowait()["type"] == "permission_parked"
    answers = []

    async def submit(content, *, request_id=None, **kwargs):
        # This message's "no" answers the card.
        assert (await rpc.handler({"tool_id": "shell-3", "permission_id": permission_id, "response": "DENY"}))["ok"]
        await asyncio.wait_for(prompt, 1)
        answers.append(events.get_nowait())
        session._events.put_nowait(_normalize_event(answers[-1]))
        session._events.put_nowait({"type": "turn_complete", "request_ids": [request_id]})
        return {"accepted": True, "request_id": request_id}

    async def drain(stream):
        async for _ in stream:
            pass

    session.send_message = AsyncMock(side_effect=submit)
    card = {
        "type": "tool-call",
        "toolCallId": f"permission_shell-3_{permission_id}",
        "toolName": "request_permission",
        "args": {"tool_id": "shell-3", "permission_id": permission_id},
    }
    state = {"messages": [{"id": "old-assistant", "role": "assistant", "content": [card]}]}
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{"type": "add-message", "content": "no"}], state=state),
    )
    try:
        await asyncio.wait_for(drain(response.stream), 1)
        assert answers[0]["type"] == "permission_denied" and answers[0]["request_ids"] == ["request-A"]
        assert answers[0]["permission_id"] == permission_id
        shown = captured["controller"].state["messages"][0]["content"][0]
        assert shown.get("result", {}).get("decision") == "deny", shown
    finally:
        tap.unsubscribe("parked-answer")
        if not prompt.done():
            prompt.cancel()
            await asyncio.gather(prompt, return_exceptions=True)


@pytest.mark.asyncio
async def test_goal_image_replay_legacy_stream_returns_only_its_receipt(monkeypatch):
    session = _FakeSession([{'type': 'token', 'text': 'unrelated reply'}, {'type': 'turn_complete'}])
    session.send_message = AsyncMock(return_value={
        'accepted': True, 'completed': True, 'replayed': True,
        'request_id': 'saved-request', 'goal_id': 'saved-goal',
        'message': 'This submission already created goal saved-goal.',
    })
    monkeypatch.setattr(messages, 'get_session_registry', lambda: _FakeRegistry(session))
    response = await messages.send_message(session.session_id, messages.MessageRequest(
        content=[{'type': 'text', 'text': '/goal inspect'}, {'type': 'image', 'image': 'data:image/png;base64,aA=='}],
        request_id='stable-client-id'))
    events = [item async for item in response.body_iterator]
    assert len(events) == 2 and 'saved-goal' in repr(events)
    assert 'unrelated reply' not in repr(events) and not session.subscriptions
    assert session.send_message.await_args.kwargs['request_id'] == 'stable-client-id'


@pytest.mark.asyncio
async def test_goal_image_replay_assistant_stream_preserves_message_id_and_owns_reply(monkeypatch):
    session = _FakeSession([{'type': 'token', 'text': 'unrelated reply'}, {'type': 'turn_complete'}])
    session.send_message = AsyncMock(return_value={
        'accepted': True, 'completed': True, 'replayed': True,
        'request_id': 'saved-request', 'goal_request_id': 'saved-request',
        'goal_id': None, 'message': 'Images remain saved as saved-request.',
    })
    monkeypatch.setattr(messages, 'get_session_registry', lambda: _FakeRegistry(session))
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)
    command = {'type': 'add-message', 'message': {'id': 'stable-client-id', 'parts': [
        {'type': 'text', 'text': '/goal inspect'}, {'type': 'image', 'image': 'data:image/png;base64,aA=='},
    ]}}
    response = await messages.assistant_transport(session.session_id, messages.AssistantRequest(
        commands=[command], state={'messages': []}))
    async for _chunk in response.stream:
        pass
    assert captured['controller'].text == ['Images remain saved as saved-request.']
    assert 'unrelated reply' not in repr(captured['controller'].state) and not session.subscriptions
    assert captured['controller'].state['goalSubmission'] == {
        'requestId': 'saved-request', 'clientRequestId': 'stable-client-id',
    }
    assert session.send_message.await_args.kwargs['request_id'] == 'stable-client-id'


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["message", "assistant"])
async def test_goal_replay_receipt_is_not_published_after_revocation(monkeypatch, route):
    from fastapi import HTTPException
    from kollabor_engine.session_context import SESSION_READ_CONTEXT_SCOPE_KEY, SessionReadContext

    class PhoneSession(_FakeSession):
        def subscribe(self, context=None):
            return super().subscribe()

    session = PhoneSession([])
    context = SessionReadContext(owner_id="owner", phone_id="phone")
    request = types.SimpleNamespace(scope={"state": {SESSION_READ_CONTEXT_SCOPE_KEY: context}})
    async def submit(content, **kwargs):
        assert kwargs["private_goal_reply"]
        context.revoked = True
        return {"accepted": True, "completed": True, "replayed": True,
                "request_id": "secret-request", "goal_request_id": "secret-request",
                "message": "private replay receipt"}
    session.send_message = AsyncMock(side_effect=submit)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    content = [{"type": "text", "text": "/goal inspect"},
               {"type": "image", "image": "data:image/png;base64,aA=="}]
    if route == "message":
        with pytest.raises(HTTPException) as refused:
            await messages.send_message(session.session_id, messages.MessageRequest(
                content=content, request_id="stable", conversation_uid="a"), request)
        assert refused.value.status_code == 403
    else:
        captured, _, _ = _install_fake_assistant_stream(monkeypatch)
        response = await messages.assistant_transport(session.session_id, messages.AssistantRequest(
            commands=[{"type": "add-message", "message": {"id": "stable", "parts": content}}],
            state={"messages": [], "conversationUid": "a"}), request)
        async for _chunk in response.stream:
            pass
        assert not captured["controller"].text
        assert "secret-request" not in repr(captured["controller"].state)
    assert not session.subscriptions


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["message", "assistant"])
async def test_completed_goal_without_replay_or_private_reply_is_rejected(monkeypatch, route):
    from fastapi import HTTPException

    session = _FakeSession([])
    session.send_message = AsyncMock(return_value={"accepted": True, "completed": True})
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    if route == "message":
        with pytest.raises(HTTPException) as refused:
            await messages.send_message(session.session_id, messages.MessageRequest(
                content="/goal show", conversation_uid="a"))
        assert refused.value.status_code == 502
    else:
        captured, _, _ = _install_fake_assistant_stream(monkeypatch)
        response = await messages.assistant_transport(session.session_id, messages.AssistantRequest(
            commands=[{"type": "add-message", "content": "/goal show"}],
            state={"messages": [], "conversationUid": "a"}))
        async for _chunk in response.stream:
            pass
        assert "private goal reply" in repr(captured["controller"].errors)
    assert not session.subscriptions


@pytest.mark.asyncio
async def test_completed_non_goal_still_reads_its_own_shared_events(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "actual response", "request_id": "owner"},
        {"type": "turn_complete", "request_id": "owner"},
    ])
    session.send_message = AsyncMock(return_value={"accepted": True, "completed": True, "request_id": "owner"})
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    response = await messages.send_message(session.session_id, messages.MessageRequest(content="hello"))
    events = [item async for item in response.body_iterator]
    assert "actual response" in repr(events) and "Goal request" not in repr(events)
    assert not session.subscriptions


def test_batched_sends_get_consecutive_provisional_ids():
    """Two messages in one request are rows L and L+1 after an L-row state (#1356).

    The web client names its pending copies user-<session>-<L + i> and tracks
    its in-flight sends by those ids, so the engine's rows must use the same
    numbers.
    """
    state = [
        {"id": "history-0", "role": "user", "content": "earlier"},
        {"id": "history-1", "role": "assistant", "content": "ok"},
    ]
    rows = messages._assistant_update_message_state(state, "s1", ["first", "second"], [], None)
    assert [row["id"] for row in rows[2:]] == ["user-s1-2", "user-s1-3", "assistant-s1-4"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_id", "expected"),
    [("Wfw7Jka", "Wfw7Jka"), ("has spaces", None), ("x" * 129, None), (7, None)],
)
async def test_a_send_is_followed_by_its_client_message_id(monkeypatch, client_id, expected):
    """The page knows its send by the message id it made (#1359).

    The daemon saves the request id on the user row, so a safe client id is the
    request id: the page can tell its own saved send from another sender's
    before the engine answers. Anything else gets a fresh id.
    """
    session = _FakeSession([{"type": "turn_complete"}])
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, _ = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[{
                "type": "add-message",
                "message": {"id": client_id, "parts": [{"type": "text", "text": "hello"}]},
            }],
            state={"messages": []},
        ),
    )
    async for _chunk in response.stream:
        pass

    request_id = session.send_message.await_args.kwargs["request_id"]
    if expected is None:
        assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    else:
        assert request_id == expected
    user_row = captured["controller"].state["messages"][0]
    assert user_row["role"] == "user"
    assert user_row["metadata"]["custom"]["requestId"] == request_id


def _saved_user(content, *request_ids):
    return {"role": "user", "content": content, "metadata": {"operator_inputs": [
        {"source": "state_rpc", "timestamp": "t", "text": "x", "request_id": request_id}
        for request_id in request_ids
    ]}}


def test_align_helper_pairs_a_user_row_with_its_saved_copy_by_request_id():
    """The daemon saves sends that arrive together as one row, behind any agent
    HUD, so its words differ from the streamed row; the request id it names
    still pairs them (#1359)."""
    streamed = [
        {"id": "user-s1-2", "role": "user", "content": "twin words",
         "metadata": {"custom": {"requestId": "mine"}}},
        {"id": "assistant-s1-3", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    stored = [
        _saved_user("<agent_hud>budget</agent_hud>\ntwin words\ntwin words", "mine", "theirs"),
        {"role": "assistant", "content": "done"},
    ]
    assert messages._assistant_align_streamed_ids(streamed, stored) is True
    assert [m["metadata"]["custom"]["historySourceIndex"] for m in streamed] == [0, 1]


def test_align_helper_does_not_pair_another_senders_identical_words():
    """Same words, another send's id: not this turn's row (#1359)."""
    streamed = [
        {"id": "user-s1-2", "role": "user", "content": "twin words",
         "metadata": {"custom": {"requestId": "mine"}}},
        {"id": "assistant-s1-3", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    stored = [_saved_user("twin words", "theirs"), {"role": "assistant", "content": "done"}]
    assert messages._assistant_align_streamed_ids(streamed, stored) is False
    assert "historySourceIndex" not in streamed[0]["metadata"]["custom"]


def test_align_helper_skips_the_live_row_of_a_watched_turn():
    """A page that sent while watching another turn's reply carries that live
    row (live-turn) in its state. It is a display row, not one of this turn's,
    so it must not stop the turn's rows from pairing (#1359)."""
    streamed = [
        {"id": "history-0", "role": "user", "content": "busy"},
        {"id": "live-turn", "role": "assistant",
         "content": [{"type": "tool-call", "toolCallId": "call-1", "toolName": "terminal"}]},
        {"id": "user-s1-2", "role": "user", "content": "twin words",
         "metadata": {"custom": {"requestId": "mine"}}},
        {"id": "assistant-s1-3", "role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    stored = [
        {"role": "user", "content": "busy"},
        {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"id": "call-1"}]}},
        {"role": "tool", "content": "", "metadata": {"tool_call_id": "call-1"}},
        _saved_user("<agent_hud>budget</agent_hud>\ntwin words\ntwin words", "mine", "theirs"),
        {"role": "assistant", "content": "done"},
    ]
    assert messages._assistant_align_streamed_ids(streamed, stored) is True
    assert [streamed[i]["metadata"]["custom"]["historySourceIndex"] for i in (2, 3)] == [3, 4]
    assert "metadata" not in streamed[1]


def test_align_helper_skips_another_senders_message_steered_into_the_turn():
    """Another client's message that arrives mid-turn is saved between this
    turn's rows (the daemon steers it into the running turn). It names only its
    own send, so it is not one of this page's rows and must not stop the
    pairing; the page adds it from history (#1359)."""
    streamed = [
        {"id": "user-s1-0", "role": "user", "content": "twin words",
         "metadata": {"custom": {"requestId": "mine"}}},
        {"id": "assistant-s1-1", "role": "assistant", "content": [
            {"type": "tool-call", "toolCallId": "c1"}, {"type": "tool-call", "toolCallId": "c2"},
            {"type": "text", "text": "done"},
        ]},
    ]
    stored = [
        _saved_user("twin words\ntwin words", "mine", "theirs"),
        {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"id": "c1"}]}},
        {"role": "tool", "content": "", "metadata": {"tool_call_id": "c1"}},
        _saved_user("ping", "theirs-ping"),
        {"role": "assistant", "content": "", "metadata": {"tool_calls": [{"id": "c2"}]}},
        {"role": "tool", "content": "", "metadata": {"tool_call_id": "c2"}},
        {"role": "assistant", "content": "done"},
    ]
    assert messages._assistant_align_streamed_ids(streamed, stored) is True
    assert [m["metadata"]["custom"]["historySourceIndex"] for m in streamed] == [0, 6]

    # A streamed user row that names no send proves nothing: no skipping.
    unnamed = copy.deepcopy(streamed)
    del unnamed[0]["metadata"]
    unnamed[1].pop("metadata", None)
    assert messages._assistant_align_streamed_ids(unnamed, stored) is False
