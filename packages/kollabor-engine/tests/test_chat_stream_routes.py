"""Engine HTTP bridge for the daemon-owned chat stream (#1451)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
                result = self.subscribe_results.pop(0)
                for event in result.get("events", []):
                    event.setdefault("generation", "daemon-1")
                return result
            return {
                "events": [
                    {
                        "seq": 7,
                        "conversation_uid": "conv-1",
                        "generation": "daemon-1",
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

    response = client.get(
        "/sessions/s1/stream?after=6&conversation_uid=conv-1&generation=daemon-1"
    )

    assert response.status_code == 200
    data_lines = [
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert json.loads(data_lines[0]) == {
        "seq": 7,
        "conversation_uid": "conv-1",
        "generation": "daemon-1",
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
            {"after": 6, "conversation_uid": "conv-1", "generation": "daemon-1"},
        )
    ]


def test_stream_route_forwards_generation_and_daemon_restart_returns_snapshot(
    monkeypatch,
):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 0,
                    "conversation_uid": "conv-1",
                    "generation": "daemon-after-restart",
                    "type": "snapshot",
                    "data": {
                        "rows": [],
                        "next_seq": 1,
                        "older": None,
                        "turns": [],
                        "inputs": [],
                    },
                }
            ]
        }
    ]

    response = client.get(
        "/sessions/s1/stream?after=6&conversation_uid=conv-1&generation=daemon-before-restart"
    )

    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert events[0]["type"] == "snapshot"
    assert events[0]["generation"] == "daemon-after-restart"
    assert session.rpc.calls == [
        (
            "state.chat_stream.subscribe",
            {
                "after": 6,
                "conversation_uid": "conv-1",
                "generation": "daemon-before-restart",
            },
        )
    ]


def test_stream_materializes_queue_image_refs_as_authenticated_attachment_paths(
    monkeypatch,
):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 0,
                    "conversation_uid": "conversation-a",
                    "generation": "daemon-1",
                    "type": "snapshot",
                    "data": {
                        "rows": [
                            {
                                "id": "web-image-1",
                                "seq": 0,
                                "kind": "input",
                                "role": "user",
                                "turn_id": None,
                                "status": "queued",
                                "parts": [
                                    {
                                        "type": "image",
                                        "url": (
                                            "kollab-queue-image:?conversation_uid="
                                            "conversation-a&request_id=web-image-1&ref=att-ref"
                                        ),
                                    }
                                ],
                                "created_at": "2026-11-25T12:00:00Z",
                            }
                        ],
                        "next_seq": 1,
                        "older": None,
                        "turns": [],
                        "inputs": [],
                    },
                }
            ]
        }
    ]

    response = client.get("/sessions/s1/stream")
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert response.status_code == 200
    assert events[0]["data"]["rows"][0]["parts"][0]["url"] == (
        "/sessions/s1/web-message-queue/items/web-image-1/attachments/att-ref"
        "?conversation_uid=conversation-a"
    )


def test_live_stream_materializes_queue_image_refs_before_sending_sse(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.queue = asyncio.Queue()
    session.queue.put_nowait(
        {
            "type": "chat_stream",
            "event": {
                "seq": 8,
                "conversation_uid": "conv-1",
                "generation": "daemon-1",
                "type": "row.upsert",
                "data": {
                    "id": "live-web-image",
                    "seq": 8,
                    "kind": "input",
                    "role": "user",
                    "turn_id": None,
                    "status": "queued",
                    "parts": [
                        {
                            "type": "image",
                            "url": (
                                "kollab-queue-image:?conversation_uid=conv-1"
                                "&request_id=live-web-image&ref=live-att-ref"
                            ),
                        }
                    ],
                    "created_at": "2026-11-25T12:00:00Z",
                },
            },
        }
    )
    session.queue.put_nowait(None)

    response = client.get("/sessions/s1/stream")
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    live = next(event for event in events if event["type"] == "row.upsert")
    assert live["data"]["parts"][0]["url"] == (
        "/sessions/s1/web-message-queue/items/live-web-image/attachments/live-att-ref"
        "?conversation_uid=conv-1"
    )


def test_legacy_after_without_identity_forces_a_snapshot_request(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 0,
                    "conversation_uid": "conv-1",
                    "generation": "daemon-1",
                    "type": "snapshot",
                    "data": {
                        "rows": [],
                        "next_seq": 1,
                        "older": None,
                        "turns": [],
                        "inputs": [],
                    },
                }
            ]
        }
    ]

    response = client.get("/sessions/s1/stream?after=6")

    assert response.status_code == 200
    assert session.rpc.calls == [
        (
            "state.chat_stream.subscribe",
            {"after": None, "conversation_uid": None, "generation": None},
        )
    ]


def test_engine_reconnect_replays_same_generation_after_requested_cursor(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 7,
                    "conversation_uid": "conv-1",
                    "generation": "daemon-1",
                    "type": "row.upsert",
                    "data": {"id": "replayed"},
                }
            ]
        }
    ]
    session.queue = asyncio.Queue()
    session.queue.put_nowait(
        {
            "type": "chat_stream",
            "event": {
                "seq": 8,
                "conversation_uid": "conv-1",
                "generation": "daemon-1",
                "type": "row.upsert",
                "data": {"id": "live"},
            },
        }
    )
    session.queue.put_nowait(None)

    response = client.get(
        "/sessions/s1/stream?after=6&conversation_uid=conv-1&generation=daemon-1"
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert [event["seq"] for event in events] == [7, 8]
    assert [event["data"]["id"] for event in events] == ["replayed", "live"]
    assert session.rpc.calls == [
        (
            "state.chat_stream.subscribe",
            {
                "after": 6,
                "conversation_uid": "conv-1",
                "generation": "daemon-1",
            },
        )
    ]


def test_live_daemon_generation_change_refreshes_snapshot_before_replay(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 0,
                    "conversation_uid": "conv-1",
                    "generation": "daemon-1",
                    "type": "snapshot",
                    "data": {
                        "rows": [],
                        "next_seq": 1,
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
                    "conversation_uid": "conv-1",
                    "generation": "daemon-2",
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
                "conversation_uid": "conv-1",
                "generation": "daemon-2",
                "type": "row.upsert",
                "data": {"id": "included-in-snapshot"},
            },
        }
    )
    session.queue.put_nowait(None)

    response = client.get(
        "/sessions/s1/stream?after=0&conversation_uid=conv-1&generation=daemon-1"
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert [event["generation"] for event in events] == ["daemon-1", "daemon-2"]
    assert events[-1]["type"] == "snapshot"
    assert session.rpc.calls[-1] == (
        "state.chat_stream.subscribe",
        {"after": None, "conversation_uid": "conv-1", "generation": "daemon-2"},
    )


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
                "generation": "daemon-1",
                "type": "row.upsert",
                "data": {"id": "input-2"},
            },
        }
    )
    session.queue.put_nowait(None)

    response = client.get(
        "/sessions/s1/stream?after=6&conversation_uid=conv-1&generation=daemon-1"
    )

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
            {"after": 6, "conversation_uid": "conv-1", "generation": "daemon-1"},
        ),
        (
            "state.chat_stream.subscribe",
            {"after": None, "conversation_uid": "conv-2", "generation": "daemon-1"},
        ),
    ]


def test_stream_discards_stale_event_when_daemon_snapshot_is_newer_conversation(
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
                    "conversation_uid": "conv-3",
                    "type": "snapshot",
                    "data": {
                        "rows": [],
                        "next_seq": 4,
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
                "seq": 9,
                "conversation_uid": "conv-2",
                "generation": "daemon-1",
                "type": "row.upsert",
                "data": {"id": "stale-input"},
            },
        }
    )
    session.queue.put_nowait(None)

    response = client.get(
        "/sessions/s1/stream?after=6&conversation_uid=conv-1&generation=daemon-1"
    )

    assert response.status_code == 200
    data_lines = [
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
    ]
    events = [json.loads(line) for line in data_lines]
    assert [event["conversation_uid"] for event in events] == ["conv-1", "conv-3"]
    assert events[1]["type"] == "snapshot"
    assert session.rpc.calls == [
        (
            "state.chat_stream.subscribe",
            {"after": 6, "conversation_uid": "conv-1", "generation": "daemon-1"},
        ),
        (
            "state.chat_stream.subscribe",
            {"after": None, "conversation_uid": "conv-2", "generation": "daemon-1"},
        ),
    ]


def test_stream_replays_from_requested_cursor_after_empty_initial_replay(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.subscribe_results = [
        {"events": []},
        {
            "events": [
                {
                    "seq": 7,
                    "conversation_uid": "conv-1",
                    "type": "row.upsert",
                    "data": {"id": "replayed-input"},
                }
            ]
        },
    ]
    session.queue = asyncio.Queue()
    session.queue.put_nowait(
        {
            "type": "chat_stream",
            "event": {
                "seq": 8,
                "conversation_uid": "conv-1",
                "generation": "daemon-1",
                "type": "row.upsert",
                "data": {"id": "live-input"},
            },
        }
    )
    session.queue.put_nowait(None)

    response = client.get(
        "/sessions/s1/stream?after=6&conversation_uid=conv-1&generation=daemon-1"
    )

    assert response.status_code == 200
    data_lines = [
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
    ]
    events = [json.loads(line) for line in data_lines]
    assert [event["seq"] for event in events] == [7, 8]
    assert session.rpc.calls == [
        (
            "state.chat_stream.subscribe",
            {"after": 6, "conversation_uid": "conv-1", "generation": "daemon-1"},
        ),
        (
            "state.chat_stream.subscribe",
            {"after": 6, "conversation_uid": "conv-1", "generation": "daemon-1"},
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
    async def input_scope(self, conversation_uid):
        return ("test-scope", conversation_uid, "test-principal")

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
        "state": "ended",
        "turn_id": None,
    }

    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert response.status_code == 409
    assert response.json()["detail"] == "Input is no longer queued"


def test_send_now_route_explains_that_queued_input_remains_queued(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.send_now_result = {
        "found": True,
        "accepted": False,
        "state": "queued",
        "turn_id": None,
    }

    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert response.status_code == 409
    assert response.json()["detail"] == "Input could not be sent now; it stays queued."


def test_send_now_route_returns_503_when_dispatch_is_unavailable(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.send_now_result = {
        "found": True,
        "accepted": False,
        "state": "queued",
        "reason": "dispatch_unavailable",
    }

    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Queue dispatch is unavailable; the message remains queued."
    )


def test_send_now_spec_documents_dispatch_unavailable_response():
    repo_root = Path(__file__).resolve().parents[3]
    spec = (repo_root / "docs/specs/webui-chat-single-stream.md").read_text(
        encoding="utf-8"
    )
    send_now_contract = next(
        line
        for line in spec.splitlines()
        if line.startswith("`POST /sessions/{id}/inputs/{input_id}/send-now`")
    )

    assert (
        "When queue dispatch is unavailable, the input stays queued and the route "
        "answers `503`."
        in send_now_contract
    )


def test_send_now_route_prefers_a_recorded_started_turn_over_unavailable_dispatch(
    monkeypatch,
):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.send_now_result = {
        "found": True,
        "accepted": False,
        "state": "started",
        "turn_id": "native-turn-1",
        "reason": "dispatch_unavailable",
    }

    response = client.post("/sessions/s1/inputs/web-a1/send-now")

    assert response.status_code == 200
    assert response.json() == {
        "id": "web-a1",
        "state": "started",
        "turn_id": "native-turn-1",
    }


def test_goal_input_passes_private_capability_to_registered_daemon(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    bus = _LiveBus()
    captured = []

    async def admit(params):
        captured.append(params)
        return {"accepted": True, "id": params["id"], "state": "queued"}

    bus.runtime.add_input = admit
    state = SimpleNamespace(
        _event_bus=bus,
        _llm_service=SimpleNamespace(conversation_history=[]),
        get_conversation_uid=lambda: "conv-1",
    )
    daemon_rpc = RpcServer()
    register_chat_stream_handlers(daemon_rpc, ChatStreamService(state))
    session.daemon.rpc = _DispatchingRpc(daemon_rpc)
    capability = "goal-" + "a" * 32

    response = client.post(
        "/sessions/s1/inputs",
        json={"id": "web-goal-1", "text": "/goal show", "attachments": [],
              "goal_reply_id": capability},
    )

    assert response.status_code == 202
    assert response.json() == {"id": "web-goal-1", "state": "queued"}
    assert capability not in response.text
    assert captured[0]["goal_reply_id"] == capability


def test_private_goal_poll_route_returns_only_the_owned_result(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    capability = "goal-" + "a" * 32
    session.poll_goal_reply = AsyncMock(return_value={
        "status": "complete",
        "conversation_uid": "conv-1",
        "success": True,
        "message": "private goal result",
        "goal_reply_id": capability,
    })

    response = client.post(
        "/sessions/s1/goal-replies/poll",
        json={"goal_reply_id": capability},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "complete",
        "success": True,
        "message": "private goal result",
    }
    assert capability not in response.text
    session.poll_goal_reply.assert_awaited_once_with(capability)


def test_send_now_after_cancelling_input_returns_409_not_404(monkeypatch):
    class CancelledInputRuntime:
        async def input_scope(self, conversation_uid):
            return ("test-scope", conversation_uid, "test-principal")

        async def add_input(self, params):
            return {"accepted": True, "id": params["id"], "state": "queued"}

        async def cancel_queued_input(self, params):
            return {"found": True, "cancelled": True}

        async def send_now(self, params):
            return {"found": False, "accepted": False}

    client, session = _install_client(monkeypatch, bypass_auth=True)
    runtime = CancelledInputRuntime()
    bus = SimpleNamespace(
        get_service=lambda name: (
            runtime if name == "web_message_queue_runtime" else None
        )
    )
    state = SimpleNamespace(
        _event_bus=bus,
        _llm_service=SimpleNamespace(conversation_history=[]),
        get_conversation_uid=lambda: "conv-1",
    )
    daemon_rpc = RpcServer()
    register_chat_stream_handlers(daemon_rpc, ChatStreamService(state))
    session.daemon.rpc = _DispatchingRpc(daemon_rpc)

    added = client.post(
        "/sessions/s1/inputs",
        json={"id": "web-cancelled", "text": "hello", "attachments": []},
    )
    cancelled = client.post("/sessions/s1/inputs/web-cancelled/cancel")
    sent = client.post("/sessions/s1/inputs/web-cancelled/send-now")

    assert added.status_code == 202
    assert cancelled.status_code == 200
    assert sent.status_code == 409


def test_stream_binds_phone_context_and_stops_when_revoked(monkeypatch):
    from kollabor_engine.session_context import (
        SESSION_READ_CONTEXT_SCOPE_KEY,
        SessionReadContext,
    )

    context = SessionReadContext(owner_id="owner", phone_id="phone")
    session = _Session()
    seen = []

    def subscribe(*, context=None):
        seen.append(context)
        return session.queue

    session.subscribe = subscribe
    monkeypatch.setattr(stream, "_get_rpc", lambda _session_id: (session, session.rpc))
    request = SimpleNamespace(scope={"state": {SESSION_READ_CONTEXT_SCOPE_KEY: context}})

    async def run():
        response = await stream.chat_stream("s1", request, after=None, conversation_uid=None, generation=None)
        context.revoked = True
        return [item async for item in response.body_iterator]

    assert asyncio.run(run()) == []
    assert seen == [context]


def test_stream_ends_on_daemon_closed_frame(monkeypatch):
    session = _Session()
    session.queue = asyncio.Queue()
    session.queue.put_nowait({"type": "daemon_closed", "session_id": "s1"})
    monkeypatch.setattr(stream, "_get_rpc", lambda _session_id: (session, session.rpc))

    async def disconnected():
        return False

    request = SimpleNamespace(scope={}, is_disconnected=disconnected)

    async def run():
        response = await stream.chat_stream("s1", request, after=None, conversation_uid=None, generation=None)
        iterator = response.body_iterator
        first = await anext(iterator)
        try:
            next_item = await asyncio.wait_for(anext(iterator), timeout=0.1)
        except StopAsyncIteration:
            next_item = None
        await iterator.aclose()
        return first, next_item

    first, next_item = asyncio.run(run())
    assert first is not None
    assert next_item is None


def test_stream_recovers_dropped_tail_even_without_a_later_chat_event(monkeypatch):
    from kollabor_engine.daemon_pool import DaemonEventQueue

    session = _Session()
    session.queue = DaemonEventQueue()
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 7,
                    "conversation_uid": "conv-1",
                    "generation": "daemon-1",
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
                    "seq": 8,
                    "conversation_uid": "conv-1",
                    "generation": "daemon-1",
                    "type": "row.delete",
                    "data": {"id": "lost"},
                }
            ]
        },
    ]
    monkeypatch.setattr(stream, "_get_rpc", lambda _session_id: (session, session.rpc))

    async def disconnected():
        return False

    request = SimpleNamespace(scope={}, is_disconnected=disconnected)

    async def run():
        response = await stream.chat_stream("s1", request, after=None, conversation_uid=None, generation=None)
        iterator = response.body_iterator
        await anext(iterator)
        session.queue.note_drop({"type": "chat_stream"})
        session.queue.put_nowait({"type": "daemon_closed", "session_id": "s1"})
        try:
            recovered = await asyncio.wait_for(anext(iterator), timeout=0.1)
        except (TimeoutError, StopAsyncIteration):
            recovered = None
        await iterator.aclose()
        return recovered

    recovered = asyncio.run(run())
    assert recovered is not None
    assert json.loads(recovered["data"])["seq"] == 8
