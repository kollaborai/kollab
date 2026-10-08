"""Tests for operator-to-Hub direct messaging and target discovery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


def test_target_catalog_leads_with_both_broadcasts_and_lists_remote_agents():
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

        assert [t["identity"] for t in targets[:2]] == ["local-broadcast", "global-broadcast"]
        assert targets[1]["description"] == "every agent in this folder and on your network"
        remote = {t["identity"]: t for t in targets if t.get("kind") == "remote"}
        assert list(remote) == ["lapis@devbox"]  # an offline row is left out
        assert remote["lapis@devbox"]["description"] == "on devbox"
        assert remote["lapis@devbox"]["can_run"] is False

    asyncio.run(scenario())


def test_off_a_network_the_menu_still_offers_global_broadcast_without_remote_rows():
    async def scenario():
        hub = _hub(agents=[])

        targets = await hub.list_agent_targets()

        assert targets[1]["identity"] == "global-broadcast"  # its reply says who was missed
        assert not [t for t in targets if t.get("kind") == "remote"]

    asyncio.run(scenario())


def test_broadcast_names_wake_everyone_with_their_scope():
    async def scenario():
        hub = _hub(agents=[])
        hub._handle_broadcast_command = AsyncMock(return_value="broadcast to 3 agent(s)")

        assert await hub.send_user_message("local-broadcast", "stand up") == "broadcast to 3 agent(s)"
        hub._handle_broadcast_command.assert_awaited_with("stand up", force=True, scope="")
        await hub.send_user_message("Global-Broadcast", "ship it")
        hub._handle_broadcast_command.assert_awaited_with("ship it", force=True, scope="network")
        await hub.send_user_message("broadcast", "hi")  # the old name stays the local one
        hub._handle_broadcast_command.assert_awaited_with("hi", force=True, scope="")

    asyncio.run(scenario())


def test_network_broadcast_says_which_computers_it_could_not_reach():
    async def scenario():
        hub = _hub(agents=[])
        hub._route_message = AsyncMock(return_value=[])
        hub._presence.get_cached_agents = lambda: []

        hub._relay_agent = None
        result = await hub._handle_broadcast_command("hi", force=True, scope="network")
        assert "no other computers on your network yet (/connect adds one)" in result

        hub._relay_agent = SimpleNamespace(trust_level=lambda: "manual")
        result = await hub._handle_broadcast_command("hi", force=True, scope="network")
        assert "other computers skipped: trust is manual" in result

        result = await hub._handle_broadcast_command("hi", force=True)
        assert "other computers" not in result  # a local broadcast never mentions them

    asyncio.run(scenario())
