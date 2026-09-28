"""Task-scoped provider/tool activity is observable without request contents."""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.state.local import LocalStateService
from kollabor.state.snapshots import ActiveOperationSnapshot, ProcessingSnapshot
from kollabor_agent.execution_context import remote_task_id
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.profile_manager import LLMProfile
from kollabor_ai.providers.errors import APIConnectionError
from kollabor_ai.providers.models import (
    ProviderType,
    TextContent,
    UnifiedResponse,
    UsageInfo,
)

TASK_A = "a" * 32
TASK_B = "b" * 32
TASK_C = "c" * 32


def _tracker() -> LLMService:
    service = object.__new__(LLMService)
    service._initialize_active_operation_tracking()
    return service


def test_operation_generation_rejects_old_provider_updates_and_cancel_pairs():
    service = _tracker()
    token_a = remote_task_id.set(TASK_A)
    try:
        generation_a = service._observe_provider_operation(
            phase="provider_request",
            provider="openai",
            request_id="a" * 32,
            operation_generation=None,
            begin=True,
        )
        assert generation_a == 1

        token_b = remote_task_id.set(TASK_B)
        try:
            generation_b = service._observe_provider_operation(
                phase="provider_request",
                provider="anthropic",
                request_id="b" * 32,
                operation_generation=None,
                begin=True,
            )
        finally:
            remote_task_id.reset(token_b)
        assert generation_b == 2

        assert not service._update_active_operation(
            "provider_response",
            operation_generation=generation_a,
            provider="openai",
            request_id="a" * 32,
        )
        assert service.get_active_operation_snapshot()["task_id"] == TASK_B
        assert service.get_active_operation_snapshot()["generation"] == generation_b

        service._record_operation_cancellation(TASK_B, 17)
        assert service._set_operation_cleanup_state(TASK_B, 17, "pending")
        assert not service._set_operation_cleanup_state(TASK_A, 17, "complete")
        assert not service._set_operation_cleanup_state(TASK_B, 16, "complete")

        # A late callback from the cancelled task cannot replace cleanup state.
        token_b2 = remote_task_id.set(TASK_B)
        try:
            assert not service._update_active_operation(
                "provider_response",
                operation_generation=generation_b,
                provider="anthropic",
                request_id="b" * 32,
            )
        finally:
            remote_task_id.reset(token_b2)
        assert service.get_active_operation_snapshot()["cleanup_state"] == "pending"

        token_c = remote_task_id.set(TASK_C)
        try:
            generation_c = service._begin_active_operation("provider_request", provider="openai", request_id="c" * 32)
        finally:
            remote_task_id.reset(token_c)
        assert generation_c == 3
        assert not service._set_operation_cleanup_state(TASK_B, 17, "complete")
        assert service.get_active_operation_snapshot()["task_id"] == TASK_C
    finally:
        remote_task_id.reset(token_a)


def test_tool_observer_reports_permission_then_execution_and_redacts_inputs():
    tracker = _tracker()
    phases = []
    tracker_operation_observer = tracker._observe_tool_operation

    class EventBus:
        async def emit_with_hooks(self, *args, **kwargs):
            return {}

    executor = ToolExecutor(mcp_integration=None, event_bus=EventBus())
    executor.set_operation_observer(
        lambda **event: phases.append(event["phase"]) or tracker_operation_observer(**event)
    )

    async def handler(_tool_data):
        return ToolExecutionResult("provider-call-1", "hub_msg", True)

    executor.register_plugin_handler("hub_msg", handler)
    task_token = remote_task_id.set(TASK_A)
    try:
        asyncio.run(
            executor.execute_tool(
                {
                    "type": "hub_msg",
                    "id": "provider-call-1",
                    "to": "relay:do-not-export",
                    "message": "PRIVATE_MESSAGE_SENTINEL",
                    "params": {"api_key": "PRIVATE_KEY_SENTINEL"},
                }
            )
        )
    finally:
        remote_task_id.reset(task_token)

    snapshot = tracker.get_active_operation_snapshot()
    assert phases == ["tool_permission", "tool_execution", "tool_complete"]
    assert snapshot["task_id"] == TASK_A
    assert snapshot["phase"] == "tool_complete"
    assert snapshot["tool_name"] == "hub_msg"
    assert snapshot["tool_call_id"] == "provider-call-1"
    assert snapshot["tool_call_id_generated"] is False
    encoded = json.dumps(snapshot)
    assert "PRIVATE_MESSAGE_SENTINEL" not in encoded
    assert "PRIVATE_KEY_SENTINEL" not in encoded
    assert "relay:do-not-export" not in encoded


