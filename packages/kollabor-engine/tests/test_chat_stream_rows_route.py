"""Older chat stream rows route behavior for issue #1451."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from kollabor_engine import server
from kollabor_engine.routes import stream


def _row(row_id: str, seq: int) -> dict:
    return {
        "id": row_id,
        "seq": seq,
        "kind": "input",
        "role": "user",
        "turn_id": None,
        "status": "ended",
        "parts": [{"type": "text", "text": row_id}],
        "created_at": "2026-11-25T12:00:00Z",
    }


class _Rpc:
    def __init__(self, responses=None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}

    async def call(self, method: str, params: dict, *, timeout: float | None = None):
        self.calls.append((method, params))
        return self.responses.get(params.get("before"), {"rows": [], "older": None})


class _Session:
    def __init__(self, responses=None):
        self.rpc = _Rpc(responses)
        self.daemon = SimpleNamespace(rpc=self.rpc)
        self.queue: asyncio.Queue = asyncio.Queue()

    def subscribe(self):
        return self.queue

    def unsubscribe(self, queue):
        assert queue is self.queue


def _install_client(monkeypatch, *, bypass_auth: bool, responses=None):
    registry = {"s1": _Session(responses)}
    monkeypatch.setattr(server, "get_session_registry", lambda: registry)
    monkeypatch.setattr(stream, "get_session_registry", lambda: registry)
    if bypass_auth:
        monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    else:
        monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    return TestClient(server.create_app()), registry["s1"]


def test_rows_route_pages_back_to_start_in_chronological_order(monkeypatch):
    responses = {
        None: {"rows": [_row("row-3", 3), _row("row-4", 4)], "older": "cursor-3"},
        "cursor-3": {
            "rows": [_row("row-1", 1), _row("row-2", 2)],
            "older": None,
        },
    }
    client, session = _install_client(
        monkeypatch, bypass_auth=True, responses=responses
    )

    recent = client.get("/sessions/s1/rows?limit=2")
    older = client.get("/sessions/s1/rows?before=cursor-3&limit=2")

    assert recent.status_code == older.status_code == 200
    assert recent.json() == {
        "rows": [_row("row-3", 3), _row("row-4", 4)],
        "older": "cursor-3",
    }
    assert older.json() == {
        "rows": [_row("row-1", 1), _row("row-2", 2)],
        "older": None,
    }
    assert session.rpc.calls == [
        ("state.chat_stream.rows", {"before": None, "limit": 2}),
        ("state.chat_stream.rows", {"before": "cursor-3", "limit": 2}),
    ]


def test_rows_route_returns_an_empty_page(monkeypatch):
    client, session = _install_client(
        monkeypatch,
        bypass_auth=True,
        responses={None: {"rows": [], "older": None}},
    )

    response = client.get("/sessions/s1/rows")

    assert response.status_code == 200
    assert response.json() == {"rows": [], "older": None}
    assert session.rpc.calls == [
        ("state.chat_stream.rows", {"before": None, "limit": 50})
    ]


def test_rows_route_materializes_queue_image_refs_for_authenticated_fetch(monkeypatch):
    row = _row("web-image-1", 1)
    row["parts"] = [
        {
            "type": "image",
            "url": (
                "kollab-queue-image:?conversation_uid=conversation-a"
                "&request_id=web-image-1&ref=att-ref"
            ),
        }
    ]
    client, _session = _install_client(
        monkeypatch,
        bypass_auth=True,
        responses={None: {"rows": [row], "older": None}},
    )

    response = client.get("/sessions/s1/rows")

    assert response.status_code == 200
    assert response.json()["rows"][0]["parts"][0]["url"] == (
        "/sessions/s1/web-message-queue/items/web-image-1/attachments/att-ref"
        "?conversation_uid=conversation-a"
    )


def test_rows_route_materializes_tool_output_preview_without_touching_result(monkeypatch):
    row = _row("tool-row-1", 1)
    row.update({"kind": "tool", "role": "assistant"})
    user_result = {
        "kind": "chat-stream-tool-output-preview",
        "payload": "ordinary result data",
        "session_id": "user-owned-value",
    }
    row["parts"] = [
        {
            "type": "tool-call",
            "tool_call_id": "call-1",
            "result": user_result,
            "chat_stream_result_preview": {
                "kind": "chat-stream-tool-output-preview",
                "row_id": "tool-row-1",
                "tool_call_id": "call-1",
                "output_bytes": 100,
                "output_chars": 100,
                "output_type": "text",
                "preview": "preview",
            },
        }
    ]
    client, _session = _install_client(
        monkeypatch,
        bypass_auth=True,
        responses={None: {"rows": [row], "older": None}},
    )

    response = client.get("/sessions/s1/rows")

    assert response.status_code == 200
    part = response.json()["rows"][0]["parts"][0]
    assert part["result"] == user_result
    assert part["chat_stream_result_preview"]["session_id"] == "s1"


def test_rows_route_materializes_text_and_args_content_preview_session_identity(
    monkeypatch,
):
    row = _row("content-row-1", 1)
    row.update({"kind": "reply", "role": "assistant"})
    row["parts"] = [
        {
            "type": "text",
            "text": "preview",
            "chat_stream_content_preview": {
                "kind": "chat-stream-content-preview",
                "row_id": "content-row-1",
                "part_index": 0,
                "field": "text",
                "output_bytes": 100,
                "output_chars": 100,
                "output_type": "text",
                "output_revision": "a" * 64,
                "preview": "preview",
            },
        },
        {
            "type": "tool-call",
            "tool_call_id": "call-1",
            "tool_name": "read",
            "args": {
                "kind": "chat-stream-content-preview",
                "field": "args",
                "payload": "ordinary user data",
            },
            "chat_stream_args_preview": {
                "kind": "chat-stream-content-preview",
                "row_id": "content-row-1",
                "part_index": 1,
                "field": "args",
                "output_bytes": 100,
                "output_chars": 100,
                "output_type": "json",
                "output_revision": "b" * 64,
                "preview": "{}",
            },
        },
    ]
    client, _session = _install_client(
        monkeypatch,
        bypass_auth=True,
        responses={None: {"rows": [row], "older": None}},
    )

    response = client.get("/sessions/s1/rows")

    assert response.status_code == 200
    parts = response.json()["rows"][0]["parts"]
    assert parts[0]["chat_stream_content_preview"]["session_id"] == "s1"
    assert parts[1]["args"] == {
        "kind": "chat-stream-content-preview",
        "field": "args",
        "payload": "ordinary user data",
    }
    assert parts[1]["chat_stream_args_preview"]["session_id"] == "s1"


def test_rows_route_materializes_row_and_part_page_marker_session_identity(monkeypatch):
    row = _row("large-row-1", 1)
    row["chat_stream_parts_preview"] = {
        "kind": "chat-stream-parts-preview",
        "row_id": row["id"],
        "part_count": 300,
        "output_bytes": 300_000,
        "output_chars": 300_000,
        "output_type": "json",
        "output_revision": "a" * 64,
    }
    row["parts"] = [
        {
            "type": "text",
            "text": "[Large part. Expand to load full content.]",
            "chat_stream_part_preview": {
                "kind": "chat-stream-part-preview",
                "row_id": row["id"],
                "part_index": 0,
                "output_bytes": 200_000,
                "output_chars": 200_000,
                "output_type": "json",
                "output_revision": "b" * 64,
                "preview": "{\"type\":\"prompt\"",
            },
        }
    ]
    client, _session = _install_client(
        monkeypatch,
        bypass_auth=True,
        responses={None: {"rows": [row], "older": None}},
    )

    response = client.get("/sessions/s1/rows")

    assert response.status_code == 200
    result = response.json()["rows"][0]
    assert result["chat_stream_parts_preview"]["session_id"] == "s1"
    assert result["parts"][0]["chat_stream_part_preview"]["session_id"] == "s1"


def test_rows_route_returns_409_for_a_stale_cursor_after_compaction(monkeypatch):
    client, _session = _install_client(
        monkeypatch,
        bypass_auth=True,
        responses={"stale": {"error": "stale_cursor"}},
    )

    response = client.get("/sessions/s1/rows?before=stale")

    assert response.status_code == 409


def test_rows_route_returns_409_for_a_cursor_from_another_conversation(monkeypatch):
    client, _session = _install_client(
        monkeypatch,
        bypass_auth=True,
        responses={"other-conversation": {"error": "conversation_changed"}},
    )

    response = client.get("/sessions/s1/rows?before=other-conversation")

    assert response.status_code == 409


def test_rows_route_returns_404_for_an_unknown_session(monkeypatch):
    client, _session = _install_client(monkeypatch, bypass_auth=True)

    response = client.get("/sessions/missing/rows")

    assert response.status_code == 404


def test_rows_route_requires_the_engine_bearer_token(monkeypatch):
    client, _session = _install_client(monkeypatch, bypass_auth=False)

    response = client.get("/sessions/s1/rows")

    assert response.status_code == 401
