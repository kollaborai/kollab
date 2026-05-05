"""Unit tests for context compaction dual-gate mechanism.

Tests _should_compact() and _on_llm_turn_complete() gate logic:
  Gate 1: prompt_tokens >= token_threshold_k * 1000
  Gate 2: human turns >= min_human_turns

Both gates must pass for compaction to trigger.

Run: python -m pytest tests/unit/test_compaction_dual_gate.py -v
"""

import asyncio
import unittest
from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import MagicMock

from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import ContextCompactionPlugin


def _msg(role: str, content: str, **meta) -> ConversationMessage:
    return ConversationMessage(
        role=role, content=content, metadata=meta, timestamp=datetime.now()
    )


def _make_plugin(
    token_threshold_k: int = 100,
    min_human_turns: int = 4,
    keep_recent: int = 4,
    enabled: bool = True,
) -> ContextCompactionPlugin:
    config = MagicMock()
    values: Dict[str, Any] = {
        "plugins.context_compaction.enabled": enabled,
        "plugins.context_compaction.token_threshold_k": token_threshold_k,
        "plugins.context_compaction.min_human_turns": min_human_turns,
        "plugins.context_compaction.keep_recent": keep_recent,
        "plugins.context_compaction.max_summary_tokens": 2000,
        "plugins.context_compaction.log_compaction_events": False,
    }
    config.get = lambda key, default=None: values.get(key, default)

    bus = MagicMock()
    renderer = MagicMock()
    plugin = ContextCompactionPlugin("test", bus, renderer, config)
    return plugin


def _wire_history(
    plugin: ContextCompactionPlugin,
    history: List[ConversationMessage],
    prompt_tokens: int = 0,
) -> None:
    llm_service = MagicMock()
    llm_service.conversation_history = history
    llm_service.session_stats = {"input_tokens": prompt_tokens, "output_tokens": 0}
    api_service = MagicMock()
    api_service.get_last_token_usage.return_value = {"prompt_tokens": prompt_tokens}
    llm_service.api_service = api_service
    plugin._llm_service = llm_service

    conv_logger = MagicMock()
    conv_logger.session_id = "test-session"
    plugin._conversation_logger = conv_logger


class TestDualGate(unittest.TestCase):
    """_should_compact() requires both token AND turn gates to pass."""

    def test_token_below_threshold_turns_ok(self) -> None:
        plugin = _make_plugin(token_threshold_k=100, min_human_turns=2)
        history = [_msg("user", "hi"), _msg("assistant", "yo")]
        _wire_history(plugin, history, prompt_tokens=50_000)

        self.assertFalse(plugin._should_compact(history))

    def test_token_above_threshold_turns_below(self) -> None:
        plugin = _make_plugin(token_threshold_k=100, min_human_turns=4)
        history = [_msg("user", f"msg{i}") for i in range(3)]
        _wire_history(plugin, history, prompt_tokens=150_000)

        self.assertFalse(plugin._should_compact(history))

    def test_both_gates_pass(self) -> None:
        plugin = _make_plugin(token_threshold_k=100, min_human_turns=4)
        history = [_msg("user", f"msg{i}") for i in range(5)]
        _wire_history(plugin, history, prompt_tokens=150_000)

        self.assertTrue(plugin._should_compact(history))

    def test_hub_messages_excluded_from_turn_count(self) -> None:
        plugin = _make_plugin(token_threshold_k=100, min_human_turns=3)
        history = [
            _msg("user", "real msg 1"),
            _msg("user", "hub msg", hub_message=True),
            _msg("user", "real msg 2"),
        ]
        _wire_history(plugin, history, prompt_tokens=150_000)

        self.assertFalse(plugin._should_compact(history))

    def test_zero_tokens_never_compacts(self) -> None:
        plugin = _make_plugin(token_threshold_k=1, min_human_turns=1)
        history = [_msg("user", "hi")]
        _wire_history(plugin, history, prompt_tokens=0)

        self.assertFalse(plugin._should_compact(history))

    def test_get_token_threshold_multiplies_by_1000(self) -> None:
        plugin = _make_plugin(token_threshold_k=50)
        self.assertEqual(plugin._get_token_threshold(), 50_000)

    def test_custom_threshold_values(self) -> None:
        plugin = _make_plugin(token_threshold_k=200, min_human_turns=10)
        history = [_msg("user", f"msg{i}") for i in range(12)]
        _wire_history(plugin, history, prompt_tokens=250_000)

        self.assertTrue(plugin._should_compact(history))


