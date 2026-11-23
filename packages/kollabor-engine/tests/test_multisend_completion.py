"""A multi-message assistant request owns every turn that it admits (#1368)."""

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest
from kollabor_engine import daemon_pool
from kollabor_engine.routes import messages
from test_assistant_route import _FakeRegistry, _FakeSession, _install_fake_assistant_stream


def _assistant_rows(captured):
    return [
        message
        for message in captured["controller"].state["messages"]
        if message.get("role") == "assistant"
    ]


def _text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "")
        for part in content or []
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _permission_call_id(state, tool_id, permission_id=None):
    for message in state.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("toolName") != "request_permission":
                continue
            args = part.get("args") or {}
            if not isinstance(args, dict) or args.get("tool_id") != tool_id:
                continue
            if permission_id is None or args.get("permission_id") == permission_id:
                return part["toolCallId"]
    raise AssertionError(
        f"no displayed permission call for tool_id={tool_id!r}, "
        f"permission_id={permission_id!r}"
    )


class _FakeCorrelationClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.mark.asyncio
async def test_cancelled_event_wait_does_not_consume_buffered_event():
    queue = asyncio.Queue()
    event = {"type": "turn_complete", "request_id": "first"}
    queue.put_nowait(event)
    controller = type("Controller", (), {})()
    controller.cancelled_event = asyncio.Event()
    controller.cancelled_event.set()

    assert await messages._assistant_next_event(queue, controller) is None
    assert queue.qsize() == 1
    assert queue.get_nowait() == event


