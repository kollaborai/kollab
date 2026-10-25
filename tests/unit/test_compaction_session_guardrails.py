"""Long-session regression tests for compaction safety boundaries."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from kollabor_events import EventType
from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import ContextCompactionPlugin


class _Config:
    def __init__(self) -> None:
        self.values = {
            "plugins.context_compaction.enabled": True,
            "plugins.context_compaction.token_threshold_k": 272,
            "plugins.context_compaction.compaction_ratio": 0.75,
            "plugins.context_compaction.min_human_turns": 1,
            "plugins.context_compaction.keep_recent": 2,
            "plugins.context_compaction.max_summary_tokens": 2000,
            "plugins.context_compaction.log_compaction_events": False,
            "plugins.context_compaction.ask_model_first": False,
            "plugins.context_compaction.max_session_turns": 90,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def _message(role: str, content: str) -> ConversationMessage:
    return ConversationMessage(
        role=role, content=content, metadata={}, timestamp=datetime.now()
    )


def _plugin(prompt_tokens: int = 410_000):
    config = _Config()
    event_bus = MagicMock()
    event_bus.register_hook = AsyncMock()
    event_bus.get_service = lambda _name: None
    history = [_message("system", "system prompt")]
    for index in range(6):
        history.append(_message("user", f"request {index}"))
        history.append(_message("assistant", f"response {index}"))
    llm_service = SimpleNamespace(
        conversation_history=history,
        session_stats={
            "input_tokens": prompt_tokens,
            "input_tokens_estimated": False,
            "output_tokens": 0,
        },
        _pending_agent_hud=[],
        api_service=SimpleNamespace(
            _provider=SimpleNamespace(config=SimpleNamespace(context_window=272_000))
        ),
    )
    plugin = ContextCompactionPlugin(
        "context_compaction", event_bus, MagicMock(), config
    )
    plugin._llm_service = llm_service
    plugin._conversation_logger = SimpleNamespace(
        session_id="session-410k", session_file=None
    )
    return plugin, event_bus, llm_service


class TestHardCapToolBoundary(IsolatedAsyncioTestCase):
    async def test_paused_410k_prompt_at_91_turns_compacts_before_next_tool(self):
        plugin, event_bus, llm_service = _plugin()
        plugin._compaction_status = "paused"
        plugin._shrink_floor = 410_000
        plugin._compaction_round = 23
        plugin._turn_count = 91
        original_history = list(llm_service.conversation_history)
        compacted_history = [
            original_history[0],
            _message("user", "[Previous Context Summary] task is still active"),
            *original_history[-2:],
        ]
        prompt_tokens = {"value": 410_000}

        async def run_compaction():
            plugin._compaction_round += 1
            plugin._pending_compaction = compacted_history
            plugin._pending_session_id = "session-410k"
            plugin._pre_compaction_len = len(original_history)
            plugin._compaction_status = "ready_to_retry"
            plugin._compaction_in_progress = False

        def start_compaction(reason="threshold", **_kwargs):
            plugin._compaction_reason = reason
            plugin._compaction_in_progress = True
            task = asyncio.create_task(run_compaction())
            plugin._compaction_task = task
            plugin._compaction_tasks.add(task)
            task.add_done_callback(plugin._compaction_task_done)
            return True

        async def apply_pending(data, _event):
            llm_service.conversation_history = list(plugin._pending_compaction)
            plugin._pending_compaction = None
            plugin._pending_session_id = None
            plugin._compaction_status = "applied"
            plugin._compaction_in_progress = False
            prompt_tokens["value"] = 190_000
            llm_service.session_stats["input_tokens"] = prompt_tokens["value"]
            return data

        plugin._get_prompt_tokens = lambda: prompt_tokens["value"]
        plugin.config.values["plugins.context_compaction.token_threshold_k"] = 150
        self.assertEqual(plugin._get_token_threshold(), 150_000)
        self.assertEqual(plugin._get_hard_cap_threshold(), 272_000)
        plugin._start_compaction = start_compaction
        plugin._apply_pending_compaction = AsyncMock(side_effect=apply_pending)

        await plugin.register_hooks()
        registered = [call.args[0] for call in event_bus.register_hook.await_args_list]
        tool_hook = next(
            (hook for hook in registered if hook.event_type == EventType.TOOL_CALL_PRE),
            None,
        )
        self.assertIsNotNone(
            tool_hook,
            "hard-cap enforcement must run before tool execution, not only before the next model request",
        )

        result = await tool_hook.callback(
            {"tool_data": {"tool_name": "terminal", "arguments": {}}},
            SimpleNamespace(),
        )

        self.assertFalse((result or {}).get("cancelled", False))
        self.assertEqual(plugin._compaction_round, 24)
        self.assertEqual(llm_service.conversation_history, compacted_history)
        self.assertEqual(prompt_tokens["value"], 190_000)
        self.assertEqual(plugin._apply_pending_compaction.await_count, 1)
        self.assertEqual(plugin._turn_count, 91)

    async def test_coordination_deferral_never_applies_at_hard_cap_338181(self):
        plugin, _event_bus, llm_service = _plugin(prompt_tokens=338_181)
        plugin.config.values["plugins.context_compaction.token_threshold_k"] = 150
        self.assertEqual(plugin._get_token_threshold(), 150_000)
        self.assertEqual(plugin._get_hard_cap_threshold(), 272_000)
        plugin._compaction_status = "paused"
        plugin._coordination_pending = MagicMock(return_value=True)
        plugin._should_compact = MagicMock(return_value=True)
        plugin._start_compaction = MagicMock(return_value=False)

        # 338K reported is already past the 272K hard cap, so pending
        # coordination must not defer the trigger another turn.
        await plugin._on_llm_turn_complete({}, SimpleNamespace())

        self.assertEqual(plugin._coordination_deferrals, 0)
        plugin._start_compaction.assert_called_once()

        original_history = list(llm_service.conversation_history)
        compacted_history = [
            original_history[0],
            _message("user", "[Previous Context Summary] task is still active"),
            *original_history[-2:],
        ]
        prompt_tokens = {"value": 338_181}

        async def run_compaction():
            plugin._compaction_round += 1
            plugin._pending_compaction = compacted_history
            plugin._pending_session_id = "session-410k"
            plugin._pre_compaction_len = len(original_history)
            plugin._compaction_status = "ready_to_retry"
            plugin._compaction_in_progress = False

        def start_compaction(reason="threshold", **_kwargs):
            plugin._compaction_reason = reason
            plugin._compaction_in_progress = True
            task = asyncio.create_task(run_compaction())
            plugin._compaction_task = task
            plugin._compaction_tasks.add(task)
            task.add_done_callback(plugin._compaction_task_done)
            return True

        async def apply_pending(data, _event):
            llm_service.conversation_history = list(plugin._pending_compaction)
            plugin._pending_compaction = None
            plugin._pending_session_id = None
            plugin._compaction_status = "applied"
            plugin._compaction_in_progress = False
            # This is safe under the 272K hard cap, though above the 150K
            # automatic trigger. The boundary must not immediately retry.
            prompt_tokens["value"] = 260_000
            llm_service.session_stats["input_tokens"] = prompt_tokens["value"]
            return data

        plugin._get_prompt_tokens = lambda: prompt_tokens["value"]
        plugin._start_compaction = start_compaction
        plugin._apply_pending_compaction = AsyncMock(side_effect=apply_pending)

        result = await plugin._on_tool_call_pre(
            {"tool_data": {"tool_name": "terminal", "arguments": {}}},
            SimpleNamespace(),
        )

        self.assertFalse((result or {}).get("cancelled", False))
        self.assertEqual(prompt_tokens["value"], 260_000)
        self.assertEqual(plugin._apply_pending_compaction.await_count, 1)
        self.assertEqual(plugin._turn_count, 1)

    async def test_boundary_counts_history_appended_after_last_report(self):
        """Issue #279: the provider count predates tool results and injected
        hub messages. The boundary must also measure the current history, or a
        prompt whose appended messages cross the cap sails through."""
        plugin, _event_bus, llm_service = _plugin(prompt_tokens=270_000)
        plugin.config.values["plugins.context_compaction.token_threshold_k"] = 150
        self.assertEqual(plugin._get_hard_cap_threshold(), 272_000)
        # Appended after the 270K response: the raw estimate of the current
        # history is now ~300K, over the 272K hard cap.
        llm_service.conversation_history.append(_message("user", "x" * 900_000))
        original_len = len(llm_service.conversation_history)
        prompt_tokens = {"value": 270_000}

        async def run_compaction():
            plugin._compaction_round += 1
            plugin._pending_compaction = [
                _message("system", "system prompt"),
                _message("user", "[Previous Context Summary] task is still active"),
                _message("user", "request 5"),
                _message("assistant", "response 5"),
            ]
            plugin._pending_session_id = "session-410k"
            plugin._pre_compaction_len = original_len
            plugin._compaction_status = "ready_to_retry"
            plugin._compaction_in_progress = False

        def start_compaction(reason="threshold", **_kwargs):
            plugin._compaction_reason = reason
            plugin._compaction_in_progress = True
            task = asyncio.create_task(run_compaction())
            plugin._compaction_task = task
            plugin._compaction_tasks.add(task)
            task.add_done_callback(plugin._compaction_task_done)
            return True

        async def apply_pending(data, _event):
            llm_service.conversation_history = list(plugin._pending_compaction)
            plugin._pending_compaction = None
            plugin._pending_session_id = None
            plugin._compaction_status = "applied"
            plugin._compaction_in_progress = False
            prompt_tokens["value"] = 190_000
            llm_service.session_stats["input_tokens"] = prompt_tokens["value"]
            return data

        plugin._get_prompt_tokens = lambda: prompt_tokens["value"]
        plugin._start_compaction = start_compaction
        plugin._apply_pending_compaction = AsyncMock(side_effect=apply_pending)

        result = await plugin._on_tool_call_pre(
            {"tool_data": {"tool_name": "terminal", "arguments": {}}},
            SimpleNamespace(),
        )

        self.assertFalse((result or {}).get("cancelled", False))
        self.assertEqual(plugin._apply_pending_compaction.await_count, 1)
        self.assertEqual(len(llm_service.conversation_history), 4)
        self.assertEqual(prompt_tokens["value"], 190_000)

    async def test_preserved_task_floor_is_bounded_to_the_hard_cap(self):
        """Preserved verbatim tasks must never rebuild a floor at or above the
        hard cap: the newest survive, the dropped count is said in the summary."""
        plugin, _event_bus, llm_service = _plugin()
        llm_service.api_service._provider.config.context_window = 20_000
        self.assertEqual(plugin._get_hard_cap_threshold(), 20_000)
        newest = _message("user", "[hub task] newest assignment " + "x" * 3900)
        preserved = [
            _message("user", f"[hub task] old assignment {index} " + "y" * 3900)
            for index in range(19)
        ] + [newest]
        to_keep = [_message("assistant", "recent " + "z" * 2000)]

        compacted = plugin._build_compacted_history(
            _message("system", "system prompt"),
            "summary of the summarized turns",
            to_keep,
            preserved,
            compaction_round=1,
        )

        self.assertLess(plugin._estimate_history_tokens(compacted), 20_000)
        self.assertIn(newest, compacted)
        dropped = len(preserved) - len([m for m in compacted if m in preserved])
        self.assertGreater(dropped, 0)
        drop_notes = [m for m in compacted if "[Dropped " in m.content]
        self.assertEqual(len(drop_notes), 1)
        # one note, not one per dropped task
        self.assertEqual(drop_notes[0].content.count("[Dropped "), 1)
        self.assertIn(
            f"[Dropped {dropped} oldest preserved task", drop_notes[0].content
        )

    def test_manual_threshold_cannot_exceed_model_percentage(self):
        plugin, _event_bus, llm_service = _plugin(prompt_tokens=410_000)
        plugin.config.values["plugins.context_compaction.token_threshold_k"] = 272
        llm_service.api_service._provider.config.context_window = 272_000

        self.assertEqual(plugin._get_token_threshold(), 204_000)

    def test_summarization_model_precedence(self):
        plugin, _event_bus, llm_service = _plugin(prompt_tokens=10_000)
        llm_service.api_service._provider.model = "session-model"
        plugin.config.values["plugins.context_compaction.summarization_model"] = (
            "plugin-model"
        )
        plugin.config.values["kollabor.llm.fast_model"] = "fast-model"

        self.assertEqual(
            plugin._resolve_summarization_model({"model": "profile-model"}),
            "plugin-model",
        )
        plugin.config.values["plugins.context_compaction.summarization_model"] = None
        self.assertEqual(
            plugin._resolve_summarization_model({"model": "profile-model"}),
            "fast-model",
        )
        plugin.config.values["kollabor.llm.fast_model"] = None
        self.assertEqual(
            plugin._resolve_summarization_model({"model": "profile-model"}),
            "session-model",
        )

    def test_session_turn_budget_warns_once_and_is_visible(self):
        plugin, _event_bus, _llm_service = _plugin(prompt_tokens=100_000)
        plugin._session_budget_session_id = "session-410k"
        plugin._turn_count = 90

        with self.assertLogs(level="WARNING") as captured:
            plugin._warn_session_turn_budget()
            plugin._turn_count = 91
            plugin._warn_session_turn_budget()

        self.assertEqual(len(captured.records), 1)
        self.assertIn("90/90 turns", captured.output[0])
        self.assertEqual(plugin._compaction_status, "warning")
        status = plugin.get_status_lines()["C"][0]
        self.assertIn("warning", status)
        self.assertIn("91/90t", status)

    def test_session_turn_budget_defaults_disabled_and_invalid_values_stay_disabled(
        self,
    ):
        plugin, _event_bus, _llm_service = _plugin(prompt_tokens=100_000)
        key = "plugins.context_compaction.max_session_turns"
        plugin.config.values.pop(key, None)

        self.assertEqual(plugin._get_session_turn_budget(), 0)
        plugin_defaults = ContextCompactionPlugin.get_default_config()["plugins"][
            "context_compaction"
        ]
        self.assertEqual(plugin_defaults["max_session_turns"], 0)

        plugin.config.values[key] = "invalid"
        self.assertEqual(plugin._get_session_turn_budget(), 0)
        plugin.config.values[key] = "90"
        self.assertEqual(plugin._get_session_turn_budget(), 90)

    async def test_compaction_failures_roll_over_with_recovery_summary(self):
        plugin, _event_bus, llm_service = _plugin(prompt_tokens=410_000)
        plugin._compaction_status = "paused"
        plugin._shrink_floor = 410_000
        plugin._turn_count = 91
        failed_attempts = []

        def fail_compaction(reason="threshold", **_kwargs):
            failed_attempts.append(reason)
            return False

        plugin._start_compaction = fail_compaction
        result = await plugin._on_tool_call_pre(
            {"tool_data": {"tool_name": "terminal", "arguments": {}}},
            SimpleNamespace(),
        )

        self.assertFalse((result or {}).get("cancelled", False))
        self.assertEqual(len(failed_attempts), 2)
        self.assertEqual(plugin._compaction_status, "rolled_over")
        self.assertTrue(
            any(
                "Automatic context rollover" in str(message.content)
                for message in llm_service.conversation_history
            )
        )

    async def test_compaction_event_records_agent_model_and_duration(self):
        import json
        import tempfile
        from pathlib import Path

        plugin, event_bus, _llm_service = _plugin(prompt_tokens=100_000)
        plugin.config.values["plugins.context_compaction.log_compaction_events"] = True
        plugin._agent_manager = SimpleNamespace(active_agent_name="coder")
        hub_plugin = SimpleNamespace(_identity=SimpleNamespace(identity="peridot"))
        event_bus.get_service = lambda name: (
            hub_plugin if name == "hub_plugin" else None
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            session_file = Path(temp_dir) / "session.jsonl"
            plugin._conversation_logger.session_file = session_file
            await plugin._log_compaction_event(
                messages_summarized=12,
                messages_kept=8,
                summary_length=321,
                pre_count=22,
                post_count=10,
                tasks_preserved=4,
                reason="hard_cap_tool",
                duration_seconds=1.25,
                summarization_model="fast-model",
            )
            record = json.loads(session_file.read_text().splitlines()[0])

        self.assertEqual(record["agent"], "coder")
        self.assertEqual(record["hub_identity"], "peridot")
        self.assertEqual(record["reason"], "hard_cap_tool")
        self.assertEqual(record["summarization_model"], "fast-model")
        self.assertEqual(record["duration_seconds"], 1.25)

    async def test_summarizer_provider_uses_selected_precedence_model(self):
        from unittest.mock import patch

        from kollabor_ai.providers.registry import ProviderRegistry

        plugin, _event_bus, llm_service = _plugin(prompt_tokens=100_000)
        plugin.config.values["plugins.context_compaction.summarization_profile"] = (
            "summary"
        )
        plugin.config.values["plugins.context_compaction.summarization_model"] = (
            "plugin-model"
        )
        plugin.config.values["kollabor.llm.fast_model"] = "fast-model"
        llm_service.api_service._provider.model = "session-model"
        profile = {
            "provider": "openai",
            "model": "profile-model",
            "api_key": "test-key",
        }
        plugin._profile_manager = SimpleNamespace(
            get_profile=lambda _name: SimpleNamespace(to_dict=lambda: dict(profile))
        )
        provider_config = SimpleNamespace(provider="openai", model=None)
        provider = object()
        captured_profiles = []

        def make_provider_config(profile_data):
            captured_profiles.append(dict(profile_data))
            return provider_config

        plugin._call_provider_with_retry = AsyncMock(return_value="summary text")
        with patch(
            "kollabor_ai.providers.registry.create_config_from_profile",
            side_effect=make_provider_config,
        ):
            with patch.object(
                ProviderRegistry,
                "get_provider",
                new=AsyncMock(return_value=provider),
            ):
                with self.assertLogs(level="INFO") as logs:
                    result = await plugin._call_summarization_llm(
                        "conversation slice", "system prompt", max_tokens=2000
                    )

        self.assertEqual(result, "summary text")
        self.assertEqual(captured_profiles[0]["model"], "plugin-model")
        self.assertEqual(profile["model"], "profile-model")
        self.assertEqual(plugin._last_summarization_model, "plugin-model")
        self.assertIsNotNone(plugin._last_compaction_duration_seconds)
        self.assertTrue(any("summarization_complete" in line for line in logs.output))

    async def test_unrecoverable_boundary_failure_cancels_with_actionable_reason(self):
        from kollabor_events import EventBus

        plugin, _mock_bus, _llm_service = _plugin(prompt_tokens=410_000)
        event_bus = EventBus()
        plugin.event_bus = event_bus
        plugin._agent_manager = None
        plugin._compaction_status = "paused"
        plugin._shrink_floor = 410_000
        plugin._turn_count = 91
        plugin._start_compaction = MagicMock(return_value=False)
        plugin._rollover_with_summary = AsyncMock(return_value=False)
        await plugin.register_hooks()

        result = await event_bus.emit_with_hooks(
            EventType.TOOL_CALL_PRE,
            {"tool_data": {"tool_name": "terminal", "arguments": {}}},
            "tool_executor",
        )

        self.assertTrue(result["cancelled"])
        decision = result["main"]["final_data"]["permission_decision"]
        self.assertIn("tool call was stopped", decision["reason"])
        self.assertEqual(plugin._compaction_status, "failed")

    def test_paused_budget_is_visible_in_widget_and_alert(self):
        plugin, _event_bus, _llm_service = _plugin(prompt_tokens=410_000)
        plugin.config.values["plugins.context_compaction.token_threshold_k"] = 150
        self.assertEqual(plugin._get_token_threshold(), 150_000)
        self.assertEqual(plugin._get_hard_cap_threshold(), 272_000)

        plugin._set_compaction_status(
            "paused",
            "Context compaction is paused at 410K/272K.",
            action="Run /compact now.",
        )
        widget_text = plugin._render_widget(width=100, context=None)

        self.assertIn("paused", widget_text)
        self.assertIn("410K/272K", widget_text)
        plugin.renderer.message_coordinator.display_message_sequence.assert_called_once()

    def test_config_widgets_expose_model_and_session_budget_controls(self):
        widget_paths = {
            widget["config_path"]
            for widget in ContextCompactionPlugin.get_config_widgets()["widgets"]
        }
        self.assertIn("plugins.context_compaction.summarization_model", widget_paths)
        self.assertIn("plugins.context_compaction.max_session_turns", widget_paths)
        self.assertIn(
            "plugins.context_compaction.hard_cap_compaction_attempts", widget_paths
        )

    async def test_tool_boundary_guard_runs_through_event_bus(self):
        from kollabor_events import EventBus

        plugin, _mock_bus, llm_service = _plugin()
        event_bus = EventBus()
        plugin.event_bus = event_bus
        plugin._agent_manager = None
        plugin._compaction_status = "paused"
        plugin._shrink_floor = 410_000
        plugin._compaction_round = 23
        plugin._turn_count = 91
        original_history = list(llm_service.conversation_history)
        compacted_history = [
            original_history[0],
            _message("user", "[Previous Context Summary] task is still active"),
            *original_history[-2:],
        ]
        prompt_tokens = {"value": 410_000}

        async def run_compaction():
            plugin._compaction_round += 1
            plugin._pending_compaction = compacted_history
            plugin._pending_session_id = "session-410k"
            plugin._pre_compaction_len = len(original_history)
            plugin._compaction_status = "ready_to_retry"
            plugin._compaction_in_progress = False

        def start_compaction(reason="threshold", **_kwargs):
            plugin._compaction_reason = reason
            plugin._compaction_in_progress = True
            task = asyncio.create_task(run_compaction())
            plugin._compaction_task = task
            plugin._compaction_tasks.add(task)
            task.add_done_callback(plugin._compaction_task_done)
            return True

        async def apply_pending(data, _event):
            llm_service.conversation_history = list(plugin._pending_compaction)
            plugin._pending_compaction = None
            plugin._pending_session_id = None
            plugin._compaction_status = "applied"
            plugin._compaction_in_progress = False
            prompt_tokens["value"] = 190_000
            llm_service.session_stats["input_tokens"] = prompt_tokens["value"]
            return data

        plugin._get_prompt_tokens = lambda: prompt_tokens["value"]
        plugin.config.values["plugins.context_compaction.token_threshold_k"] = 150
        self.assertEqual(plugin._get_token_threshold(), 150_000)
        self.assertEqual(plugin._get_hard_cap_threshold(), 272_000)
        plugin._start_compaction = start_compaction
        plugin._apply_pending_compaction = AsyncMock(side_effect=apply_pending)
        await plugin.register_hooks()

        result = await event_bus.emit_with_hooks(
            EventType.TOOL_CALL_PRE,
            {"tool_data": {"tool_name": "terminal", "arguments": {}}},
            "tool_executor",
        )

        self.assertFalse((result or {}).get("cancelled", False))
        self.assertEqual(plugin._compaction_round, 24)
        self.assertEqual(llm_service.conversation_history, compacted_history)
        self.assertEqual(prompt_tokens["value"], 190_000)

    def test_new_session_resets_turn_and_hard_cap_guard_state(self):
        plugin, _event_bus, _llm_service = _plugin(prompt_tokens=410_000)
        plugin._session_budget_session_id = "previous-session"
        plugin._turn_count = 91
        plugin._compaction_round = 23
        plugin._consecutive_failures = 3
        plugin._disabled_for_session = True
        plugin._shrink_floor = 410_000
        plugin._compaction_just_applied = True
        plugin._token_usage_stale_after_compaction = True
        plugin._token_estimate_scale = 0.5
        plugin._last_hard_cap_guard_turn = 91
        plugin._last_hard_cap_guard_signature = (12, 3000)
        plugin._compaction_status = "paused"
        plugin._conversation_logger.session_id = "new-session"

        plugin._sync_session_budget()

        self.assertEqual(plugin._turn_count, 0)
        self.assertEqual(plugin._compaction_round, 0)
        self.assertEqual(plugin._consecutive_failures, 0)
        self.assertFalse(plugin._disabled_for_session)
        self.assertIsNone(plugin._shrink_floor)
        self.assertFalse(plugin._compaction_just_applied)
        self.assertFalse(plugin._token_usage_stale_after_compaction)
        self.assertIsNone(plugin._token_estimate_scale)
        self.assertIsNone(plugin._last_hard_cap_guard_turn)
        self.assertEqual(plugin._compaction_status, "idle")

    def test_session_budget_restores_turns_and_age_from_session_stats(self):
        import time

        plugin, _event_bus, llm_service = _plugin(prompt_tokens=100_000)
        started_at = time.time() - 3600
        llm_service.session_stats.update(
            {
                "context_compaction_session_id": "session-410k",
                "context_compaction_turn_count": 91,
                "context_compaction_started_at": started_at,
            }
        )
        plugin._session_budget_session_id = None

        plugin._sync_session_budget()

        self.assertEqual(plugin._turn_count, 91)
        self.assertEqual(plugin._session_budget_session_id, "session-410k")
        self.assertGreaterEqual(
            time.monotonic() - plugin._session_started_monotonic, 3599
        )

    async def test_request_boundary_cancels_when_hard_cap_recovery_fails(self):
        from kollabor_events import EventBus

        plugin, _mock_bus, _llm_service = _plugin(prompt_tokens=410_000)
        event_bus = EventBus()
        plugin.event_bus = event_bus
        plugin._agent_manager = None
        plugin._start_compaction = MagicMock(return_value=False)
        plugin._rollover_with_summary = AsyncMock(return_value=False)
        await plugin.register_hooks()

        result = await event_bus.emit_with_hooks(
            EventType.LLM_REQUEST_PRE,
            {"model": "gpt-6-luna", "message_count": 10},
            "llm_service",
        )

        self.assertTrue(result["cancelled"])
        decision = result["main"]["final_data"]["permission_decision"]
        self.assertIn("next model request", decision["reason"])
        self.assertEqual(plugin._compaction_status, "failed")