class TestOnLlmTurnComplete(unittest.IsolatedAsyncioTestCase):
    """_on_llm_turn_complete() wraps _should_compact with state guards."""

    async def test_compaction_disabled_in_config(self) -> None:
        plugin = _make_plugin(enabled=False)
        history = [_msg("user", "hi")]
        _wire_history(plugin, history, prompt_tokens=999_999)

        result = await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertIsNotNone(result)
        self.assertFalse(plugin._compaction_in_progress)

    async def test_compaction_disabled_for_session(self) -> None:
        plugin = _make_plugin()
        plugin._disabled_for_session = True
        history = [_msg("user", "hi")]
        _wire_history(plugin, history, prompt_tokens=999_999)

        result = await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertIsNotNone(result)
        self.assertFalse(plugin._compaction_in_progress)

    async def test_already_compacting_skips(self) -> None:
        plugin = _make_plugin()
        plugin._compaction_in_progress = True
        history = [_msg("user", "hi")]
        _wire_history(plugin, history, prompt_tokens=999_999)

        result = await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertIsNotNone(result)

    async def test_empty_history_returns_unchanged(self) -> None:
        plugin = _make_plugin()
        _wire_history(plugin, [], prompt_tokens=999_999)

        result = await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertIsNotNone(result)
        self.assertFalse(plugin._compaction_in_progress)

    async def test_gates_pass_triggers_compaction(self) -> None:
        plugin = _make_plugin(token_threshold_k=100, min_human_turns=2)
        history = [_msg("user", f"msg{i}") for i in range(4)]
        _wire_history(plugin, history, prompt_tokens=150_000)

        result = await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertIsNotNone(result)
        self.assertTrue(plugin._compaction_in_progress)
        self.assertIsNotNone(plugin._compaction_task)

        # cleanup
        if plugin._compaction_task:
            plugin._compaction_task.cancel()
            try:
                await plugin._compaction_task
            except asyncio.CancelledError:
                pass

    async def test_gates_fail_no_compaction(self) -> None:
        plugin = _make_plugin(token_threshold_k=100, min_human_turns=4)
        history = [_msg("user", "only one")]
        _wire_history(plugin, history, prompt_tokens=50_000)

        result = await plugin._on_llm_turn_complete({}, MagicMock())
        self.assertIsNotNone(result)
        self.assertFalse(plugin._compaction_in_progress)


def _make_auto_plugin(
    compaction_ratio: float = 0.75,
    min_human_turns: int = 6,
    keep_recent: int = 8,
) -> ContextCompactionPlugin:
    """Create a plugin with token_threshold_k=0 (auto-detect mode)."""
    config = MagicMock()
    values: Dict[str, Any] = {
        "plugins.context_compaction.enabled": True,
        "plugins.context_compaction.token_threshold_k": 0,
        "plugins.context_compaction.compaction_ratio": compaction_ratio,
        "plugins.context_compaction.min_human_turns": min_human_turns,
        "plugins.context_compaction.keep_recent": keep_recent,
        "plugins.context_compaction.max_summary_tokens": 2000,
        "plugins.context_compaction.log_compaction_events": False,
    }
    config.get = lambda key, default=None: values.get(key, default)

    bus = MagicMock()
    renderer = MagicMock()
    plugin = ContextCompactionPlugin("test", bus, renderer, config)
    return plugin


def _wire_profile(plugin: ContextCompactionPlugin, model: str, provider: str) -> None:
    """Wire a mock profile manager with the given model/provider."""
    profile = MagicMock()
    profile.get_model.return_value = model
    profile.get_provider.return_value = provider

    pm = MagicMock()
    pm.get_active_profile.return_value = profile
    plugin._profile_manager = pm