@pytest.mark.asyncio
async def test_two_separate_turns_keep_stream_open_until_both_finish(monkeypatch):
    assistant_ids = {}
    original_turn_message = messages._assistant_turn_message

    def capture_turn_message(session_id, current_messages, turn):
        message = original_turn_message(session_id, current_messages, turn)
        text = _text(message)
        if text:
            assistant_ids.setdefault(text, []).append(message["id"])
        return message

    monkeypatch.setattr(messages, "_assistant_turn_message", capture_turn_message)
    session = _FakeSession([
        {"type": "token", "text": "first reply", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {"type": "add-message", "rowId": "user-session-test-0", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
                {"type": "add-message", "rowId": "user-session-test-1", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert session.send_message.await_count == 2
    assistants = _assistant_rows(captured)
    assert [_text(message) for message in assistants] == ["first reply", "second reply"]
    messages_state = captured["controller"].state["messages"]
    assert [(message["role"], _text(message)) for message in messages_state] == [
        ("user", "one"),
        ("assistant", "first reply"),
        ("user", "two"),
        ("assistant", "second reply"),
    ]
    users = [message for message in messages_state if message.get("role") == "user"]
    assert [message["metadata"]["custom"]["requestId"] for message in users] == ["first", "second"]
    assert [message["id"] for message in users] == ["user-session-test-0", "user-session-test-1"]
    assert len(set(assistant_ids["first reply"])) == 1
    assert len(set(assistant_ids["second reply"])) == 1


@pytest.mark.asyncio
async def test_shared_turn_completes_both_messages(monkeypatch):
    session = _FakeSession([
        {"type": "turn_complete", "request_id": "unrelated"},
        {"type": "token", "text": "combined reply", "request_ids": ["first", "second"]},
        {"type": "turn_complete", "request_ids": ["first", "second"]},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert [_text(message) for message in _assistant_rows(captured)] == ["combined reply"]
    assert session.send_message.await_count == 2


@pytest.mark.asyncio
async def test_foreign_and_unnamed_completions_do_not_finish_admitted_turns(monkeypatch):
    session = _FakeSession([
        {"type": "turn_complete", "request_id": "foreign"},
        {"type": "turn_complete"},
        {"type": "token", "text": "first", "request_id": "first"},
        {"type": "turn_complete", "request_ids": ["first"]},
        {"type": "token", "text": "second", "request_ids": ["second"]},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert [_text(message) for message in _assistant_rows(captured)] == ["first", "second"]


@pytest.mark.asyncio
async def test_failed_first_turn_does_not_drop_the_later_turn(monkeypatch):
    session = _FakeSession([
        {"type": "error", "message": "first turn failed", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first", "stop_reason": "error"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=1)
    assistants = _assistant_rows(captured)
    assert len(assistants) == 2
    assert assistants[0]["status"]["reason"] == "error"
    assert _text(assistants[1]) == "second reply"
    assert captured["controller"].state["usage"]["stopReason"] == "error"


@pytest.mark.asyncio
async def test_rejected_later_send_still_drains_prior_admitted_turn(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "first reply", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
    ])

    async def accept(_content, *, request_id):
        if request_id == "second":
            return {"accepted": False, "reason": "queue full"}
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert [_text(message) for message in _assistant_rows(captured)] == ["first reply"]
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "Acknowledged as accepted: first" in error
    assert "Rejected and not accepted: second" in error
    assert "queue full" in error
    assert captured["controller"].state["usage"]["stopReason"] == "error"


@pytest.mark.asyncio
async def test_cancelled_turn_is_terminal_but_later_turn_still_streams(monkeypatch):
    session = _FakeSession([
        {"type": "turn_complete", "request_id": "first", "stop_reason": "cancelled"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=1)
    assistants = _assistant_rows(captured)
    assert len(assistants) == 2
    assert _text(assistants[0]) == ""
    assert _text(assistants[1]) == "second reply"


@pytest.mark.asyncio
async def test_completion_queued_before_next_admission_returns_is_retained(monkeypatch):
    session = _FakeSession([])

    async def accept(_content, *, request_id):
        if request_id == "second":
            # The first turn completes while the second admission is still
            # awaiting its acknowledgement.
            session._events.put_nowait({"type": "token", "text": "first reply", "request_id": "first"})
            session._events.put_nowait({"type": "turn_complete", "request_id": "first"})
            await asyncio.sleep(0)
            session._events.put_nowait({"type": "token", "text": "second reply", "request_id": "second"})
            session._events.put_nowait({"type": "turn_complete", "request_id": "second"})
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert [_text(message) for message in _assistant_rows(captured)] == ["first reply", "second reply"]


@pytest.mark.asyncio
async def test_multiple_admissions_without_echoed_ids_fail_explicitly(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "first reply"},
        {"type": "turn_complete"},
        {"type": "token", "text": "second reply"},
        {"type": "turn_complete"},
    ])
    session.send_message = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=0.1)
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "accepted message(s) first, second" in error
    assert "unnamed turn_complete" in error
    assert "These sends were admitted" in error
    assert "accepted message(s) first, second may still be running" in error
    assert "No messages were resubmitted" in error
    assert [_text(message) for message in _assistant_rows(captured)] == []
    users = [
        message for message in captured["controller"].state["messages"]
        if message.get("role") == "user"
    ]
    assert [message["metadata"]["custom"]["requestId"] for message in users] == [
        "first", "second",
    ]


@pytest.mark.asyncio
async def test_missing_admission_echo_can_correlate_exact_named_events(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "first reply", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_ids": ["second"]},
        {"type": "turn_complete", "request_ids": ["second"]},
    ])
    session.send_message = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert captured["controller"].errors == []
    assert [_text(message) for message in _assistant_rows(captured)] == [
        "first reply", "second reply",
    ]


@pytest.mark.asyncio
async def test_missing_admission_echo_has_a_bounded_idle_failure(monkeypatch):
    session = _FakeSession([])
    session.send_message = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=0.2)
    assert len(captured["controller"].errors) == 1
    assert "no safely identified terminal event" in captured["controller"].errors[0]
    assert "These sends were admitted" in captured["controller"].errors[0]
    assert "accepted message(s) first, second may still be running" in captured["controller"].errors[0]


@pytest.mark.asyncio
async def test_missing_echo_does_not_silently_skip_later_batch_commands(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "first reply", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])
    session.send_message = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
            {"type": "add-message", "message": {"id": "third", "parts": [{"type": "text", "text": "three"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=0.2)
    # All three accepted=True results count as submitted even when their
    # admission acknowledgements omit request IDs. Exact event IDs recover
    # the first two turns; the uncompleted third remains explicitly pending.
    assert session.send_message.await_count == 3
    assert [call.kwargs["request_id"] for call in session.send_message.await_args_list] == [
        "first", "second", "third",
    ]
    assert [_text(message) for message in _assistant_rows(captured)] == [
        "first reply", "second reply",
    ]
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "accepted message(s) third" in error
    assert "accepted message(s) third may still be running" in error
    assert "No messages were resubmitted" in error


@pytest.mark.asyncio
async def test_rejected_later_command_reports_partial_admission(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "first reply", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        if request_id == "third":
            return {"accepted": False, "reason": "queue full"}
        return {"accepted": True}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
            {"type": "add-message", "message": {"id": "third", "parts": [{"type": "text", "text": "three"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=1)
    assert session.send_message.await_count == 3
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "Acknowledged as accepted: first, second" in error
    assert "Rejected and not accepted: third" in error
    assert "queue full" in error
    assert "No messages were resubmitted" in error
    assert captured["controller"].state["usage"]["stopReason"] == "error"


@pytest.mark.asyncio
async def test_tagged_partial_rejection_keeps_accepted_turn_result_and_error_status(monkeypatch):
    session = _FakeSession([
        {"type": "token", "text": "first reply", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
    ])

    async def accept(_content, *, request_id):
        if request_id == "second":
            return {"accepted": False, "reason": "queue full"}
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
            {"type": "add-message", "message": {"id": "third", "parts": [{"type": "text", "text": "three"}]}},
        ], state={"messages": []}),
    )

    await asyncio.wait_for(drain(response.stream), timeout=1)

    assert session.send_message.await_count == 2
    assert [call.kwargs["request_id"] for call in session.send_message.await_args_list] == [
        "first", "second",
    ]
    assert [_text(message) for message in _assistant_rows(captured)] == ["first reply"]
    assistant = _assistant_rows(captured)[0]
    assert assistant["status"]["type"] == "incomplete"
    assert assistant["status"]["reason"] == "error"
    assert "queue full" in assistant["status"]["error"]
    assert captured["controller"].state["usage"]["stopReason"] == "error"
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "Acknowledged as accepted: first" in error
    assert "Rejected and not accepted: second" in error
    assert "Not submitted: third" in error


@pytest.mark.asyncio
async def test_permission_handover_keeps_later_admitted_turn_owned(monkeypatch):
    permission_id = "prompt-first"
    session = _FakeSession([
        {
            "type": "permission_request",
            "tool_id": "shell-first",
            "tool_name": "terminal",
            "request_id": "first",
            "permission_id": permission_id,
        },
        {"type": "permission_granted", "tool_id": "shell-first", "request_id": "first", "permission_id": permission_id},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    first_run = captured["controller"]
    handover_state = copy.deepcopy(first_run.state)
    assert len(_assistant_rows(captured)) == 1
    assert first_run.state["messages"][-1]["role"] == "user"
    permission_call_id = _permission_call_id(
        handover_state, "shell-first", permission_id
    )

    second_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {
                "type": "add-tool-result",
                "toolCallId": permission_call_id,
                "result": {"decision": "approve", "scope": "once", "permission_id": permission_id},
            },
        ], state=handover_state),
    )
    await asyncio.wait_for(drain(second_response.stream), timeout=1)

    assert session.send_message.await_count == 2
    assert session.resolve_permission.await_count == 1
    assert captured["controller"].errors == []
    state_messages = captured["controller"].state["messages"]
    assert [(message["role"], _text(message)) for message in state_messages] == [
        ("user", "one"),
        ("assistant", ""),
        ("user", "two"),
        ("assistant", "second reply"),
    ]
    first_permission = state_messages[1]["content"][0]
    assert first_permission["result"]["decision"] == "approve"


@pytest.mark.asyncio
async def test_handover_capability_is_session_bound_and_unforgeable(monkeypatch):
    owner = _FakeSession([])
    owner.session_id = "owner-session"
    owner_queue = owner.subscribe()
    token, claim = messages._assistant_reserve_handover(owner, owner_queue, None)
    assert token is not None
    record = {"request_ids": ["accepted-owner-message"]}
    messages._assistant_update_handover(owner, token, claim, record, owner_queue)
    messages._assistant_release_handover(owner, token, claim, record)

    other = _FakeSession([])
    other.session_id = "other-session"
    other.send_message = AsyncMock(return_value={"accepted": True})
    sessions = {owner.session_id: owner, other.session_id: other}
    monkeypatch.setattr(
        messages,
        "get_session_registry",
        lambda: type("Registry", (), {"get": lambda _self, session_id: sessions.get(session_id)})(),
    )
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    for forged in (token, "f" * 32):
        response = await messages.assistant_transport(
            other.session_id,
            messages.AssistantRequest(commands=[
                {
                    "type": "add-tool-result",
                    "toolCallId": "permission_shell-owner",
                    "result": {"decision": "approve", "scope": "once"},
                },
            ], state={messages._ASSISTANT_HANDOVER_STATE_KEY: forged}),
        )
        await asyncio.wait_for(drain(response.stream), timeout=1)
        assert len(captured["controller"].errors) == 1
        assert "capability is invalid or expired" in captured["controller"].errors[0]
        assert other.resolve_permission.await_count == 0
        assert other.send_message.await_count == 0

    assert token in messages._assistant_handover_ledger(owner)
    assert messages._assistant_handover_ledger(other) == {}
    _, cleanup_claim, cleanup_error, _, _ = messages._assistant_claim_handover(owner, token)
    assert cleanup_error is None
    messages._assistant_finish_handover(owner, token, cleanup_claim)


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_rejected_invalid_or_expired_capability_is_cleared_for_later_sends(
    monkeypatch, expired
):
    session = _FakeSession([{"type": "turn_complete", "request_id": "fresh"}])
    token = "f" * 32
    if expired:
        messages._assistant_handover_ledger(session)[token] = {
            "status": "expired",
            "record": {"request_ids": ["possibly-running"]},
            "expires_at": messages._assistant_correlation_monotonic() + 60,
            "queue": None,
            "context": None,
            "claim_id": None,
        }
    session.send_message = AsyncMock(
        return_value={"accepted": True, "request_id": "fresh"}
    )
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    rejected = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{
            "type": "add-tool-result",
            "toolCallId": "permission_stale",
            "result": {"decision": "approve", "scope": "once"},
        }], state={
            "messages": [],
            messages._ASSISTANT_HANDOVER_STATE_KEY: token,
        }),
    )
    await asyncio.wait_for(drain(rejected.stream), timeout=1)
    rejected_state = copy.deepcopy(captured["controller"].state)
    assert rejected_state[messages._ASSISTANT_HANDOVER_STATE_KEY] is None
    error = captured["controller"].errors[0]
    assert "handover" in error.lower()
    if expired:
        assert "possibly-running" in error

    resumed = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{
            "type": "add-message",
            "message": {"id": "fresh", "parts": [{"type": "text", "text": "new send"}]},
        }], state=rejected_state),
    )
    await asyncio.wait_for(drain(resumed.stream), timeout=1)

    assert session.send_message.await_count == 1
    assert session.send_message.await_args.kwargs["request_id"] == "fresh"
    assert captured["controller"].errors == []
    messages._assistant_handover_ledger(session).pop(token, None)


@pytest.mark.asyncio
async def test_disconnected_permission_followup_retries_without_gap_or_replay(monkeypatch):
    permission_id = "prompt-first"
    session = _FakeSession([])
    source = daemon_pool.DaemonHandle(session.session_id, "agent", launcher=None)
    session.subscribe = source.subscribe
    session.unsubscribe = source.unsubscribe

    async def accept(_content, *, request_id):
        if request_id == "second":
            source._fan_out({
                "type": "permission_request",
                "tool_id": "shell-first",
                "tool_name": "terminal",
                "request_id": "first",
                "permission_id": permission_id,
            })
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    first_state = copy.deepcopy(captured["controller"].state)
    token = first_state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    permission_call_id = _permission_call_id(
        first_state, "shell-first", permission_id
    )
    assert len(source._subscribers) == 1
    retained_queue = next(iter(source._subscribers))

    permission_command = {
        "type": "add-tool-result",
        "toolCallId": permission_call_id,
        "result": {"decision": "approve", "scope": "once", "permission_id": permission_id},
    }
    disconnected_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[permission_command], state=first_state),
    )
    disconnected_task = asyncio.create_task(drain(disconnected_response.stream))
    for _ in range(100):
        if session.resolve_permission.await_count:
            break
        await asyncio.sleep(0.001)
    assert session.resolve_permission.await_count == 1
    assert source._subscribers == {retained_queue}
    captured["controller"].cancelled_event.set()
    await asyncio.wait_for(disconnected_task, timeout=1)

    entry = messages._assistant_handover_ledger(session)[token]
    assert entry["status"] == "available"
    assert entry["queue"] is retained_queue
    assert source._subscribers == {retained_queue}

    source._fan_out({
        "type": "permission_granted",
        "tool_id": "shell-first",
        "request_id": "first",
        "permission_id": permission_id,
    })
    source._fan_out({"type": "turn_complete", "request_id": "first"})
    source._fan_out({"type": "token", "text": "second reply", "request_id": "second"})
    source._fan_out({"type": "turn_complete", "request_id": "second"})
    retry_state = copy.deepcopy(captured["controller"].state)
    retry_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[permission_command], state=retry_state),
    )
    await asyncio.wait_for(drain(retry_response.stream), timeout=1)

    assert session.send_message.await_count == 2
    assert session.resolve_permission.await_count == 1
    assert token not in messages._assistant_handover_ledger(session)
    assert source._subscribers == set()
    state_messages = captured["controller"].state["messages"]
    assert [(message["role"], _text(message)) for message in state_messages] == [
        ("user", "one"),
        ("assistant", ""),
        ("user", "two"),
        ("assistant", "second reply"),
    ]


@pytest.mark.asyncio
async def test_cancel_racing_terminal_event_is_preserved_across_reconnect(monkeypatch):
    session = _FakeSession([])
    source = daemon_pool.DaemonHandle(session.session_id, "agent", launcher=None)
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    def subscribe_with_cancel(*args, **kwargs):
        queue = source.subscribe(*args, **kwargs)
        original_get = queue.get

        async def cancel_on_final_terminal():
            event = await original_get()
            if (
                event.get("type") == "turn_complete"
                and event.get("request_id") == "second"
            ):
                # Make queue.get() and cancellation complete in the same loop turn.
                captured["controller"].cancelled_event.set()
            return event

        queue.get = cancel_on_final_terminal
        return queue

    session.subscribe = subscribe_with_cancel
    session.unsubscribe = source.unsubscribe

    async def accept(_content, *, request_id):
        source._fan_out(
            {"type": "token", "text": f"{request_id} reply", "request_id": request_id}
        )
        source._fan_out({"type": "turn_complete", "request_id": request_id})
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "first",
                        "parts": [{"type": "text", "text": "first message"}],
                    },
                },
                {
                    "type": "add-message",
                    "message": {
                        "id": "second",
                        "parts": [{"type": "text", "text": "second message"}],
                    },
                },
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    first_state = copy.deepcopy(captured["controller"].state)

    resumed = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "third",
                        "parts": [{"type": "text", "text": "third message"}],
                    },
                }
            ],
            state=first_state,
        ),
    )
    await asyncio.wait_for(drain(resumed.stream), timeout=1)

    assert [
        call.kwargs["request_id"] for call in session.send_message.await_args_list
    ] == ["first", "second", "third"]
    assert messages._assistant_handover_ledger(session) == {}
    assert source._subscribers == set()
    assert captured["controller"].errors == []
    assert {"first reply", "second reply", "third reply"} <= {
        _text(row) for row in _assistant_rows(captured)
    }


