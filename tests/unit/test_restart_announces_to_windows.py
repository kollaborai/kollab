"""A restart in place tells the attached windows before it shuts down (#968).

The attach ack also names the version the image runs.
"""

import asyncio
import json
import os
import secrets
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_tui.display_tap import DisplayTap
from plugins.hub.messenger import AgentSocketServer


@pytest.mark.asyncio
async def test_restart_tells_the_attached_windows_before_it_shuts_down(monkeypatch):
    from plugins.hub.plugin import HubPlugin

    loop = asyncio.get_running_loop()
    order = []
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity="sunstone")
    tap = SimpleNamespace(
        publish=lambda event: order.append(("publish", event["type"], event.get("identity"), loop.time()))
    )
    plugin.event_bus = SimpleNamespace(get_service=lambda name: tap if name == "display_tap" else None)
    plugin.network_turn_open = lambda: False

    async def shutdown_and_exec():
        order.append(("shutdown", loop.time()))

    async def no_watchdog(*args):
        return None

    plugin._perform_self_restart = shutdown_and_exec
    plugin._self_restart_watchdog = no_watchdog  # the real one execs this test process away after 8 s
    monkeypatch.setattr(sys, "argv", ["kollab", "--detached", "--as", "sunstone"])
    monkeypatch.setattr(os, "environ", dict(os.environ))
    conversation = SimpleNamespace(current_session_id="s1", messages=[])
    llm = SimpleNamespace(is_processing=False, conversation_manager=conversation)

    await plugin._restart_when_idle(llm)

    assert [item[:3] for item in order if item[0] == "publish"] == [("publish", "daemon_restarting", "sunstone")]
    published, shutdown = order[0], order[1]
    assert published[0] == "publish" and shutdown[0] == "shutdown"
    assert shutdown[1] - published[3] >= 0.2  # the attach streams get time to write it out


@pytest.fixture
def socket_dir(monkeypatch):
    d = Path("/tmp") / f"kollab-attach-test-{secrets.token_hex(4)}"
    d.mkdir(mode=0o700)
    monkeypatch.setattr("plugins.hub.messenger.get_socket_dir", lambda: d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_attach_ack_names_the_version_this_image_runs(socket_dir):
    from kollabor_agent.runtime import own_build

    server = AgentSocketServer(
        agent_id="ack-version-test", on_message=None, socket_name="ack-version-" + os.urandom(4).hex()
    )
    server._display_tap = DisplayTap(history_size=10)
    path = await server.start()
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        writer.write((json.dumps({"action": "attach", "mode": "interactive", "client_id": "t1"}) + "\n").encode())
        await writer.drain()
        ack = json.loads((await asyncio.wait_for(reader.readline(), 5)).decode())
        assert ack["type"] == "attach_ack"
        assert ack["version"] == own_build()[0]
    finally:
        writer.close()
        await server.stop()
