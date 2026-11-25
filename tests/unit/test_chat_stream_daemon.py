"""Daemon socket actions for the ordered chat stream (#1451)."""

from __future__ import annotations

import pytest
from kollabor_events.chat_stream import sequence_problems
from kollabor_rpc import RpcRequest, RpcServer

from kollabor.state.chat_stream import ChatStreamService, register_chat_stream_handlers


class _QueueRuntime:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.queued: set[str] = set()

    async def add_input(self, params):
        self.calls.append(("add", params))
        self.queued.add(params["id"])
        return {"accepted": True, "id": params["id"], "state": "queued"}

    async def cancel_queued_input(self, params):
        self.calls.append(("cancel", params))
        found = params["id"] in self.queued
        if found:
            self.queued.remove(params["id"])
        return {
            "found": found,
            "cancelled": found,
        }

    async def send_now(self, params):
        self.calls.append(("send-now", params))
        found = params["id"] in self.queued
        if found:
            self.queued.remove(params["id"])
        return {
            "found": found,
            "accepted": found,
            "state": "joined",
            "turn_id": "turn-1",
        }


class _Bus:
    def __init__(self, queue_runtime):
        self.queue_runtime = queue_runtime
        self.live_events = []

    def get_service(self, name):
        if name == "web_message_queue_runtime":
            return self.queue_runtime
        if name == "display_tap":
            return self
        return None

    def publish(self, event):
        self.live_events.append(event)


class _State:
    def __init__(self):
        self._queue_runtime = _QueueRuntime()
        self._event_bus = _Bus(self._queue_runtime)
        self._llm_service = type("LLM", (), {"conversation_history": []})()

    def get_conversation_uid(self):
        return "conv-1"

    def chat_stream_snapshot(self):
        return {"rows": [], "turns": [], "inputs": [], "older": None}


async def _call(server, method, params):
    response = await server.handle_request(
        RpcRequest(request_id=f"{method}-{len(params)}", method=method, params=params)
    )
    assert response.error is None
    return response.result


@pytest.mark.asyncio
async def test_socket_actions_admit_cancel_and_send_one_input_by_client_id():
    state = _State()
    stream = ChatStreamService(state)
    rpc = RpcServer()
    register_chat_stream_handlers(rpc, stream)

    added = await _call(
        rpc,
        "state.chat_stream.add_input",
        {"id": "web-a1", "text": "hello", "attachments": []},
    )
    assert added == {"id": "web-a1", "state": "queued"}
    assert state._queue_runtime.calls[0] == (
        "add",
        {"id": "web-a1", "text": "hello", "attachments": []},
    )

    initial = await _call(
        rpc,
        "state.chat_stream.subscribe",
        {"after": None, "conversation_uid": None},
    )
    replay = await _call(
        rpc,
        "state.chat_stream.subscribe",
        {"after": 0, "conversation_uid": "conv-1"},
    )
    assert initial["events"][0]["type"] == "snapshot"
    assert [event["type"] for event in replay["events"]] == [
        "input.state",
        "row.upsert",
    ]
    assert replay["events"][1]["data"]["id"] == "web-a1"
    assert (
        sequence_problems([initial["events"][0], *replay["events"]], complete=False)
        == []
    )

    cancelled = await _call(
        rpc,
        "state.chat_stream.cancel_queued_input",
        {"id": "web-a1"},
    )
    assert cancelled == {"found": True, "cancelled": True}
    assert state._queue_runtime.calls[-1] == ("cancel", {"id": "web-a1"})

    added_for_send = await _call(
        rpc,
        "state.chat_stream.add_input",
        {"id": "web-a2", "text": "send this now", "attachments": []},
    )
    assert added_for_send == {"id": "web-a2", "state": "queued"}
    sent = await _call(rpc, "state.chat_stream.send_now", {"id": "web-a2"})
    assert sent == {"found": True, "state": "joined", "turn_id": "turn-1"}
    assert state._queue_runtime.calls[-1] == ("send-now", {"id": "web-a2"})


@pytest.mark.asyncio
async def test_socket_cancel_reports_unknown_input_without_mutating_the_log():
    state = _State()
    stream = ChatStreamService(state)
    rpc = RpcServer()
    register_chat_stream_handlers(rpc, stream)

    result = await _call(
        rpc,
        "state.chat_stream.cancel_queued_input",
        {"id": "missing"},
    )

    assert result == {"found": False, "cancelled": False}
    assert state._queue_runtime.calls == [("cancel", {"id": "missing"})]


@pytest.mark.asyncio
async def test_resending_the_same_input_id_returns_current_state():
    state = _State()
    stream = ChatStreamService(state)
    rpc = RpcServer()
    register_chat_stream_handlers(rpc, stream)
    params = {"id": "web-a1", "text": "hello", "attachments": []}

    first = await _call(rpc, "state.chat_stream.add_input", params)
    second = await _call(rpc, "state.chat_stream.add_input", params)

    assert first == second == {"id": "web-a1", "state": "queued"}
    assert [kind for kind, _params in state._queue_runtime.calls] == ["add"]