@pytest.mark.asyncio
async def test_reused_tool_id_outcome_with_wrong_permission_id_does_not_reconcile(monkeypatch):
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    permission_id = "prompt-first"
    session = _FakeSession([{
        "type": "permission_request",
        "tool_id": "shell-first",
        "tool_name": "terminal",
        "request_id": "first",
        "permission_id": permission_id,
    }])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    session.resolve_permission = AsyncMock(return_value=False)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    state = copy.deepcopy(captured["controller"].state)
    token = state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    queue = session.subscriptions[-1]
    assert queue.qsize() == 0
    permission_call_id = _permission_call_id(state, "shell-first", permission_id)

    decision = {
        "type": "add-tool-result",
        "toolCallId": permission_call_id,
        "result": {
            "decision": "approve",
            "scope": "once",
            "permission_id": permission_id,
        },
    }
    first_attempt = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[decision], state=state),
    )
    await asyncio.wait_for(drain(first_attempt.stream), timeout=1)
    assert "may still be running" in captured["controller"].errors[0]
    state = copy.deepcopy(captured["controller"].state)
    entry = messages._assistant_handover_ledger(session)[token]
    assert entry["status"] == "available"
    assert entry["record"]["permission_decisions"][permission_id]["status"] == "attempted"

    queue.put_nowait({
        "type": "permission_granted",
        "tool_id": "shell-first",
        "request_id": "second",
        "permission_id": "a-different-prompt",
    })
    queue.put_nowait({"type": "turn_complete", "request_id": "first"})
    queue.put_nowait({"type": "token", "text": "second reply", "request_id": "second"})
    queue.put_nowait({"type": "turn_complete", "request_id": "second"})

    second_attempt = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[decision], state=state),
    )
    await asyncio.wait_for(drain(second_attempt.stream), timeout=1)

    assert session.resolve_permission.await_count == 2
    assert session.send_message.await_count == 2
    assert queue.qsize() == 0
    assert token not in messages._assistant_handover_ledger(session)
    assert len(captured["controller"].errors) == 1
    assert "permission resolution could not be confirmed" in captured["controller"].errors[0].lower()
    assert "reached their terminal events" in captured["controller"].errors[0]
    assert captured["controller"].state["usage"]["stopReason"] == "error"
    state_messages = captured["controller"].state["messages"]
    assert _text(state_messages[-1]) == "second reply"
    permission_part = state_messages[1]["content"][0]
    assert permission_part["toolCallId"] == permission_call_id
    assert "result" not in permission_part


