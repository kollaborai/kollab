"""A chat message is followed by its request id (#551).

Native admission keeps the id with the queued input and echoes it; every event
of the turn that answers it names it in ``request_ids``. Before, a message sent
while another turn ran ended its stream on that turn's untagged completion.
"""

import asyncio
import queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor.user_input_source import UserInputSource
from kollabor_agent.queue_processor import QueuedInput, QueueProcessor, TaggedInput, batch_request_ids
from kollabor_events import EventBus, EventType, Hook, HookPriority
from kollabor_tui.display_tap import DisplayTap, publish_semantic
from kollabor_tui.turn_context import current_turn


async def _native_service():
    """A native (non-ACP) service with the real admission pipeline and a spy queue."""
    service = object.__new__(LLMService)
    service.event_bus = EventBus()
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "openai")
    )
    service._message_handler = MessageHandler(service)
    service._queue_processor = SimpleNamespace(processing_queue=asyncio.Queue(maxsize=10))
    service._enqueue_with_overflow_strategy = AsyncMock()
    service.message_display_service = SimpleNamespace(display_user_message=MagicMock())
    service.conversation_logger = SimpleNamespace(log_user_message=AsyncMock(return_value="parent-2"))
    service.current_parent_uuid = "parent-1"
    service.supports_image_input = MagicMock(return_value=True)
    service.is_processing = True  # another turn runs: this input waits behind it
    service.create_background_task = MagicMock()
    # Chat input may answer an open ask (a "yes"): none is open here.
    service.tool_executor = SimpleNamespace(yes_approvals=SimpleNamespace(on_human_input=MagicMock()))
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
async def test_native_admission_keeps_the_request_id_and_echoes_it():
    service = await _native_service()
    result = await service.submit_human_input("hi", source=UserInputSource.STATE_RPC, request_id="r1")
    assert result == {"status": "queued", "request_id": "r1"}
    service._enqueue_with_overflow_strategy.assert_awaited_once_with(TaggedInput("hi", ("r1",)))

    # Input with no id (the terminal, a hub peer) queues as before and echoes none.
    service._enqueue_with_overflow_strategy.reset_mock()
    assert await service.submit_human_input("typed here", source=UserInputSource.TUI) == {"status": "queued"}
    service._enqueue_with_overflow_strategy.assert_awaited_once_with("typed here")


def _drain(q):
    events = []
    while True:
        try:
            events.append(q.get_nowait())
        except queue.Empty:
            return events


def test_a_batch_answers_every_request_in_it_and_its_events_name_them():
    assert batch_request_ids([TaggedInput("a", ("r1",)), "typed here", TaggedInput("b", ("r2",))]) == ("r1", "r2")
    processor = QueueProcessor.__new__(QueueProcessor)
    processor._turn_token = None
    tap = DisplayTap()
    q = tap.subscribe("test")
    try:
        processor._stamp_turn(None, ("r1", "r2"))
        publish_semantic(tap, "token", text="hello")
        publish_semantic(tap, "turn_complete", stop_reason="end_turn")
        processor._stamp_turn("voice-1", ("r3",))  # a voice turn keeps its own id too
        publish_semantic(tap, "turn_complete", stop_reason="end_turn")
    finally:
        processor._stamp_turn(None)
    publish_semantic(tap, "turn_complete", stop_reason="end_turn")  # the next turn answers no request

    token, done, voice, later = _drain(q)
    assert token["request_ids"] == done["request_ids"] == ["r1", "r2"]
    assert (voice["request_id"], voice["request_ids"]) == ("voice-1", ["r3"])
    assert "request_ids" not in later and "request_id" not in later


