"""A restart must not announce again what the human was already told about."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.hub.relay_commands import RelayCommands
from plugins.hub.relay_state import RelayStateStore

_JOIN = "a1" * 16
_KNOCK = "b2" * 16


class _Bridge:
    """Just enough of the agent bridge for `new_arrivals`: the pending join requests."""

    def __init__(self):
        self.identity = SimpleNamespace(agent_id="local-agent")
        self.rows = []
        self.unreadable = False

    def pending_enrollment_requests(self, *, source_agent):
        if self.unreadable:
            raise RuntimeError("the issuer is not ready")
        return list(self.rows)


def _row(enrollment_id, name):
    return SimpleNamespace(
        enrollment_id=enrollment_id, device_name=name, decision_available=True
    )


def _commands(tmp_path, bridge, *, knocks=(), online=True):
    """A commands object over the shared state dir; `knocks` is what the directory lists."""
    commands = RelayCommands(
        tmp_path, state_dir=tmp_path / "state", agent_bridge=bridge
    )
    commands._domain_online = lambda: ("relay.example", online)
    commands._knock_count = AsyncMock(return_value=len(knocks))
    commands._knock_rows = tuple(knocks)
    commands._knocks_fetched = online
    return commands


def _saved(tmp_path):
    return RelayStateStore(tmp_path, tmp_path / "state").state.announced


@pytest.mark.asyncio
async def test_a_restart_announces_only_what_is_new(tmp_path):
    bridge = _Bridge()
    bridge.rows = [_row(_JOIN, "ana-laptop")]
    knocks = [(_KNOCK, "bo-phone")]

    first = await _commands(tmp_path, bridge, knocks=knocks).new_arrivals()
    assert len(first) == 2

    # A daemon restart: a new commands object over the same state dir.
    restarted = _commands(tmp_path, bridge, knocks=knocks)
    assert await restarted.new_arrivals() == []

    bridge.rows.append(_row("c3" * 16, "cy-desktop"))
    fresh = await restarted.new_arrivals()
    assert len(fresh) == 1 and "cy-desktop" in fresh[0]
    assert await restarted.new_arrivals() == []


@pytest.mark.asyncio
async def test_only_ids_that_are_still_pending_are_kept(tmp_path):
    bridge = _Bridge()
    bridge.rows = [_row(_JOIN, "ana-laptop")]
    knocks = [(_KNOCK, "bo-phone")]
    await _commands(tmp_path, bridge, knocks=knocks).new_arrivals()
    assert sorted(_saved(tmp_path)) == [f"join:{_JOIN}", f"knock:{_KNOCK}"]

    # Both were decided meanwhile: nothing pending, nothing remembered.
    bridge.rows = []
    await _commands(tmp_path, bridge).new_arrivals()
    assert _saved(tmp_path) == []


@pytest.mark.asyncio
async def test_what_could_not_be_read_keeps_its_ids(tmp_path):
    bridge = _Bridge()
    bridge.rows = [_row(_JOIN, "ana-laptop")]
    knocks = [(_KNOCK, "bo-phone")]
    await _commands(tmp_path, bridge, knocks=knocks).new_arrivals()

    # Restarted while the issuer is not ready and the directory is unreachable:
    # an empty reading is not "decided", so nothing is forgotten, nothing repeats.
    bridge.unreadable = True
    cold = _commands(tmp_path, bridge, online=False)
    assert await cold.new_arrivals() == []
    assert sorted(_saved(tmp_path)) == [f"join:{_JOIN}", f"knock:{_KNOCK}"]
