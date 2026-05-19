"""Regression tests for hub roster injection."""

import asyncio
import re
import unittest
from types import SimpleNamespace

from kollabor_events.data_models import ConversationMessage
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


class _LlmService:
    def __init__(self) -> None:
        self.conversation_history = [
            ConversationMessage(role="system", content="base prompt")
        ]


class _EventBus:
    def __init__(self) -> None:
        self.llm_service = _LlmService()

    def get_service(self, name: str):
        if name == "llm_service":
            return self.llm_service
        return None


class _ChangeFeed:
    def get_claims(self):
        return {
            "claims": {
                "hub_identity:zircon": {
                    "identity": "zircon",
                    "path": "hub_identity:zircon",
                    "task": "spawn reservation for coder",
                }
            },
            "count": 1,
        }

    def get_subscriptions(self, identity: str):
        return [re.compile(r"plugins/hub/.*")]

    def get_recent(self, limit: int = 20):
        return {
            "entries": [
                "malformed-change-entry",
                {
                    "identity": "lapis",
                    "action": "edit",
                    "path": "plugins/hub/plugin.py",
                },
            ],
            "count": 2,
        }


class TestHubRosterInject(unittest.TestCase):
    def test_malformed_roster_update_does_not_poison_roster(self):
        plugin = HubPlugin.__new__(HubPlugin)
        plugin._roster = [{"identity": "lapis", "state": "idle"}]

        asyncio.run(
            plugin._on_message(
                HubMessage(
                    action="roster_update",
                    content='["lapis", "sapphire"]',
                    from_identity="hub",
                )
            )
        )

        self.assertEqual(plugin._roster, [{"identity": "lapis", "state": "idle"}])

    def test_inject_handles_dict_backed_claims_and_malformed_changes(self):
        plugin = HubPlugin.__new__(HubPlugin)
        plugin._identity = SimpleNamespace(
            identity="koordinator",
            is_coordinator=True,
        )
        plugin._roster = [
            {
                "identity": "lapis",
                "state": "working",
                "current_task": "debug roster inject",
                "is_coordinator": False,
            },
            "malformed-roster-entry",
        ]
        plugin.config = None
        plugin._task_ledger = None
        plugin._scratchpad = None
        plugin._vault = None
        plugin._change_feed = _ChangeFeed()
        plugin.event_bus = _EventBus()

        asyncio.run(plugin._inject_roster_context({}))

        content = plugin.event_bus.llm_service.conversation_history[0].content
        self.assertIn("lapis - working: debug roster inject", content)
        self.assertIn("zircon -> hub_identity:zircon", content)
        self.assertIn("lapis edit plugins/hub/plugin.py", content)
        self.assertNotIn("malformed-roster-entry", content)
        self.assertNotIn("malformed-change-entry", content)


if __name__ == "__main__":
    unittest.main()
