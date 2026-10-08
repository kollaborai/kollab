"""Tests for operator-to-Hub direct messaging and target discovery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.plugin import HubPlugin


class Presence:
    def __init__(self, agents):
        self.agents = agents

    async def discover_agents_async(self, include_self=False):
        return self.agents


def _hub(*, agents, identity="koordinator"):
    hub = HubPlugin.__new__(HubPlugin)
    hub._presence = Presence(agents)
    hub._identity = AgentRuntime(
        identity=identity,
        agent_id="koordinator-id",
        is_coordinator=True,
    )
    hub.config = {"plugins.hub.user_name": "me"}
    return hub


def test_online_operator_message_carries_human_and_source_metadata():
    async def scenario():
        target = AgentRuntime(
            identity="zicron",
            name="coder",
            state="waiting",
            current_task="",
        )
        hub = _hub(agents=[target])
        hub._route_message = AsyncMock(return_value=[])

        result = await hub.send_user_message("zicron", "Please work on x.")

        assert result == "sent to zicron as me from koordinator"
        message = hub._route_message.await_args.args[0]
        assert message.from_agent == "human"
        assert message.from_identity == "me"
        assert message.to == "zicron"
        assert message.content == "Please work on x."
        assert message.force is True
        assert message.metadata == {
            "source_agent": "koordinator",
            "source": "tui",
            "operator_message": True,
        }

    asyncio.run(scenario())


def test_offline_pool_identity_is_started_with_provenance_in_initial_task():
    async def scenario():
        hub = _hub(agents=[])
        hub._handle_spawn_command = AsyncMock(
            return_value="Created agent 'lapis' (agent type: coder)"
        )

        result = await hub.send_user_message("lapis", "inspect the queue")

        assert result.startswith("started lapis as me from koordinator")
        spawn_args = hub._handle_spawn_command.await_args.args[0]
        assert spawn_args["name"] == "lapis"
        assert spawn_args["task"] == (
            "[message from me via koordinator] inspect the queue"
        )

    asyncio.run(scenario())


def test_target_catalog_merges_online_custom_identity_with_offline_pool():
    async def scenario():
        hub = _hub(
            agents=[
                AgentRuntime(
                    identity="zicron",
                    name="reviewer",
                    state="active",
                    description="checks the work",
                )
            ]
        )

        targets = await hub.list_agent_targets()
        by_identity = {target["identity"]: target for target in targets}

        assert by_identity["lapis"]["status"] == "offline"
        assert by_identity["lapis"]["can_run"] is True
        assert by_identity["zicron"]["status"] == "online"
        assert by_identity["zicron"]["description"] == "checks the work"
        assert by_identity["zicron"]["can_run"] is False

    asyncio.run(scenario())


def test_target_catalog_leads_with_the_three_broadcasts_and_lists_remote_agents():
    async def scenario():
        hub = _hub(agents=[])
        hub.network_agent_rows = AsyncMock(
            return_value=(
                "synthyo",
                [
                    {"name": "lapis", "device": "devbox", "handle": "lapis@devbox",
                     "state": "idle", "online": True},
                    {"name": "ruby", "device": "devbox", "handle": "ruby@devbox",
                     "state": "idle", "online": False},
                ],
            )
        )

        targets = await hub.list_agent_targets()

        assert [(t["identity"], t["description"]) for t in targets[:3]] == [
            ("broadcast", "every agent in this project"),
            ("local-broadcast", "every agent on this computer"),
            ("global-broadcast", "every agent on every computer in your network"),
        ]
        remote = {t["identity"]: t for t in targets if t.get("kind") == "remote"}
        assert list(remote) == ["lapis@devbox"]  # an offline row is left out
        assert remote["lapis@devbox"]["description"] == "on devbox"
        assert remote["lapis@devbox"]["can_run"] is False

    asyncio.run(scenario())


def test_off_a_network_the_menu_still_offers_global_broadcast_without_remote_rows():
    async def scenario():
        hub = _hub(agents=[])

        targets = await hub.list_agent_targets()

        assert targets[2]["identity"] == "global-broadcast"  # its reply says who was missed
        assert not [t for t in targets if t.get("kind") == "remote"]

    asyncio.run(scenario())


def test_broadcast_names_wake_everyone_with_their_scope():
    async def scenario():
        hub = _hub(agents=[])
        hub._handle_broadcast_command = AsyncMock(return_value="broadcast to 3 agent(s)")

        assert await hub.send_user_message("local-broadcast", "stand up") == "broadcast to 3 agent(s)"
        hub._handle_broadcast_command.assert_awaited_with("stand up", force=True, scope="machine")
        await hub.send_user_message("Global-Broadcast", "ship it")
        hub._handle_broadcast_command.assert_awaited_with("ship it", force=True, scope="network")
        await hub.send_user_message("broadcast", "hi")  # this project only
        hub._handle_broadcast_command.assert_awaited_with("hi", force=True, scope="")

    asyncio.run(scenario())


def test_network_broadcast_says_which_computers_it_could_not_reach():
    async def scenario():
        hub = _hub(agents=[])
        hub._route_message = AsyncMock(return_value=[])
        hub._presence.get_cached_agents = lambda: []
        hub._broadcast_other_folders = AsyncMock(return_value=0)

        hub._relay_agent = None
        result = await hub._handle_broadcast_command("hi", force=True, scope="network")
        assert "no other computers on your network yet (/connect adds one)" in result

        hub._relay_agent = SimpleNamespace(trust_level=lambda: "manual")
        result = await hub._handle_broadcast_command("hi", force=True, scope="network")
        assert "other computers skipped: trust is manual" in result

        result = await hub._handle_broadcast_command("hi", force=True)
        assert result == "broadcast to 0 agent(s)"  # @broadcast: this project only

    asyncio.run(scenario())


def test_local_broadcast_also_reaches_live_agents_in_other_folders(tmp_path):
    async def scenario():
        hub = _hub(agents=[])
        hub._route_message = AsyncMock(return_value=[])
        hub._presence.get_cached_agents = lambda: []
        here, other = tmp_path / "here" / "hub", tmp_path / "other" / "hub"
        records = [
            (here, {"socket_path": "/s/self"}),
            (other, {"socket_path": "/s/lapis"}),
            (other, {"socket_path": "/s/ruby"}),
        ]
        send = AsyncMock(side_effect=[True, False])
        with (
            patch("plugins.hub.presence.get_hub_dir", return_value=here),
            patch("plugins.hub.presence.live_agents_on_machine", return_value=records),
            patch("plugins.hub.plugin.AgentMessenger.send_to_agent", send),
        ):
            result = await hub._handle_broadcast_command("hi", force=True, scope="machine")

        assert result == "broadcast to 0 agent(s); 1 in other folders"  # ruby never acked
        assert [call.args[0] for call in send.await_args_list] == ["/s/lapis", "/s/ruby"]

    asyncio.run(scenario())
