"""Tests for QueueProcessor."""

import asyncio
import time
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor.llm.streaming_handler import StreamingHandler
from kollabor_agent.execution_context import remote_task_id
from kollabor_agent.queue_processor import (
    QueuedInput,
    QueueProcessor,
    _tool_results_requiring_followup,
)
from kollabor_agent.tool_executor import ToolExecutionResult
from kollabor_events import EventType
from kollabor_events.data_models import ConversationMessage
from tests.unit.llm.voice_admission_helpers import (
    make_voice_origin_context,
    revoke_voice_admission_context,
)


@dataclass
class MockQueueConfig:
    """Mock queue config for testing."""

    overflow_strategy: str = "drop_oldest"
    log_queue_events: bool = False
    enable_queue_metrics: bool = False
    block_timeout: Optional[float] = None


@dataclass
class MockTaskConfig:
    """Mock task config for QueueProcessor tests."""

    queue: MockQueueConfig = field(default_factory=MockQueueConfig)


class TestQueueProcessor(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Shared mutable containers
        self.conversation_history = []
        self.session_stats = {
            "messages": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        self.stats = {"total_thinking_time": 0}
        self.queue_metrics = {
            "total_enqueue_attempts": 0,
            "total_enqueue_successes": 0,
            "drop_oldest_count": 0,
            "drop_newest_count": 0,
            "block_count": 0,
            "block_timeout_count": 0,
        }

        # Mock dependencies
        self.task_config = MockTaskConfig()

        self.api_service = AsyncMock()
        self.api_service._provider = None
        self.tool_executor = MagicMock()
        self.response_parser = MagicMock()
        self.message_display_service = MagicMock()
        self.renderer = MagicMock()
        self.config = MagicMock()
        self.config.get = MagicMock(return_value=0.1)
        self.event_bus = MagicMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.conversation_logger = AsyncMock()
        self.streaming_handler = MagicMock()
        self.streaming_handler.call_llm = AsyncMock(return_value="test response")
        self.native_tools_handler = MagicMock()
        self.native_tools_handler.tools = None
        self.native_tools_handler.tool_calling_enabled = False
        self.native_tools_handler.discovery_complete = asyncio.Event()
        self.native_tools_handler.discovery_complete.set()

        self.add_message_fn = MagicMock()

        self.processor = QueueProcessor(
            conversation_history=self.conversation_history,
            session_stats=self.session_stats,
            stats=self.stats,
            queue_metrics=self.queue_metrics,
            task_config=self.task_config,
            api_service=self.api_service,
            tool_executor=self.tool_executor,
            response_parser=self.response_parser,
            message_display_service=self.message_display_service,
            renderer=self.renderer,
            config=self.config,
            event_bus=self.event_bus,
            conversation_logger=self.conversation_logger,
            streaming_handler=self.streaming_handler,
            native_tools_handler=self.native_tools_handler,
            add_message_fn=self.add_message_fn,
            max_queue_size=10,
        )
        # Default to turn_completed=True so process_queue doesn't enter
        # the infinite continue_conversation loop
        self.processor.turn_completed = True

    def tearDown(self):
        """Clean up."""
        self.loop.close()

    def test_context_blocks_never_move_off_a_message_the_model_saw(self):
        """Blocks stay where they were attached; state is re-sent only on change.

        Moving them to each new user message rewrote the previous one, and a
        prefix-caching provider fell back to the system prompt every turn.
        """
        history = self.conversation_history
        attach = self.processor._attach_context
        injected = lambda m: m.metadata.get("injected_context")  # noqa: E731

        first = ConversationMessage(role="user", content="task")
        history.append(first)
        attach([("context:session", "S1"), ("hub_live", "H1"), ("", "[env] +mcp:a")])
        self.assertEqual(injected(first), "S1\n\n---\n\nH1\n\n---\n\n[env] +mcp:a")
        self.assertEqual(first.content, "task")

        # tool loop: unchanged state is not re-sent, a one-shot rides the tail
        history.append(ConversationMessage(role="assistant", content="calling"))
        tool = ConversationMessage(role="tool", content="out", metadata={"tool_call_id": "t1"})
        history.append(tool)
        attach([("context:session", "S1"), ("hub_live", "H1")])
        self.assertIsNone(injected(tool))
        attach([("context:session", "S1"), ("", "[env] +mcp:b")])
        self.assertEqual(injected(tool), "[env] +mcp:b")

        # next user message: only what changed, and the first message is untouched
        second = ConversationMessage(role="user", content="next")
        history.append(second)
        attach([("context:session", "S1"), ("hub_live", "H2")])
        self.assertEqual(injected(second), "H2")
        self.assertEqual(injected(first), "S1\n\n---\n\nH1\n\n---\n\n[env] +mcp:a")

        # compaction dropped the carrier: the state goes out again
        third = ConversationMessage(role="user", content="after compaction")
        history[:] = [second, third]
        attach([("context:session", "S1"), ("hub_live", "H2")])
        self.assertEqual(injected(third), "S1")

    def test_cancelled_pre_request_never_calls_provider(self):
        self.event_bus.emit_with_hooks.return_value = {"cancelled": True}
        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="remote-task",
            ))
        self.streaming_handler.call_llm.assert_not_awaited()
        self.api_service.call_llm.assert_not_awaited()

    def test_a_failed_turn_tells_web_clients_why(self):
        """The error goes out as a semantic event before turn_complete, so the
        web UI can show it; the terminal draws its own red line."""
        self.streaming_handler.call_llm.side_effect = RuntimeError(
            "LLM provider not available"
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="turn",
            )
        )

        tap = self.renderer.get_service.return_value
        events = [call.args[0] for call in tap.publish.call_args_list]
        self.assertEqual([e["type"] for e in events[-2:]], ["error", "turn_complete"])
        self.assertEqual(events[-2]["message"], "LLM provider not available")
        self.message_display_service.display_error_message.assert_called_once_with(
            "LLM provider not available"
        )
        event_types = [call.args[0] for call in self.event_bus.emit_with_hooks.await_args_list]
        self.assertIn(EventType.LLM_REQUEST_ERROR, event_types)
        self.streaming_handler.call_llm.assert_awaited_once()

    def _stop_after_streaming(self, *chunks):
        """Run a turn whose reply streams `chunks` and is then stopped.

        Uses the real StreamingHandler: the text a Stop keeps lives in it.
        Returns what happened in order: "history" for each message added, then
        the semantic event types, so a test can check the engine's turn_complete
        read comes after the row.
        """

        async def call_llm(*, streaming_callback, **_):
            for chunk in chunks:
                await streaming_callback(chunk)
            raise asyncio.CancelledError("Request cancelled by user")

        order = []
        self.renderer.get_service.return_value.publish.side_effect = (
            lambda event: order.append(event["type"])
        )
        self.add_message_fn.side_effect = lambda *a, **k: order.append("history")
        self.processor._streaming_handler = StreamingHandler(
            SimpleNamespace(call_llm=call_llm), MagicMock(), self.renderer
        )
        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="t",
            )
        )
        return order

    def _assistant_messages(self):
        return [
            call.args[0]
            for call in self.add_message_fn.call_args_list
            if call.args[0].role == "assistant"
        ]

    def test_a_stop_keeps_the_text_already_shown(self):
        order = self._stop_after_streaming("Hello ", "wor")

        replies = self._assistant_messages()
        self.assertEqual([m.content for m in replies], ["Hello wor"])
        self.assertEqual(replies[0].metadata, {"interrupted": True})
        self.assertEqual(self.add_message_fn.call_args.kwargs["parent_uuid"], "t")
        logged = self.conversation_logger.log_assistant_message
        logged.assert_awaited_once()
        self.assertEqual(logged.await_args.args[0], "Hello wor")
        # the engine reads history when turn_complete goes out
        self.assertLess(order.index("history"), order.index("turn_complete"))

    def test_a_stop_before_any_text_keeps_nothing(self):
        for chunks in ((), ("\n",)):
            with self.subTest(chunks=chunks):
                self.add_message_fn.reset_mock()
                self.conversation_logger.reset_mock()

                self._stop_after_streaming(*chunks)

                self.assertEqual(self._assistant_messages(), [])
                self.conversation_logger.log_assistant_message.assert_not_awaited()

    def test_a_stopped_voice_turn_keeps_its_text_under_its_voice_turn(self):
        """The worker then cuts it to the part that was played, which may be none."""
        voice = make_voice_origin_context(turn_id="turn-9")
        # a talk-over revokes the grant before the cancel lands
        revoke_voice_admission_context(voice)
        self.processor.active_origin_context = voice

        self._stop_after_streaming("Hello wor")

        [reply] = self._assistant_messages()
        self.assertEqual(reply.content, "Hello wor")
        self.assertEqual(reply.metadata, {"interrupted": True, "voice_turn_id": "turn-9"})
        logged = self.conversation_logger.log_assistant_message
        self.assertEqual(logged.await_args.kwargs["voice_turn_id"], "turn-9")

    def _reply_with(self, text):
        """A model call that answers `text` with no tools, through the real turn."""
        self.streaming_handler.call_llm = AsyncMock(return_value=text)
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 0
        self.response_parser.parse_response.return_value = {
            "content": text,
            "components": {},
            "turn_completed": True,
        }
        self.response_parser.get_all_tools.return_value = []
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=(text, False, False)
        )

    def test_a_voice_reply_names_its_voice_turn(self):
        """A talk-over after the reply finished cuts that reply to what was heard."""
        self.processor.active_origin_context = make_voice_origin_context(turn_id="turn-9")
        self._reply_with("The build is green.")

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="t",
            )
        )

        [reply] = self._assistant_messages()
        self.assertEqual(reply.metadata.get("voice_turn_id"), "turn-9")
        self.processor.active_origin_context = None
        self.assertEqual(self.processor._voice_turn_metadata(), {})

    def test_every_event_of_a_voice_turn_carries_its_id(self):
        """The Engine streams a voice turn's reply by its id: a talked-over turn's
        late events, its turn_complete above all, end no other turn's stream."""
        from kollabor_agent.queue_processor import QueuedInput
        from kollabor_tui.display_tap import DisplayTap, publish_semantic
        from kollabor_tui.turn_context import current_turn

        tap = DisplayTap()
        self.renderer.get_service = MagicMock(return_value=tap)
        self._reply_with("Sure.")

        running = []  # which voice turn the daemon says runs (a talk-over cancels only it)

        async def call_llm(*_args, **_kwargs):
            running.append(self.processor.open_voice_turn)
            publish_semantic(self.renderer, "token", text="Sure.")
            return "Sure."

        self.streaming_handler.call_llm = AsyncMock(side_effect=call_llm)

        async def turn():
            voice = QueuedInput(
                "is it green", {}, origin_context=make_voice_origin_context("turn-9")
            )
            await self.processor.process_message_batch([voice], "parent")
            publish_semantic(self.renderer, "token", text="typed reply")  # the next turn
            return current_turn.get()

        after = self.loop.run_until_complete(turn())
        self.assertEqual(running, ["turn-9"])
        ids = [
            (e["type"], e.get("request_id"))
            for e in tap.get_snapshot()
            if e["type"] in ("token", "turn_complete")
        ]
        self.assertEqual(
            ids,
            [("token", "turn-9"), ("turn_complete", "turn-9"), ("token", None)],
        )
        self.assertNotIn("request_id", after)

    def test_a_voice_turn_that_fails_says_so_on_its_own_stream(self):
        """Its error and turn_complete carry its id: the Engine says the error and
        ends the stream, never waits out the idle timeout."""
        from kollabor_agent.queue_processor import QueuedInput
        from kollabor_tui.display_tap import DisplayTap

        tap = DisplayTap()
        self.renderer.get_service = MagicMock(return_value=tap)
        self.streaming_handler.call_llm = AsyncMock(side_effect=RuntimeError("provider down"))
        voice = QueuedInput("is it green", {}, origin_context=make_voice_origin_context("turn-9"))
        self.loop.run_until_complete(self.processor.process_message_batch([voice], "parent"))
        ids = [
            (e["type"], e.get("request_id"))
            for e in tap.get_snapshot()
            if e["type"] in ("error", "turn_complete")
        ]
        self.assertEqual(ids, [("error", "turn-9"), ("turn_complete", "turn-9")])

    def test_a_turn_that_ends_while_a_voice_turn_waits_leaves_its_stamp_alone(self):
        """A hub turn still running when a voice turn queues ends by clearing its
        own stamp: the voice turn's stays its own, and goes when it ends."""
        from kollabor_agent.queue_processor import QueuedInput
        from kollabor_tui.display_tap import DisplayTap, publish_semantic
        from kollabor_tui.turn_context import current_turn

        tap = DisplayTap()
        self.renderer.get_service = MagicMock(return_value=tap)
        self._reply_with("Sure.")
        self.streaming_handler.call_llm = AsyncMock(return_value="Sure.")

        async def voice_turn():
            voice = QueuedInput(
                "is it green", {}, origin_context=make_voice_origin_context("turn-9")
            )
            await self.processor.process_message_batch([voice], "parent")
            publish_semantic(self.renderer, "token", text="typed reply")  # the next turn
            return current_turn.get()

        async def scenario():
            await self.processor._turn_lock.acquire()  # a hub turn is running
            waiting = asyncio.create_task(voice_turn())
            await asyncio.sleep(0.01)
            self.processor._stamp_voice_turn(None)  # the hub turn ends
            self.processor._turn_lock.release()
            return await waiting

        after = self.loop.run_until_complete(scenario())
        tokens = [e.get("request_id") for e in tap.get_snapshot() if e["type"] == "token"]
        self.assertEqual(tokens, [None])  # the typed turn's token: not the old voice id
        self.assertNotIn("request_id", after)

    def _talk_over_turns(self):
        """A voice reply in history, the cut's service, and what each model call saw."""
        from kollabor.state.local import LocalStateService

        self._reply_with("Let me check.")
        reply = ConversationMessage(
            role="assistant", content="Let me check.", metadata={"voice_turn_id": "turn-9"}
        )
        self.conversation_history.append(reply)
        llm = SimpleNamespace(
            _queue_processor=self.processor, conversation_history=self.conversation_history,
            conversation_logger=SimpleNamespace(), current_parent_uuid="parent",
        )
        seen = []

        async def call_llm(*args, **kwargs):
            seen.append(reply.content)
            return "Let me check."

        self.streaming_handler.call_llm = AsyncMock(side_effect=call_llm)
        return reply, LocalStateService(llm, profile_manager=object(), event_bus=None), seen

    def test_a_talk_over_cut_waits_for_the_voice_turns_last_round(self):
        """The turn lock is free between a turn's rounds: a cut taken there would
        land before the rest of the turn, which its one post per turn never cuts."""
        from kollabor_agent.queue_processor import QueuedInput

        reply, state, seen = self._talk_over_turns()
        answers = []

        async def batch(messages):
            await self.processor.process_message_batch(messages, "parent")
            self.processor.turn_completed = False  # its tools ran: one more round
            # The talk-over, between the rounds: answered at once, holding nothing.
            answers.append(await state.record_interrupted_voice_reply("turn-9", "Let me"))

        async def scenario():
            voice = QueuedInput("is it green", {}, origin_context=make_voice_origin_context("turn-9"))
            await self.processor.enqueue(voice)
            await self.processor.process_queue(
                MagicMock(), batch, lambda: self.processor.continue_conversation("parent")
            )
            return reply.content

        # then the cut landed, as the turn ended: before the drain returned
        self.assertEqual(self.loop.run_until_complete(scenario()), "Let me")
        self.assertEqual(answers, [{"recorded": False, "pending": True}])
        self.assertEqual(seen, ["Let me check.", "Let me check."])  # its last round ran uncut
        self.assertIsNone(self.processor._open_voice_turn)
        self.assertEqual(self.processor._after_turn, [])

    def test_a_talk_over_then_speaking_again_cuts_before_the_next_turns_first_round(self):
        """Talk over and speak again at once: the new turn's model call sees the cut."""
        from kollabor_agent.queue_processor import QueuedInput

        reply, state, seen = self._talk_over_turns()
        answers = []

        async def batch(messages):
            await self.processor.process_message_batch(messages, "parent")
            if not answers:  # the reply is playing: the user talks over it, then speaks
                answers.append(await state.record_interrupted_voice_reply("turn-9", "Let me"))
                await self.processor.enqueue(QueuedInput(
                    "and the blue one", {}, origin_context=make_voice_origin_context("turn-10")
                ))
                self.processor.turn_completed = False  # it still had a round to run

        async def scenario():
            voice = QueuedInput("is it green", {}, origin_context=make_voice_origin_context("turn-9"))
            await self.processor.enqueue(voice)
            await self.processor.process_queue(
                MagicMock(), batch, lambda: self.processor.continue_conversation("parent")
            )

        self.loop.run_until_complete(scenario())
        self.assertEqual(answers, [{"recorded": False, "pending": True}])
        self.assertEqual(seen, ["Let me check.", "Let me"])  # turn-10's first round: cut
        self.assertEqual(self.processor._after_turn, [])

    def test_a_stopped_voice_turn_leaves_the_next_turn_its_own_grant(self):
        """A Stop, a talk-over or an error while a voice turn's tool runs ends that
        turn. The next turn, spoken or typed, never runs on the ended turn's grant:
        an invalid one refuses all its tools. Its events keep the id of the voice
        turn whose reply stream waits for them (the newest voice turn in it, else
        the one it cut off), or that stream would never end."""
        from kollabor_agent.queue_processor import QueuedInput

        seen = []
        invalid = {"source": "livekit_voice", "voice_tool_policy": "invalid"}
        # Input that cuts in while the turn still has a round to run fails closed.
        cases = (
            ("stop", True, "own"),
            ("stop", False, None),
            ("error", True, "own"),
            ("new input", True, {**invalid, "turn_id": "turn-2"}),
            ("new input", False, {**invalid, "turn_id": "turn-1"}),
        )
        for ended_by, said, expected in cases:
            with self.subTest(ended_by=ended_by, said=said):
                seen.clear()
                spoken = make_voice_origin_context("turn-2")  # a turn's end revokes its grant
                after = QueuedInput("and the blue one", {}, origin_context=spoken) if said else "and the blue one"
                expected = spoken if expected == "own" else expected

                async def turn(**kwargs):
                    seen.append(kwargs.get("execution_context"))
                    self.processor.turn_completed = len(seen) > 1
                    if len(seen) == 1 and ended_by == "stop":  # its tool runs, the Stop lands
                        self.processor.request_cancellation(origin="human")
                    return "parent"

                async def next_round():  # the turn's next round
                    if ended_by == "error":
                        raise RuntimeError("the model went away")
                    await self.processor.enqueue(after)  # the input arrives as it runs

                self.processor._execute_llm_turn = turn
                drain = lambda: self.processor.process_queue(  # noqa: E731
                    MagicMock(), lambda m: self.processor.process_message_batch(m, "parent"), next_round
                )
                self.loop.run_until_complete(self.processor.enqueue(QueuedInput(
                    "is it green", {}, origin_context=make_voice_origin_context("turn-1")
                )))
                self.loop.run_until_complete(drain())
                if ended_by != "new input":
                    self.processor.cancel_processing = False  # the next message clears the latch
                    self.loop.run_until_complete(self.processor.enqueue(after))
                    self.loop.run_until_complete(drain())
                self.assertEqual(seen[1], expected)
                self.assertIsNone(self.processor.active_origin_context)

    def test_a_cut_queued_during_another_round_lands_as_that_round_ends(self):
        """A hub wake's round holds the lock when the talk-over comes: the cut
        waits for it, then lands with no next turn needed."""
        reply, state, seen = self._talk_over_turns()
        answers = []
        first_call = self.streaming_handler.call_llm.side_effect

        async def call_llm(*args, **kwargs):
            answers.append(await state.record_interrupted_voice_reply("turn-9", "Let me"))
            return await first_call(*args, **kwargs)

        self.streaming_handler.call_llm = AsyncMock(side_effect=call_llm)
        self.loop.run_until_complete(self.processor.continue_conversation("parent"))
        self.assertEqual(answers, [{"recorded": False, "pending": True}])
        self.assertEqual(seen, ["Let me check."])  # never cut under the round
        self.assertEqual(reply.content, "Let me")
        self.assertEqual(self.processor._after_turn, [])

    def test_a_cut_queued_behind_the_lock_lands_before_the_next_round(self):
        reply, state, seen = self._talk_over_turns()

        async def scenario():
            await self.processor._turn_lock.acquire()  # something else is writing
            answer = await state.record_interrupted_voice_reply("turn-9", "Let me")
            self.processor._turn_lock.release()
            await self.processor.continue_conversation("parent")
            return answer

        self.assertEqual(self.loop.run_until_complete(scenario()), {"recorded": False, "pending": True})
        self.assertEqual(seen, ["Let me"])  # its model call saw the cut

    def test_a_cut_never_waits_behind_a_round_about_to_take_the_lock(self):
        """The lock is free but a woken round has it next: the cut still answers at
        once, so nothing behind it on the connection waits out that round."""
        reply, state, seen = self._talk_over_turns()

        async def scenario():
            await self.processor._turn_lock.acquire()
            woken = asyncio.create_task(self.processor.continue_conversation("parent"))
            await asyncio.sleep(0)  # it waits on the lock
            self.processor._turn_lock.release()  # and takes it next
            answer = await asyncio.wait_for(
                state.record_interrupted_voice_reply("turn-9", "Let me"), 0.05
            )
            await woken
            return answer

        self.assertEqual(self.loop.run_until_complete(scenario()), {"recorded": False, "pending": True})
        self.assertEqual(seen, ["Let me"])  # and it landed before that round's model call

    def test_a_cut_queued_while_a_flush_runs_lands_in_that_flush(self):
        """A flush holds the lock while it runs a cut, so one queued meanwhile starts
        no flush of its own: the running flush takes it too, not the next round."""
        ran, gate = [], asyncio.Event()

        async def first():
            await gate.wait()  # a big log being cut
            ran.append("first")

        async def second():
            ran.append("second")

        async def scenario():
            self.processor.after_turn("turn-8", first)  # no round running: a flush starts
            await asyncio.sleep(0)
            self.assertTrue(self.processor._turn_lock.locked())  # and is cutting
            self.processor.after_turn("turn-9", second)
            gate.set()
            await self.processor._after_turn_flush

        self.loop.run_until_complete(scenario())
        self.assertEqual(ran, ["first", "second"])
        self.assertEqual(self.processor._after_turn, [])

    def test_a_round_that_raises_leaves_its_cut_to_a_task_once_the_lock_is_free(self):
        """A round that raises skips its end-of-round flush (here a pre-request hook
        cancels it): a cut queued during it lands from a task, not at the next round."""
        reply, state, seen = self._talk_over_turns()

        async def hooks(*args, **kwargs):
            if not self.processor._after_turn:  # talked over while the round runs
                await state.record_interrupted_voice_reply("turn-9", "Let me")
            return {"cancelled": True}

        self.event_bus.emit_with_hooks = AsyncMock(side_effect=hooks)

        async def scenario():
            with self.assertRaises(asyncio.CancelledError):
                await self.processor._execute_llm_turn(True, "parent")
            await asyncio.wait_for(self.processor._after_turn_flush, 1)

        self.loop.run_until_complete(scenario())
        self.assertEqual(reply.content, "Let me")
        self.assertEqual(self.processor._after_turn, [])

    def test_a_drain_that_dies_leaves_its_turns_cut_to_a_task(self):
        """The cut waits for its voice turn to end; a drain cancelled before then
        ends the turn in its finally, and a task lands the cut."""
        from kollabor_agent.queue_processor import QueuedInput

        reply, state, seen = self._talk_over_turns()
        queued = asyncio.Event()

        async def batch(messages):
            await self.processor.process_message_batch(messages, "parent")
            await state.record_interrupted_voice_reply("turn-9", "Let me")  # turn-9 is open
            queued.set()
            await asyncio.Event().wait()  # the drain dies here

        async def scenario():
            voice = QueuedInput("is it green", {}, origin_context=make_voice_origin_context("turn-9"))
            await self.processor.enqueue(voice)
            drain = asyncio.create_task(self.processor.process_queue(
                MagicMock(), batch, lambda: self.processor.continue_conversation("parent")
            ))
            await queued.wait()
            self.assertEqual(reply.content, "Let me check.")  # never cut under its turn
            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass
            await asyncio.wait_for(self.processor._after_turn_flush, 1)

        self.loop.run_until_complete(scenario())
        self.assertEqual(reply.content, "Let me")
        self.assertEqual(self.processor._after_turn, [])

    def test_a_stop_while_tools_run_keeps_the_reply_shown_before_them(self):
        """The reply joins history after its tools; a Stop in between kept nothing."""
        self.streaming_handler.call_llm = AsyncMock(
            return_value="Running it.\n<terminal>sleep 9</terminal>"
        )
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 0
        self.tool_executor.execute_tool = AsyncMock(
            side_effect=asyncio.CancelledError("Request cancelled by user")
        )
        self.response_parser.parse_response.return_value = {
            "content": "Running it.",
            "components": {},
            "turn_completed": False,
        }
        self.response_parser.get_all_tools.return_value = [
            {"id": "terminal_1", "type": "terminal", "command": "sleep 9"}
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.conversation_logger.log_system_message = AsyncMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("Running it.", False, False)
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="t",
            )
        )

        replies = self._assistant_messages()
        self.assertEqual(
            [(m.content, m.metadata) for m in replies],
            [("Running it.", {"interrupted": True})],
        )

    def test_context_window_failure_emits_typed_error_without_retry(self):
        class ContextWindowExceeded(RuntimeError):
            error_code = "context_window_exceeded"

        self.streaming_handler.call_llm.side_effect = ContextWindowExceeded(
            "model context window exceeded"
        )

        self.api_service._provider = SimpleNamespace(
            config=SimpleNamespace(context_window=272_000)
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="overflow",
            )
        )

        error_call = next(
            call
            for call in self.event_bus.emit_with_hooks.await_args_list
            if call.args[0] is EventType.LLM_REQUEST_ERROR
        )
        error_data = error_call.args[1]
        self.assertEqual(error_data["error_code"], "context_window_exceeded")
        self.assertTrue(error_data["context_window_exceeded"])
        self.streaming_handler.call_llm.assert_awaited_once()
        tap = self.renderer.get_service.return_value
        self.assertNotIn(
            "usage_update",
            [call.args[0]["type"] for call in tap.publish.call_args_list],
        )


    def test_pre_request_hook_can_withhold_tools_for_one_request(self):
        self.native_tools_handler.tools = [{"name": "terminal"}]
        self.event_bus.emit_with_hooks.return_value = {
            "main": {"final_data": {"withhold_tools": True}}
        }

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="relay-event",
            )
        )

        kwargs = self.streaming_handler.call_llm.call_args.kwargs
        self.assertIsNone(kwargs["native_tools"])
        self.assertIsNone(kwargs["native_tools_provider"])

    def test_human_queue_drain_does_not_inherit_remote_task_context(self):
        async def scenario():
            token = remote_task_id.set("previous-remote-task")
            observed = []

            async def process(messages):
                observed.append(remote_task_id.get())
                self.processor.turn_completed = True

            try:
                self.processor.processing_queue.put_nowait("human input")
                await self.processor.process_queue(MagicMock(), process, AsyncMock())
                self.assertEqual(observed, [None])
                self.assertEqual(remote_task_id.get(), "previous-remote-task")
            finally:
                remote_task_id.reset(token)

        self.loop.run_until_complete(scenario())

    def test_init(self):
        """Test QueueProcessor initialization."""
        self.assertIsNotNone(self.processor)
        self.assertEqual(self.processor.max_queue_size, 10)
        self.assertEqual(self.processor.dropped_messages, 0)
        self.assertFalse(self.processor.is_processing)
        # turn_completed set to True in setUp to prevent infinite loops
        self.assertFalse(self.processor.cancel_processing)

    def test_voice_metadata_survives_batch_and_resets_for_typed_input(self):
        from kollabor_agent.queue_processor import QueuedInput
        self.processor._execute_llm_turn = AsyncMock(return_value="parent")
        voice = {
            "epoch": "epoch", "reply_id": "reply",
            "spoken_ack": "Let me check on that.",
            "playback_context": ["Should I run the tests?"],
        }
        self.loop.run_until_complete(self.processor.process_message_batch(
            [QueuedInput("Check the tests", voice)], "parent"
        ))
        message = self.add_message_fn.call_args.args[0]
        self.assertEqual(message.metadata["voice"], voice)
        self.assertIn("Voice instructions", message.content)
        self.assertIn('[you already said: "Let me check on that."]', message.content)
        self.assertIn("one period (.) with no tools", message.content)
        self.assertIn("Should I run the tests?", message.content)
        self.assertEqual(self.processor.active_voice, voice)
        self.loop.run_until_complete(self.processor.process_message_batch(["Typed input"], "parent"))
        self.assertIsNone(self.processor.active_voice)

    def test_the_users_yes_tells_the_model_what_it_approved(self):
        """The note rides on the yes itself, model-only, once."""
        from kollabor_agent.yes_approval import YesApprovals

        approvals = YesApprovals()
        self.processor.tool_executor = MagicMock(yes_approvals=approvals)
        self.processor._execute_llm_turn = AsyncMock(return_value="parent")
        approvals.ask({"type": "terminal", "id": "c1", "command": "gh pr list"})
        approvals.on_reply_done()
        approvals.on_human_input("yes")

        self.loop.run_until_complete(self.processor.process_message_batch(["yes"], "parent"))
        yes = self.add_message_fn.call_args.args[0]
        self.assertEqual(yes.content, "yes")
        self.assertIn("gh pr list", yes.metadata["injected_context"])

        self.loop.run_until_complete(self.processor.process_message_batch(["thanks"], "parent"))
        self.assertNotIn("injected_context", self.add_message_fn.call_args.args[0].metadata)

    def test_what_a_reply_asked_opens_when_its_chain_ends(self):
        """A yes answers a reply only once it is finished: the chain's end is that point."""
        from kollabor_agent.yes_approval import YesApprovals

        approvals = YesApprovals()
        self.processor.tool_executor = MagicMock(yes_approvals=approvals)
        call = {"type": "terminal", "id": "c1", "command": "gh pr list"}
        self.processor.is_processing = True
        approvals.ask(call)
        approvals.on_human_input("yes")  # while the reply is still written: answers nothing
        self.assertFalse(approvals.take({**call, "id": "c0"}))
        approvals.ask(call)
        self.processor.is_processing = False
        approvals.on_human_input("yes")
        self.assertTrue(approvals.take({**call, "id": "c2"}))

    def test_livekit_origin_survives_tool_continuation(self):
        from kollabor_agent.queue_processor import QueuedInput
        from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

        origin_context = make_voice_origin_context("turn-1")
        self.processor.turn_completed = False
        self.processor._execute_llm_turn = AsyncMock(return_value="parent-uuid")
        queued = QueuedInput("show status", {}, origin_context=origin_context)

        self.loop.run_until_complete(
            self.processor.process_message_batch([queued], "parent-uuid")
        )

        self.assertEqual(
            self.processor._execute_llm_turn.await_args.kwargs["execution_context"],
            origin_context,
        )
        self.processor._execute_llm_turn.reset_mock()
        self.processor.turn_completed = False

        self.loop.run_until_complete(
            self.processor.continue_conversation("parent-uuid")
        )

        self.assertEqual(
            self.processor._execute_llm_turn.await_args.kwargs["execution_context"],
            origin_context,
        )


    def test_mixed_voice_turn_provenance_fails_closed(self):
        from kollabor_agent.queue_processor import (
            QueuedInput,
            merge_livekit_origin_contexts,
        )
        from kollabor_agent.voice_admission import validate_voice_admission_context
        from tests.unit.llm.voice_admission_helpers import make_voice_origin_context

        voice_context = make_voice_origin_context("turn-1")
        merged = merge_livekit_origin_contexts(
            [
                QueuedInput("voice text", {}, origin_context=voice_context),
                "typed input",
            ]
        )

        self.assertEqual(merged["source"], "livekit_voice")
        self.assertEqual(merged["voice_tool_policy"], "invalid")
        self.assertEqual(merged["turn_id"], "turn-1")  # its reply stream still waits
        self.assertFalse(validate_voice_admission_context(voice_context))
        self.assertFalse(validate_voice_admission_context(merged))


    def test_voice_rules_ride_a_sessions_first_turn_then_a_short_nudge(self):
        from kollabor_agent.queue_processor import QueuedInput
        self.processor._execute_llm_turn = AsyncMock(return_value="parent")
        self.add_message_fn.side_effect = (
            lambda message, **_: self.conversation_history.append(message)
        )

        def say(text, **voice):
            voice = {"epoch": "e1", **voice}
            self.loop.run_until_complete(self.processor.process_message_batch(
                [QueuedInput(text, voice)], "parent"
            ))
            return self.conversation_history[-1].content

        first = say("Hello, can you hear me?")
        self.assertIn("<display_text>", first)
        self.assertIn("<spoken_text>", first)
        self.assertEqual(
            say("Run the tests"),
            "[voice mode on: the user said this aloud]\nRun the tests",
        )
        self.assertEqual(
            say("Check the build", spoken_ack="On it."),
            '[voice mode on: the user said this aloud] [you already said: "On it."]'
            "\nCheck the build",
        )
        # a new voice session, or rules that compaction dropped, send them again
        self.assertIn("<display_text>", say("Hi again", epoch="e2"))
        self.conversation_history.clear()
        self.assertIn("<display_text>", say("After compaction"))

    def test_typed_overflow_cannot_drop_a_durably_admitted_voice_input(self):
        from kollabor_agent.queue_processor import QueuedInput
        self.processor.processing_queue = asyncio.Queue(maxsize=1)
        voice = QueuedInput("Check the tests", {"epoch": "epoch"})
        self.loop.run_until_complete(self.processor.enqueue(voice))
        with self.assertRaisesRegex(RuntimeError, "cannot discard"):
            self.loop.run_until_complete(self.processor.enqueue("Typed input"))
        self.assertIs(self.processor.processing_queue.get_nowait(), voice)
        self.assertEqual(self.processor.dropped_messages, 0)
        self.assertFalse(self.processor.is_processing)
        # turn_completed set to True in setUp to prevent infinite loops
        self.assertFalse(self.processor.cancel_processing)

    def test_remote_cancellation_requires_idle_matching_generation(self):
        self.processor.is_processing = True
        generation = self.processor.request_cancellation(origin="remote_task")

        self.assertFalse(self.processor.clear_remote_task_cancellation(generation))
        self.processor.is_processing = False
        self.assertFalse(self.processor.clear_remote_task_cancellation(generation + 1))
        self.assertTrue(self.processor.cancel_processing)
        self.assertTrue(self.processor.clear_remote_task_cancellation(generation))
        self.assertFalse(self.processor.cancel_processing)

    def test_direct_cancellation_write_supersedes_remote_generation(self):
        generation = self.processor.request_cancellation(origin="remote_task")

        self.processor.cancel_processing = True

        self.assertEqual(self.processor.cancel_origin, "external")
        self.assertGreater(self.processor.cancel_generation, generation)
        self.assertFalse(self.processor.clear_remote_task_cancellation(generation))
        self.assertTrue(self.processor.cancel_processing)

    def test_enqueue_success(self):
        """Test successful message enqueue."""
        self.loop.run_until_complete(self.processor.enqueue("test message"))

        self.assertEqual(self.queue_metrics["total_enqueue_attempts"], 1)
        self.assertEqual(self.queue_metrics["total_enqueue_successes"], 1)
        self.assertEqual(self.processor.processing_queue.qsize(), 1)

    def test_enqueue_drop_newest_strategy(self):
        """Test drop_newest overflow strategy raises RuntimeError."""
        self.task_config.queue.overflow_strategy = "drop_newest"
        self.processor.max_queue_size = 1
        self.processor.processing_queue = asyncio.Queue(maxsize=1)

        # Fill queue
        self.loop.run_until_complete(self.processor.enqueue("first"))

        # Should raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            self.loop.run_until_complete(self.processor.enqueue("second"))

        self.assertIn("Queue is full", str(ctx.exception))
        self.assertEqual(self.queue_metrics["drop_newest_count"], 1)

    def test_enqueue_block_strategy_timeout(self):
        """Test block strategy with timeout."""
        self.task_config.queue.overflow_strategy = "block"
        self.task_config.queue.block_timeout = 0.1
        self.processor.max_queue_size = 1
        self.processor.processing_queue = asyncio.Queue(maxsize=1)

        # Fill queue
        self.loop.run_until_complete(self.processor.enqueue("first"))

        # Should timeout and drop
        self.loop.run_until_complete(self.processor.enqueue("second"))

        self.assertEqual(self.queue_metrics["block_count"], 1)
        self.assertEqual(self.queue_metrics["block_timeout_count"], 1)
        self.assertEqual(self.processor.dropped_messages, 1)

    def test_loop2_continuation_survives_past_300s(self):
        """LOOP 2 must not force-complete a working chain on wall-clock.

        Regression: process_queue's LOOP 2 force-set turn_completed=True
        once the continuation chain's cumulative wall-clock passed 300s,
        orphaning the last turn's tool results and leaving the session
        silent (koordinator 2026-07-03 14:24, bismuth 15:15).
        """
        task_manager = MagicMock()
        process_batch_fn = AsyncMock()

        offset = {"v": 0.0}
        calls = {"n": 0}

        async def continue_fn():
            calls["n"] += 1
            if calls["n"] == 1:
                # Simulate the first continuation turn taking >300s.
                offset["v"] += 400.0
            if calls["n"] >= 2:
                self.processor.turn_completed = True  # natural completion

        self.processor.turn_completed = False
        real_mono = time.monotonic

        with patch(
            "kollabor_agent.queue_processor.time.monotonic",
            side_effect=lambda: real_mono() + offset["v"],
        ):
            self.loop.run_until_complete(
                self.processor.process_queue(
                    task_manager, process_batch_fn, continue_fn
                )
            )

        self.assertEqual(
            calls["n"],
            2,
            "LOOP 2 must continue past 300s until the model completes the "
            "turn (old code force-completed after 1 call)",
        )
        self.assertTrue(self.processor.turn_completed)

    def test_loop2_stops_on_identical_tool_errors(self):
        """LOOP 2 stops once 3 identical failing tool batches have run in a row.

        The turn that led into LOOP 2 counts as the first one. (The old loop
        needed 5 and its log line claimed 3.)
        """
        calls = {"n": 0}

        async def continue_fn():
            calls["n"] += 1
            self.processor._last_tool_error_sig = "terminal:boom"
            if calls["n"] >= 50:
                self.processor.turn_completed = True

        self.processor.turn_completed = False
        self.processor._last_tool_error_sig = "terminal:boom"
        self.loop.run_until_complete(
            self.processor.process_queue(MagicMock(), AsyncMock(), continue_fn)
        )
        self.assertEqual(calls["n"], 2)
        self.assertTrue(self.processor.turn_completed)
        self.message_display_service.display_error_message.assert_called_once()

    def test_process_queue_empty(self):
        """Test processing empty queue returns immediately."""
        task_manager = MagicMock()
        process_batch_fn = AsyncMock()
        continue_fn = AsyncMock()

        self.loop.run_until_complete(
            self.processor.process_queue(task_manager, process_batch_fn, continue_fn)
        )

        self.assertFalse(process_batch_fn.called)
        self.assertFalse(continue_fn.called)
        self.assertFalse(self.processor.is_processing)

    def test_process_queue_with_messages(self):
        """Test processing messages from queue."""
        task_manager = MagicMock()
        process_batch_fn = AsyncMock()
        continue_fn = AsyncMock()

        # Add messages
        self.loop.run_until_complete(self.processor.enqueue("msg1"))
        self.loop.run_until_complete(self.processor.enqueue("msg2"))

        self.loop.run_until_complete(
            self.processor.process_queue(task_manager, process_batch_fn, continue_fn)
        )

        process_batch_fn.assert_called_once_with(["msg1", "msg2"])
        self.assertFalse(self.processor.is_processing)

    def test_context_blocks_ride_in_metadata_not_content(self):
        """Context blocks reach the request without rewriting the message text."""

        class ContextService:
            def increment_turn(self):
                pass

            def build_curator_injection(self):
                return "[ephemeral context]"

            def build_context_snapshot(self):
                return None

            def build_confirmation_injection(self):
                return None

            def build_divergence_warnings(self):
                return None

            def drain_ephemeral_injections(self):
                return [("", "[legacy context]")]

        wire_contents = []

        async def capture_request(**kwargs):
            wire_contents.append(
                [
                    (message.content, message.metadata.get("injected_context"))
                    for message in kwargs["conversation_history"]
                ]
            )
            return "test response"

        self.event_bus.get_service.return_value = ContextService()
        self.conversation_history.append(
            ConversationMessage(role="user", content="original prompt")
        )
        self.streaming_handler.call_llm.side_effect = capture_request
        self.api_service.last_stop_reason = ""
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_tool_calls.return_value = []
        self.api_service.last_thinking_content = None
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 0
        self.response_parser.parse_response.return_value = {
            "content": "test response",
            "components": {},
            "turn_completed": True,
        }
        self.response_parser.get_all_tools.return_value = []
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("test response", False, False)
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        self.assertEqual(
            wire_contents,
            [[("original prompt", "[ephemeral context]\n\n---\n\n[legacy context]")]],
        )
        native_tools_provider = self.streaming_handler.call_llm.call_args.kwargs[
            "native_tools_provider"
        ]
        discovered_tools = [{"name": "list_tasks"}]
        self.native_tools_handler.tools = discovered_tools
        self.assertIs(native_tools_provider(), discovered_tools)
        self.assertEqual(self.conversation_history[-1].content, "original prompt")

    def test_pipe_mode_suppresses_intermediate_tool_response(self):
        """Pipe mode emits the continuation, not the pre-tool response."""
        self.renderer.pipe_mode = True
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 1
        self.tool_executor.format_result_for_conversation.return_value = "ok"
        self.tool_executor.execute_tool = AsyncMock(
            return_value=ToolExecutionResult(
                tool_id="terminal_1",
                tool_type="terminal",
                success=True,
                output="",
            )
        )
        self.response_parser.parse_response.return_value = {
            "content": "intermediate answer",
            "components": {},
            "turn_completed": False,
        }
        self.response_parser.get_all_tools.return_value = [
            {"id": "terminal_1", "type": "terminal", "command": "printf ''"}
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.conversation_logger.log_system_message = AsyncMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("intermediate answer", False, False)
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        self.message_display_service.display_complete_response.assert_not_called()
        self.message_display_service.display_tool_results.assert_called_once()

    def test_xml_tool_turn_keeps_the_reply_and_adds_display_metadata(self):
        """The model keeps its own tags; the web gets display-only metadata.

        The stored reply goes back to the provider on every later request, so
        it keeps the <terminal> tag the model wrote. The web UI reads
        display_content and xml_tool_calls instead -- never tool_calls, which
        _prepare_messages would send a provider as a call it never issued --
        and each result by id, since one result can span many lines.
        """
        raw = "Running it.\n<terminal>echo fake-tool-ran</terminal>"
        self.streaming_handler.call_llm = AsyncMock(return_value=raw)
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 1
        self.tool_executor.format_result_for_conversation.return_value = (
            "[terminal] fake-tool-ran\nsecond line"
        )
        self.tool_executor.execute_tool = AsyncMock(
            return_value=ToolExecutionResult(
                tool_id="terminal_1",
                tool_type="terminal",
                success=True,
                output="fake-tool-ran",
            )
        )
        self.response_parser.parse_response.return_value = {
            "content": "Running it.",
            "components": {},
            "turn_completed": False,
        }
        self.response_parser.get_all_tools.return_value = [
            {
                "id": "terminal_1",
                "type": "terminal",
                "command": "echo fake-tool-ran",
                "_position": 12,
            }
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.conversation_logger.log_system_message = AsyncMock()
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("Running it.", False, False)
        )
        tap = MagicMock()
        self.renderer.get_service = MagicMock(return_value=tap)

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        assistant = self.add_message_fn.call_args.args[0]
        self.assertEqual(assistant.role, "assistant")
        self.assertEqual(assistant.content, raw)
        self.assertEqual(assistant.metadata.get("display_content"), "Running it.")
        self.assertEqual(
            assistant.metadata.get("xml_tool_calls"),
            [
                {
                    "id": "terminal_1",
                    "name": "terminal",
                    "input": {"command": "echo fake-tool-ran"},
                }
            ],
        )
        self.assertNotIn("tool_calls", assistant.metadata)
        batch = self.conversation_history[-1]
        self.assertTrue(batch.metadata.get("tool_output_batch"))
        self.assertEqual(
            batch.metadata.get("xml_tool_results"),
            [
                {
                    "id": "terminal_1",
                    "content": "[terminal] fake-tool-ran\nsecond line",
                    "tool_execution_time": 0.0,
                }
            ],
        )
        self.assertEqual(
            self.conversation_logger.log_system_message.call_args.kwargs["tool_result"],
            batch.metadata["xml_tool_results"][0],
        )

        # The clean text also reaches non-terminal clients as a semantic
        # event, so the web live stream can drop the raw tags it streamed.
        response_events = [
            call.args[0]
            for call in tap.publish.call_args_list
            if call.args and call.args[0].get("type") == "response"
        ]
        self.assertEqual(response_events, [{"type": "response", "text": "Running it."}])

    def test_model_first_compaction_resumes_once_after_staging(self):
        """The same user turn resumes only after the staged swap is applied."""
        from kollabor_events import EventType
        from plugins.context_compaction_plugin import ContextCompactionPlugin

        history = self.conversation_history
        history.extend(
            ConversationMessage(
                role="user" if index % 2 == 0 else "assistant",
                content=f"prior message {index}",
            )
            for index in range(12)
        )
        compact_values = {
            "plugins.context_compaction.keep_recent": 2,
            "plugins.context_compaction.max_summary_tokens": 2000,
        }
        compact_config = MagicMock()
        compact_config.get = lambda key, default=None: compact_values.get(key, default)
        plugin = ContextCompactionPlugin(
            "test", MagicMock(), MagicMock(), compact_config
        )
        plugin._llm_service = SimpleNamespace(
            conversation_history=history,
            session_stats=self.session_stats,
            is_processing=False,
            _pending_agent_hud=[],
        )
        plugin._conversation_logger = SimpleNamespace(session_id="session-1")
        plugin._awaiting_model = True
        plugin._write_compaction_checkpoint = MagicMock(return_value=None)
        plugin._log_compaction_event = AsyncMock()

        summary_started = asyncio.Event()
        release_summary = asyncio.Event()

        async def summarize(*_args, **_kwargs):
            summary_started.set()
            await release_summary.wait()
            return "S" * 200

        plugin._call_summarization_llm = AsyncMock(side_effect=summarize)
        applied_swaps = []

        async def emit_with_hooks(*args, **kwargs):
            event_type = args[0] if args else kwargs["event_type"]
            data = args[1] if len(args) > 1 else kwargs.get("data", {})
            if event_type == EventType.LLM_REQUEST_PRE:
                had_pending_compaction = plugin._pending_compaction is not None
                data = await plugin._apply_pending_compaction(data, MagicMock())
                if had_pending_compaction:
                    applied_swaps.append(list(history))
            return data

        self.event_bus.emit_with_hooks = AsyncMock(side_effect=emit_with_hooks)
        llm_call_count = 0
        request_snapshots = []
        llm_responses = [
            "Preparing the requested work. <compact>task: continue</compact>",
            "The original response is complete.",
        ]

        async def call_llm(*_args, **_kwargs):
            nonlocal llm_call_count
            llm_call_count += 1
            request_snapshots.append(
                [
                    (message.role, message.content, dict(message.metadata or {}))
                    for message in history
                ]
            )
            return llm_responses[llm_call_count - 1]

        self.streaming_handler.call_llm = AsyncMock(side_effect=call_llm)
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_provider_reasoning = MagicMock(return_value=None)
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 1
        self.tool_executor.format_result_for_conversation.return_value = (
            "[compact] history staged"
        )

        async def execute_compact(tool_data, *_args, **_kwargs):
            return await plugin._handle_compact_tool(tool_data)

        self.tool_executor.execute_tool = AsyncMock(side_effect=execute_compact)
        self.response_parser.parse_response.side_effect = [
            {
                "content": "Preparing the requested work.",
                "components": {},
                "turn_completed": False,
            },
            {
                "content": "The original response is complete.",
                "components": {},
                "turn_completed": True,
            },
        ]
        self.response_parser.get_all_tools.side_effect = [
            [
                {
                    "id": "compact_1",
                    "type": "compact",
                    "notes": "task: continue",
                    "_position": 29,
                }
            ],
            [],
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.conversation_logger.log_system_message = AsyncMock()
        self.processor._bridge_relay = AsyncMock()
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            side_effect=[
                ("Preparing the requested work.", False, False),
                ("The original response is complete.", False, False),
            ]
        )
        self.renderer.get_service = MagicMock(return_value=MagicMock())

        def append_message(message, **_kwargs):
            history.append(message)

        self.add_message_fn.side_effect = append_message
        parent_uuid = ["parent-uuid"]
        late_message = ConversationMessage(
            role="user", content="arrived while compaction was running"
        )
        batch_inputs = []
        turn_complete_calls = []
        turn_complete_at_batch_return = []
        continuation_calls = 0

        def capture_semantic(_renderer, event_type, **_payload):
            if event_type == "turn_complete":
                turn_complete_calls.append(llm_call_count)

        async def process_batch(messages):
            batch_inputs.append(list(messages))
            turn = asyncio.create_task(
                self.processor.process_message_batch(messages, parent_uuid[0])
            )
            try:
                await asyncio.wait_for(summary_started.wait(), timeout=2)
                history.append(late_message)
            finally:
                release_summary.set()
            parent_uuid[0] = await turn
            turn_complete_at_batch_return.append(len(turn_complete_calls))

        async def continue_turn():
            nonlocal continuation_calls
            continuation_calls += 1
            parent_uuid[0] = await self.processor.continue_conversation(
                parent_uuid[0]
            )

        async def run_queue():
            await self.processor.enqueue("original user request")
            await asyncio.wait_for(
                self.processor.process_queue(
                    MagicMock(), process_batch, continue_turn
                ),
                timeout=5,
            )

        try:
            with patch(
                "kollabor_agent.queue_processor.publish_semantic",
                side_effect=capture_semantic,
            ):
                self.loop.run_until_complete(run_queue())
        finally:
            release_summary.set()
            pending_tasks = tuple(plugin._compaction_tasks)
            if pending_tasks:
                self.loop.run_until_complete(
                    asyncio.gather(*pending_tasks, return_exceptions=True)
                )

        self.assertEqual(batch_inputs, [["original user request"]])
        self.assertEqual(turn_complete_at_batch_return, [0])
        self.assertEqual(turn_complete_calls, [2])
        self.assertEqual(continuation_calls, 1)
        self.assertEqual(self.streaming_handler.call_llm.await_count, 2)
        self.tool_executor.execute_tool.assert_awaited_once()
        self.assertEqual(len(applied_swaps), 1)
        self.assertIsNone(plugin._pending_compaction)
        self.assertFalse(plugin._compaction_in_progress)
        self.assertEqual(sum(message is late_message for message in history), 1)

        self.assertEqual(len(request_snapshots), 2)
        first_request_users = [
            content
            for role, content, _metadata in request_snapshots[0]
            if role == "user" and content == "original user request"
        ]
        second_request_users = [
            content
            for role, content, _metadata in request_snapshots[1]
            if role == "user" and content == "original user request"
        ]
        self.assertEqual(len(first_request_users), 1)
        self.assertLessEqual(len(second_request_users), 1)
        late_in_resumed_request = [
            content
            for role, content, _metadata in request_snapshots[1]
            if role == "user" and content == late_message.content
        ]
        self.assertEqual(len(late_in_resumed_request), 1)
        compact_summaries = [
            content
            for _role, content, metadata in request_snapshots[1]
            if metadata.get("context_compaction")
        ]
        self.assertEqual(len(compact_summaries), 1)

        tool_batches = [
            message for message in history if message.metadata.get("tool_output_batch")
        ]
        self.assertEqual(len(tool_batches), 1)
        self.assertEqual(len(tool_batches[0].metadata["xml_tool_results"]), 1)
        final_responses = [
            message
            for message in history
            if message.role == "assistant"
            and message.content == "The original response is complete."
        ]
        self.assertEqual(len(final_responses), 1)


    def test_native_and_xml_results_log_display_data_without_changing_model_calls(self):
        native = SimpleNamespace(id="native-one", name="file_edit", input={"file": "owned.txt"})
        diffs = [{"path": "owned.txt", "old_text": "before", "new_text": "after"}]
        self.streaming_handler.call_llm = AsyncMock(return_value="Checking\n<terminal>echo xml</terminal>")
        self.native_tools_handler.tool_calling_enabled = True
        self.api_service.has_pending_tool_calls = MagicMock(return_value=True)
        self.api_service.get_last_tool_calls = MagicMock(return_value=[native])
        self.api_service.format_tool_result = MagicMock(return_value={"role": "tool", "content": "denied"})
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.get_last_provider_reasoning = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.native_tools_handler.execute_tool_calls = AsyncMock(return_value=[ToolExecutionResult(
            "native-one", "file_edit", False, error="denied", execution_time=0.25, metadata={"diffs": diffs},
        )])
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 2
        self.tool_executor.format_result_for_conversation.return_value = "[terminal] xml"
        self.tool_executor.execute_tool = AsyncMock(return_value=ToolExecutionResult(
            "xml-one", "terminal", True, output="xml",
        ))
        self.response_parser.parse_response.return_value = {
            "content": "Checking", "components": {}, "turn_completed": False,
        }
        self.response_parser.get_all_tools.return_value = [
            {"id": "xml-one", "type": "terminal", "command": "echo xml"},
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(return_value="assistant-uuid")
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(return_value=("Checking", False, False))
        self.loop.run_until_complete(self.processor._execute_llm_turn_inner(
            user_message_provided=True, current_parent_uuid="parent-uuid",
        ))
        logged = self.conversation_logger.log_assistant_message.call_args.kwargs["tool_calls"]
        self.assertEqual([call["id"] for call in logged], ["native-one", "xml-one"])
        result_logs = self.conversation_logger.log_system_message.call_args_list
        self.assertEqual(result_logs[0].args[0], "Executed file_edit (native-one): denied")
        self.assertEqual(result_logs[0].kwargs["tool_result"], {
            "id": "native-one", "content": "denied", "is_error": True,
            "tool_execution_time": 0.25, "metadata": {"diffs": diffs},
        })
        self.assertEqual(result_logs[1].kwargs["tool_result"]["id"], "xml-one")
        native_result = next(message for message in self.conversation_history if message.role == "tool")
        self.assertEqual(native_result.metadata["diffs"], diffs)
        self.assertTrue(native_result.metadata["is_error"])
        self.assertEqual(self.add_message_fn.call_args.args[0].metadata["tool_calls"], [{
            "id": "native-one", "type": "function",
            "function": {"name": "file_edit", "arguments": '{"file": "owned.txt"}'},
        }])

    def test_a_native_call_written_as_text_runs_as_that_tool(self):
        """A model can write its native call into the reply instead of making it.

        The parser reads ``<functions.hub_msg>{...}`` as an MCP call by name;
        the turn sends it to the hub_msg tool, where the native call would
        have gone, and the web card shows the call's own arguments.
        """
        call_text = (
            '<functions.hub_msg>{"to":"koordinator@server","message":"hi",'
            '"wait":"true"}</functions.hub_msg>'
        )
        self.streaming_handler.call_llm = AsyncMock(return_value="On it.\n" + call_text)
        self.native_tools_handler.tool_calling_enabled = False
        self.native_tools_handler.mcp_integration.tool_registry = {}
        self.tool_executor.plugin_handlers = {"hub_msg": MagicMock()}
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 1
        self.tool_executor.format_result_for_conversation.return_value = "[hub_msg] sent"
        self.tool_executor.execute_tool = AsyncMock(
            return_value=ToolExecutionResult(
                tool_id="mcp_tool_0", tool_type="hub_msg", success=True, output="sent"
            )
        )
        arguments = {"to": "koordinator@server", "message": "hi", "wait": "true"}
        self.response_parser.parse_response.return_value = {
            "content": "On it.",
            "components": {},
            "turn_completed": False,
        }
        self.response_parser.get_all_tools.return_value = [
            {
                "type": "mcp_tool",
                "id": "mcp_tool_0",
                "name": "hub_msg",
                "arguments": arguments,
                "content": "",
                "raw": call_text,
                "_position": 7,
            }
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.conversation_logger.log_system_message = AsyncMock()
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("On it.", False, False)
        )
        self.renderer.get_service = MagicMock(return_value=MagicMock())

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        tool_data = self.tool_executor.execute_tool.call_args.args[0]
        self.assertEqual(tool_data["type"], "hub_msg")
        self.assertEqual(tool_data["to"], "koordinator@server")
        self.assertEqual(tool_data["message"], "hi")
        assistant = self.add_message_fn.call_args.args[0]
        self.assertEqual(assistant.metadata.get("display_content"), "On it.")
        self.assertEqual(
            assistant.metadata.get("xml_tool_calls"),
            [{"id": "mcp_tool_0", "name": "hub_msg", "input": arguments}],
        )

    def test_a_text_call_beside_native_calls_is_an_echo_and_never_runs(self):
        """gpt-5.6-luna called hub_msg natively and also ended its reply with
        the arguments as a <tool_call> block (live story 7, 2026-10-09).
        Running the block would send the message twice, or fail as a tool
        named "unknown"; the reply still shows without it."""
        from kollabor_agent.queue_processor import _xml_display_metadata
        from kollabor_ai.response_parser import ResponseParser

        self.tool_executor.plugin_handlers = {"hub_msg": MagicMock()}
        self.native_tools_handler.mcp_integration.tool_registry = {}
        reply = (
            "Sent.\n<terminal>pwd</terminal>\n"
            '<tool_call>{"to":"koordinator@server","message":"`uname -n`?"}</tool_call>\n'
            '<tool_call>{"name":"hub_msg","arguments":{"to":"koordinator@server"}}</tool_call>\n'
            '<functions.hub_msg>{"to":"koordinator@server"}</functions.hub_msg>'
        )
        parser = ResponseParser()
        parsed = parser.parse_response(reply)
        tools = parser.get_all_tools(parsed)

        kept = self.processor._resolve_text_tool_calls(tools, has_native_tools=True)
        self.assertEqual([tool["type"] for tool in kept], ["terminal"])
        self.assertEqual(
            _xml_display_metadata([], reply, parsed["content"], hid_text_calls=True),
            {"display_content": parsed["content"]},
        )
        self.assertNotIn("<tool_call>", parsed["content"])

        # Without native calls the named call runs as hub_msg (the two identical
        # ones once, as duplicates always do), and the nameless one answers
        # that it names no tool.
        alone = self.processor._resolve_text_tool_calls(tools, has_native_tools=False)
        self.assertEqual(
            sorted(tool["type"] for tool in alone),
            ["hub_msg", "malformed_tool", "terminal"],
        )

    def _truncated_turn(self, continuation_text, continuation_stop):
        """Run one turn whose first reply is cut at the output limit."""
        replies = iter([("one two se", "length"), (continuation_text, continuation_stop)])

        async def call_llm(**kwargs):
            text, stop = next(replies)
            self.api_service.last_stop_reason = stop
            return text

        self.streaming_handler.call_llm = AsyncMock(side_effect=call_llm)
        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls = MagicMock(return_value=False)
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.take_executed_count.return_value = 0
        self.response_parser.parse_response.side_effect = lambda text: {
            "content": text,
            "components": {},
            "turn_completed": True,
        }
        self.response_parser.get_all_tools.return_value = []
        self.conversation_logger.log_assistant_message = AsyncMock(return_value="a-uuid")
        self.conversation_logger.log_system_message = AsyncMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            side_effect=lambda raw, clean, *a, **k: (clean, False, False)
        )
        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

    def test_empty_continuation_of_a_cut_reply_is_announced(self):
        """Live 2026-10-02: a local model answered the continue request with
        nothing, and the reply silently ended mid-word."""
        self._truncated_turn("", "stop")
        notices = [
            c.args[0] for c in self.message_display_service.display_system_message.call_args_list
        ]
        self.assertTrue(any("output limit" in n for n in notices), notices)

    def test_completed_continuation_is_not_announced(self):
        self._truncated_turn("venteen", "stop")
        notices = [
            c.args[0] for c in self.message_display_service.display_system_message.call_args_list
        ]
        self.assertFalse(any("output limit" in n for n in notices), notices)

    def test_voice_ignore_period_is_not_drawn_on_screen(self):
        self.processor.active_voice = {"epoch": "e", "reply_id": "r"}
        self.renderer.pipe_mode = False
        self.config.get = lambda key, default=None: 0 if key.endswith("delay") else default
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = ""
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.api_service.has_pending_tool_calls = MagicMock(return_value=False)
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.response_parser.parse_response.return_value = {
            "content": ".",
            "components": {},
            "turn_completed": True,
        }
        self.response_parser.get_all_tools.return_value = []
        self.conversation_logger.log_assistant_message = AsyncMock(return_value="a")
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=(".", False, False)
        )
        self.message_display_service.display_complete_response = MagicMock()

        self.loop.run_until_complete(self.processor._execute_llm_turn_inner(True, "parent"))

        self.message_display_service.display_complete_response.assert_not_called()

    def test_voice_update_is_queued_before_tools_and_final_reply_uses_same_identity(self):
        order = []
        voice = {"epoch": "e", "reply_id": "request"}
        self.processor.active_voice = voice
        self.renderer.pipe_mode = False
        self.config.get = lambda key, default=None: 0 if key.endswith("delay") else default
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = "private reasoning"
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.api_service.has_pending_tool_calls = MagicMock(return_value=False)
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 1
        self.tool_executor.format_result_for_conversation.return_value = (
            "private tool output"
        )

        async def execute(_):
            order.append("tools")
            return ToolExecutionResult(
                tool_id="read",
                tool_type="terminal",
                success=True,
                output="private tool output",
            )

        async def emit(event, data, source):
            if event == "voice_response_ready":
                order.append((data["spoken_text"], data["voice"]))
                self.assertNotIn("private tool output", data["spoken_text"])
            return {}

        self.tool_executor.execute_tool = AsyncMock(side_effect=execute)
        self.event_bus.emit_with_hooks = AsyncMock(side_effect=emit)
        self.response_parser.parse_response.return_value = {
            "content": "I'll recheck the voice mode files.",
            "spoken_text": "I'll recheck the voice mode files.",
            "components": {},
            "turn_completed": False,
        }
        self.response_parser.get_all_tools.return_value = [
            {"id": "read", "type": "terminal", "command": "true"}
        ]
        self.conversation_logger.log_assistant_message = AsyncMock(return_value="assistant")
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("I'll recheck the voice mode files.", False, False)
        )
        self.loop.run_until_complete(self.processor._execute_llm_turn_inner(True, "parent"))
        self.assertEqual(order, [("I'll recheck the voice mode files.", voice), "tools"])
        self.assertEqual(self.processor.active_voice, voice)

        self.response_parser.parse_response.return_value["turn_completed"] = True
        self.response_parser.parse_response.return_value["spoken_text"] = "The check is finished."
        self.response_parser.get_all_tools.return_value = []
        self.processor._emit_llm_response_and_handle.return_value = (
            "The check is finished.",
            False,
            False,
        )
        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(False, "parent")
        )
        self.assertEqual(order[-1], ("The check is finished.", voice))
        self.assertIsNone(self.processor.active_voice)

    def test_question_tag_does_not_suspend_xml_tools(self):
        """A <question> tag is plain text: the XML tool in the same reply runs."""
        from kollabor_ai.response_parser import ResponseParser

        parser = ResponseParser()
        parsed = parser.parse_response(
            "<question>Which file should I inspect?</question>\n"
            "<terminal>printf ''</terminal>"
        )
        self.assertFalse(parsed["turn_completed"])
        self.assertNotIn("question", parsed["components"])

        self.native_tools_handler.tool_calling_enabled = False
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_token_usage = MagicMock(return_value=None)
        self.api_service.last_thinking_content = None
        self.api_service.last_stop_reason = ""
        self.api_service.model = "test-model"
        self.api_service.provider_type = "test"
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 1
        self.tool_executor.format_result_for_conversation.return_value = "ok"
        self.tool_executor.execute_tool = AsyncMock(
            return_value=ToolExecutionResult(
                tool_id="terminal_1",
                tool_type="terminal",
                success=True,
                output="",
            )
        )
        self.response_parser.parse_response.return_value = parsed
        self.response_parser.get_all_tools.return_value = parser.get_all_tools(parsed)
        self.conversation_logger.log_assistant_message = AsyncMock(
            return_value="assistant-uuid"
        )
        self.conversation_logger.log_system_message = AsyncMock()
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(
            return_value=("Which file should I inspect?", False, False)
        )

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True,
                current_parent_uuid="parent-uuid",
            )
        )

        self.tool_executor.execute_tool.assert_awaited_once()
        self.message_display_service.display_tool_results.assert_called_once()

    def test_repeated_tool_error_breaker_trips_on_third_identical_signature(self):
        from kollabor_agent.queue_processor import RepeatedToolErrorBreaker

        breaker = RepeatedToolErrorBreaker()
        self.assertFalse(breaker.observe("terminal:boom"))
        self.assertFalse(breaker.observe("terminal:boom"))
        self.assertTrue(breaker.observe("terminal:boom"))
        self.assertEqual(breaker.count, 3)

    def test_repeated_tool_error_breaker_resets_on_change_or_success(self):
        from kollabor_agent.queue_processor import RepeatedToolErrorBreaker

        breaker = RepeatedToolErrorBreaker()
        for sig in ("a", "a", "b", "b", None, "b", "b"):
            self.assertFalse(breaker.observe(sig))
        self.assertTrue(breaker.observe("b"))

    def test_tool_history_budget_defaults_to_measured_overhead(self):
        """Default overhead is 48K tokens (measured 2026-10-02), not the old 60K guess."""
        self.api_service._provider = MagicMock()
        self.api_service._provider.config = {
            "context_window": 128000,
            "max_tokens": 16384,
        }
        self.processor.config = {}
        self.processor._tool_output_batch_override = 0
        self.processor._tool_output_max_chars = 10**9
        limit = self.processor._tool_history_limit_chars(
            response="", raw_tool_calls=[], xml_tool_calls=[]
        )
        # window - max_tokens - overhead - margin - 2 one-token estimates - 512
        self.assertEqual(limit, (128000 - 16384 - 48000 - 4000 - 2 - 512) * 3)

    def test_api_format_mismatch_message_names_real_profile_settings(self):
        from kollabor_agent import queue_processor

        text = queue_processor._API_FORMAT_MISMATCH_MESSAGE
        self.assertNotIn("tool_format", text)
        self.assertIn("provider", text)
        self.assertIn("base_url", text)

    # ------------------------------------------------------------------
    # Tests for _emit_llm_response_and_handle
    # ------------------------------------------------------------------

    def test_emit_llm_response_no_event_bus(self):
        """Returns (clean_response, False, False) when event_bus is None."""
        self.processor.event_bus = None
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="hello",
                clean_response="hello",
                thinking_duration=0.5,
            )
        )
        self.assertEqual(result, ("hello", False, False))

    def test_emit_llm_response_basic(self):
        """Emits LLM_RESPONSE event and returns clean response when no modifications."""
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="some text",
                clean_response="some text",
                thinking_duration=1.2,
            )
        )
        self.assertEqual(result, ("some text", False, False))
        self.event_bus.emit_with_hooks.assert_called_once()

    def test_emit_llm_response_turn_complete_from_hook(self):
        """Sets turn_complete=True when a hook sets it in final_data."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "pre": {"final_data": {"turn_complete": True}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="x", clean_response="x", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("x", False, True))

    def test_emit_llm_response_suppress_display_from_hook(self):
        """Sets suppress_display=True when a hook sets it in final_data."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "main": {"final_data": {"suppress_display": True}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="y", clean_response="y", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("y", True, False))

    def test_emit_llm_response_clean_response_modified_by_hook(self):
        """Hook can replace clean_response via final_data."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "post": {"final_data": {"clean_response": "HOOKED"}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="original", clean_response="original", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("HOOKED", False, False))

    def test_emit_llm_response_multiple_phases_last_wins(self):
        """Multiple phases can modify clean_response; last one wins."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "pre": {"final_data": {"clean_response": "FIRST"}},
                "main": {"final_data": {"clean_response": "SECOND"}},
                "post": {"final_data": {"clean_response": "THIRD"}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="orig", clean_response="orig", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("THIRD", False, False))

    def test_emit_llm_response_turn_complete_and_suppress_combined(self):
        """Both flags can be set simultaneously, from different phases."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "pre": {"final_data": {"turn_complete": True}},
                "main": {"final_data": {"suppress_display": True}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="x", clean_response="x", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("x", True, True))

    def test_emit_llm_response_hub_tags_in_original_strips_logging(self):
        """When response_text contains hub tags, hook modifications are logged."""
        self.event_bus.emit_with_hooks = AsyncMock(
            return_value={
                "post": {"final_data": {"clean_response": "cleaned"}},
            }
        )
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text='<hub_msg to="x">hi</hub_msg>',
                clean_response='<hub_msg to="x">hi</hub_msg>',
                thinking_duration=0.0,
            )
        )
        # Should still return modified clean_response
        self.assertEqual(result, ("cleaned", False, False))

    def test_emit_llm_response_empty_response(self):
        """Handles empty/None response_text gracefully."""
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="", clean_response="", thinking_duration=0.0
            )
        )
        self.assertEqual(result, ("", False, False))

    def test_emit_llm_response_with_log_prefix(self):
        """Log prefix parameter doesn't affect return value."""
        self.event_bus.emit_with_hooks = AsyncMock(return_value={})
        result = self.loop.run_until_complete(
            self.processor._emit_llm_response_and_handle(
                response_text="x", clean_response="x", thinking_duration=0.0,
                log_prefix="native",
            )
        )
        self.assertEqual(result, ("x", False, False))

    def test_file_ingestion_uses_raw_hash_for_unchanged_read_detection(self):
        """Rendered read headers must not replace the disk-content hash."""
        from kollabor_ai.context_service.hash_utils import compute_hash
        from kollabor_ai.context_service.service import ContextService

        context_service = ContextService(heavy_threshold_kb=1)
        self.event_bus.get_service.return_value = context_service
        path = "/workspace/large.py"
        raw = b"x" * 9000
        result = ToolExecutionResult(
            tool_id="file_read_1",
            tool_type="file_read",
            success=True,
            output="rendered read header\n\n" + raw.decode("utf-8"),
            metadata={
                "file_path": path,
                "file_content_hash": compute_hash(raw),
            },
        )

        self.processor._ingest_tool_results([result], "message-1")

        self.assertEqual(
            context_service.file_read_hook(path, raw)["action"],
            "stale",
        )


    def test_cache_metrics_accumulate_across_truncated_continuation(self):
        """Per-turn cache metrics include continuation usage and totals add."""
        self.conversation_history.append(ConversationMessage(role="user", content="prompt"))
        self.api_service.last_stop_reason = "length"
        self.api_service.provider_type = "test"
        self.api_service.model = "test-model"
        usages = iter([
            {"prompt_tokens": 10, "completion_tokens": 4, "cache_read_tokens": 3, "cache_creation_tokens": 2},
            {"prompt_tokens": 11, "completion_tokens": 5, "cache_read_tokens": 7, "cache_creation_tokens": 6},
        ])
        def get_usage():
            usage = next(usages)
            self.api_service.last_token_usage_is_estimated = usage["prompt_tokens"] == 10
            return usage
        self.api_service.get_last_token_usage = MagicMock(side_effect=get_usage)
        async def call_llm(**kwargs):
            if self.streaming_handler.call_llm.call_count == 1:
                self.api_service.last_stop_reason = "length"
                return "partial"
            self.api_service.last_stop_reason = ""
            return "final"

        self.streaming_handler.call_llm = AsyncMock(side_effect=call_llm)
        self.response_parser.parse_response.return_value = {
            "content": "final", "components": {}, "turn_completed": True,
        }
        self.response_parser.get_all_tools.return_value = []
        self.api_service.has_pending_tool_calls.return_value = False
        self.api_service.get_last_tool_calls.return_value = []
        self.api_service.last_thinking_content = None
        self.tool_executor.is_cancelled.return_value = False
        self.tool_executor.take_executed_count.return_value = 0
        self.conversation_logger.log_assistant_message = AsyncMock(return_value="id")
        self.processor._drain_env_block = MagicMock(return_value=None)
        self.processor._emit_llm_response_and_handle = AsyncMock(return_value=("final", False, False))

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(user_message_provided=True, current_parent_uuid="parent")
        )

        self.assertEqual(self.session_stats["cache_read_tokens"], 10)
        self.assertEqual(self.session_stats["cache_creation_tokens"], 8)
        self.assertEqual(self.session_stats["total_cache_read_tokens"], 7)
        self.assertEqual(self.session_stats["total_cache_creation_tokens"], 6)

        self.assertEqual(self.session_stats["total_input_tokens"], 11)
        self.assertEqual(self.session_stats["total_output_tokens"], 5)
        # the context size is the last request's prompt, which the provider
        # reported: not the two prompts added together (#396)
        self.assertEqual(self.session_stats["input_tokens"], 11)
        self.assertFalse(self.session_stats["input_tokens_estimated"])


    # --- First-turn stall revival (#453) ---

    def _layout_bus(self, mode: str) -> None:
        """Bus whose layout_manager reports `mode`; other services absent."""
        layout = SimpleNamespace(runtime_mode=mode)
        self.event_bus.get_service = MagicMock(
            side_effect=lambda name: layout if name == "layout_manager" else None
        )

    def _queued_nudges(self):
        return [
            m
            for m in self.processor.processing_queue._queue
            if isinstance(m, QueuedInput)
        ]

    def test_first_turn_stall_signature_shapes(self):
        """The two stall shapes match; closed tags and finished replies don't."""
        from kollabor_agent.queue_processor import first_turn_stall_signature

        self.assertEqual(
            first_turn_stall_signature("checking the hub now\n<hub_capture>"),
            ("unfinished_tool_tag", "<hub_capture>"),
        )
        self.assertEqual(
            first_turn_stall_signature("running it:\n<termi"),
            ("unfinished_tool_tag", "<termi>"),
        )
        self.assertEqual(
            first_turn_stall_signature("todo:\n  [ ] read brief\n\nstarting with the brief:"),
            ("announced_no_action", ""),
        )
        self.assertIsNone(first_turn_stall_signature("<terminal>ls</terminal>\ndone"))
        self.assertIsNone(first_turn_stall_signature("all finished, report attached."))
        self.assertIsNone(first_turn_stall_signature(""))

    def test_daemon_plan_only_reply_queues_one_corrective_nudge(self):
        """A daemon zero-tool reply ending on an announced step gets one nudge."""
        self._layout_bus("daemon")
        self._reply_with("plan:\n  [ ] read brief\n\nstarting with the brief:")
        self.processor._first_turn_nudge_budget = 1

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="t",
            )
        )

        [nudge] = self._queued_nudges()
        self.assertTrue(nudge.is_system_nudge)
        self.assertIn("no tool call", nudge.content)
        self.assertEqual(self.processor._first_turn_nudge_budget, 0)

    def test_daemon_unclosed_tag_reply_also_nudges(self):
        """A daemon reply ending in a bare unclosed tool tag gets one nudge."""
        self._layout_bus("daemon")
        self._reply_with("checking the agent:\n<hub_capture>")
        self.processor._first_turn_nudge_budget = 1

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="t",
            )
        )

        [nudge] = self._queued_nudges()
        self.assertTrue(nudge.is_system_nudge)
        self.assertIn("<hub_capture>", nudge.content)

    def test_interactive_session_never_nudges(self):
        """A watched session gets no nudge: a human can push the agent onward."""
        self._layout_bus("local")
        self._reply_with("plan:\n\nstarting now:")
        self.processor._first_turn_nudge_budget = 1

        self.loop.run_until_complete(
            self.processor._execute_llm_turn_inner(
                user_message_provided=True, current_parent_uuid="t",
            )
        )

        self.assertEqual(self._queued_nudges(), [])

    def test_a_nudge_batch_never_rearms_but_real_input_does(self):
        """One nudge per real inbound batch: a nudge batch stays disarmed."""
        from kollabor_agent.queue_processor import QueuedInput as QI

        self._layout_bus("daemon")
        self._reply_with("plan:\n\nstarting now:")

        async def drive(messages):
            self.processor.processing_queue._queue.clear()
            self.processor._first_turn_nudge_budget = 0
            await self.processor.process_message_batch(messages, "t")

        self.loop.run_until_complete(
            drive([QI("take that step now", {}, is_system_nudge=True)])
        )
        self.assertEqual(self._queued_nudges(), [])
        self.assertEqual(self.processor._first_turn_nudge_budget, 0)

        self.loop.run_until_complete(drive([QI("real work", {})]))
        [nudge] = self._queued_nudges()
        self.assertTrue(nudge.is_system_nudge)
        self.assertEqual(self.processor._first_turn_nudge_budget, 0)



