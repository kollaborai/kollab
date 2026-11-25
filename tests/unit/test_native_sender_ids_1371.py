"""#1371: a native admission keeps the sender's request id with its row and its queued input.

The saved user row and the input it admits both name the id the sender chose,
so a page can pair its bubble, the saved row and the batch events that answer it.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor.user_input_source import UserInputSource
from kollabor_events import EventBus, EventType, Hook, HookPriority


async def _native_service(*, idle=False):
    """A native (non-ACP) service with the real admission pipeline and a spy queue."""
    service = object.__new__(LLMService)
    service.event_bus = EventBus()
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai")
    )
    service._message_handler = MessageHandler(service)
    service._queue_processor = SimpleNamespace(
        processing_queue=asyncio.Queue(maxsize=10)
    )
    service._enqueue_with_overflow_strategy = AsyncMock()
    service.message_display_service = SimpleNamespace(display_user_message=MagicMock())
    service.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(return_value="row-1")
    )
    service.current_parent_uuid = "parent-1"
    service.supports_image_input = MagicMock(return_value=True)
    # A turn runs unless the test says the chat is idle.
    service.is_processing = not idle
    service.create_background_task = MagicMock()
    service.tool_executor = SimpleNamespace(
        yes_approvals=SimpleNamespace(
            on_human_input=MagicMock(), has_open_ask=MagicMock(return_value=False)
        )
    )
    service.conversation_manager = SimpleNamespace(current_session_id="s1")
    await service.event_bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=service._handle_user_input,
        )
    )
    return service


@pytest.mark.asyncio
async def test_native_admission_saves_the_sender_request_id_with_its_row():
    service = await _native_service()

    result = await service.submit_human_input(
        "hi", source=UserInputSource.STATE_RPC, request_id="client-req-N"
    )

    assert result == {"status": "queued", "request_id": "client-req-N"}
    saved = service.conversation_logger.log_user_message.await_args
    assert saved.kwargs.get("message_metadata") == {"request_id": "client-req-N"}
