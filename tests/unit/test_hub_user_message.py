"""Tests for operator-to-Hub direct messaging and target discovery."""

import asyncio
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
    hub.config = {"plugins.hub.user_name": "malmazan"}
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

        assert result == "sent to zicron as malmazan from koordinator"
        message = hub._route_message.await_args.args[0]
        assert message.from_agent == "human"
        assert message.from_identity == "malmazan"
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

        assert result.startswith("started lapis as malmazan from koordinator")
        spawn_args = hub._handle_spawn_command.await_args.args[0]
        assert spawn_args["name"] == "lapis"
        assert spawn_args["task"] == (
            "[message from malmazan via koordinator] inspect the queue"
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