class TestQueueProcessorToolContinuation(unittest.TestCase):
    def test_state_update_requires_followup(self):
        """Every executed tool result is fed back before the turn can end."""
        results = [
            ToolExecutionResult(
                tool_id="state_update_1",
                tool_type="state_update",
                success=True,
                output="saved: ['state']",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), results)

    def test_hub_message_requires_followup(self):
        """Hub sends are tool results and should be visible to the model."""
        results = [
            ToolExecutionResult(
                tool_id="hub_msg_1",
                tool_type="hub_msg",
                success=True,
                output="delivered to koordinator",
            ),
        ]

        self.assertEqual(_tool_results_requiring_followup(results), results)

    def test_failed_state_update_requires_followup(self):
        """State-save failures should still be shown to the model."""
        failed_state = ToolExecutionResult(
            tool_id="state_update_1",
            tool_type="state_update",
            success=False,
            error="vault not initialized",
        )
        results = [failed_state]

        self.assertEqual(_tool_results_requiring_followup(results), [failed_state])

    def test_real_tool_requires_followup(self):
        """Task-producing tool results require a follow-up turn."""
        read_result = ToolExecutionResult(
            tool_id="file_read_1",
            tool_type="file_read",
            success=True,
            output="file contents",
        )
        results = [read_result]

        self.assertEqual(_tool_results_requiring_followup(results), [read_result])

    def test_no_tool_results_needs_no_followup(self):
        """Natural stop condition: no tool calls means no continuation."""
        self.assertEqual(_tool_results_requiring_followup([]), [])

    def test_end_turn_result_releases_the_whole_batch(self):
        """hub_msg wait="true" is "send, then stop": nothing needs another turn."""
        wait_send = ToolExecutionResult(
            tool_id="hub_msg_1",
            tool_type="hub_msg",
            success=True,
            output="delivered to lapis",
            metadata={"end_turn": True},
        )
        read_result = ToolExecutionResult(
            tool_id="file_read_1",
            tool_type="file_read",
            success=True,
            output="file contents",
        )

        self.assertEqual(_tool_results_requiring_followup([wait_send]), [])
        self.assertEqual(
            _tool_results_requiring_followup([read_result, wait_send]), []
        )

    def test_end_turn_never_hides_a_failure_from_the_model(self):
        failed_send = ToolExecutionResult(
            tool_id="hub_msg_1",
            tool_type="hub_msg",
            success=False,
            error="rejected: cooldown",
            metadata={"end_turn": True},
        )
        wait_send = ToolExecutionResult(
            tool_id="hub_msg_2",
            tool_type="hub_msg",
            success=True,
            output="delivered to lapis",
            metadata={"end_turn": True},
        )
        failed_read = ToolExecutionResult(
            tool_id="file_read_1",
            tool_type="file_read",
            success=False,
            error="no such file",
        )

        self.assertEqual(_tool_results_requiring_followup([failed_send]), [failed_send])
        self.assertEqual(
            _tool_results_requiring_followup([wait_send, failed_read]),
            [wait_send, failed_read],
        )

    def test_only_a_real_true_ends_the_turn(self):
        """Mock or string metadata must not stop a chain by accident."""
        lookalike = ToolExecutionResult(
            tool_id="x_1",
            tool_type="x",
            success=True,
            metadata={"end_turn": "true", "wait": True},
        )

        self.assertEqual(_tool_results_requiring_followup([lookalike]), [lookalike])


