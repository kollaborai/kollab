"""Same-named agents in different projects must not cross name lookups."""

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock

import pytest

from kollabor.cli import _handle_cli_hub
from kollabor_agent.runtime import AgentRuntime
from plugins.hub import presence
from plugins.hub.plugin import HubPlugin


@pytest.fixture
def collision(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "shared"
    root.mkdir(mode=0o700)
    local = root / "this-project"
    other = root / "other-project"
    local.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    monkeypatch.setattr(presence, "_system_home_directory", lambda: home)
    monkeypatch.setattr(presence, "hub_socket_root", lambda: root)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", root)
    monkeypatch.setattr(presence, "get_presence_dir", lambda: local)
    for directory, agent_id, heartbeat in ((local, "lapis-id", 1), (other, "lapis-id", 2)):
        record = {"agent_id": agent_id, "identity": "lapis", "pid": os.getpid(),
                  "socket_path": str(directory / "lapis.sock"),
                  "last_heartbeat": time.time() + heartbeat}
        (directory / f"{agent_id}.json").write_text(json.dumps(record))
    identity = AgentRuntime(agent_id="caller", identity="caller")
    plugin = HubPlugin()
    plugin._identity = identity
    plugin._presence = presence.PresenceManager(identity)
    return plugin, local, other


def test_peer_name_lookup_stays_in_project(collision):
    plugin, local, _ = collision
    assert plugin._get_peer_by_identity("lapis").socket_path == str(local / "lapis.sock")


def test_named_stop_stays_in_project(collision, monkeypatch):
    plugin, local, _ = collision
    stop = AsyncMock(return_value="stopped")
    monkeypatch.setattr(plugin, "_stop_peer_agent", stop)
    asyncio.run(plugin._handle_stop_command("lapis"))
    assert stop.await_count == 1
    assert stop.await_args.args[0].socket_path == str(local / "lapis.sock")


def test_cli_message_stays_in_project(collision, monkeypatch):
    _, local, _ = collision
    send = AsyncMock(return_value=True)
    monkeypatch.setattr("plugins.hub.messenger.AgentMessenger.send_to_agent", send)
    asyncio.run(_handle_cli_hub(["msg", "lapis", "hello"]))
    assert send.await_count == 1
    assert send.await_args.args[0] == str(local / "lapis.sock")
