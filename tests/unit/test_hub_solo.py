"""Solo agents: present for their host (the engine), invisible to the mesh.

Engine sessions whose bundle sets ``"hub": false`` run with KOLLAB_HUB_SOLO.
They keep presence + socket so the engine can attach, but must never see,
message, or be reached by peers -- otherwise one web user's input is
broadcast into every other session and agent on the machine.
"""

import json
import os
import time
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.agent_manager import Agent
from kollabor_agent.runtime import AgentRuntime
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin
from plugins.hub.presence import PresenceManager


def _publish(presence_dir, identity, solo=False):
    rt = AgentRuntime(name="t", identity=identity, solo=solo)
    rt.agent_id = identity
    rt.pid = os.getpid()
    rt.last_heartbeat = time.time()
    (presence_dir / f"{identity}.json").write_text(json.dumps(rt.to_presence_dict()))
    return rt


def _view(presence_dir, me):
    pm = PresenceManager.__new__(PresenceManager)
    pm.identity = me
    pm._presence_dir = presence_dir
    pm._presence_file = presence_dir / f"{me.agent_id}.json"
    pm._cached_agents = []
    pm._cache_time = 0.0
    return pm


@pytest.mark.asyncio
async def test_discovery_hides_solo_agents_both_ways(tmp_path):
    ruby = _publish(tmp_path, "ruby")
    _publish(tmp_path, "jade")
    bismuth = _publish(tmp_path, "bismuth", solo=True)

    mesh = _view(tmp_path, ruby)
    assert [a.identity for a in mesh.discover_agents()] == ["jade"]
    assert [a.identity for a in await mesh.discover_agents_async()] == ["jade"]
    # Identity claims still see every holder; the roster cache does not.
    claimed = await mesh.discover_agents_async(include_solo=True)
    assert {a.identity for a in claimed} == {"jade", "bismuth"}
    assert [a.identity for a in mesh.get_cached_agents()] == ["jade"]

    solo = _view(tmp_path, bismuth)
    assert solo.discover_agents() == []
    assert {a.identity for a in solo.discover_agents(include_solo=True)} == {
        "ruby",
        "jade",
    }


@pytest.mark.asyncio
async def test_solo_agent_drops_hub_messages():
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._identity = AgentRuntime(name="t", identity="bismuth", solo=True)
    plugin._relay_agent = MagicMock(defer_local=AsyncMock(return_value=False))

    await plugin._on_message_received(
        HubMessage(
            action="message",
            from_agent="x",
            from_identity="ruby",
            to="bismuth",
            content="hi",
        )
    )

    plugin._relay_agent.defer_local.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("solo", [True, False])
async def test_solo_agent_registers_only_state_hooks(solo):
    bus = MagicMock(register_hook=AsyncMock())
    plugin = HubPlugin(event_bus=bus)
    plugin._identity = AgentRuntime(name="t", identity="bismuth", solo=solo)
    plugin._cli_args = Namespace(attach=True)  # skip the hub start task

    with patch.object(plugin, "_is_enabled", return_value=True):
        await plugin.register_hooks()

    names = {call.args[0].name for call in bus.register_hook.await_args_list}
    assert {"hub_working_state", "hub_idle_state"} <= names
    assert ("hub_user_broadcast" in names) is not solo
    assert ("hub_roster_inject" in names) is not solo


def test_off_mesh_agents_get_no_hub_docs(tmp_path, monkeypatch):
    for key in ("KOLLAB_HUB_SOLO", "KOLLAB_HUB_DISABLED", "KOLLAB_NO_HUB"):
        monkeypatch.delenv(key, raising=False)
    system = tmp_path / "system"
    system.mkdir()
    (system / "hub-collaboration.md").write_text("HUB")
    (system / "other.md").write_text("OTHER")
    renderer = MagicMock(render=lambda text: text)
    agent = Agent(name="t", directory=tmp_path, system_prompt="")

    with (
        patch(
            "kollabor_config.config_utils.get_global_agents_dir",
            return_value=tmp_path,
        ),
        patch("kollabor_config.config_utils.get_local_agents_dir", return_value=None),
    ):
        assert "HUB" in agent._load_system_docs(renderer)
        monkeypatch.setenv("KOLLAB_HUB_SOLO", "1")
        assert agent._load_system_docs(renderer) == "OTHER"


def test_hub_tags_render_nothing_for_a_solo_hub():
    from kollabor_ai.prompt_renderer import PromptRenderer

    hub = HubPlugin(event_bus=MagicMock())
    hub._identity = AgentRuntime(name="t", identity="bismuth", solo=True)
    bus = MagicMock(get_service=lambda name: hub if name == "hub_plugin" else None)
    renderer = PromptRenderer(event_bus=bus)

    assert renderer.render('<trender type="hub_identity" />').strip() == ""
    hub._identity.solo = False
    assert "bismuth" in renderer.render('<trender type="hub_identity" />')