if __name__ == "__main__":
    unittest.main()


class TestToolErrorSignature452(unittest.TestCase):
    """Only the same call failing the same way repeats a signature (#452)."""

    ERROR = "Command exited with code 1"

    def _failed(self, tool_id: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_id=tool_id,
            tool_type="terminal",
            success=False,
            error=self.ERROR,
        )

    def test_different_inputs_never_trip_breaker(self):
        """Three different failing searches are not the same failing call."""
        from kollabor_agent.queue_processor import (
            RepeatedToolErrorBreaker,
            tool_error_signature,
        )

        inputs_by_id = {
            "t1": {"command": "rg foo src/"},
            "t2": {"command": "rg bar docs/"},
            "t3": {"command": "rg baz tests/"},
        }
        sigs = [
            tool_error_signature([self._failed(tid)], inputs_by_id)
            for tid in ("t1", "t2", "t3")
        ]
        self.assertTrue(all(sigs))
        self.assertEqual(len(set(sigs)), 3)

        breaker = RepeatedToolErrorBreaker()
        for sig in sigs:
            self.assertFalse(breaker.observe(sig))

    def test_same_input_trips_breaker_on_third(self):
        """The same input failing the same way is a stuck loop."""
        from kollabor_agent.queue_processor import (
            RepeatedToolErrorBreaker,
            tool_error_signature,
        )

        inputs_by_id = {"t1": {"command": "rg missing src/"}}
        sig = tool_error_signature([self._failed("t1")], inputs_by_id)
        self.assertIsNotNone(sig)

        breaker = RepeatedToolErrorBreaker()
        self.assertFalse(breaker.observe(sig))
        self.assertFalse(breaker.observe(sig))
        self.assertTrue(breaker.observe(sig))

    def test_empty_failed_returns_none(self):
        """No failures means no signature."""
        from kollabor_agent.queue_processor import tool_error_signature

        self.assertIsNone(tool_error_signature([], {}))