@pytest.mark.asyncio
async def test_false_permission_rpc_recovers_from_matching_permission_id_and_terminals(monkeypatch):
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    permission_id = "permission-prompt-first"
    session = _FakeSession([
        {
            "type": "permission_request",
            "tool_id": "shell-first",
            "tool_name": "terminal",
            "request_id": "first",
            "permission_id": permission_id,
        },
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    session.resolve_permission = AsyncMock(return_value=False)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    first_state = copy.deepcopy(captured["controller"].state)
    token = first_state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    queue = session.subscriptions[-1]
    assert queue.qsize() == 0
    permission_call_id = _permission_call_id(
        first_state, "shell-first", permission_id
    )

    decision = {
        "type": "add-tool-result",
        "toolCallId": permission_call_id,
        "result": {
            "decision": "approve",
            "scope": "once",
            "permission_id": permission_id,
        },
    }
    first_attempt = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[decision], state=first_state),
    )
    await asyncio.wait_for(drain(first_attempt.stream), timeout=1)
    assert len(captured["controller"].errors) == 1
    assert "may still be running" in captured["controller"].errors[0]
    state = copy.deepcopy(captured["controller"].state)
    assert messages._assistant_handover_ledger(session)[token]["status"] == "available"

    queue.put_nowait({
        "type": "permission_granted",
        "tool_id": "shell-first",
        "request_id": "first",
        "permission_id": permission_id,
    })
    queue.put_nowait({"type": "turn_complete", "request_id": "first"})
    queue.put_nowait({"type": "token", "text": "second reply", "request_id": "second"})
    queue.put_nowait({"type": "turn_complete", "request_id": "second"})

    second_attempt = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[decision], state=state),
    )
    await asyncio.wait_for(drain(second_attempt.stream), timeout=1)

    assert session.resolve_permission.await_count == 2
    assert session.resolve_permission.await_args.kwargs["permission_id"] == permission_id
    assert queue.qsize() == 0
    assert token not in messages._assistant_handover_ledger(session)
    assert captured["controller"].errors == []
    assert captured["controller"].state["usage"]["stopReason"] == "end_turn"
    assert captured["controller"].state.get("error") == ""
    assert [(message["role"], _text(message)) for message in captured["controller"].state["messages"]] == [
        ("user", "one"),
        ("assistant", ""),
        ("user", "two"),
        ("assistant", "second reply"),
    ]


