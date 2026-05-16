import time
import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.registry import AgentRegistry
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


class FakeDnsRegistry:
    def is_approved(self, identity: str) -> bool:
        return False

    def resolve(self, identity: str):
        return MagicMock(approval_state="unknown")


class MemoryDnsStorage:
    def __init__(self, records):
        self.records = records

    def load_registry(self):
        return dict(self.records)

    def save_registry(self, records):
        self.records = dict(records)


class TestHubMeshForce(unittest.IsolatedAsyncioTestCase):
    async def test_force_message_bypasses_sender_approval_gate(self) -> None:
        plugin = HubPlugin(event_bus=MagicMock())
        plugin._dns_registry = FakeDnsRegistry()
        plugin._identity = AgentRuntime(
            name="coder",
            identity="sapphire",
            agent_id="sapphire-id",
            is_coordinator=False,
        )
        plugin._presence = MagicMock()
        plugin._presence.discover_agents_async = AsyncMock(
            return_value=[
                AgentRuntime(
                    name="coordinator",
                    identity="koordinator",
                    agent_id="koordinator-id",
                    socket_path="/tmp/koordinator.sock",
                )
            ]
        )
        plugin._deliver_to_agent = AsyncMock(return_value=True)

        rejections = await plugin._route_message(
            HubMessage(
                action="message",
                from_agent="sapphire-id",
                from_identity="sapphire",
                to="koordinator",
                content="report complete",
                force=True,
            )
        )

        self.assertEqual(rejections, [])
        plugin._deliver_to_agent.assert_awaited_once()

    async def test_hub_msg_tool_reports_rejection_as_failure(self) -> None:
        plugin = HubPlugin(event_bus=MagicMock())
        plugin._identity = AgentRuntime(
            name="coder",
            identity="sapphire",
            agent_id="sapphire-id",
            is_coordinator=False,
        )
        plugin._presence = MagicMock()
        plugin._route_message = AsyncMock(
            return_value=[("mesh", "sender not approved for mesh participation")]
        )
        plugin._display_outgoing_message = MagicMock()
        plugin._bridge_forward = AsyncMock()
        plugin._resolve_scope = MagicMock(return_value="direct")

        result = await plugin._handle_hub_msg_tool(
            {
                "id": "hub_msg_1",
                "to": "koordinator",
                "content": "report complete",
            }
        )

        self.assertFalse(result.success)
        self.assertIn("sender not approved", result.error)

    async def test_hub_msg_tool_marks_task_assignment_metadata(self) -> None:
        plugin = HubPlugin(event_bus=MagicMock())
        plugin._identity = AgentRuntime(
            name="coordinator",
            identity="koordinator",
            agent_id="koordinator-id",
            is_coordinator=True,
        )
        plugin._presence = MagicMock()
        plugin._presence.scan_all_presence = MagicMock(
            return_value=[
                AgentRuntime(
                    name="coder",
                    identity="lapis",
                    agent_id="lapis-id",
                )
            ]
        )
        plugin._route_message = AsyncMock(return_value=[])
        plugin._display_outgoing_message = MagicMock()
        plugin._bridge_forward = AsyncMock()
        plugin._resolve_scope = MagicMock(return_value="direct")

        await plugin._handle_hub_msg_tool(
            {
                "id": "hub_msg_2",
                "to": "lapis",
                "content": "review Agent HUD and report back",
            }
        )

        routed = plugin._route_message.await_args.args[0]
        self.assertTrue(routed.metadata["task_assignment"])

    async def test_route_message_queues_direct_target_identity_when_offline(self) -> None:
        plugin = HubPlugin(event_bus=MagicMock())
        plugin._identity = AgentRuntime(
            name="coder",
            identity="sapphire",
            agent_id="sapphire-id",
            is_coordinator=False,
        )
        plugin._presence = MagicMock()
        plugin._presence.discover_agents_async = AsyncMock(return_value=[])

        msg = HubMessage(
            action="message",
            from_agent="sapphire-id",
            from_identity="sapphire",
            to="koordinator",
            content="review complete",
        )

        with unittest.mock.patch(
            "plugins.hub.plugin.AgentMessenger.send_to_file",
            new=AsyncMock(),
        ) as send_to_file:
            rejections = await plugin._route_message(msg)

        self.assertEqual(rejections, [])
        send_to_file.assert_awaited_once_with("koordinator", msg)
        self.assertEqual(msg.metadata["_queued_for"], ["koordinator"])

    async def test_dns_liveness_keeps_self_approval_when_peer_cache_excludes_self(
        self,
    ) -> None:
        storage = MemoryDnsStorage(
            {
                "sapphire": AgentRecord(
                    designation="sapphire",
                    agent_id="sapphire-id",
                    runtime="kollab",
                    approval_state="auto_approved",
                    last_seen=time.time() - 60,
                    ttl=1,
                )
            }
        )
        registry = AgentRegistry(storage)

        removed = registry.refresh_liveness(
            live_agents=[],
            preserve_designations=["sapphire"],
        )

        self.assertEqual(removed, 0)
        record = registry.resolve("sapphire")
        self.assertIsNotNone(record)
        self.assertTrue(record.is_approved)


if __name__ == "__main__":
    unittest.main()
