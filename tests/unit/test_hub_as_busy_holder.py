"""`--as <name>` must not take an identity a live but busy agent holds (#673).

A busy agent (event loop stuck on a long turn) has a live pid, a stale
heartbeat and a socket that does not answer. Presence discovery keeps its files
(#545) but leaves it out of the roster, so the spawn guard (#38) never saw it
and a second process took the same identity.

These tests run the real ``PresenceManager`` and the real ``ping_agent`` against
real presence files; only process exit and the end of identity assignment are
stubbed.
"""

import json
import os
import time
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest
from kollabor_agent.runtime import AgentRuntime
from plugins.hub.plugin import HubPlugin
from plugins.hub.presence import PresenceManager


class _Stop(Exception):
    """Raised by the preliminary publish: identity assignment is done.

    ``_start_hub`` logs it as a failed startup and returns, which ends the run
    right after the identity is chosen.
    """


def _busy_holder(tmp_path, monkeypatch, identity="jasper", heartbeat_age=60):
    presence, sockets = tmp_path / "presence", tmp_path / "sockets"
    presence.mkdir()
    sockets.mkdir()
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: presence)
    monkeypatch.setattr("plugins.hub.presence.get_socket_dir", lambda: sockets)
    sock = sockets / f"{identity}.sock"
    sock.write_text("")  # nothing answers on it, as when the agent is stuck
    holder = AgentRuntime(name="coder", identity=identity)
    data = holder.to_presence_dict()
    data.update(pid=os.getpid(), socket_path=str(sock), last_heartbeat=time.time() - heartbeat_age)
    presence_file = presence / f"{holder.agent_id}.json"
    presence_file.write_text(json.dumps(data))
    return presence_file


def _plugin(as_identity=None, configured=""):
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._cli_args = Namespace(as_identity=as_identity, attach=None)
    plugin._identity = AgentRuntime(name="coder")
    plugin._identity.agent_id = "newcomer00001"
    plugin._presence = PresenceManager(plugin._identity)
    plugin._presence.publish = MagicMock(side_effect=_Stop)
    plugin._election = MagicMock()
    plugin._election.try_become_coordinator.return_value = False
    plugin.config = MagicMock()
    plugin.config.get.side_effect = lambda key, default="": (
        configured if key == "plugins.hub.identity" else default
    )
    plugin.event_bus.get_service.return_value = None
    return plugin


@pytest.mark.asyncio
async def test_explicit_as_refuses_identity_held_by_busy_agent(tmp_path, monkeypatch):
    holder_file = _busy_holder(tmp_path, monkeypatch)
    plugin = _plugin(as_identity="jasper")

    with patch("os._exit", side_effect=SystemExit(1)) as mock_exit:
        with pytest.raises(SystemExit):
            await plugin._start_hub()

    mock_exit.assert_called_once_with(1)
    assert holder_file.exists(), "the busy agent's presence file must stay"


@pytest.mark.asyncio
async def test_implicit_identity_skips_busy_agent(tmp_path, monkeypatch):
    holder_file = _busy_holder(tmp_path, monkeypatch)
    plugin = _plugin(configured="jasper")

    with patch("os._exit") as mock_exit:
        await plugin._start_hub()

    mock_exit.assert_not_called()
    assert plugin._identity.identity != "jasper"
    assert holder_file.exists()


@pytest.mark.asyncio
async def test_explicit_as_reclaims_identity_of_agent_gone_for_good(tmp_path, monkeypatch):
    """A holder silent past the grace window is stale: its identity is free."""
    holder_file = _busy_holder(
        tmp_path, monkeypatch, heartbeat_age=PresenceManager.LIVE_PID_GRACE_SECONDS + 60
    )
    plugin = _plugin(as_identity="jasper")

    with patch("os._exit") as mock_exit:
        await plugin._start_hub()

    mock_exit.assert_not_called()
    assert plugin._identity.identity == "jasper"
    assert not holder_file.exists()
