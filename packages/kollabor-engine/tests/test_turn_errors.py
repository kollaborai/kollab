"""A failed turn's error outlives the turn: the engine keeps the last one so a
reloaded page can show why the turn ended (GET /sessions/{id}/history)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
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
