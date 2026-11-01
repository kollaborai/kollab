"""Runtime-status snapshot semantics for context compaction."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

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


def _plugin(
    *, context_window: int | None, input_tokens: int, history_chars: int = 3000
):
    history = [SimpleNamespace(content="x" * history_chars)]
    provider_config = SimpleNamespace(context_window=context_window)
    api_service = SimpleNamespace(
        _provider=SimpleNamespace(config=provider_config),
        get_last_token_usage=lambda: None,
    )
    llm_service = SimpleNamespace(
        conversation_history=history,
        session_stats={
            "input_tokens": input_tokens,
            "input_tokens_estimated": False,
            "output_tokens": 0,
        },
        api_service=api_service,
    )
    plugin = ContextCompactionPlugin(
        "context_compaction", MagicMock(), MagicMock(), _Config()
    )
    plugin._llm_service = llm_service
    plugin._compaction_status = "idle"
    plugin._compaction_status_message = ""
    plugin._compaction_status_action = ""
    return plugin


def test_snapshot_marks_stale_request_historical_and_gate_unknown() -> None:
    plugin = _plugin(context_window=1_000_000, input_tokens=250_000)
    plugin._token_usage_stale_after_compaction = True
    plugin._token_estimate_scale = 0.5
    plugin._compaction_status = "paused"
    plugin._compaction_status_message = "Compaction is paused."
    plugin._compaction_status_action = "Run /compact now."

    snapshot = plugin.get_runtime_status_snapshot()

    assert snapshot["last_request_prompt_tokens"] == 250_000
    assert snapshot["current_prompt_tokens"] == 500
    assert snapshot["current_prompt_tokens_estimated"] is True
    assert snapshot["outgoing_prompt_tokens"] == 500
    assert snapshot["token_gate_prompt_tokens"] is None
    assert snapshot["token_gate_prompt_tokens_estimated"] is None
    assert snapshot["model_context_window_tokens"] == 1_000_000
    assert snapshot["automatic_compaction_threshold_tokens"] == 272_000
    assert snapshot["tokens_until_automatic_compaction"] is None
    assert snapshot["hard_cap_tokens"] == 272_000
    assert snapshot["automatic_compaction_enabled"] is True
    assert snapshot["automatic_compaction_disabled_reason"] is None
    assert snapshot["compaction_status"] == "paused"
    assert snapshot["compaction_message"] == "Compaction is paused."
    assert snapshot["compaction_action"] == "Run /compact now."



def test_snapshot_keeps_gate_unknown_after_swap_when_usage_is_missing() -> None:
    plugin = _plugin(context_window=1_000_000, input_tokens=0)
    plugin._token_usage_stale_after_compaction = True

    snapshot = plugin.get_runtime_status_snapshot()

    assert snapshot["last_request_prompt_tokens"] is None
    assert snapshot["current_prompt_tokens"] > 0
    assert snapshot["current_prompt_tokens_estimated"] is True
    assert snapshot["token_gate_prompt_tokens"] is None
    assert snapshot["token_gate_prompt_tokens_estimated"] is None
    assert snapshot["tokens_until_automatic_compaction"] is None


def test_snapshot_keeps_window_threshold_and_hard_cap_distinct() -> None:
    plugin = _plugin(
        context_window=100_000,
        input_tokens=30_000,
        history_chars=90_000,
    )

    snapshot = plugin.get_runtime_status_snapshot()

    assert snapshot["model_context_window_tokens"] == 100_000
    assert snapshot["automatic_compaction_threshold_tokens"] == 75_000
    assert snapshot["tokens_until_automatic_compaction"] == 45_000
    assert snapshot["hard_cap_tokens"] == 100_000


def test_snapshot_marks_unknown_window_and_reports_policy_fallback() -> None:
    plugin = _plugin(context_window=None, input_tokens=110_000)

    snapshot = plugin.get_runtime_status_snapshot()

    assert snapshot["model_context_window_tokens"] is None
    assert snapshot["automatic_compaction_threshold_tokens"] == 100_000
    assert snapshot["hard_cap_tokens"] == 100_000
    assert snapshot["threshold_source"] == "fallback"
    assert snapshot["tokens_until_automatic_compaction"] == 0
    # Reaching the token gate is not the same as a compaction being active.
    assert snapshot["compaction_status"] == "idle"


def test_snapshot_marks_missing_provider_usage_as_estimated() -> None:
    plugin = _plugin(context_window=1_000_000, input_tokens=0, history_chars=30_000)

    snapshot = plugin.get_runtime_status_snapshot()

    assert snapshot["last_request_prompt_tokens"] is None
    assert snapshot["current_prompt_tokens"] == 10_000
    assert snapshot["current_prompt_tokens_estimated"] is True
    assert snapshot["token_gate_prompt_tokens"] == 10_000
    assert snapshot["token_gate_prompt_tokens_estimated"] is True
    assert snapshot["tokens_until_automatic_compaction"] == 262_000
    assert isinstance(datetime.fromisoformat(snapshot["observed_at"]), datetime)


def test_threshold_source_only_reports_a_binding_manual_override() -> None:
    plugin = _plugin(context_window=1_000_000, input_tokens=200_000)

    for manual_k, expected_threshold, expected_source in (
        (200, 200_000, "manual"),
        (272, 272_000, "automatic"),
        (300, 272_000, "automatic"),
    ):
        plugin.config.values["plugins.context_compaction.token_threshold_k"] = manual_k
        snapshot = plugin.get_runtime_status_snapshot()
        assert snapshot["automatic_compaction_threshold_tokens"] == expected_threshold
        assert snapshot["threshold_source"] == expected_source

    plugin = _plugin(context_window=None, input_tokens=60_000)
    plugin.config.values["plugins.context_compaction.token_threshold_k"] = 50
    snapshot = plugin.get_runtime_status_snapshot()
    assert snapshot["automatic_compaction_threshold_tokens"] == 50_000
    assert snapshot["threshold_source"] == "manual"


def test_snapshot_reports_effective_session_compaction_enablement() -> None:
    plugin = _plugin(context_window=1_000_000, input_tokens=100_000)
    plugin._disabled_for_session = True
    plugin._compaction_status = "failed"

    snapshot = plugin.get_runtime_status_snapshot()

    assert snapshot["automatic_compaction_enabled"] is False
    assert snapshot["automatic_compaction_disabled_reason"] == "session_failures"
    assert snapshot["compaction_status"] == "failed"

    plugin = _plugin(context_window=1_000_000, input_tokens=100_000)
    plugin.config.values["plugins.context_compaction.enabled"] = False
    snapshot = plugin.get_runtime_status_snapshot()

    assert snapshot["automatic_compaction_enabled"] is False
    assert snapshot["automatic_compaction_disabled_reason"] == "configuration"