@pytest.mark.asyncio
async def test_missing_permission_id_fails_closed_but_drains_exact_turn_terminals(monkeypatch):
    session = _FakeSession([
        {
            "type": "permission_request",
            "tool_id": "shell-first",
            "tool_name": "terminal",
            "request_id": "first",
        },
        {"type": "permission_granted", "tool_id": "shell-first", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ])

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    session.resolve_permission = AsyncMock(return_value=True)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    state = copy.deepcopy(captured["controller"].state)
    token = state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    permission_call_id = _permission_call_id(state, "shell-first")

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{
            "type": "add-tool-result",
            "toolCallId": permission_call_id,
            "result": {"decision": "approve", "scope": "once"},
        }], state=state),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)

    assert session.resolve_permission.await_count == 0
    assert token not in messages._assistant_handover_ledger(session)
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "no unique server-recorded permission_id" in error
    assert "reached their terminal events" in error
    assert captured["controller"].state["usage"]["stopReason"] == "error"
    assert _text(captured["controller"].state["messages"][-1]) == "second reply"


@pytest.mark.asyncio
async def test_missing_owner_error_does_not_inherit_prior_prompt_attempt(monkeypatch):
    first_permission_id = "prompt-first"
    session = _FakeSession([
        {
            "type": "permission_request",
            "tool_id": "shell-first",
            "tool_name": "terminal",
            "request_id": "first",
            "permission_id": first_permission_id,
        },
    ])

    async def resolve_permission(tool_id, decision, scope, *, permission_id):
        assert (tool_id, decision, scope, permission_id) == (
            "shell-first", "approve", "once", first_permission_id,
        )
        session._events.put_nowait({
            "type": "permission_granted",
            "tool_id": "shell-first",
            "request_id": "first",
            "permission_id": first_permission_id,
        })
        session._events.put_nowait({"type": "turn_complete", "request_id": "first"})
        session._events.put_nowait({
            "type": "permission_request",
            "tool_id": "shell-second",
            "tool_name": "terminal",
            "request_id": "second",
        })
        session._events.put_nowait({
            "type": "token", "text": "second reply", "request_id": "second",
        })
        session._events.put_nowait({"type": "turn_complete", "request_id": "second"})
        return True

    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    session.resolve_permission = AsyncMock(side_effect=resolve_permission)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    first_state = copy.deepcopy(captured["controller"].state)

    first_answer = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{
            "type": "add-tool-result",
            "toolCallId": _permission_call_id(first_state, "shell-first", first_permission_id),
            "result": {"decision": "approve", "scope": "once", "permission_id": first_permission_id},
        }], state=first_state),
    )
    await asyncio.wait_for(drain(first_answer.stream), timeout=1)
    second_state = copy.deepcopy(captured["controller"].state)

    missing_owner_answer = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[{
            "type": "add-tool-result",
            "toolCallId": _permission_call_id(second_state, "shell-second"),
            "result": {"decision": "deny", "scope": "once"},
        }], state=second_state),
    )
    await asyncio.wait_for(drain(missing_owner_answer.stream), timeout=1)

    assert session.resolve_permission.await_count == 1
    error = "\n".join(captured["controller"].errors)
    assert "No permission response was submitted." in error
    assert "may or may not have taken effect" not in error
    assert "second reply" in _text(captured["controller"].state["messages"][-1])