class TestAutoDetectThreshold(unittest.TestCase):
    """Provider-based auto-detection of compaction threshold."""

    def test_anthropic_claude_opus(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.75)
        _wire_profile(plugin, "claude-opus-4-6", "anthropic")
        # 1M context * 0.75 = 750K
        self.assertEqual(plugin._get_token_threshold(), 750_000)

    def test_anthropic_claude_haiku(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.75)
        _wire_profile(plugin, "claude-haiku-4-5", "anthropic")
        # 200K context * 0.75 = 150K
        self.assertEqual(plugin._get_token_threshold(), 150_000)

    def test_openai_gpt54(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.75)
        _wire_profile(plugin, "gpt-5.4", "openai")
        # 1.05M context * 0.75 = 787.5K
        self.assertEqual(plugin._get_token_threshold(), 787_500)

    def test_gemini_31_pro(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.80)
        _wire_profile(plugin, "gemini-3.1-pro-preview", "gemini")
        # 1M context * 0.80 = 800K
        self.assertEqual(plugin._get_token_threshold(), 800_000)

    def test_unknown_model_falls_back_to_provider(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.75)
        _wire_profile(plugin, "some-future-model", "anthropic")
        # anthropic default 200K * 0.75 = 150K
        self.assertEqual(plugin._get_token_threshold(), 150_000)

    def test_unknown_provider_falls_back_to_hardcoded(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.75)
        _wire_profile(plugin, "mystery-model", "mystery-provider")
        # no match -> fallback 100K
        self.assertEqual(plugin._get_token_threshold(), 100_000)

    def test_manual_override_takes_precedence(self) -> None:
        plugin = _make_plugin(token_threshold_k=50)
        _wire_profile(plugin, "claude-opus-4-6", "anthropic")
        # manual 50K overrides auto-detect
        self.assertEqual(plugin._get_token_threshold(), 50_000)

    def test_no_profile_manager_falls_back(self) -> None:
        plugin = _make_auto_plugin()
        plugin._profile_manager = None
        self.assertEqual(plugin._get_token_threshold(), 100_000)

    def test_ratio_clamped_low(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.10)
        _wire_profile(plugin, "claude-opus-4-6", "anthropic")
        # clamped to 0.50: 1M * 0.50 = 500K
        self.assertEqual(plugin._get_token_threshold(), 500_000)

    def test_ratio_clamped_high(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=1.5)
        _wire_profile(plugin, "claude-opus-4-6", "anthropic")
        # clamped to 0.95: 1M * 0.95 = 950K
        self.assertEqual(plugin._get_token_threshold(), 950_000)

    def test_context_window_resolution_gemini_prefix(self) -> None:
        plugin = _make_auto_plugin()
        _wire_profile(plugin, "gemini-2.5-flash-latest", "gemini")
        # matches "gemini-2.5-flash" prefix -> 1,048,576
        self.assertEqual(plugin._resolve_context_window(), 1_048_576)

    def test_context_window_longest_prefix_wins(self) -> None:
        plugin = _make_auto_plugin()
        _wire_profile(plugin, "gemini-3.1-pro-preview", "gemini")
        # "gemini-3.1-pro" (len 14) wins over "gemini" (len 6)
        self.assertEqual(plugin._resolve_context_window(), 1_000_000)

    def test_glm_model(self) -> None:
        plugin = _make_auto_plugin(compaction_ratio=0.75)
        _wire_profile(plugin, "glm-5.1-turbo", "custom")
        # matches "glm-5" prefix -> 202,752 * 0.75 = 152064
        self.assertEqual(plugin._get_token_threshold(), 152_064)


class TestKeepRecentScaling(unittest.TestCase):
    """keep_recent scales with context window size."""

    def test_large_context_scales_up(self) -> None:
        plugin = _make_auto_plugin(keep_recent=8)
        _wire_profile(plugin, "claude-opus-4-6", "anthropic")
        # 1M / 100K = 10, max(8, 10) = 10
        ctx = plugin._resolve_context_window()
        keep = max(8, ctx // 100_000)
        self.assertEqual(keep, 10)

    def test_small_context_uses_config(self) -> None:
        plugin = _make_auto_plugin(keep_recent=8)
        _wire_profile(plugin, "deepseek-chat", "custom")
        # 128K / 100K = 1, max(8, 1) = 8
        ctx = plugin._resolve_context_window()
        keep = max(8, ctx // 100_000)
        self.assertEqual(keep, 8)

    def test_gpt54_mini_context_stays_at_config(self) -> None:
        plugin = _make_auto_plugin(keep_recent=8)
        _wire_profile(plugin, "gpt-5.4-mini", "openai")
        # 400K / 100K = 4, max(8, 4) = 8
        ctx = plugin._resolve_context_window()
        keep = max(8, ctx // 100_000)
        self.assertEqual(keep, 8)


if __name__ == "__main__":
    unittest.main()
