"""Typed snapshot and RPC coverage for context compaction runtime status."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.state.handlers import register_state_handlers
from kollabor.state.remote import RemoteStateService
from kollabor.state.snapshots import ContextCompactionSnapshot


def _snapshot_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "observed_at": "2026-10-31T08:30:00+00:00",
        "automatic_compaction_enabled": True,
        "automatic_compaction_disabled_reason": None,
        "model_context_window_tokens": None,
        "last_request_prompt_tokens": None,
        "current_prompt_tokens": 0,
        "current_prompt_tokens_estimated": False,
        "outgoing_prompt_tokens": 0,
        "outgoing_prompt_tokens_estimated": False,
        "token_gate_prompt_tokens": 0,
        "token_gate_prompt_tokens_estimated": False,
        "automatic_compaction_threshold_tokens": 100_000,
        "threshold_source": "fallback",
        "configured_token_threshold_k": 0,
        "tokens_until_automatic_compaction": 0,
        "hard_cap_tokens": 100_000,
        "compaction_status": "idle",
        "compaction_message": "",
        "compaction_action": "",
        "available": True,
        "error": None,
    }


def test_context_compaction_snapshot_roundtrips_nulls_and_real_zeroes() -> None:
    payload = _snapshot_payload()

    snapshot = ContextCompactionSnapshot.from_dict(payload)

    assert snapshot.model_context_window_tokens is None
    assert snapshot.current_prompt_tokens == 0
    assert snapshot.tokens_until_automatic_compaction == 0
    assert json.loads(json.dumps(snapshot.to_dict())) == payload


@pytest.mark.asyncio
async def test_context_compaction_handler_serializes_typed_snapshot() -> None:
    payload = _snapshot_payload()
    snapshot = ContextCompactionSnapshot.from_dict(payload)
    state_service = SimpleNamespace(
        get_context_compaction_snapshot=AsyncMock(return_value=snapshot)
    )
    registered: dict[str, object] = {}
    rpc_server = SimpleNamespace(register=registered.__setitem__)

    register_state_handlers(rpc_server, state_service)
    handler = registered["state.get_context_compaction_snapshot"]
    result = await handler({})  # type: ignore[operator]

    assert result == payload


@pytest.mark.asyncio
async def test_remote_state_service_reconstructs_typed_snapshot() -> None:
    payload = _snapshot_payload()

    async def call(method: str, params: dict[str, object], timeout: float) -> object:
        assert method == "state.get_context_compaction_snapshot"
        assert params == {}
        assert timeout == RemoteStateService.DEFAULT_TIMEOUT
        return payload

    remote = RemoteStateService(SimpleNamespace(call=call))

    snapshot = await remote.get_context_compaction_snapshot()

    assert isinstance(snapshot, ContextCompactionSnapshot)
    assert snapshot.to_dict() == payload