@pytest.mark.asyncio
async def test_handover_terminal_drop_reports_partial_loss_without_resubmitting(monkeypatch):
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    permission_id = "prompt-first"
    session = _FakeSession([])
    source = daemon_pool.DaemonHandle(session.session_id, "agent", launcher=None)
    session.subscribe = source.subscribe
    session.unsubscribe = source.unsubscribe

    async def accept(_content, *, request_id):
        if request_id == "second":
            source._fan_out({
                "type": "permission_request",
                "tool_id": "shell-first",
                "tool_name": "terminal",
                "request_id": "first",
                "permission_id": permission_id,
            })
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    handover_state = copy.deepcopy(captured["controller"].state)
    queue = next(iter(source._subscribers))
    queue._maxsize = 1
    source._fan_out({"type": "token", "text": "buffered", "request_id": "first", "seq": 40})
    source._fan_out({"type": "turn_complete", "request_id": "first", "seq": 41})
    permission_call_id = _permission_call_id(
        handover_state, "shell-first", permission_id
    )

    second_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {
                "type": "add-tool-result",
                "toolCallId": permission_call_id,
                "result": {"decision": "approve", "scope": "once", "permission_id": permission_id},
            },
        ], state=handover_state),
    )
    chunks = []

    async def collect_chunks():
        async for chunk in second_response.stream:
            chunks.append(chunk)

    await asyncio.wait_for(collect_chunks(), timeout=1)

    assert session.send_message.await_count == 2
    assert session.resolve_permission.await_count == 1
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "event stream lost" in error.lower()
    assert "first, second" in error
    assert "may still be running" in error
    assert "No messages were resubmitted" in error
    assert captured["controller"].state["usage"]["stopReason"] == "error"
    finishes = [chunk for chunk in chunks if chunk.type == "message-finish"]
    assert finishes and finishes[-1].finishReason == "error"
    assert "event stream lost" in finishes[-1].error["message"].lower()


