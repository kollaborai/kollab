"""ACP harness compaction (#1104).

/compact and the automatic trigger ask the harness to compact its own context,
using the command the harness advertises. Kollab's copy of the history is left
alone, and a harness that advertises no compaction command gets a plain message.
An explicit threshold (active loadout, then global) wins over the automatic ratio
and the 272K cap, and never exceeds the model's real window.

Run: KOLLAB_NO_KEYRING=1 python -m pytest tests/unit/test_issue_1104_acp_harness_compaction.py -v
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor_ai.loadout_manager import LoadoutManager
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
        "plugins.context_compaction.ask_model_first": False,
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


def _loadouts(threshold_k=None, default="acp-claude"):
    entry = {"provider_profile": "claude", "model": "opus"}
    if threshold_k is not None:
        entry["compaction_threshold_k"] = threshold_k
    return {
        LoadoutManager.CONFIG_KEY: {default: entry},
        LoadoutManager.DEFAULT_CONFIG_KEY: default,
    }


def _session_on(loadout_name, profile="claude"):
    """Profile manager for a session on ``loadout_name`` whose active provider profile is ``profile``."""
    profiles = MagicMock()
    profiles.active_profile_name = profile
    profiles.active_loadout_name = loadout_name
    return profiles


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

    async def test_inactive_acp_session_does_not_claim_command_is_unadvertised(self):
        plugin, _llm, acp = _plugin()
        acp.active = False

        message = await plugin._start_manual_compaction(ask=False)

        self.assertIn("ACP session is not active", message)
        self.assertNotIn("advertises no compaction command", message)
        acp.submit.assert_not_awaited()


class TestAutomaticCompactOnAcpChat(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_crossing_is_retried_after_inflight_compaction(self):
        plugin, _llm, acp = _plugin()
        started, release = asyncio.Event(), asyncio.Event()

        async def submit(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {"text": "compacted", "stop_reason": "end_turn"}

        acp.submit = AsyncMock(side_effect=submit)
        plugin.acp_usage_updated(300_000, 1_000_000, session_key=("session-1", 1))
        await started.wait()
        plugin.acp_usage_updated(300_000, 1_000_000, session_key=("session-2", 2))
        release.set()
        await _settle(plugin)

        self.assertEqual(acp.submit.await_count, 2)

    async def test_new_session_rearms_when_its_first_meter_is_above_threshold(self):
        plugin, _llm, acp = _plugin()
        bus = MagicMock()
        bus.get_service = lambda name: plugin if name == "context_compaction" else None
        orch = ACPOrchestrator(bus)
        orch._runner = SimpleNamespace(
            session_id="session-1", generation=1,
            available_commands=[{"name": "compact"}],
        )

        orch._on_runner_update({"kind": "usage_update", "payload": {"used": 300_000, "size": 1_000_000}})
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 1)

        orch._runner.session_id = "session-2"
        orch._runner.generation = 2
        orch._on_runner_update({"kind": "usage_update", "payload": {"used": 300_000, "size": 1_000_000}})
        await _settle(plugin)

        self.assertEqual(acp.submit.await_count, 2)

    async def test_crossing_the_automatic_trigger_sends_once_until_the_meter_drops(self):
        plugin, _llm, acp = _plugin()
        plugin.acp_usage_updated(300_000, 1_000_000)  # automatic: 0.75 of 1M, capped at 272K
        plugin.acp_usage_updated(900_000, 1_000_000)  # still over: no second send
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 1)

        plugin.acp_usage_updated(200_000, 1_000_000)  # meter dropped under the trigger
        plugin.acp_usage_updated(300_000, 1_000_000)
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 2)

    async def test_automatic_trigger_is_the_ratio_of_a_smaller_harness_window(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.compaction_ratio": 0.5})
        plugin.acp_usage_updated(140_000, 300_000)  # under 0.5 of 300K
        await _settle(plugin)
        acp.submit.assert_not_awaited()
        plugin.acp_usage_updated(160_000, 300_000)  # over 150K
        await _settle(plugin)
        acp.submit.assert_awaited_once()

    async def test_automatic_trigger_retries_if_command_arrives_after_threshold(self):
        plugin, _llm, acp = _plugin(command=None)
        plugin.acp_usage_updated(300_000, 1_000_000)
        await _settle(plugin)

        acp._command = "/compact"
        plugin.acp_usage_updated(300_000, 1_000_000)
        await _settle(plugin)

        acp.submit.assert_awaited_once()

    async def test_automatic_trigger_retries_after_compaction_submission_fails(self):
        plugin, _llm, acp = _plugin()
        acp.submit = AsyncMock(side_effect=[RuntimeError("temporary disconnect"), {"text": "compacted"}])

        plugin.acp_usage_updated(300_000, 1_000_000)
        await _settle(plugin)
        self.assertTrue(plugin._acp_compact_armed)

        plugin.acp_usage_updated(300_000, 1_000_000)
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 2)

    async def test_threshold_retry_survives_failed_manual_compaction_interleave(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.ask_model_first": True})

        async def fail_after_threshold_update(_text, *, record=True):
            plugin.acp_usage_updated(300_000, 1_000_000)
            raise RuntimeError("temporary disconnect")

        acp.submit = AsyncMock(side_effect=fail_after_threshold_update)
        plugin._compact_acp_harness("requested by user", ask=True)
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 1)

        acp.submit = AsyncMock(return_value={"text": "", "stop_reason": "end_turn"})
        plugin.acp_usage_updated(300_000, 1_000_000)
        await _settle(plugin)

        self.assertEqual(acp.submit.await_count, 2)

    async def test_successful_manual_compaction_consumes_crossing_seen_in_flight(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.ask_model_first": False})
        gate = asyncio.Event()

        async def wait_for_manual_compaction(_text, *, record=True):
            await gate.wait()
            return {"text": "", "stop_reason": "end_turn"}

        acp.submit = AsyncMock(side_effect=wait_for_manual_compaction)
        plugin.acp_usage_updated(100_000, 1_000_000)
        await plugin._start_manual_compaction(ask=False)
        plugin.acp_usage_updated(350_000, 1_000_000)
        gate.set()
        await _settle(plugin)

        plugin.acp_usage_updated(310_000, 1_000_000)
        await _settle(plugin)

        self.assertEqual(acp.submit.await_count, 1)

    async def test_explicit_global_threshold_is_the_trigger_for_acp_too(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.token_threshold_k": 500})
        plugin.acp_usage_updated(600_000, 1_000_000)  # over 500K, under the ratio
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
            "payload": {"used": 300_000, "size": 1_000_000},
            "request_id": "request-1",
            "turn_id": 1,
        })
        await _settle(plugin)
        acp.submit.assert_awaited_once()


class TestAskThenCompact(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_asks_the_model_then_compacts_once_with_its_notes(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.ask_model_first": True})
        acp.submit = AsyncMock(side_effect=[
            {"text": "<compact>keep the auth refactor and the open PR</compact>"},
            {"text": "compacted"},
        ])
        plugin.acp_usage_updated(300_000, 1_000_000)
        plugin.acp_usage_updated(900_000, 1_000_000)
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 2)
        self.assertIn("<compact>", acp.submit.await_args_list[0].args[0])
        self.assertEqual(
            acp.submit.await_args_list[1].args,
            ("/compact keep the auth refactor and the open PR",),
        )

    async def test_ask_model_first_off_sends_the_bare_command(self):
        plugin, _llm, acp = _plugin({"plugins.context_compaction.ask_model_first": False})
        plugin.acp_usage_updated(300_000, 1_000_000)
        await _settle(plugin)
        acp.submit.assert_awaited_once()
        self.assertEqual(acp.submit.await_args.args, ("/compact",))

    async def test_loadout_threshold_trigger_asks_then_compacts_once(self):
        plugin, _llm, acp = _plugin({**_loadouts(800), "plugins.context_compaction.ask_model_first": True})
        plugin._profile_manager = _session_on("acp-claude")
        acp.submit = AsyncMock(side_effect=[
            {"text": "<compact>keep the migration notes</compact>"},
            {"text": "compacted"},
        ])
        plugin.acp_usage_updated(850_000, 1_000_000)  # over the loadout's 800K
        plugin.acp_usage_updated(900_000, 1_000_000)  # still over: no second round
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 2)
        self.assertEqual(
            acp.submit.await_args_list[1].args,
            ("/compact keep the migration notes",),
        )

    async def test_loadout_threshold_above_the_automatic_trigger_waits_for_it(self):
        plugin, _llm, acp = _plugin(_loadouts(800))
        plugin._profile_manager = _session_on("acp-claude")
        plugin.acp_usage_updated(600_000, 1_000_000)  # over the 272K automatic trigger, under 800K
        await _settle(plugin)
        acp.submit.assert_not_awaited()
        plugin.acp_usage_updated(850_000, 1_000_000)
        await _settle(plugin)
        acp.submit.assert_awaited_once()


class TestExplicitThresholdWins(unittest.TestCase):
    @staticmethod
    def _native(window, settings=None):
        plugin, _llm, _acp = _plugin(settings)
        plugin._get_context_window_tokens = lambda: window
        return plugin

    def test_explicit_global_threshold_beats_the_272k_cap(self):
        plugin = self._native(1_000_000, {"plugins.context_compaction.token_threshold_k": 500})
        self.assertEqual(plugin._get_token_threshold_details(), (500_000, "manual"))

    def test_unset_threshold_is_automatic_and_capped_at_272k(self):
        plugin = self._native(1_000_000)
        self.assertEqual(plugin._get_token_threshold_details(), (272_000, "automatic"))

    def test_explicit_threshold_never_exceeds_the_real_window(self):
        plugin = self._native(300_000, {"plugins.context_compaction.token_threshold_k": 500})
        self.assertEqual(plugin._get_token_threshold_details(), (300_000, "manual"))

    def test_loadout_threshold_beats_global_and_the_cap(self):
        settings = {"plugins.context_compaction.token_threshold_k": 150, **_loadouts(500)}
        plugin = self._native(1_000_000, settings)
        plugin._profile_manager = _session_on("acp-claude")
        self.assertEqual(plugin._get_token_threshold_details(), (500_000, "manual"))

    def test_unset_loadout_threshold_falls_back_to_the_global_one(self):
        settings = {"plugins.context_compaction.token_threshold_k": 150, **_loadouts(None)}
        plugin = self._native(1_000_000, settings)
        plugin._profile_manager = _session_on("acp-claude")
        self.assertEqual(plugin._get_token_threshold_details(), (150_000, "manual"))

    def test_loadout_and_global_unset_is_automatic(self):
        plugin = self._native(1_000_000, _loadouts(None))
        plugin._profile_manager = _session_on("acp-claude")
        self.assertEqual(plugin._get_token_threshold_details(), (272_000, "automatic"))


class TestSessionLoadoutThreshold(unittest.TestCase):
    """The threshold comes from the loadout the session runs on, not the default one (#1104)."""

    def test_session_on_a_non_default_loadout_uses_its_own_threshold(self):
        settings = {
            LoadoutManager.CONFIG_KEY: {
                "acp-claude": {"provider_profile": "claude", "model": "opus"},
                "acp-claude-500k": {
                    "provider_profile": "claude",
                    "model": "opus",
                    "compaction_threshold_k": 500,
                },
            },
            LoadoutManager.DEFAULT_CONFIG_KEY: "acp-claude",
        }
        plugin, _llm, _acp = _plugin(settings)
        plugin._get_context_window_tokens = lambda: 1_000_000
        plugin._profile_manager = _session_on("acp-claude-500k")
        self.assertEqual(plugin._get_token_threshold_details(), (500_000, "manual"))

    def test_a_loadout_threshold_does_not_follow_the_session_to_another_profile(self):
        settings = {
            LoadoutManager.CONFIG_KEY: {
                "acp-claude-500k": {
                    "provider_profile": "claude",
                    "model": "opus",
                    "compaction_threshold_k": 500,
                },
            },
        }
        plugin, _llm, _acp = _plugin(settings)
        plugin._get_context_window_tokens = lambda: 1_000_000
        plugin._profile_manager = _session_on("acp-claude-500k", profile="other")
        self.assertEqual(plugin._get_token_threshold_details(), (272_000, "automatic"))


class TestCompactionThresholdForm(unittest.TestCase):
    """The loadout form's Compaction Threshold field, shared by /llm and the web UI (#1104)."""

    @staticmethod
    def _values(raw):
        from kollabor.panels.llm import _loadout_values

        return _loadout_values(
            {"temperature": "", "effort": "default", "description": "",
             "compaction_threshold_k": raw},
            False, "opus", is_acp=True, provider="acp",
        )

    def test_a_number_is_saved_in_thousands_of_tokens(self):
        values, errors = self._values("500")
        self.assertEqual(values["compaction_threshold_k"], 500)
        self.assertEqual(errors, {})

    def test_blank_means_automatic(self):
        values, errors = self._values("")
        self.assertIsNone(values["compaction_threshold_k"])
        self.assertEqual(errors, {})

    def test_zero_and_non_numbers_are_refused(self):
        for raw in ("0", "lots", "1.5"):
            _values, errors = self._values(raw)
            self.assertIn("compaction_threshold_k", errors, raw)

    def test_oversized_numeric_threshold_is_refused_without_raising(self):
        values, errors = self._values("9" * 5000)
        self.assertIsNone(values["compaction_threshold_k"])
        self.assertIn("compaction_threshold_k", errors)


class TestInternalPromptsStayOutOfTheTranscript(unittest.IsolatedAsyncioTestCase):
    """The ask-first question and the /compact command are kollab's own prompts, not the user's (#1104)."""

    async def test_ask_and_compact_are_not_recorded_as_user_messages(self):
        plugin, _llm, acp = _plugin({**_loadouts(800), "plugins.context_compaction.ask_model_first": True})
        plugin._profile_manager = _session_on("acp-claude")
        acp.submit = AsyncMock(side_effect=[
            {"text": "<compact>keep the migration notes</compact>"},
            {"text": "compacted"},
        ])
        plugin.acp_usage_updated(850_000, 1_000_000)
        await _settle(plugin)
        self.assertEqual(acp.submit.await_count, 2)
        for call in acp.submit.await_args_list:
            self.assertIs(call.kwargs.get("record"), False)


if __name__ == "__main__":
    unittest.main()
