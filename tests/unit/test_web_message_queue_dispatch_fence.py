"""A claimed WebUI item must not cross a daemon conversation switch."""

from __future__ import annotations

import asyncio
import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor.state.web_message_queue_rpc import (
    WebMessageQueueRegistry,
    daemon_conversation_scope_resolver,
    register_web_message_queue_handlers,
)
from kollabor_agent.queue_processor import QueueProcessor
from kollabor_ai.conversation_manager import ConversationManager
from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_rpc import RpcCallerContext, RpcServer
from kollabor_tui.display_tap import DisplayTap


def _native_queue() -> QueueProcessor:
    """A real native admission queue with enough room for the stale send."""
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = DisplayTap()
    processor.mark_progress = lambda: None
    processor.processing_queue = asyncio.Queue(maxsize=4)
    processor.max_queue_size = 4
    processor.dropped_messages = 0
    processor._cancel_generation = 0
    processor._queue_metrics = {
        "total_enqueue_attempts": 0,
        "total_enqueue_successes": 0,
        "drop_oldest_count": 0,
        "drop_newest_count": 0,
        "block_count": 0,
        "block_timeout_count": 0,
    }
    processor.task_config = SimpleNamespace(
        queue=SimpleNamespace(
            overflow_strategy="block", block_timeout=0, log_queue_events=False
        )
    )
    return processor


async def _state_service(processor: QueueProcessor) -> LocalStateService:
    manager = ConversationManager.__new__(ConversationManager)
    manager.current_session_id = "session-a"
    manager.conversation_metadata = {"conversation_uid": "conversation-a"}
    llm = object.__new__(LLMService)
    llm.event_bus = EventBus()
    llm.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai")
    )
    llm._message_handler = MessageHandler(llm)
    llm._queue_processor = processor
    llm.message_display_service = SimpleNamespace(display_user_message=MagicMock())
    llm.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(return_value="parent-2")
    )
    llm.current_parent_uuid = "parent-1"
    llm.is_processing = True
    llm.create_background_task = MagicMock()
    llm.tool_executor = SimpleNamespace(
        yes_approvals=SimpleNamespace(on_human_input=MagicMock())
    )
    llm.conversation_manager = manager
    await llm.event_bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=llm._handle_user_input,
        )
    )
    return LocalStateService(llm, profile_manager=object(), event_bus=None)


async def _rpc_call(server: RpcServer, request_id: str, method: str, params: dict):
    uid = os.getuid() if hasattr(os, "getuid") else 501
    return await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": request_id,
            "method": method,
            "params": params,
        },
        caller_context=RpcCallerContext.from_unix_peer(uid),
    )


@pytest.mark.asyncio
async def test_claimed_old_conversation_item_is_rejected_before_native_admission():
    processor = _native_queue()
    state = await _state_service(processor)
    old_uid = state.get_conversation_uid()
    assert old_uid == "conversation-a"

    rpc_server = RpcServer()
    registry = WebMessageQueueRegistry(max_scopes=2)
    register_web_message_queue_handlers(
        rpc_server,
        registry,
        resolve_scope=daemon_conversation_scope_resolver(state),
    )
    register_state_handlers(rpc_server, state)

    allocation = await _rpc_call(
        rpc_server,
        "alloc",
        "state.web_queue.allocate_ids",
        {"expected_conversation_uid": old_uid, "item_count": 1, "operation_count": 4},
    )
    assert allocation["error"] is None
    ids = allocation["result"]
    payload = b"queued for the old conversation"
    enqueued = await _rpc_call(
        rpc_server,
        "enqueue",
        "state.web_queue.enqueue_batch",
        {
            "expected_conversation_uid": old_uid,
            "operation_id": ids["operation_ids"][0],
            "batch_id": ids["batch_id"],
            "items": [
                {
                    "request_id": ids["request_ids"][0],
                    "payload_b64": base64.b64encode(payload).decode("ascii"),
                }
            ],
        },
    )
    assert enqueued["error"] is None
    claim = await _rpc_call(
        rpc_server,
        "claim",
        "state.web_queue.claim_batch",
        {
            "expected_conversation_uid": old_uid,
            "operation_id": ids["operation_ids"][1],
            "batch_id": ids["batch_id"],
        },
    )
    item_claim = await _rpc_call(
        rpc_server,
        "claim-item",
        "state.web_queue.claim_next_item",
        {
            "expected_conversation_uid": old_uid,
            "operation_id": ids["operation_ids"][2],
            "claim_id": claim["result"]["claim_id"],
        },
    )
    assert item_claim["error"] is None
    item_payload = await _rpc_call(
        rpc_server,
        "item-payload",
        "state.web_queue.item_payload",
        {
            "expected_conversation_uid": old_uid,
            "attempt_id": item_claim["result"]["attempt_id"],
        },
    )
    assert item_payload["error"] is None
    claimed_text = base64.b64decode(
        item_payload["result"]["items"][0]["payload_b64"]
    ).decode("utf-8")

    # Exercise the real ConversationManager reset path, which replaces the
    # conversation metadata and therefore its daemon-owned UID.
    manager = state._llm_service.conversation_manager
    async with state._llm_service.conversation_transition():
        manager.reset_session("session-b")
    new_uid = state.get_conversation_uid()
    assert new_uid and new_uid != old_uid

    admission = await _rpc_call(
        rpc_server,
        "stale-admission",
        "state.send_message",
        {
            "message": claimed_text,
            "request_id": ids["request_ids"][0],
            "expected_conversation_uid": old_uid,
        },
    )

    assert (
        processor.processing_queue.empty()
    ), "stale queued payload reached the native provider queue after a conversation switch"
    assert admission["result"]["accepted"] is False
