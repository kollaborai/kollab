"""Engine HTTP bridge for the daemon-owned chat stream (#1451)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from kollabor_engine import server
from kollabor_engine.routes import stream

from kollabor.state.chat_stream import ChatStreamService, register_chat_stream_handlers
from kollabor_rpc import RpcRequest, RpcServer


class _Rpc:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.send_now_result = {
            "found": True,
            "accepted": True,
            "state": "started",
            "turn_id": "turn-1",
        }
        self.subscribe_results = []

    async def call(self, method: str, params: dict, *, timeout: float | None = None):
        self.calls.append((method, params))
        if method == "state.chat_stream.subscribe":
            if self.subscribe_results:
                return self.subscribe_results.pop(0)
            return {
                "events": [
                    {
                        "seq": 7,
                        "conversation_uid": "conv-1",
                        "type": "snapshot",
                        "data": {
                            "rows": [],
                            "next_seq": 8,
                            "older": None,
                            "turns": [],
                            "inputs": [],
                        },
                    }
                ]
            }
        if method == "state.chat_stream.add_input":
            return {"id": params["id"], "state": "queued"}
        if method == "state.chat_stream.cancel_queued_input":
            return {"found": False, "cancelled": False}
        if method == "state.chat_stream.send_now":
            return self.send_now_result
        raise AssertionError(f"unexpected daemon method: {method}")


class _Session:
    def __init__(self):
        self.rpc = _Rpc()
        self.daemon = SimpleNamespace(rpc=self.rpc)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.queue.put_nowait(None)

    def subscribe(self):
        return self.queue

    def unsubscribe(self, queue):
        assert queue is self.queue


def _install_client(monkeypatch, *, bypass_auth: bool):
    registry = {"s1": _Session()}
    monkeypatch.setattr(server, "get_session_registry", lambda: registry)
    monkeypatch.setattr(stream, "get_session_registry", lambda: registry)
    if bypass_auth:
        monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    else:
        monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    return TestClient(server.create_app()), registry["s1"]


def test_stream_route_requires_the_engine_bearer_token(monkeypatch):
    client, _session = _install_client(monkeypatch, bypass_auth=False)

    response = client.get("/sessions/s1/stream")

    assert response.status_code == 401


def test_stream_route_forwards_cursor_and_sends_whole_events_as_sse(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)

    response = client.get("/sessions/s1/stream?after=6&conversation_uid=conv-1")

    assert response.status_code == 200
    data_lines = [
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert json.loads(data_lines[0]) == {
        "seq": 7,
        "conversation_uid": "conv-1",
        "type": "snapshot",
        "data": {
            "rows": [],
            "next_seq": 8,
            "older": None,
            "turns": [],
            "inputs": [],
        },
    }
    assert session.rpc.calls == [
        (
            "state.chat_stream.subscribe",
            {"after": 6, "conversation_uid": "conv-1"},
        )
    ]


def test_stream_refreshes_the_snapshot_when_live_events_switch_conversations(
    monkeypatch,
):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 7,
                    "conversation_uid": "conv-1",
                    "type": "snapshot",
                    "data": {
                        "rows": [],
                        "next_seq": 8,
                        "older": None,
                        "turns": [],
                        "inputs": [],
                    },
                }
            ]
        },
        {
            "events": [
                {
                    "seq": 0,
                    "conversation_uid": "conv-2",
                    "type": "snapshot",
                    "data": {
                        "rows": [],
                        "next_seq": 2,
                        "older": None,
                        "turns": [],
                        "inputs": [],
                    },
                }
            ]
        },
    ]
    session.queue = asyncio.Queue()
    session.queue.put_nowait(
        {
            "type": "chat_stream",
            "event": {
                "seq": 1,
                "conversation_uid": "conv-2",
                "type": "row.upsert",
                "data": {"id": "input-2"},
            },
        }
    )
    session.queue.put_nowait(None)

    response = client.get("/sessions/s1/stream?after=6&conversation_uid=conv-1")

    assert response.status_code == 200
    data_lines = [
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
    ]
    events = [json.loads(line) for line in data_lines]
    assert [event["conversation_uid"] for event in events] == ["conv-1", "conv-2"]
    assert events[1]["type"] == "snapshot"
    assert session.rpc.calls == [
        (
            "state.chat_stream.subscribe",
            {"after": 6, "conversation_uid": "conv-1"},
        ),
        (
            "state.chat_stream.subscribe",
            {"after": None, "conversation_uid": "conv-2"},
        ),
    ]


def test_input_route_returns_202_with_the_sender_id_and_state(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)

    response = client.post(
        "/sessions/s1/inputs",
        json={"id": "web-a1", "text": "hello", "attachments": []},
    )

    assert response.status_code == 202
    assert response.json() == {"id": "web-a1", "state": "queued"}
    assert session.rpc.calls == [
        (
            "state.chat_stream.add_input",
            {"id": "web-a1", "text": "hello", "attachments": []},
        )
    ]


def test_cancel_route_returns_404_for_an_unknown_input(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)

    response = client.post("/sessions/s1/inputs/missing/cancel")

    assert response.status_code == 404
    assert session.rpc.calls == [
        (
            "state.chat_stream.cancel_queued_input",
            {"id": "missing"},
        )
    ]


def test_send_now_route_returns_202_with_the_started_turn(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)

    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert response.status_code == 202
    assert response.json() == {
        "id": "web-a1",
        "state": "started",
        "turn_id": "turn-1",
    }
    assert session.rpc.calls == [("state.chat_stream.send_now", {"id": "web-a1"})]


class _LiveQueueRuntime:
    async def add_input(self, params):
        return {"accepted": True, "id": params["id"], "state": "queued"}

    async def send_now(self, params):
        return {
            "found": True,
            "accepted": True,
            "state": "started",
            "turn_id": "turn-1",
        }


def test_live_queue_runtime_supplies_conversation_scoped_identity():
    scope = asyncio.run(_LiveQueueRuntime().input_scope("conv-1"))
    assert scope == ("test-scope", "conv-1", "test-principal")


class _LiveBus:
    def __init__(self):
        self.runtime = _LiveQueueRuntime()

    def get_service(self, name):
        return self.runtime if name == "web_message_queue_runtime" else None


class _DispatchingRpc:
    def __init__(self, server):
        self.server = server
        self.next_request_id = 0
        self.calls = []

    async def call(self, method, params, *, timeout=None):
        self.calls.append((method, params))
        self.next_request_id += 1
        response = await self.server.handle_request(
            RpcRequest(
                request_id=f"engine-{self.next_request_id}",
                method=method,
                params=params,
            )
        )
        assert response.error is None
        return response.result


def test_send_now_http_route_uses_registered_daemon_handler_response(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    state = SimpleNamespace(
        _event_bus=_LiveBus(),
        _llm_service=SimpleNamespace(conversation_history=[]),
        get_conversation_uid=lambda: "conv-1",
    )
    daemon_rpc = RpcServer()
    register_chat_stream_handlers(daemon_rpc, ChatStreamService(state))
    adapter = _DispatchingRpc(daemon_rpc)
    session.daemon.rpc = adapter

    admitted = client.post(
        "/sessions/s1/inputs",
        json={"id": "web-a1", "text": "send immediately", "attachments": []},
    )
    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert admitted.status_code == 202
    assert response.status_code == 202
    assert response.json() == {
        "id": "web-a1",
        "state": "started",
        "turn_id": "turn-1",
    }
    assert adapter.calls == [
        (
            "state.chat_stream.add_input",
            {"id": "web-a1", "text": "send immediately", "attachments": []},
        ),
        ("state.chat_stream.send_now", {"id": "web-a1"}),
    ]


def test_send_now_route_returns_current_state_when_input_already_moved(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.send_now_result = {
        "found": True,
        "accepted": False,
        "state": "joined",
        "turn_id": "turn-7",
    }

    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert response.status_code == 200
    assert response.json() == {
        "id": "web-a1",
        "state": "joined",
        "turn_id": "turn-7",
    }


def test_send_now_route_returns_404_for_an_unknown_input(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.send_now_result = {"found": False, "accepted": False}

    response = client.post("/sessions/s1/inputs/missing/send-now")

    assert response.status_code == 404


def test_send_now_route_returns_409_when_input_is_not_queued(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.send_now_result = {
        "found": True,
        "accepted": False,
        "state": "queued",
        "turn_id": None,
    }

    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert response.status_code == 409