@pytest.mark.asyncio
async def test_a_tagged_chat_message_overflows_like_any_typed_message():
    # QueuedInput marks admitted voice, which the queue may never drop; a chat
    # message with an id is typed input and keeps the configured strategy.
    # Tanzanite on #554: the dropped one must still end its stream, saying so.
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = DisplayTap()
    events = processor.renderer.subscribe("test")
    processor.mark_progress = lambda: None
    processor.processing_queue = asyncio.Queue(maxsize=1)
    processor.max_queue_size = 1
    processor.dropped_messages = 0
    processor._queue_metrics = {"total_enqueue_attempts": 0, "total_enqueue_successes": 0, "drop_oldest_count": 0}
    processor.task_config = SimpleNamespace(
        queue=SimpleNamespace(overflow_strategy="drop_oldest", log_queue_events=False)
    )
    await processor.enqueue(TaggedInput("a", ("r1",)))
    await processor.enqueue(TaggedInput("b", ("r2",)))
    assert processor.processing_queue.get_nowait() == TaggedInput("b", ("r2",))
    error, done = _drain(events)
    assert (error["type"], error["request_ids"]) == ("error", ["r1"]) and "dropped" in error["message"]
    assert (done["type"], done["request_ids"], done["stop_reason"]) == ("turn_complete", ["r1"], "error")

    # Tourmaline on #554 round 2: the block strategy's timeout dropped one silently.
    processor.processing_queue.put_nowait(TaggedInput("b", ("r2",)))
    processor.task_config.queue = SimpleNamespace(overflow_strategy="block", block_timeout=0.01, log_queue_events=False)
    processor._queue_metrics.update(block_count=0, block_timeout_count=0)
    await processor.enqueue(TaggedInput("c", ("r3",)))
    assert processor.processing_queue.get_nowait() == TaggedInput("b", ("r2",))
    error, done = _drain(events)
    assert (error["type"], error["request_ids"], done["request_ids"]) == ("error", ["r3"], ["r3"])

    # Chrysoprase on #554 round 3: drop_newest, and drop_oldest with admitted
    # voice in the queue, refuse the new message by raising, and it never ended.
    processor.processing_queue.put_nowait("typed here")
    processor.task_config.queue = SimpleNamespace(overflow_strategy="drop_newest", log_queue_events=False)
    processor._queue_metrics["drop_newest_count"] = 0
    with pytest.raises(RuntimeError):
        await processor.enqueue(TaggedInput("d", ("r4",)))
    error, done = _drain(events)
    assert (error["type"], error["request_ids"], done["request_ids"]) == ("error", ["r4"], ["r4"])
    processor.processing_queue.get_nowait()
    processor.processing_queue.put_nowait(QueuedInput("spoken", {}))
    processor.task_config.queue.overflow_strategy = "drop_oldest"
    with pytest.raises(RuntimeError):
        await processor.enqueue(TaggedInput("e", ("r5",)))
    error, done = _drain(events)
    assert (error["type"], error["request_ids"], done["request_ids"]) == ("error", ["r5"], ["r5"])
    assert processor.processing_queue.get_nowait() == QueuedInput("spoken", {})


@pytest.mark.asyncio
async def test_a_stop_ends_a_message_still_waiting_for_room():
    # Variscite on #554 round 4: B waited for room when Stop swept the queue, went
    # in after the sweep, and waited there with its stream open.
    for strategy in ("block", "drop_oldest"):
        processor = QueueProcessor.__new__(QueueProcessor)
        processor.renderer = DisplayTap()
        events = processor.renderer.subscribe("test")
        processor.mark_progress = lambda: None
        processor._cancel_generation = 0
        processor.processing_queue = asyncio.Queue(maxsize=1)
        processor.max_queue_size = 1
        processor.dropped_messages = 0
        processor._queue_metrics = {"total_enqueue_attempts": 0, "total_enqueue_successes": 0,
                                    "drop_oldest_count": 0, "block_count": 0, "block_timeout_count": 0}
        processor.task_config = SimpleNamespace(
            queue=SimpleNamespace(overflow_strategy=strategy, block_timeout=5, log_queue_events=False)
        )
        processor.processing_queue.put_nowait(TaggedInput("a", ("A",)))
        admitting = asyncio.ensure_future(processor.enqueue(TaggedInput("b", ("B",))))
        await asyncio.sleep(0)  # B waits for room, or in the pause after A was dropped
        processor.request_cancellation(origin="human")
        await admitting
        assert processor.processing_queue.empty(), strategy
        ended = [e["request_ids"] for e in _drain(events) if e["type"] == "turn_complete"]
        assert sorted(ended) == [["A"], ["B"]], strategy


@pytest.mark.asyncio
async def test_a_chain_a_safety_exit_stops_ends_its_requests(monkeypatch):
    # Variscite on #554 round 4: the turn cap, the stuck-loop breaker and a
    # continuation error ended a chain with no completion naming its request,
    # and the next turn named that request as well.
    import kollabor_agent.queue_processor as module

    monkeypatch.setattr(module, "MAX_CONTINUATION_TURNS", 0)
    processor = _turn_processor()
    processor.renderer = DisplayTap()
    events = processor.renderer.subscribe("test")
    processor.processing_queue = asyncio.Queue()
    processor.processing_queue.put_nowait(TaggedInput("a", ("A",)))
    processor.turn_completed = False
    processor._last_tool_error_sig = None
    processor._open_voice_turn = None
    processor.message_display_service = MagicMock()

    async def batch(messages):
        processor.active_request_ids = ("A",)  # the turn began and is still continuing

    await processor._drain_queue(None, batch, AsyncMock())
    done = [e for e in _drain(events) if e["type"] == "turn_complete"]
    assert [(e["request_ids"], e["stop_reason"]) for e in done] == [(["A"], "error")]
    assert processor.active_request_ids == ()


