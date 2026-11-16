"""ACP harness compaction (#1104).

/compact and the automatic trigger ask the harness to compact its own context,
using the command the harness advertises. Kollab's copy of the history is left
alone, and a harness that advertises no compaction command gets a plain message.

Run: KOLLAB_NO_KEYRING=1 python -m pytest tests/unit/test_issue_1104_acp_harness_compaction.py -v
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from plugins.context_compaction_plugin import ContextCompactionPlugin


class _FakeACP:
    """The orchestrator surface the compaction plugin uses."""

    active = True

    def __init__(self, command="/compact"):
        self._command = command
        self.submit = AsyncMock(return_value={"text": "", "stop_reason": "end_turn"})

    def advertised_command(self, name):
        return self._command if name == "compact" else None


def _plugin(settings=None, command="/compact"):
    values = {
        "plugins.context_compaction.enabled": True,
        "plugins.context_compaction.compaction_ratio": 0.75,
        "plugins.context_compaction.token_threshold_k": 0,
    }
    values.update(settings or {})
    config = MagicMock()
    config.get = lambda key, default=None: values.get(key, default)
    bus = MagicMock()
    bus.get_service = lambda name: None
    bus.emit_with_hooks = AsyncMock()
    plugin = ContextCompactionPlugin("test", bus, MagicMock(), config)
    acp = _FakeACP(command)
    llm = MagicMock()
    llm.conversation_history = []
    llm._profile_selects_acp = lambda: (True, False)
    llm._acp_service = lambda: acp
    plugin._llm_service = llm
    return plugin, llm, acp


async def _settle(plugin):
    """Wait for every background task the plugin started."""
    await asyncio.gather(*list(plugin._compaction_tasks), return_exceptions=True)


class TestHarnessCommandIsCaptured(unittest.IsolatedAsyncioTestCase):
    async def test_available_commands_update_is_kept_on_the_runner(self):
        runner = ACPRunner(ACPProfile.builtin("codex-cli", cwd=os.getcwd()))
        runner.session_id = "session-1"
        update = SimpleNamespace(
            model_dump=lambda by_alias=False: {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [
                    {"name": "compact", "description": "Compact"},
                    {"name": "review"},
                ],
            }
        )
        await runner._session_update("session-1", update)
        self.assertEqual(
            [command["name"] for command in runner.available_commands],
            ["compact", "review"],
        )

    def test_advertised_command_reports_only_what_the_harness_advertises(self):
        orch = ACPOrchestrator(MagicMock())
        orch._runner = SimpleNamespace(available_commands=[{"name": "compact"}])
        self.assertEqual(orch.advertised_command("compact"), "/compact")
        orch._runner = SimpleNamespace(available_commands=[{"name": "review"}])
        self.assertIsNone(orch.advertised_command("compact"))


class TestCompactOnAcpChat(unittest.IsolatedAsyncioTestCase):
    async def test_compact_sends_the_harness_command_and_keeps_kollab_history(self):
        plugin, llm, acp = _plugin()
        plugin._start_compaction = MagicMock(return_value=True)
        before = list(llm.conversation_history)

        message = await plugin._handle_compact_command(SimpleNamespace(args=[]))
        await _settle(plugin)

        self.assertEqual(acp.submit.await_args.args, ("/compact",))
        self.assertEqual(llm.conversation_history, before)
        plugin._start_compaction.assert_not_called()
        self.assertIn("own context", message)

    async def test_harness_without_a_compaction_command_says_so(self):
        plugin, llm, acp = _plugin(command=None)
        before = list(llm.conversation_history)

        message = await plugin._handle_compact_command(SimpleNamespace(args=[]))
        await _settle(plugin)

        acp.submit.assert_not_awaited()
        self.assertEqual(llm.conversation_history, before)
        self.assertIn("compacts its own context", message)


class TestAutomaticCompactOnAcpChat(unittest.IsolatedAsyncioTestCase):
    async def test_ratio_crossed_sends_the_command_once_until_the_meter_drops(self):
        plugin, _llm, acp = _plugin()  # compaction_ratio 0.75
        plugin.acp_usage_updated(800_000, 1_000_000)
        plugin.acp_usage_updated(900_000, 1_000_000)
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 1)

        plugin.acp_usage_updated(400_000, 1_000_000)  # meter dropped under the ratio
        plugin.acp_usage_updated(800_000, 1_000_000)
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 2)

    async def test_token_threshold_overrides_the_ratio_when_set(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.token_threshold_k": 500})
        plugin.acp_usage_updated(600_000, 1_000_000)  # 60% of window: over 500k, under the ratio
        await _settle(plugin)
        acp.submit.assert_awaited_once()

    async def test_disabled_sends_nothing(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.enabled": False})
        plugin.acp_usage_updated(950_000, 1_000_000)
        await _settle(plugin)
        acp.submit.assert_not_awaited()

    async def test_orchestrator_usage_update_reaches_the_plugin(self):
        plugin, _llm, acp = _plugin()
        bus = MagicMock()
        bus.get_service = lambda name: plugin if name == "context_compaction" else None
        orch = ACPOrchestrator(bus)
        orch._on_runner_update({
            "kind": "usage_update",
            "payload": {"used": 800_000, "size": 1_000_000},
            "request_id": "request-1",
            "turn_id": 1,
        })
        await _settle(plugin)
        acp.submit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