@pytest.mark.asyncio
async def test_revocation_takes_priority_over_handover_queue_gap(monkeypatch):
    session = _FakeSession([{
        "type": "permission_request",
        "tool_id": "shell-first",
        "tool_name": "terminal",
        "request_id": "first",
    }])
    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)
    handover_state = copy.deepcopy(captured["controller"].state)
    permission_call_id = _permission_call_id(handover_state, "shell-first")
    token = handover_state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    queue = session.subscriptions[-1]
    queue.revoked = True
    queue.dropped_count = 1
    queue.dropped_terminal = True

    second_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {
                "type": "add-tool-result",
                "toolCallId": permission_call_id,
                "result": {"decision": "approve", "scope": "once"},
            },
        ], state=handover_state),
    )
    await asyncio.wait_for(drain(second_response.stream), timeout=1)

    assert session.resolve_permission.await_count == 0
    assert captured["controller"].errors == []
    assert token not in messages._assistant_handover_ledger(session)


@pytest.mark.asyncio
async def test_handover_capacity_full_rejects_before_any_admission(monkeypatch):
    monkeypatch.setattr(messages, "_ASSISTANT_HANDOVER_MAX_ENTRIES", 1)
    session = _FakeSession([])
    queue = session.subscribe()
    existing_token, existing_claim = messages._assistant_reserve_handover(
        session, queue, None
    )
    assert existing_token is not None
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "third", "parts": [{"type": "text", "text": "three"}]}},
            {"type": "add-message", "message": {"id": "fourth", "parts": [{"type": "text", "text": "four"}]}},
        ], state={"messages": []}),
    )
    chunks = []

    async def collect_chunks():
        async for chunk in response.stream:
            chunks.append(chunk)

    await asyncio.wait_for(collect_chunks(), timeout=1)

    assert session.send_message.await_count == 0
    assert len(captured["controller"].errors) == 1
    assert "No messages were submitted" in captured["controller"].errors[0]
    assert existing_token in messages._assistant_handover_ledger(session)
    finishes = [chunk for chunk in chunks if chunk.type == "message-finish"]
    assert finishes and finishes[-1].finishReason == "error"
    messages._assistant_finish_handover(session, existing_token, existing_claim)


@pytest.mark.asyncio
async def test_single_send_permission_handover_uses_capacity_until_terminal(monkeypatch):
    monkeypatch.setattr(messages, "_ASSISTANT_HANDOVER_MAX_ENTRIES", 1)
    permission_id = "prompt-single"
    session = _FakeSession([
        {
            "type": "permission_request",
            "tool_id": "shell-single",
            "tool_name": "terminal",
            "request_id": "single",
            "permission_id": permission_id,
        },
        {"type": "permission_granted", "tool_id": "shell-single", "request_id": "single", "permission_id": permission_id},
        {"type": "turn_complete", "request_id": "single"},
    ])
    session.send_message = AsyncMock(return_value={"accepted": True, "request_id": "single"})
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    first_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "single", "parts": [{"type": "text", "text": "one"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(first_response.stream), timeout=1)

    handover_state = copy.deepcopy(captured["controller"].state)
    token = handover_state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    entry = messages._assistant_handover_ledger(session)[token]
    assert entry["status"] == "available"
    assert entry["record"]["request_ids"] == ["single"]
    assert entry["queue"] in session.subscriptions
    permission_call_id = _permission_call_id(
        handover_state, "shell-single", permission_id
    )
    assert len(_assistant_rows(captured)) == 1
    assert _assistant_rows(captured)[0]["status"]["reason"] == "tool-calls"

    second_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {
                "type": "add-tool-result",
                "toolCallId": permission_call_id,
                "result": {"decision": "approve", "scope": "once", "permission_id": permission_id},
            },
        ], state=handover_state),
    )
    await asyncio.wait_for(drain(second_response.stream), timeout=1)

    assert session.send_message.await_count == 1
    assert session.resolve_permission.await_count == 1
    assert token not in messages._assistant_handover_ledger(session)
    assert session.subscriptions == []
    assert captured["controller"].errors == []


