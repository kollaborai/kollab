"""Assistant transport must pass prompt IDs through the real EngineSession API."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_engine.routes import messages
from kollabor_engine.session import EngineSession
from test_assistant_route import _FakeRegistry, _install_fake_assistant_stream


@pytest.mark.asyncio
async def test_multisend_false_permission_rpc_reconciles_through_engine_session(
    monkeypatch,
):
    """This stays red until EngineSession accepts and forwards permission_id (#1372)."""
    session_id = "multisend-permission-rpc"
    permission_id = "prompt-first"
    events: asyncio.Queue = asyncio.Queue()
    for event in (
        {
            "type": "permission_request",
            "tool_id": "shell-first",
            "tool_name": "terminal",
            "request_id": "first",
            "permission_id": permission_id,
        },
        {
            "type": "permission_granted",
            "tool_id": "shell-first",
            "request_id": "first",
            "permission_id": permission_id,
        },
        {"type": "turn_complete", "request_id": "first"},
        {"type": "token", "text": "second reply", "request_id": "second"},
        {"type": "turn_complete", "request_id": "second"},
    ):
        events.put_nowait(event)

    daemon_rpc = SimpleNamespace(call=AsyncMock(return_value={"ok": True}))
    engine_state = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=lambda _content, **kwargs: {
                "accepted": True,
                "request_id": kwargs["request_id"],
            }
        )
    )
    daemon = SimpleNamespace(
        alive=True,
        rpc=daemon_rpc,
        state=engine_state,
        subscribe=lambda **_kwargs: events,
        unsubscribe=lambda _queue: None,
        fan_out=lambda _event: None,
        mark_cancelled=lambda: None,
        identity="local-daemon",
        agent_name="test-agent",
        pid=1,
    )
    session = EngineSession(
        session_id=session_id,
        profile=SimpleNamespace(name="test", model="test", provider="custom"),
    )
    session.daemon = daemon
    session._pending_permissions["shell-first"] = {
        "type": "permission_request",
        "tool_id": "shell-first",
        "request_id": "first",
        "permission_id": permission_id,
    }
    session.refresh_history = AsyncMock(return_value=[])
    monkeypatch.setattr(
        messages, "get_session_registry", lambda: _FakeRegistry(session)
    )
    captured, _, drain = _install_fake_assistant_stream(monkeypatch)

    first = await messages.assistant_transport(
        session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-message",
                    "message": {
                        "id": "first",
                        "parts": [{"type": "text", "text": "one"}],
                    },
                },
                {
                    "type": "add-message",
                    "message": {
                        "id": "second",
                        "parts": [{"type": "text", "text": "two"}],
                    },
                },
            ],
            state={"messages": []},
        ),
    )
    await asyncio.wait_for(drain(first.stream), timeout=1)
    state = captured["controller"].state

    followup = await messages.assistant_transport(
        session_id,
        messages.AssistantRequest(
            commands=[
                {
                    "type": "add-tool-result",
                    "toolCallId": "permission_shell-first",
                    "result": {
                        "decision": "approve",
                        "scope": "once",
                        "permission_id": permission_id,
                    },
                }
            ],
            state=state,
        ),
    )
    await asyncio.wait_for(drain(followup.stream), timeout=1)

    assert daemon_rpc.call.await_count == 1
    assert daemon_rpc.call.await_args.args[1]["permission_id"] == permission_id
    assert engine_state.send_message.await_count == 2
    assert captured["controller"].errors == []
    assert captured["controller"].state["usage"]["stopReason"] == "end_turn"
    assert [
        (row["role"], messages.content_to_text(row.get("content")))
        for row in captured["controller"].state["messages"]
    ] == [
        ("user", "one"),
        ("assistant", ""),
        ("user", "two"),
        ("assistant", "second reply"),
    ]