@pytest.mark.parametrize(
    "provider_tool_id",
    [
        "call_y5ut05W2QVuPaWTtyN4lJic7",
        "toolu_01AbCdEfGhIjKlMnOpQrStUv",
    ],
)
def test_provider_tool_ids_preserve_bounded_mixed_case_values(provider_tool_id):
    tracker = _tracker()

    class EventBus:
        async def emit_with_hooks(self, *args, **kwargs):
            return {}

    executor = ToolExecutor(mcp_integration=None, event_bus=EventBus())
    executor.register_plugin_handler(
        "hub_msg",
        lambda _tool_data: asyncio.sleep(
            0, result=ToolExecutionResult(provider_tool_id, "hub_msg", True)
        ),
    )
    executor.set_operation_observer(tracker._observe_tool_operation)
    task_token = remote_task_id.set(TASK_A)
    try:
        asyncio.run(
            executor.execute_tool(
                {"type": "hub_msg", "id": provider_tool_id, "message": "private"}
            )
        )
    finally:
        remote_task_id.reset(task_token)

    snapshot = tracker.get_active_operation_snapshot()
    assert snapshot["tool_call_id"] == provider_tool_id
    assert snapshot["tool_call_id_generated"] is False
    restored = ActiveOperationSnapshot.from_dict(snapshot)
    assert restored.tool_call_id == provider_tool_id
    assert restored.tool_call_id_generated is False


@pytest.mark.parametrize("invalid_id", ["bad id", "bad\nvalue", "x" * 129])
def test_unsafe_or_oversized_tool_ids_get_diagnostic_ids(invalid_id):
    tracker = _tracker()

    class EventBus:
        async def emit_with_hooks(self, *args, **kwargs):
            return {}

    executor = ToolExecutor(mcp_integration=None, event_bus=EventBus())
    executor.set_operation_observer(tracker._observe_tool_operation)
    task_token = remote_task_id.set(TASK_A)
    try:
        asyncio.run(
            executor.execute_tool({"type": "file_create", "id": invalid_id})
        )
    finally:
        remote_task_id.reset(task_token)

    snapshot = tracker.get_active_operation_snapshot()
    assert snapshot["tool_call_id_generated"] is True
    assert re.fullmatch(r"[0-9a-f]{32}", snapshot["tool_call_id"])


def test_invalid_tool_call_id_uses_marked_opaque_diagnostic_id():
    tracker = _tracker()
    events = []

    class EventBus:
        async def emit_with_hooks(self, *args, **kwargs):
            return {"cancelled": True, "main": {"final_data": {}}}

    executor = ToolExecutor(mcp_integration=None, event_bus=EventBus())
    executor.set_operation_observer(lambda **event: events.append(event) or tracker._observe_tool_operation(**event))
    task_token = remote_task_id.set(TASK_A)
    try:
        asyncio.run(executor.execute_tool({"type": "file_create", "id": "bad id"}))
    finally:
        remote_task_id.reset(task_token)

    snapshot = tracker.get_active_operation_snapshot()
    assert snapshot["tool_name"] == "file_create"
    assert snapshot["tool_call_id_generated"] is True
    assert re.fullmatch(r"[0-9a-f]{32}", snapshot["tool_call_id"])
    assert all(event["tool_call_id_generated"] for event in events)


