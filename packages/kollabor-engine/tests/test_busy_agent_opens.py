"""A busy agent opens at once: nothing on the open path waits for it to answer.

A busy agent, or one on another computer whose RPC replies queue behind its
live events in the network tunnel, can take longer than the 10 s RPC timeout
to answer. Opening it in the web UI must not depend on that.
"""

import asyncio
from types import SimpleNamespace

import pytest
from kollabor_engine import session as session_module
from kollabor_engine.routes import permissions as permissions_route
from kollabor_engine.routes import sessions as sessions_route
from kollabor_engine.server import get_session_registry
from kollabor_engine.session import EngineSession

PROMPT = {"type": "permission_request", "tool_id": "t1", "tool_name": "shell"}


class BusyState:
    """A daemon's state RPCs that answer only once ``answer`` is set."""

    def __init__(self):
        self.answer = asyncio.Event()

    async def _late(self, value):
        await self.answer.wait()
        return value

    def get_permission_state(self):
        return self._late(SimpleNamespace(approval_mode="CONFIRM_ALL"))

    def get_active_profile(self):
        return self._late(SimpleNamespace(name="openai-oauth", model="gpt", provider="openai"))

    def get_system_info(self):
        return self._late(SimpleNamespace(cwd="/home/far/proj"))

    async def get_conversation(self, since=None, anchor=None):
        return await self._late(SimpleNamespace(messages=[], anchor=""))


def _daemon(state):
    events: asyncio.Queue = asyncio.Queue()
    return SimpleNamespace(
        alive=True, pid=0, identity="lapis", agent_name="", state=state, launcher=None,
        subscribe=lambda: events, unsubscribe=lambda queue: None,
    )


@pytest.fixture
def registry():
    sessions = get_session_registry()
    before = dict(sessions)
    sessions.clear()
    yield sessions
    sessions.clear()
    sessions.update(before)


@pytest.mark.asyncio
async def test_permissions_answer_from_the_engine_while_the_agent_is_busy(registry):
    session = EngineSession(session_id="lapis@far-box", profile=None, approval_mode="confirm_all")
    session.daemon = _daemon(BusyState())
    session._pending_permissions["t1"] = PROMPT  # the daemon already pushed it
    registry[session.session_id] = session

    answer = await asyncio.wait_for(permissions_route.get_permissions("lapis@far-box"), timeout=1)

    assert answer["pending_prompts"] == [PROMPT] and answer["approval_mode"] == "confirm_all"


@pytest.mark.asyncio
async def test_opening_an_agent_does_not_wait_for_what_it_runs_with(monkeypatch):
    state = BusyState()

    async def adopt(session_id, found, *, owned=False, webui_owned=False):
        assert owned is False
        assert webui_owned is False
        return _daemon(state)

    async def stop(session_id):
        return None

    monkeypatch.setattr(session_module, "get_daemon_pool", lambda: SimpleNamespace(adopt=adopt, stop=stop))
    found = {"session_id": "lapis@far-box", "identity": "lapis", "device": "far-box", "workspace": ""}

    session = await asyncio.wait_for(EngineSession.adopt(found), timeout=1)
    try:
        assert session.approval_mode == "" and session.profile is None  # not known yet

        state.answer.set()  # the agent answers later; the row fills in
        await asyncio.wait_for(session._facts_task, timeout=1)

        assert session.approval_mode == "confirm_all" and session.profile.name == "openai-oauth"
        assert session.workspace == "/home/far/proj"  # its folder on that computer
    finally:
        await session.shutdown()


@pytest.mark.asyncio
async def test_history_route_returns_cached_snapshot_while_agent_is_busy(registry):
    """A blocked history RPC must not keep the Web UI in Opening forever."""
    state = BusyState()
    session = EngineSession(session_id="lapis@far-box", profile=None)
    session.daemon = _daemon(state)
    session.history = [{"role": "user", "content": "cached"}]
    registry[session.session_id] = session

    result = await asyncio.wait_for(
        sessions_route.get_history(session.session_id), timeout=1
    )

    assert result["history"] == session.history
    assert not state.answer.is_set()
