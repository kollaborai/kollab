"""A failed turn's error outlives the turn: the engine keeps the last one so a
reloaded page can show why the turn ended (GET /sessions/{id}/history)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine import history_paging
from kollabor_engine.session import EngineSession  # type: ignore[import-not-found]


@pytest_asyncio.fixture
async def client():
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def session():
    """A real EngineSession over a stub daemon whose conversation has 3 messages."""
    from kollabor_engine.server import get_session_registry  # type: ignore[import-not-found]

    events: asyncio.Queue = asyncio.Queue()
    conversation = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]
    session = EngineSession(
        session_id="sess_turn_errors",
        profile=SimpleNamespace(name="fake", model="fake-model", provider="custom"),
    )
    session.daemon = SimpleNamespace(
        alive=True,
        identity="",
        agent_name="",
        pid=1,
        subscribe=lambda: events,
        unsubscribe=lambda queue: None,
        fan_out=lambda event: None,
        mark_cancelled=lambda: None,
        state=SimpleNamespace(
            get_conversation=AsyncMock(
                return_value=SimpleNamespace(messages=conversation)
            ),
            restart_session=AsyncMock(),
        ),
    )
    session.events = events
    registry = get_session_registry()
    registry[session.session_id] = session
    yield session
    registry.pop(session.session_id, None)


@pytest.mark.asyncio
async def test_the_last_failed_turn_is_remembered_until_a_turn_succeeds(session):
    tracker = asyncio.create_task(session._track_events())
    session.events.put_nowait({"type": "error", "message": "provider down"})
    session.events.put_nowait({"type": "turn_complete"})
    await asyncio.sleep(0.05)

    assert session.last_turn_error == {"message": "provider down", "history_length": 3}

    session.events.put_nowait({"type": "turn_complete"})
    session.events.put_nowait({"type": "daemon_closed"})
    await asyncio.wait_for(tracker, 1)

    assert session.last_turn_error is None


@pytest.mark.asyncio
async def test_history_returns_the_turn_error_in_place(client, session):
    session.last_turn_error = {"message": "provider down", "history_length": 3}

    body = (await client.get(f"/sessions/{session.session_id}/history")).json()
    for _ in range(100):
        if len(session.history) == 3:
            break
        await asyncio.sleep(0.02)
    assert len(session.history) == 3

    body = (await client.get(f"/sessions/{session.session_id}/history")).json()
    assert len(body["history"]) == 3
    assert body["last_turn_error"] == {"message": "provider down", "history_length": 3}

    # A limited slice keeps the error after the same message.
    body = (await client.get(f"/sessions/{session.session_id}/history?limit=2")).json()
    assert len(body["history"]) == 2
    assert body["last_turn_error"]["history_length"] == 2


@pytest.mark.asyncio
async def test_clearing_the_history_clears_the_turn_error(client, session):
    session.last_turn_error = {"message": "provider down", "history_length": 3}

    await client.delete(f"/sessions/{session.session_id}/history")

    assert session.last_turn_error is None
    body = (await client.get(f"/sessions/{session.session_id}/history")).json()
    assert body["last_turn_error"] is None


@pytest.mark.asyncio
async def test_history_route_defaults_to_bounded_newest_page(client, session, monkeypatch):
    monkeypatch.setattr(history_paging, "_CURSOR_SECRET", b"x" * 32)

    # Entirely synthetic: 219 ordinary messages plus one huge tool result make
    # an approximately 1.8 MB session without reading any real conversation.
    history = [
        {
            "role": "user",
            "content": f"synthetic-message-{index}-" + ("x" * 2_700),
            "metadata": {},
        }
        for index in range(219)
    ]
    history.append(
        {
            "role": "tool",
            "content": "synthetic-tool-output-" + ("y" * 1_200_000),
            "metadata": {"tool_call_id": "synthetic-call-219"},
        }
    )
    assert len(history) == 220
    history_size = len(
        json.dumps(history, separators=(",", ":")).encode("utf-8")
    )
    assert history_size >= 1_800_000

    session._schedule_refresh = lambda notify=True: None
    session.history = history

    response = await client.get(f"/sessions/{session.session_id}/history")
    body = response.json()

    assert response.status_code == 200
    assert len(body["history"]) == 50
    assert body["history"][0]["content"].startswith("synthetic-message-170-")
    tool_message = body["history"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["metadata"]["history_output_truncated"] is True
    assert tool_message["metadata"]["history_output_ref"]
    assert len(response.content) <= 150_000
    assert body["has_more_older"] is True
    assert body["older_cursor"]
    assert body["newer_cursor"]

    output_response = await client.get(
        f"/sessions/{session.session_id}/history/output",
        params={"ref": tool_message["metadata"]["history_output_ref"]},
    )
    assert output_response.status_code == 200
    assert output_response.json()["content"] == history[-1]["content"]


@pytest.mark.asyncio
async def test_cancel_turn_tells_open_windows(session):
    """A cancelled tool call may never report a result: the stream says so (#305)."""
    seen = []
    session.daemon.fan_out = seen.append
    session.daemon.state.cancel_current_request = AsyncMock()

    session.cancel_turn()
    await asyncio.sleep(0)

    assert {"type": "turn_cancelled"} in seen
