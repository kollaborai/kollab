"""#968: POST /sessions/{id}/restart-agent asks the chat's daemon to restart itself in place."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.session import EngineSession  # type: ignore[import-not-found]


@pytest_asyncio.fixture
async def client():
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


@pytest.fixture
def make_session():
    from kollabor_engine.server import get_session_registry  # type: ignore[import-not-found]

    registry = get_session_registry()
    made = []

    def make(restart_agent):
        session = EngineSession(
            session_id="sess_restart_agent",
            profile=SimpleNamespace(name="fake", model="fake-model", provider="custom"),
        )
        session.daemon = SimpleNamespace(
            alive=True, identity="", agent_name="", pid=1, state=SimpleNamespace(restart_agent=restart_agent)
        )
        registry[session.session_id] = session
        made.append(session.session_id)
        return session

    yield make
    for session_id in made:
        registry.pop(session_id, None)


@pytest.mark.asyncio
async def test_asks_the_chats_daemon_to_restart_and_says_whether_it_waits(client, make_session):
    restart = AsyncMock(return_value={"restarting": True, "waiting_for_turn": True})
    session = make_session(restart)

    response = await client.post(f"/sessions/{session.session_id}/restart-agent")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "session_id": session.session_id,
        "restarting": True,
        "waiting_for_turn": True,
    }
    restart.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_daemon_that_cannot_restart_answers_409_with_why(client, make_session):
    session = make_session(AsyncMock(side_effect=ValueError("this window runs without a daemon")))

    response = await client.post(f"/sessions/{session.session_id}/restart-agent")

    assert response.status_code == 409
    assert "without a daemon" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unknown_chat_is_404(client):
    assert (await client.post("/sessions/nope/restart-agent")).status_code == 404
