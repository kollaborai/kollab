"""The web sidebar's network block: this computer's name and the remote agents."""

from types import SimpleNamespace

import pytest
from kollabor_engine.routes import sessions as sessions_route

REMOTE = [{"name": "lapis", "device": "prod-box", "handle": "lapis@prod-box", "state": "idle"}]


class DaemonState:
    """Stands in for a daemon's state RPC: answers get_hub_state or raises."""

    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot
        self.error = error

    async def get_hub_state(self):
        if self.error is not None:
            raise self.error
        return self.snapshot


def daemon(session_id, state, solo=False):
    return SimpleNamespace(
        session_id=session_id,
        alive=True,
        solo=solo,
        state=state,
        to_dict=lambda: {"session_id": session_id},
    )


@pytest.fixture
def registry(monkeypatch):
    """The engine's session registry, with no detached daemons from presence."""
    monkeypatch.setattr(
        sessions_route.HubBridge, "discover_sessions", lambda self, use_cache=False: []
    )
    live = sessions_route.get_session_registry()
    added = []

    def add(session):
        live[session.session_id] = session
        added.append(session.session_id)

    yield add
    for session_id in added:
        live.pop(session_id, None)


@pytest.mark.asyncio
async def test_network_block_names_this_computer_and_its_remote_agents(registry):
    registry(daemon("net-ok", DaemonState(SimpleNamespace(device="devbox", remote=REMOTE))))

    result = await sessions_route.list_sessions()

    assert result["network"] == {"device": "devbox", "remote": REMOTE}


@pytest.mark.asyncio
async def test_a_daemon_that_fails_is_skipped_for_one_that_answers(registry):
    registry(daemon("net-broken", DaemonState(error=RuntimeError("daemon down"))))
    registry(daemon("net-ok", DaemonState(SimpleNamespace(device="devbox", remote=REMOTE))))

    result = await sessions_route.list_sessions()

    assert result["network"]["remote"] == REMOTE


@pytest.mark.asyncio
async def test_solo_daemon_has_no_relay_so_it_is_not_asked(registry, monkeypatch):
    monkeypatch.setattr(sessions_route.socket, "gethostname", lambda: "devbox.local")
    registry(
        daemon("net-solo", DaemonState(SimpleNamespace(device="other", remote=REMOTE)), solo=True)
    )

    result = await sessions_route.list_sessions()

    assert result["network"] == {"device": "devbox", "remote": []}


@pytest.mark.asyncio
async def test_no_answer_means_no_remote_rows(registry, monkeypatch):
    monkeypatch.setattr(sessions_route.socket, "gethostname", lambda: "devbox.local")
    registry(daemon("net-down", DaemonState(error=RuntimeError("daemon down"))))

    result = await sessions_route.list_sessions()

    assert result["network"] == {"device": "devbox", "remote": []}
