"""Native input is reported as accepted only when the queue admitted it (#589).

The queue can drop a message after the caller was told it was queued: a block
timeout, a drop_oldest race for the freed slot, or a Stop that sweeps it while it
waits for room. Each drop ends the request's stream once, and the caller must
also get a rejection instead of ``accepted: true``.
"""

import asyncio
import queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor.state.local import LocalStateService
from kollabor_agent.queue_processor import QueueProcessor, TaggedInput
from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_tui.display_tap import DisplayTap


def _queue(strategy, *, block_timeout=0.0):
    """A real QueueProcessor state with one slot and a tap that records its events."""
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = DisplayTap()
    processor.mark_progress = lambda: None
    processor.processing_queue = asyncio.Queue(maxsize=1)
    processor.max_queue_size = 1
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
            overflow_strategy=strategy, block_timeout=block_timeout, log_queue_events=False
        )
    )
    return processor


async def _native_state(processor):
    """LocalStateService over the real native admission pipeline and this queue."""
    llm = object.__new__(LLMService)
    llm.event_bus = EventBus()
    llm.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai")
    )
    llm._message_handler = MessageHandler(llm)
    llm._queue_processor = processor
    llm.message_display_service = SimpleNamespace(display_user_message=MagicMock())
    llm.conversation_logger = SimpleNamespace(log_user_message=AsyncMock(return_value="parent-2"))
    llm.current_parent_uuid = "parent-1"
    llm.is_processing = True  # another turn runs: this input waits behind it
    llm.create_background_task = MagicMock()
    llm.tool_executor = SimpleNamespace(yes_approvals=SimpleNamespace(on_human_input=MagicMock()))
    llm.conversation_manager = SimpleNamespace(current_session_id="s1")
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


def _drain(events):
    drained = []
    while True:
        try:
            drained.append(events.get_nowait())
        except queue.Empty:
            return drained


def _ended(events, request_id):
    """(type, stop_reason) of every event that names the request, in order."""
    return [
        (event["type"], event.get("stop_reason"))
        for event in _drain(events)
        if request_id in event.get("request_ids", ())
    ]


@pytest.mark.asyncio
async def test_native_input_is_rejected_when_the_block_timeout_drops_it():
    # The reproduction in #589: one slot, block_timeout=0, a second input for "queue-probe".
    processor = _queue("block", block_timeout=0)
    events = processor.renderer.subscribe("test")
    processor.processing_queue.put_nowait(TaggedInput("original", ("first",)))
    state = await _native_state(processor)

    result = await state.send_message("probe", request_id="queue-probe")

    assert result["accepted"] is False, result
    assert processor.processing_queue.get_nowait() == TaggedInput("original", ("first",))
    assert processor.dropped_messages == 1
    assert _ended(events, "queue-probe") == [("error", None), ("turn_complete", "error")]


@pytest.mark.asyncio
async def test_native_input_with_room_is_accepted_and_queued_under_its_request_id():
    processor = _queue("block", block_timeout=0)
    events = processor.renderer.subscribe("test")
    state = await _native_state(processor)

    result = await state.send_message("probe", request_id="queue-probe")

    assert result == {"accepted": True, "reason": "", "request_id": "queue-probe"}
    assert processor.processing_queue.get_nowait() == TaggedInput("probe", ("queue-probe",))
    assert _ended(events, "queue-probe") == []


@pytest.mark.asyncio
async def test_drop_oldest_race_for_the_freed_slot_is_rejected_not_queued():
    # drop_oldest removes the old input, then pauses; a rival input takes the slot.
    processor = _queue("drop_oldest")
    events = processor.renderer.subscribe("test")
    processor.processing_queue.put_nowait(TaggedInput("original", ("first",)))
    state = await _native_state(processor)

    send = asyncio.ensure_future(state.send_message("probe", request_id="queue-probe"))
    for _ in range(1000):  # the oldest is gone; the probe is in its pause
        if processor.processing_queue.empty():
            break
        await asyncio.sleep(0)
    processor.processing_queue.put_nowait(TaggedInput("rival", ("rival",)))
    result = await send

    assert result["accepted"] is False, result
    assert processor.processing_queue.get_nowait() == TaggedInput("rival", ("rival",))
    assert processor.dropped_messages == 1
    assert _ended(events, "queue-probe") == [("error", None), ("turn_complete", "error")]


@pytest.mark.asyncio
async def test_a_stop_that_sweeps_a_waiting_input_rejects_it():
    processor = _queue("block", block_timeout=5)
    events = processor.renderer.subscribe("test")
    processor.processing_queue.put_nowait(TaggedInput("original", ("first",)))
    state = await _native_state(processor)

    send = asyncio.ensure_future(state.send_message("probe", request_id="queue-probe"))
    for _ in range(1000):  # the probe is waiting for room
        if processor._queue_metrics["block_count"]:
            break
        await asyncio.sleep(0)
    processor.request_cancellation(origin="human")
    result = await send

    assert result["accepted"] is False, result
    assert processor.processing_queue.empty()
    assert _ended(events, "queue-probe") == [("error", None), ("turn_complete", "error")]


@pytest.mark.asyncio
async def test_drop_newest_refusal_is_not_reported_as_accepted():
    processor = _queue("drop_newest")
    events = processor.renderer.subscribe("test")
    processor.processing_queue.put_nowait(TaggedInput("original", ("first",)))
    state = await _native_state(processor)

    result = await state.send_message("probe", request_id="queue-probe")

    assert result["accepted"] is False, result
    assert processor.processing_queue.get_nowait() == TaggedInput("original", ("first",))
    assert _ended(events, "queue-probe") == [("error", None), ("turn_complete", "error")]
