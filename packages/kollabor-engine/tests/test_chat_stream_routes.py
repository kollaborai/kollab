"""Engine HTTP bridge for the daemon-owned chat stream (#1451)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from kollabor_engine import auth, server
from kollabor_engine.daemon_pool import REMOTE_STATE_TIMEOUT_SECONDS
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
        self.tool_output_result = None
        self.content_result = None
        self.parts_result = None
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
        if method == "state.chat_stream.tool_output":
            return self.tool_output_result
        if method == "state.chat_stream.content":
            return self.content_result
        if method == "state.chat_stream.parts":
            return self.parts_result
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


def test_tool_output_route_returns_a_validated_chunk(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.tool_output_result = {
        "row_id": "row-1",
        "tool_call_id": "call-1",
        "output_revision": "a" * 64,
        "offset": 4,
        "next_offset": 7,
        "total_chars": 9,
        "output_type": "text",
        "content": "abc",
        "done": False,
    }

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/tool-output",
        params={
            "tool_call_id": "call-1",
            "output_revision": "a" * 64,
            "offset": 4,
            "limit": 3,
        },
    )

    assert response.status_code == 200
    assert response.json() == session.rpc.tool_output_result
    assert session.rpc.calls[-1] == (
        "state.chat_stream.tool_output",
        {
            "row_id": "row-1",
            "tool_call_id": "call-1",
            "output_revision": "a" * 64,
            "offset": 4,
            "limit": 3,
        },
    )


def test_tool_output_route_rejects_a_changed_output_revision(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.tool_output_result = {"error": "output_changed"}

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/tool-output",
        params={"tool_call_id": "call-1", "output_revision": "a" * 64},
    )

    assert response.status_code == 409


def test_chat_content_route_returns_a_validated_chunk(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.content_result = {
        "row_id": "row-1",
        "part_index": 2,
        "field": "reasoning",
        "offset": 4,
        "next_offset": 7,
        "total_chars": 9,
        "output_type": "text",
        "output_revision": "b" * 64,
        "content": "abc",
        "done": False,
    }

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/content",
        params={
            "part_index": 2,
            "field": "reasoning",
            "output_revision": "b" * 64,
            "offset": 4,
            "limit": 3,
        },
    )

    assert response.status_code == 200
    assert response.json() == session.rpc.content_result
    assert session.rpc.calls[-1] == (
        "state.chat_stream.content",
        {
            "row_id": "row-1",
            "part_index": 2,
            "field": "reasoning",
            "output_revision": "b" * 64,
            "offset": 4,
            "limit": 3,
        },
    )


def test_chat_content_route_rejects_a_changed_output_revision(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.content_result = {"error": "output_changed"}

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/content",
        params={
            "part_index": 0,
            "field": "text",
            "output_revision": "c" * 64,
        },
    )

    assert response.status_code == 409


def test_chat_parts_route_returns_validated_page_and_materializes_marker_session(
    monkeypatch,
):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.parts_result = {
        "row_id": "row-1",
        "offset": 4,
        "next_offset": 5,
        "total_parts": 5,
        "output_revision": "d" * 64,
        "parts": [
            {
                "type": "text",
                "text": "Large part. Expand to load full content.",
                "chat_stream_part_preview": {
                    "kind": "chat-stream-part-preview",
                    "row_id": "row-1",
                    "part_index": 4,
                    "output_bytes": 200_000,
                    "output_chars": 200_000,
                    "output_type": "json",
                    "output_revision": "e" * 64,
                    "preview": "{...}",
                },
            }
        ],
        "done": True,
    }

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/parts",
        params={"output_revision": "d" * 64, "offset": 4, "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["parts"][0]["chat_stream_part_preview"]["session_id"] == "s1"
    assert session.rpc.calls[-1] == (
        "state.chat_stream.parts",
        {
            "row_id": "row-1",
            "output_revision": "d" * 64,
            "offset": 4,
            "limit": 1,
        },
    )


def test_chat_parts_route_bounds_materialized_page_and_advances_cursor(
    monkeypatch,
):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    parts = [
        {
            "type": "text",
            "text": "bounded preview",
            "chat_stream_part_preview": {
                "kind": "chat-stream-part-preview",
                "row_id": "row-1",
                "part_index": index,
                "output_bytes": 40_000,
                "output_chars": 40_000,
                "output_type": "json",
                "output_revision": "e" * 64,
                "preview": "preview",
            },
        }
        for index in range(stream._PART_PAGE_SIZE)
    ]
    raw_payload = {
        "row_id": "row-1",
        "offset": 0,
        "next_offset": len(parts),
        "total_parts": len(parts),
        "output_revision": "d" * 64,
        "parts": parts,
        "done": True,
    }
    raw_size = len(
        json.dumps(raw_payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    parts[0]["text"] += "x" * (stream._PART_PAGE_RPC_BYTES - raw_size)
    assert len(
        json.dumps(raw_payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) == stream._PART_PAGE_RPC_BYTES
    session.rpc.parts_result = raw_payload

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/parts",
        params={"output_revision": "d" * 64, "limit": stream._PART_PAGE_SIZE},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["parts"]) == payload["next_offset"] - payload["offset"]
    assert all(
        part["chat_stream_part_preview"]["session_id"] == "s1"
        for part in payload["parts"]
    )
    assert len(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) <= stream._PART_PAGE_RPC_BYTES
    assert 0 < len(payload["parts"]) < len(parts)
    assert payload["next_offset"] == len(payload["parts"])
    assert payload["done"] is False
    next_offset = payload["next_offset"]
    session.rpc.parts_result = {
        "row_id": "row-1",
        "offset": next_offset,
        "next_offset": len(parts),
        "total_parts": len(parts),
        "output_revision": "d" * 64,
        "parts": parts[next_offset:],
        "done": True,
    }

    next_response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/parts",
        params={
            "output_revision": "d" * 64,
            "offset": next_offset,
            "limit": stream._PART_PAGE_SIZE,
        },
    )

    assert next_response.status_code == 200
    next_payload = next_response.json()
    assert next_payload["offset"] == next_offset
    assert next_payload["next_offset"] == len(parts)
    assert next_payload["done"] is True
    assert len(payload["parts"]) + len(next_payload["parts"]) == len(parts)


def test_chat_parts_route_replaces_single_oversized_materialized_part(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    parts = [
        {
            "type": "text",
            "text": "bounded preview",
            "chat_stream_part_preview": {
                "kind": "chat-stream-part-preview",
                "row_id": "row-1",
                "part_index": 5,
                "output_bytes": 200_000,
                "output_chars": 200_000,
                "output_type": "json",
                "output_revision": "e" * 64,
                "preview": "preview",
            },
        }
    ]
    raw_payload = {
        "row_id": "row-1",
        "offset": 5,
        "next_offset": 6,
        "total_parts": 6,
        "output_revision": "d" * 64,
        "parts": parts,
        "done": True,
    }
    raw_size = len(
        json.dumps(raw_payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    parts[0]["text"] += "x" * (stream._PART_PAGE_RPC_BYTES - raw_size)
    assert len(
        json.dumps(raw_payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) == stream._PART_PAGE_RPC_BYTES
    session.rpc.parts_result = raw_payload

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/parts",
        params={"output_revision": "d" * 64, "offset": 5, "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["offset"] == 5
    assert payload["next_offset"] == 6
    assert payload["total_parts"] == 6
    assert payload["done"] is True
    assert len(payload["parts"]) == 1
    assert len(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) <= stream._PART_PAGE_RPC_BYTES
    assert payload["parts"][0]["text"] == (
        "[Large part. Expand to load full content.]"
    )
    assert payload["parts"][0]["chat_stream_part_preview"]["session_id"] == "s1"


def test_chat_parts_route_makes_unmarked_oversized_item_retrievable(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    original_part = {
        "type": "image",
        "url": "kollab-queue-image:?conversation_uid=c&request_id=r&ref=x",
    }
    parts = [original_part.copy()]
    raw_payload = {
        "row_id": "row-1",
        "offset": 0,
        "next_offset": 1,
        "total_parts": 1,
        "output_revision": "d" * 64,
        "parts": parts,
        "done": True,
    }
    raw_size = len(
        json.dumps(raw_payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    parts[0]["url"] += "x" * (stream._PART_PAGE_RPC_BYTES - raw_size)
    original_part = parts[0].copy()
    assert len(
        json.dumps(raw_payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) == stream._PART_PAGE_RPC_BYTES
    session.rpc.parts_result = raw_payload

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/parts",
        params={"output_revision": "d" * 64, "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_offset"] == 1
    assert payload["done"] is True
    assert len(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ) <= stream._PART_PAGE_RPC_BYTES
    marker = payload["parts"][0]["chat_stream_part_preview"]
    assert marker["kind"] == "chat-stream-part-preview"
    assert marker["session_id"] == "s1"
    canonical = json.dumps(
        original_part,
        ensure_ascii=False,
        allow_nan=False,
        default=str,
        separators=(",", ":"),
    )
    revision = hashlib.sha256(
        b"json\0" + canonical.encode("utf-8", errors="replace")
    ).hexdigest()
    assert marker["row_id"] == "row-1"
    assert marker["part_index"] == 0
    assert marker["output_bytes"] == len(canonical.encode("utf-8"))
    assert marker["output_chars"] == len(canonical)
    assert marker["output_type"] == "json"
    assert marker["output_revision"] == revision
    assert marker["preview"] == canonical[:1024]

    chunk = canonical[: stream._TOOL_OUTPUT_CHUNK_CHARS]
    session.rpc.content_result = {
        "row_id": "row-1",
        "part_index": 0,
        "field": "part",
        "offset": 0,
        "next_offset": len(chunk),
        "total_chars": len(canonical),
        "output_type": "json",
        "output_revision": revision,
        "content": chunk,
        "done": len(chunk) == len(canonical),
    }
    content_response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/content",
        params={
            "part_index": 0,
            "field": "part",
            "output_revision": revision,
            "offset": 0,
        },
    )
    assert content_response.status_code == 200
    assert session.rpc.calls[-1] == (
        "state.chat_stream.content",
        {
            "row_id": "row-1",
            "part_index": 0,
            "field": "part",
            "output_revision": revision,
            "offset": 0,
            "limit": stream._TOOL_OUTPUT_CHUNK_CHARS,
        },
    )


def test_chat_parts_route_preserves_unresolved_image_slots(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.parts_result = {
        "row_id": "row-1",
        "offset": 0,
        "next_offset": 1,
        "total_parts": 1,
        "output_revision": "d" * 64,
        "parts": [{"type": "image", "url": "kollab-queue-image:unresolvable"}],
        "done": True,
    }

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/parts",
        params={"output_revision": "d" * 64, "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["parts"] == [
        {"type": "text", "text": "[Image attachment unavailable.]"}
    ]
    assert len(payload["parts"]) == payload["next_offset"] - payload["offset"]


def test_chat_parts_route_rejects_a_changed_parts_revision(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.rpc.parts_result = {"error": "output_changed"}

    response = client.get(
        "/sessions/s1/chat-stream/rows/row-1/parts",
        params={"output_revision": "d" * 64},
    )

    assert response.status_code == 409


def test_snapshot_materializes_tool_output_marker_with_session_id(monkeypatch):
    client, session = _install_client(monkeypatch, bypass_auth=True)
    user_result = {
        "kind": "chat-stream-tool-output-preview",
        "payload": "ordinary result data",
        "session_id": "user-owned-value",
    }
    session.rpc.subscribe_results = [
        {
            "events": [
                {
                    "seq": 0,
                    "conversation_uid": "conv-1",
                    "generation": "daemon-1",
                    "type": "snapshot",
                    "data": {
                        "rows": [
                            {
                                "id": "row-1",
                                "seq": 1,
                                "kind": "tool",
                                "role": "assistant",
                                "turn_id": "turn-1",
                                "status": "completed",
                                "parts": [
                                    {
                                        "type": "tool-call",
                                        "tool_call_id": "call-1",
                                        "result": user_result,
                                        "chat_stream_result_preview": {
                                            "kind": "chat-stream-tool-output-preview",
                                            "row_id": "row-1",
                                            "tool_call_id": "call-1",
                                            "output_bytes": 100,
                                            "output_chars": 100,
                                            "output_type": "text",
                                            "output_revision": "a" * 64,
                                            "preview": "preview",
                                        },
                                    }
                                ],
                                "created_at": "2026-11-01T00:00:00Z",
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
    data_line = next(
        line[6:] for line in response.text.splitlines() if line.startswith("data: ")
    )
    part = json.loads(data_line)["data"]["rows"][0]["parts"][0]
    marker = part["chat_stream_result_preview"]

    assert response.status_code == 200
    assert part["result"] == user_result
    assert marker["session_id"] == "s1"


def test_authenticated_tool_preview_retrieval_round_trips_and_rejects_stale_revision(
    monkeypatch,
):
    token = "stream-tool-output-test-token"
    monkeypatch.setattr(auth, "_current_token", token)
    client, session = _install_client(monkeypatch, bypass_auth=False)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_manager=None, conversation_history=[]),
        get_conversation_uid=lambda: "conv-1",
    )
    stream_service = ChatStreamService(state)
    daemon_rpc = RpcServer()
    register_chat_stream_handlers(daemon_rpc, stream_service)
    adapter = _DispatchingRpc(daemon_rpc)
    session.daemon.rpc = adapter
    output = "large 🌵 output\n" + "x" * 8_500
    long_text = "assistant reply 🌵\n" * 20_000
    row = {
        "id": "tool-row-live",
        "seq": 0,
        "kind": "tool",
        "role": "assistant",
        "turn_id": "turn-tool-output",
        "status": "completed",
        "parts": [
            {
                "type": "tool-call",
                "tool_call_id": "call-live",
                "tool_name": "read",
                "args": {},
                "result": output,
            },
            {"type": "text", "text": long_text},
        ],
        "created_at": "2026-11-01T00:00:00Z",
    }
    stream_service._upsert("conv-1", row)

    unauthorized = client.get(
        "/sessions/s1/chat-stream/rows/tool-row-live/tool-output",
        params={"tool_call_id": "call-live", "output_revision": "a" * 64},
    )
    assert unauthorized.status_code == 401

    stream_response = client.get(
        "/sessions/s1/stream", headers={"Authorization": f"Bearer {token}"}
    )
    data_line = next(
        line[6:]
        for line in stream_response.text.splitlines()
        if line.startswith("data: ")
    )
    event = json.loads(data_line)
    tool_part = event["data"]["rows"][0]["parts"][0]
    tool_marker = tool_part["chat_stream_result_preview"]
    content_marker = event["data"]["rows"][0]["parts"][1][
        "chat_stream_content_preview"
    ]
    assert event["type"] == "snapshot"
    assert "result" not in tool_part
    assert tool_marker["kind"] == "chat-stream-tool-output-preview"
    assert tool_marker["session_id"] == "s1"
    assert content_marker["kind"] == "chat-stream-content-preview"
    assert content_marker["session_id"] == "s1"

    chunk = client.get(
        "/sessions/s1/chat-stream/rows/tool-row-live/tool-output",
        params={
            "tool_call_id": "call-live",
            "output_revision": tool_marker["output_revision"],
            "offset": 0,
            "limit": 32_768,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chunk.status_code == 200
    assert chunk.json()["content"] == output
    assert chunk.json()["done"] is True
    assert chunk.json()["output_revision"] == tool_marker["output_revision"]

    text_chunks = []
    text_offset = 0
    while True:
        content_chunk = client.get(
            "/sessions/s1/chat-stream/rows/tool-row-live/content",
            params={
                "part_index": 1,
                "field": "text",
                "output_revision": content_marker["output_revision"],
                "offset": text_offset,
                "limit": 32_768,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert content_chunk.status_code == 200
        payload = content_chunk.json()
        assert payload["row_id"] == "tool-row-live"
        assert payload["field"] == "text"
        assert payload["output_revision"] == content_marker["output_revision"]
        text_chunks.append(payload["content"])
        if payload["done"]:
            break
        text_offset = payload["next_offset"]
    assert "".join(text_chunks) == long_text

    changed_row = {
        **row,
        "parts": [
            dict(row["parts"][0], result="updated output"),
            {"type": "text", "text": "updated " + long_text},
        ],
    }
    stream_service._upsert("conv-1", changed_row)
    stale = client.get(
        "/sessions/s1/chat-stream/rows/tool-row-live/tool-output",
        params={
            "tool_call_id": "call-live",
            "output_revision": tool_marker["output_revision"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert stale.status_code == 409
    stale_content = client.get(
        "/sessions/s1/chat-stream/rows/tool-row-live/content",
        params={
            "part_index": 1,
            "field": "text",
            "output_revision": content_marker["output_revision"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert stale_content.status_code == 409


def test_authenticated_large_row_pages_parts_and_recovers_oversized_part(
    monkeypatch,
):
    token = "stream-large-row-test-token"
    monkeypatch.setattr(auth, "_current_token", token)
    client, session = _install_client(monkeypatch, bypass_auth=False)
    state = SimpleNamespace(
        _llm_service=SimpleNamespace(conversation_manager=None, conversation_history=[]),
        get_conversation_uid=lambda: "conv-1",
    )
    stream_service = ChatStreamService(state)
    daemon_rpc = RpcServer()
    register_chat_stream_handlers(daemon_rpc, stream_service)
    session.daemon.rpc = _DispatchingRpc(daemon_rpc)
    original_parts = [
        {"type": "image", "url": "data:image/png;base64," + "A" * 300_000},
        {"type": "prompt", "prompt_id": "prompt-large", "data": {"payload": "B" * 300_000}},
        {
            "type": "hub-note",
            "from": "agent-large",
            "to": "",
            "text": "C" * 300_000,
            "observed": False,
        },
    ]
    row = {
        "id": "large-row-live",
        "seq": 0,
        "kind": "reply",
        "role": "assistant",
        "turn_id": "turn-large-row",
        "status": "completed",
        "parts": original_parts,
        "created_at": "2026-11-01T00:00:00Z",
    }
    stream_service._upsert("conv-1", row)

    unauthorized = client.get(
        "/sessions/s1/chat-stream/rows/large-row-live/parts",
        params={"output_revision": "a" * 64},
    )
    assert unauthorized.status_code == 401

    stream_response = client.get(
        "/sessions/s1/stream", headers={"Authorization": f"Bearer {token}"}
    )
    data_line = next(
        line[6:]
        for line in stream_response.text.splitlines()
        if line.startswith("data: ")
    )
    event = json.loads(data_line)
    projected = event["data"]["rows"][0]
    marker = projected["chat_stream_parts_preview"]
    assert marker["session_id"] == "s1"
    assert len(projected["parts"]) == 1

    response = client.get(
        "/sessions/s1/chat-stream/rows/large-row-live/parts",
        params={
            "output_revision": marker["output_revision"],
            "offset": 0,
            "limit": 3,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    page = response.json()
    assert page["done"] is True
    assert len(page["parts"]) == 3
    part_marker = page["parts"][0]["chat_stream_part_preview"]
    assert part_marker["session_id"] == "s1"

    chunks = []
    offset = 0
    while True:
        chunk_response = client.get(
            "/sessions/s1/chat-stream/rows/large-row-live/content",
            params={
                "part_index": 0,
                "field": "part",
                "output_revision": part_marker["output_revision"],
                "offset": offset,
                "limit": 32_768,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert chunk_response.status_code == 200
        chunk = chunk_response.json()
        assert len(chunk["content"].encode("utf-8")) <= 32_768
        chunks.append(chunk["content"])
        if chunk["done"]:
            break
        offset = chunk["next_offset"]
    assert json.loads("".join(chunks)) == original_parts[0]

    changed = {**row, "parts": [{"type": "text", "text": "new content"}]}
    stream_service._upsert("conv-1", changed)
    stale_page = client.get(
        "/sessions/s1/chat-stream/rows/large-row-live/parts",
        params={"output_revision": marker["output_revision"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    stale_part = client.get(
        "/sessions/s1/chat-stream/rows/large-row-live/content",
        params={
            "part_index": 0,
            "field": "part",
            "output_revision": part_marker["output_revision"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert stale_page.status_code == 409
    assert stale_part.status_code == 409


def test_an_agent_on_older_code_is_reported_as_needing_a_restart(monkeypatch):
    """A daemon without the chat-stream methods runs older Kollab code: say so, with 426, not a bare 503."""
    from kollabor_rpc import RpcMethodNotFound

    client, session = _install_client(monkeypatch, bypass_auth=True)

    async def older_daemon(method, params, *, timeout=None):
        raise RpcMethodNotFound(f"method not found: {method}")

    session.rpc.call = older_daemon
    for response in (
        client.get("/sessions/s1/stream"),
        client.post("/sessions/s1/inputs", json={"id": "web-a1", "text": "hi", "attachments": []}),
        client.post("/sessions/s1/inputs/web-a1/send-now", json={}),
    ):
        assert response.status_code == 426
        assert response.json()["detail"] == stream.AGENT_NEEDS_RESTART


@pytest.mark.parametrize(
    ("state_timeout", "expected"),
    [(REMOTE_STATE_TIMEOUT_SECONDS, REMOTE_STATE_TIMEOUT_SECONDS), (None, 15.0)],
    ids=["agent-on-another-device", "local-agent"],
)
def test_chat_stream_daemon_calls_use_the_sessions_state_budget(
    monkeypatch, state_timeout, expected
):
    """A remote agent's replies cross the relay: its chat calls get its budget (#880)."""

    client, session = _install_client(monkeypatch, bypass_auth=True)
    session.daemon.state_timeout = state_timeout
    timeouts: list[tuple[str, float | None]] = []
    call = session.rpc.call

    async def recording_call(method, params, *, timeout=None):
        timeouts.append((method, timeout))
        if method == "state.chat_stream.rows":
            return {"rows": [], "older": None}
        return await call(method, params, timeout=timeout)

    session.rpc.call = recording_call

    assert client.get("/sessions/s1/stream").status_code == 200
    assert client.get("/sessions/s1/rows?before=cursor").status_code == 200
    assert (
        client.post("/sessions/s1/inputs", json={"id": "web-1", "text": "hi"}).status_code
        == 202
    )
    assert client.post("/sessions/s1/inputs/web-1/send-now").status_code == 202

    assert timeouts == [
        ("state.chat_stream.subscribe", expected),
        ("state.chat_stream.rows", expected),
        ("state.chat_stream.add_input", expected),
        ("state.chat_stream.send_now", expected),
    ]
