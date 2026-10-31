"""LocalStateService context-compaction snapshot provider tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kollabor.state.local import LocalStateService
from kollabor.state.snapshots import ContextCompactionSnapshot


def _plugin_snapshot() -> dict[str, object]:
    return {
        "schema_version": 1,
        "observed_at": "2026-10-31T08:30:00+00:00",
        "automatic_compaction_enabled": True,
        "automatic_compaction_disabled_reason": None,
        "model_context_window_tokens": 128_000,
        "last_request_prompt_tokens": 64_000,
        "current_prompt_tokens": 63_500,
        "current_prompt_tokens_estimated": False,
        "outgoing_prompt_tokens": 63_500,
        "outgoing_prompt_tokens_estimated": False,
        "token_gate_prompt_tokens": 64_000,
        "token_gate_prompt_tokens_estimated": False,
        "automatic_compaction_threshold_tokens": 96_000,
        "threshold_source": "automatic",
        "configured_token_threshold_k": 0,
        "tokens_until_automatic_compaction": 32_000,
        "hard_cap_tokens": 128_000,
        "compaction_status": "idle",
        "compaction_message": "",
        "compaction_action": "",
    }


def _state_service(event_bus: object) -> LocalStateService:
    return LocalStateService(
        llm_service=object(),
        profile_manager=object(),
        event_bus=event_bus,
    )


@pytest.mark.asyncio
async def test_local_state_service_reads_registered_compaction_plugin() -> None:
    raw_snapshot = _plugin_snapshot()
    plugin = SimpleNamespace(
        get_runtime_status_snapshot=Mock(return_value=raw_snapshot)
    )
    event_bus = SimpleNamespace(get_service=Mock(return_value=plugin))

    snapshot = await _state_service(event_bus).get_context_compaction_snapshot()

    assert snapshot == ContextCompactionSnapshot.from_dict(raw_snapshot)
    assert snapshot.automatic_compaction_enabled is True
    assert snapshot.automatic_compaction_disabled_reason is None
    assert snapshot.available is True
    event_bus.get_service.assert_called_once_with("context_compaction_plugin")


@pytest.mark.asyncio
async def test_local_state_service_marks_missing_plugin_unavailable() -> None:
    event_bus = SimpleNamespace(get_service=Mock(return_value=None))

    snapshot = await _state_service(event_bus).get_context_compaction_snapshot()

    assert snapshot.available is False
    assert snapshot.model_context_window_tokens is None
    assert snapshot.error == "Context compaction status unavailable"


@pytest.mark.asyncio
async def test_local_state_service_hides_plugin_exception_details() -> None:
    plugin = SimpleNamespace(
        get_runtime_status_snapshot=Mock(side_effect=RuntimeError("private detail"))
    )
    event_bus = SimpleNamespace(get_service=Mock(return_value=plugin))

    snapshot = await _state_service(event_bus).get_context_compaction_snapshot()

    assert snapshot.available is False
    assert snapshot.error == "Context compaction status unavailable"
    assert "private detail" not in str(snapshot.to_dict())