def test_processing_snapshot_round_trips_nested_allowlisted_operation():
    snapshot = ProcessingSnapshot(
        is_processing=True,
        active_operation=ActiveOperationSnapshot(
            task_id=TASK_A,
            generation=4,
            phase="tool_execution",
            provider="openai",
            request_id="d" * 32,
            tool_name="file_create",
            tool_call_id="call_42",
            cancel_generation=7,
            cleanup_state="pending",
        ),
    )
    wire = json.loads(json.dumps(snapshot.to_dict()))
    restored = ProcessingSnapshot.from_dict(wire)
    assert restored.active_operation == snapshot.active_operation
    assert isinstance(restored.active_operation, ActiveOperationSnapshot)

    legacy = ProcessingSnapshot.from_dict({"is_processing": False})
    assert legacy.active_operation.phase == "idle"

    malformed = ActiveOperationSnapshot.from_dict(
        {
            "task_id": "not-a-task-id",
            "generation": True,
            "phase": "https://sensitive.invalid/path",
            "provider": "https://provider.invalid/key",
            "request_id": "PRIVATE_SENTINEL",
            "tool_name": "secret_command_value",
            "cleanup_state": "credential=secret",
        }
    )
    assert malformed == ActiveOperationSnapshot()


@pytest.mark.asyncio
async def test_local_processing_snapshot_reads_sanitized_llm_operation():
    llm = MagicMock()
    llm.is_processing = True
    llm.current_processing_tokens = 12
    llm.status_service = None
    llm.pending_tools = []
    llm.task_manager = None
    llm.get_active_operation_snapshot.return_value = {
        "task_id": TASK_A,
        "generation": 9,
        "phase": "provider_streaming",
        "provider": "openai",
        "request_id": "f" * 32,
        "tool_name": None,
        "tool_call_id": None,
        "tool_call_id_generated": False,
        "cancel_generation": None,
        "cleanup_state": "none",
        "prompt": "PRIVATE_PROMPT_SENTINEL",
    }
    state = LocalStateService(llm_service=llm, profile_manager=MagicMock())

    snapshot = await state.get_processing_state()

    assert snapshot.is_processing is True
    assert snapshot.active_operation.task_id == TASK_A
    assert snapshot.active_operation.generation == 9
    assert snapshot.active_operation.phase == "provider_streaming"
    assert "PRIVATE_PROMPT_SENTINEL" not in json.dumps(snapshot.to_dict())


@pytest.mark.asyncio
async def test_remote_cancel_cleanup_is_correlated_to_task_and_generation():
    service = _tracker()
    service._cancellation_cleanup_tasks = {}
    service.api_service = MagicMock()
    service.tool_executor = MagicMock()
    cleanup = asyncio.get_running_loop().create_future()
    service.create_background_task = MagicMock(return_value=cleanup)

    queue_processor = MagicMock()
    queue_processor.is_processing = True
    queue_processor.cancel_processing = False
    queue_processor.cancel_origin = None
    queue_processor.cancel_generation = 0

    def request_cancellation(*, origin):
        queue_processor.cancel_generation += 1
        queue_processor.cancel_processing = True
        queue_processor.cancel_origin = origin
        return queue_processor.cancel_generation

    queue_processor.request_cancellation.side_effect = request_cancellation

    def clear_cancellation(generation):
        if (
            queue_processor.is_processing
            or generation != queue_processor.cancel_generation
            or queue_processor.cancel_origin != "remote_task"
        ):
            return False
        queue_processor.cancel_processing = False
        queue_processor.cancel_origin = None
        return True

    queue_processor.clear_remote_task_cancellation.side_effect = clear_cancellation
    service._queue_processor = queue_processor
    token = remote_task_id.set(TASK_A)
    try:
        service._begin_active_operation("provider_request", provider="openai")
        cancel_generation = service.cancel_current_request(origin="remote_task", task_id=TASK_A)
    finally:
        remote_task_id.reset(token)

    assert cancel_generation == 1
    operation = service.get_active_operation_snapshot()
    assert operation["task_id"] == TASK_A
    assert operation["cancel_generation"] == cancel_generation
    assert operation["phase"] == "cancellation_cleanup"
    assert operation["cleanup_state"] == "pending"
    assert not service.remote_task_cancellation_ready(cancel_generation, task_id=TASK_B)
    assert not service.remote_task_cancellation_ready(cancel_generation + 1, task_id=TASK_A)

    queue_processor.is_processing = False
    cleanup.set_result(None)
    assert service.remote_task_cancellation_ready(cancel_generation, task_id=TASK_A)
    assert service.clear_remote_task_cancellation(cancel_generation, task_id=TASK_A)
    assert service.get_active_operation_snapshot()["cleanup_state"] == "complete"


