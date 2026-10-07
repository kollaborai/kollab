"""POST /sessions/{id}/profile: the session reports the daemon's live effort and vision support."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.session import EngineSession  # type: ignore[import-not-found]

import kollabor_ai
from kollabor.state.snapshots import ProfileSnapshot


@pytest_asyncio.fixture
async def client():
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def live_session():
    """A real EngineSession (so to_dict is the real one) over a stub daemon."""
    from kollabor_engine.server import get_session_registry  # type: ignore[import-not-found]

    session = EngineSession(
        session_id="sess_profile",
        profile=SimpleNamespace(name="fake", model="fake-model", provider="custom"),
    )
    session.daemon = SimpleNamespace(
        alive=True,
        identity="",
        agent_name="",
        pid=1,
        state=SimpleNamespace(set_active_profile=AsyncMock()),
    )
    registry = get_session_registry()
    registry[session.session_id] = session
    yield session
    registry.pop(session.session_id, None)


def _mirror_profile(monkeypatch, **fields):
    profile = SimpleNamespace(**fields)
    monkeypatch.setattr(
        kollabor_ai,
        "ProfileManager",
        lambda: SimpleNamespace(get_profile=lambda name: profile),
    )
    return profile


def test_a_fresh_session_reports_no_effort_and_no_vision(live_session):
    state = live_session.to_dict()
    assert state["effort"] == ""
    assert state["supports_vision"] is False


@pytest.mark.asyncio
async def test_apply_reports_the_daemons_effort_and_model(
    client, live_session, monkeypatch
):
    _mirror_profile(
        monkeypatch, name="gem", model="stale", provider="gemini", effort=""
    )
    live_session.state.set_active_profile.return_value = ProfileSnapshot(
        name="gem", model="gemini-2.5-pro", provider="gemini", effort="high"
    )

    response = await client.post(
        "/sessions/sess_profile/profile", json={"name": "gem", "effort": "high"}
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["profile"], body["model"], body["effort"]) == (
        "gem",
        "gemini-2.5-pro",
        "high",
    )
    assert body["supports_vision"] is True
    assert live_session.to_dict()["effort"] == "high"


@pytest.mark.asyncio
async def test_switching_back_keeps_the_override_the_daemon_still_holds(
    client, live_session, monkeypatch
):
    # No effort in the request, but the daemon's in-memory profile still has it.
    _mirror_profile(
        monkeypatch, name="fake", model="fake-model", provider="custom", effort=""
    )
    live_session.state.set_active_profile.return_value = ProfileSnapshot(
        name="fake", model="fake-model", provider="custom", effort="max"
    )

    response = await client.post("/sessions/sess_profile/profile", json={"name": "fake"})

    assert response.json()["effort"] == "max"
    assert response.json()["supports_vision"] is False
