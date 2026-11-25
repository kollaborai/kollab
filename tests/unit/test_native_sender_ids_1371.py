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
from kollabor_agent.queue_processor import QueuedInput
from kollabor_agent.voice_admission import issue_voice_admission_context
from kollabor_events import EventBus, EventType, Hook, HookPriority

# A LiveKit room's binding, as the voice runtime issues it for one room session.
ROOM_BINDING = {
    "owner_id": "room-owner",
    "voice_session_id": "vs-1",
    "control_session_id": "cs-1",
    "conversation_session_id": "conv-1",
    "agent_id": "agent-1",
    "thread_id": None,
    "voice_provider_profile_id": "pp-1",
    "voice_prompt_revision": "rev-1",
    "voice_tool_policy": "standard",
    "generation": 3,
    "service_lease_epoch": 7,
}


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


@pytest.mark.asyncio
@pytest.mark.parametrize("arrives_as", ["device-voice", "livekit-room"])
async def test_native_voice_admission_queues_under_the_sender_request_id(arrives_as):
    service = await _native_service(idle=True)
    if arrives_as == "device-voice":
        voice_kwargs = {"voice": {"admission_id": "a1", "epoch": "e1"}}
    else:
        origin = issue_voice_admission_context(ROOM_BINDING, "lv-turn-1")
        voice_kwargs = {"origin_context": origin}

    await service.process_user_input("hi", request_id="client-req-V", **voice_kwargs)

    queued = service._enqueue_with_overflow_strategy.await_args.args[0]
    assert isinstance(queued, QueuedInput)
    assert queued.request_ids == ("client-req-V",)
