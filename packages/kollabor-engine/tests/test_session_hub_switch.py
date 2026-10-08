"""PATCH /sessions/{id} {"hub": bool}: the session's agent leaves the hub mesh
or rejoins it, live (the web UI's Properties switch)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.session import EngineSession  # type: ignore[import-not-found]


@pytest_asyncio.fixture
async def client(monkeypatch):
    import kollabor_engine.auth as engine_auth  # type: ignore[import-not-found]
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    monkeypatch.setattr(engine_auth, "validate_token", lambda token: token == "t")
    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": "Bearer t"},
    ) as ac:
        yield ac


@pytest.fixture
def live_session():
    from kollabor_engine.server import get_session_registry  # type: ignore[import-not-found]

    session = EngineSession(
        session_id="sess_hub",
        profile=SimpleNamespace(name="fake", model="fake-model", provider="custom"),
    )
    session.daemon = SimpleNamespace(
        alive=True,
        identity="lapis",
        agent_name="",
        pid=1,
        state=SimpleNamespace(set_hub_participation=AsyncMock()),
    )
    registry = get_session_registry()
    registry[session.session_id] = session
    yield session
    registry.pop(session.session_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_the_switch_reaches_the_daemon(client, live_session, enabled):
    response = await client.patch("/sessions/sess_hub", json={"hub": enabled})

    assert response.status_code == 200
    assert response.json()["hub"] is enabled
    live_session.state.set_hub_participation.assert_awaited_once_with(enabled)
    assert live_session.solo is not enabled


@pytest.mark.asyncio
async def test_hub_must_be_true_or_false(client, live_session):
    response = await client.patch("/sessions/sess_hub", json={"hub": "off"})

    assert response.status_code == 400
    live_session.state.set_hub_participation.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stopped_daemon_cannot_switch(client, live_session):
    live_session.daemon.alive = False

    response = await client.patch("/sessions/sess_hub", json={"hub": False})

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_a_daemon_without_a_hub_says_so(client, live_session):
    live_session.state.set_hub_participation.side_effect = ValueError("this agent runs no hub")

    response = await client.patch("/sessions/sess_hub", json={"hub": False})

    assert response.status_code == 400
    assert response.json()["detail"] == "this agent runs no hub"
    assert live_session.solo is False