@pytest.mark.asyncio
async def test_api_observer_marks_only_provider_lifecycle_metadata(tmp_path):
    config = MagicMock()
    config.get = lambda _key, default=None: default
    profile = LLMProfile(
        name="activity-test",
        provider="anthropic",
        model="test-model",
        base_url="https://api.example.com",
        api_key="test-key",
        streaming=False,
    )
    service = APICommunicationService(config, tmp_path, profile)
    provider = MagicMock()
    provider.provider_name = "anthropic"
    provider._provider_name = "anthropic"
    provider.last_request_payload = {"messages": []}
    provider.call = AsyncMock(
        return_value=UnifiedResponse(
            content=[TextContent(text="ok")],
            usage=UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="test-model",
            provider=ProviderType.ANTHROPIC,
            finish_reason="end_turn",
            raw_response={"id": "response-1"},
        )
    )
    service._provider = provider
    service.set_session_id("active-operation-test")
    events = []
    service.set_operation_observer(lambda **event: events.append(event) or 11)

    await service.call_llm([{"role": "user", "content": "PRIVATE_PROMPT_SENTINEL"}])

    assert [event["phase"] for event in events] == [
        "provider_request",
        "provider_response",
    ]
    assert events[0]["begin"] is True
    assert events[1]["operation_generation"] == 11
    assert events[0]["provider"] == "anthropic"
    assert re.fullmatch(r"[0-9a-f]{32}", events[0]["request_id"])
    assert "PRIVATE_PROMPT_SENTINEL" not in json.dumps(events)
    assert all(
        set(event)
        == {
            "phase",
            "provider",
            "request_id",
            "operation_generation",
            "begin",
        }
        for event in events
    )


@pytest.mark.asyncio
async def test_provider_retry_observer_tracks_wait_and_same_request_id(tmp_path):
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.max_retries": 1,
        "kollabor.llm.enable_streaming": False,
    }.get(key, default)
    profile = LLMProfile(
        name="activity-retry-test",
        provider="openai",
        model="test-model",
        api_key="test-key",
    )
    service = APICommunicationService(config, tmp_path, profile)
    provider = MagicMock()
    provider.provider_name = "openai"
    service._provider = provider
    service._call_provider_nonstream = AsyncMock(side_effect=[APIConnectionError("temporary", "openai"), "ok"])
    service._log_raw_interaction = MagicMock()
    service._sleep_or_cancel = AsyncMock(return_value=None)
    events = []
    service.set_operation_observer(lambda **event: events.append(event) or 3)

    result = await service.call_llm([{"role": "user", "content": "PRIVATE_RETRY_SENTINEL"}])

    assert result == "ok"
    assert [event["phase"] for event in events] == [
        "provider_request",
        "provider_retry_wait",
        "provider_request",
        "provider_response",
    ]
    assert len({event["request_id"] for event in events}) == 1
    assert events[0]["begin"] is True
    assert all(event["provider"] == "openai" for event in events)
    assert "PRIVATE_RETRY_SENTINEL" not in json.dumps(events)