@pytest.mark.asyncio
async def test_a_stop_ends_a_queued_chat_message_and_keeps_typed_input():
    # Chrysoprase on #554 round 3: a Stop left X queued and no drain ran it, so
    # X's stream waited for whatever message came next.
    processor = QueueProcessor.__new__(QueueProcessor)
    processor.renderer = DisplayTap()
    events = processor.renderer.subscribe("test")
    processor._cancel_generation = 0
    processor.processing_queue = asyncio.Queue(maxsize=10)
    for item in ("typed here", TaggedInput("x", ("X",)), QueuedInput("spoken", {})):
        processor.processing_queue.put_nowait(item)
    processor.request_cancellation(origin="human")
    kept = [processor.processing_queue.get_nowait() for _ in range(processor.processing_queue.qsize())]
    assert kept == ["typed here", QueuedInput("spoken", {})]  # these still run with the next message
    error, done = _drain(events)
    assert (error["type"], error["request_ids"]) == ("error", ["X"]) and "Stopped" in error["message"]
    assert (done["type"], done["request_ids"]) == ("turn_complete", ["X"])


def _turn_processor():
    """Turns through the real lock and stamping, with the model call replaced."""
    processor = QueueProcessor.__new__(QueueProcessor)
    processor._turn_lock = asyncio.Lock()
    processor._turn_token = None
    processor.active_request_ids = ()
    processor.active_origin_context = None
    processor.cancel_processing = False
    processor._after_turn = []
    processor._run_after_turn = AsyncMock()
    processor.tool_executor = SimpleNamespace(yes_approvals=None)
    processor._add_message_fn = lambda *args, **kwargs: None
    return processor


@pytest.mark.asyncio
async def test_a_batch_that_cuts_into_a_chain_answers_the_chains_request_too():
    # Tanzanite on #554: A's tool round was still continuing when B arrived. B's
    # batch replaced A's id, so A's stream never saw a completion naming it.
    processor = _turn_processor()
    processor.active_request_ids = ("A",)  # A's chain still owes A its completion
    seen = []

    async def model_call(*args, **kwargs):
        seen.append(current_turn.get().get("request_ids"))
        return "parent"

    processor._execute_llm_turn_inner = model_call
    await processor.process_message_batch([TaggedInput("b", ("B",))], "p0")
    assert seen == [["A", "B"]]


@pytest.mark.asyncio
async def test_a_turn_ending_while_a_batch_waits_keeps_the_batchs_request():
    # Tanzanite on #554: a hub turn holding the turn lock cleared the ids of a
    # chat batch waiting for it, so the batch's completion named no request.
    processor = _turn_processor()
    seen, release = [], asyncio.Event()

    async def model_call(*args, **kwargs):
        seen.append(current_turn.get().get("request_ids"))
        if len(seen) == 1:
            await release.wait()  # the hub turn runs on while the batch waits for the lock
        processor.active_request_ids = ()  # a completion clears what it answered
        return "parent"

    processor._execute_llm_turn_inner = model_call
    hub = asyncio.create_task(processor.continue_conversation("p0"))
    await asyncio.sleep(0)
    batch = asyncio.create_task(processor.process_message_batch([TaggedInput("b", ("B",))], "p0"))
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(hub, batch)
    assert seen == [None, ["B"]]


@pytest.mark.asyncio
async def test_a_batch_that_fails_before_its_turn_starts_ends_its_requests():
    # Chalcedony on #554 round 5: a batch whose message could not be saved
    # raised before any turn took its requests up, and their streams waited
    # for a completion that never came.
    processor = _turn_processor()
    processor.renderer = DisplayTap()
    events = processor.renderer.subscribe("test")

    def unsaved(*args, **kwargs):
        raise OSError("conversation storage unavailable")

    processor._add_message_fn = unsaved
    with pytest.raises(OSError):
        await processor.process_message_batch([TaggedInput("x", ("X",))], "p0")
    error, done = _drain(events)
    assert (error["type"], error["request_ids"]) == ("error", ["X"])
    assert error["message"] == "conversation storage unavailable"
    assert (done["type"], done["request_ids"], done["stop_reason"]) == ("turn_complete", ["X"], "error")
    assert processor.active_request_ids == ()  # the next turn names none of them


@pytest.mark.asyncio
async def test_a_turn_that_dies_before_its_body_ends_its_requests_on_that_turn():
    # Chrysoprase on #554 round 3: a pre-request hook that cancelled X's turn
    # left X unanswered, and the next message's turn named X as well.
    processor = _turn_processor()
    processor.renderer = DisplayTap()
    events = processor.renderer.subscribe("test")

    async def denied(*args, **kwargs):
        raise asyncio.CancelledError("Model request denied by pre-request hook")

    processor._execute_llm_turn_inner = denied
    with pytest.raises(asyncio.CancelledError):
        await processor.process_message_batch([TaggedInput("x", ("X",))], "p0")
    error, done = _drain(events)
    assert (error["type"], error["request_ids"]) == ("error", ["X"]) and "denied" in error["message"]
    assert (done["type"], done["request_ids"], done["stop_reason"]) == ("turn_complete", ["X"], "error")

    seen = []

    async def model_call(*args, **kwargs):
        seen.append(current_turn.get().get("request_ids"))
        return "parent"

    processor._execute_llm_turn_inner = model_call
    await processor.process_message_batch([TaggedInput("y", ("Y",))], "p0")
    assert seen == [["Y"]]