@pytest.mark.asyncio
async def test_long_pending_permission_expires_to_bounded_accepted_id_tombstone(monkeypatch):
    monkeypatch.setattr(messages, "_ASSISTANT_HANDOVER_TTL_SECONDS", 0.01)
    monkeypatch.setattr(messages, "_ASSISTANT_HANDOVER_TOMBSTONE_TTL_SECONDS", 0.05)
    clock = _FakeCorrelationClock()
    monkeypatch.setattr(messages, "_assistant_correlation_monotonic", clock.monotonic)
    loop = asyncio.get_running_loop()
    call_later = loop.call_later
    timers = []

    class _ManualTimer:
        def __init__(self, callback, args):
            self.callback = callback
            self.args = args
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def fire(self):
            if not self.cancelled:
                self.callback(*self.args)

    def controlled_call_later(delay, callback, *args, context=None):
        if getattr(callback, "__name__", None) == "expire":
            timer = _ManualTimer(callback, args)
            timers.append(timer)
            return timer
        return call_later(delay, callback, *args, context=context)

    monkeypatch.setattr(loop, "call_later", controlled_call_later)
    session = _FakeSession([{
        "type": "permission_request",
        "tool_id": "shell-first",
        "tool_name": "terminal",
        "request_id": "first",
    }])
    async def accept(_content, *, request_id):
        return {"accepted": True, "request_id": request_id}

    session.send_message = AsyncMock(side_effect=accept)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )
    await asyncio.wait_for(drain(response.stream), timeout=1)
    handover_state = copy.deepcopy(captured["controller"].state)
    token = handover_state[messages._ASSISTANT_HANDOVER_STATE_KEY]
    queue = session.subscriptions[-1]
    assert token in messages._assistant_handover_ledger(session)

    clock.advance(0.02)
    timers[0].fire()
    entry = messages._assistant_handover_ledger(session)[token]
    assert entry["status"] == "expired"
    assert entry["queue"] is None
    assert queue not in session.subscriptions
    assert entry["record"]["request_ids"] == ["first", "second"]

    second_response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {
                "type": "add-tool-result",
                "toolCallId": "permission_shell-first",
                "result": {"decision": "approve", "scope": "once"},
            },
        ], state=handover_state),
    )
    await asyncio.wait_for(drain(second_response.stream), timeout=1)

    assert session.send_message.await_count == 2
    assert session.resolve_permission.await_count == 0
    error = captured["controller"].errors[0]
    assert "Permission handover expired" in error
    assert "first, second" in error
    assert "may still be running" in error
    assert "No messages were resubmitted" in error

    clock.advance(0.06)
    timers[1].fire()
    assert token not in messages._assistant_handover_ledger(session)


@pytest.mark.asyncio
async def test_missing_echo_deadline_is_reset_by_correlated_activity(monkeypatch):
    events = [
        {"type": "token", "text": "first part one", "request_id": "first"},
        {"type": "token", "text": "first part two", "request_id": "first"},
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ]
    session = _FakeSession([])
    session.send_message = AsyncMock(return_value={"accepted": True})
    clock = _FakeCorrelationClock()
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(messages, "_assistant_correlation_monotonic", clock.monotonic)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))

    async def produce_events():
        for event in events:
            await asyncio.sleep(0.001)
            clock.advance(0.006)
            session._events.put_nowait(event)

    producer = asyncio.create_task(produce_events())
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    try:
        await asyncio.wait_for(drain(response.stream), timeout=0.2)
    finally:
        await producer
    assert clock.now > messages.EVENT_IDLE_TIMEOUT_SECONDS
    assert captured["controller"].errors == []
    assert [_text(message) for message in _assistant_rows(captured)] == [
        "first part onefirst part two", "second reply",
    ]


@pytest.mark.asyncio
async def test_foreign_events_do_not_reset_untagged_correlation_deadline(monkeypatch):
    session = _FakeSession([])
    session.send_message = AsyncMock(return_value={"accepted": True})
    clock = _FakeCorrelationClock()
    monkeypatch.setattr(messages, "EVENT_IDLE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(messages, "_assistant_correlation_monotonic", clock.monotonic)
    monkeypatch.setattr(messages, "get_session_registry", lambda: _FakeRegistry(session))

    async def produce_foreign_events():
        for index in range(5):
            await asyncio.sleep(0.001)
            clock.advance(0.006)
            session._events.put_nowait({
                "type": "token", "text": f"foreign {index}", "request_id": "foreign",
            })

    producer = asyncio.create_task(produce_foreign_events())
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)
    response = await messages.assistant_transport(
        session.session_id,
        messages.AssistantRequest(commands=[
            {"type": "add-message", "message": {"id": "first", "parts": [{"type": "text", "text": "one"}]}},
            {"type": "add-message", "message": {"id": "second", "parts": [{"type": "text", "text": "two"}]}},
        ], state={"messages": []}),
    )

    try:
        await asyncio.wait_for(drain(response.stream), timeout=0.2)
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
    assert len(captured["controller"].errors) == 1
    error = captured["controller"].errors[0]
    assert "no safely identified terminal event" in error
    assert "accepted message(s) first, second may still be running" in error
    assert not _assistant_rows(captured)
