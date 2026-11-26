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
