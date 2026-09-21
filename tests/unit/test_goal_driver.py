"""GoalTurnDriver wiring tests: steering injection, evidence provenance
from real tool batches, completion after batch settles, pause intents at
the boundary, and user-input priority — the round-3 gates at the seam
level (spec sections 8.3, 8.5, 9.3).
"""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from kollabor.goals.driver import GoalTurnDriver
from kollabor.goals.service import GoalControl, GoalService
from kollabor.state.goal_store import GoalStore


class FakeQueueProcessor:
    def __init__(self):
        self.processing_queue = asyncio.Queue()
        self.is_processing = False
        self.turn_completed = True
        self.cancel_processing = False
        self.on_tool_batch = None


class FakeCoordinator:
    """Just enough coordinator surface for the driver."""

    def __init__(self, turn_script=None):
        self.response_parser = SimpleNamespace(register_plugin_tag=lambda *a, **k: None)
        self.tool_executor = SimpleNamespace(
            register_plugin_handler=lambda *a, **k: None
        )
        self._queue_processor = FakeQueueProcessor()
        self.injected_messages: list[tuple[str, str]] = []
        self.continues = 0
        # each entry: list of fake tool results produced by that turn
        self.turn_script = turn_script or []

    async def inject_system_message(self, content: str, subtype: str = "") -> None:
        self.injected_messages.append((subtype, content))

    async def _continue_conversation(self) -> None:
        self.continues += 1
        if self.turn_script:
            batch = self.turn_script.pop(0)
            if self.on_tool_batch_hook and batch:
                self.on_tool_batch_hook(batch)
        self._queue_processor.turn_completed = True

    def create_background_task(self, coro, name=None):
        task = asyncio.get_running_loop().create_task(coro)
        return task


class FakeResult:
    def __init__(self, tool_type, output, tool_id="t"):
        self.tool_type = tool_type
        self.output = output
        self.tool_id = tool_id
        self.success = True


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = GoalStore(Path(self.tmp.name) / "goals.db")
        self.service = GoalService(self.store, daemon_id="daemon-A")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _make_driver(self, turn_script=None) -> tuple[GoalTurnDriver, FakeCoordinator]:
        coord = FakeCoordinator(turn_script)
        driver = GoalTurnDriver(self.service, coord)
        coord.on_tool_batch_hook = driver._on_tool_batch
        return driver, coord

    def _create(self, **kw) -> str:
        return self.service.create_goal(
            "conv-1", "make the tests pass", "/repo", **kw
        ).goal_id

    def test_steering_injected_each_turn_with_live_counter(self):
        goal_id = self._create(auto_turn_limit=1)
        driver, coord = self._make_driver()
        asyncio.run(driver.start(goal_id))

        self.assertTrue(coord.injected_messages)
        subtype, note = coord.injected_messages[0]
        self.assertEqual(subtype, "goal_context")
        self.assertIn("[goal context]", note)
        self.assertIn("turn 1/1", note)  # live counter at injection time
        # one turn dispatched, then the auto limit stopped the goal
        rec = self.store.get(goal_id)
        self.assertEqual(rec.turn_count, 1)
        self.assertEqual(rec.status, "budget_limited")
        self.assertIn("auto_turn_limit", rec.blocked_reason or "")

    def test_cancelled_turn_pauses(self):
        goal_id = self._create(auto_turn_limit=3)
        driver, coord = self._make_driver()

        async def cancel_mid_turn():
            coord._queue_processor.cancel_processing = True
            await asyncio.sleep(0)

        original = coord._continue_conversation

        async def turn_then_cancel():
            await original()
            coord._queue_processor.cancel_processing = True

        coord._continue_conversation = turn_then_cancel
        asyncio.run(driver.start(goal_id))
        self.assertEqual(self.store.get(goal_id).status, "paused")

    def test_completion_via_reported_evidence(self):
        goal_id = self._create()
        driver, coord = self._make_driver(
            turn_script=[[FakeResult("shell", "pytest: 12 passed")]]
        )

        # make turn 1 complete the goal: after tools recorded, the model's
        # goal_report arrives through the tool handler path
        original_continue = coord._continue_conversation

        async def turn_with_report():
            await original_continue()
            current = self.service.current_goal_attempt()
            if current is not None:
                record, attempt = current
                control = GoalControl(
                    goal_id=goal_id,
                    expected_record_version=record.record_version,
                    kind="complete",
                    reason="tests green",
                    evidence=[
                        {"ref": "shell:pytest: 12 passed", "claim": "suite green"}
                    ],
                )
                self.service.handle_goal_report(control, attempt)

        coord._continue_conversation = turn_with_report
        asyncio.run(driver.start(goal_id))

        rec = self.store.get(goal_id)
        self.assertEqual(rec.status, "complete")
        self.assertTrue(rec.completion_declared)  # no checker registered

    def test_invented_evidence_rejected_at_wiring_level(self):
        goal_id = self._create(auto_turn_limit=1)
        driver, coord = self._make_driver(
            turn_script=[[FakeResult("read", "file contents")]]
        )
        original_continue = coord._continue_conversation
        seen = {}

        async def turn_with_report():
            await original_continue()
            current = self.service.current_goal_attempt()
            if current is not None and "result" not in seen:
                seen["result"] = True
                record, attempt = current
                control = GoalControl(
                    goal_id=goal_id,
                    expected_record_version=record.record_version,
                    kind="complete",
                    reason="done",
                    evidence=[{"ref": "shell:i never ran this", "claim": "x"}],
                )
                out = self.service.handle_goal_report(control, attempt)
                seen["out"] = out

        coord._continue_conversation = turn_with_report
        asyncio.run(driver.start(goal_id))
        self.assertFalse(seen["out"]["accepted"])
        self.assertIn("not produced by a goal tool", seen["out"]["detail"])
        self.assertNotEqual(self.store.get(goal_id).status, "complete")

    def test_user_input_wins_no_continuation(self):
        goal_id = self._create()
        driver, coord = self._make_driver(turn_script=[[FakeResult("read", "hello")]])
        original_continue = coord._continue_conversation

        async def turn_then_user():
            await original_continue()
            # user message lands right after turn 1 settles
            await coord._queue_processor.processing_queue.put("user msg")

        coord._continue_conversation = turn_then_user
        asyncio.run(driver.start(goal_id))
        rec = self.store.get(goal_id)
        self.assertEqual(rec.status, "active")  # not stopped, just yielded
        self.assertEqual(rec.turn_count, 1)  # exactly one turn dispatched

    def test_pause_intent_applied_at_boundary(self):
        goal_id = self._create()
        driver, coord = self._make_driver(turn_script=[[FakeResult("read", "x")]])
        original_continue = coord._continue_conversation

        async def turn_then_pause():
            await original_continue()
            self.service.pause(goal_id)  # user hits /goal pause mid-flow

        coord._continue_conversation = turn_then_pause
        asyncio.run(driver.start(goal_id))
        self.assertEqual(self.store.get(goal_id).status, "paused")
        self.assertEqual(self.store.get(goal_id).turn_count, 1)

    def test_attachment_promotion_and_missing_pause(self):
        # promote → create with ref → artifact present: turn runs;
        # then delete artifact, resume → turn pauses hard
        ref = self.service.promote_image_attachment("data:image/png;base64,aGVsbG8=")
        rec = self.service.create_goal(
            "conv-2",
            "diagram goal",
            "/repo",
            attachment_refs=[ref],
            auto_turn_limit=1,
        )
        driver, coord = self._make_driver()
        asyncio.run(driver.start(rec.goal_id))
        self.assertEqual(self.store.get(rec.goal_id).status, "budget_limited")

        art = self.store.db_path.parent / "artifacts" / ref["ref"]
        art.unlink()
        self.service.resume(rec.goal_id, auto_turn_limit=5)
        coord2 = FakeCoordinator()
        driver2 = GoalTurnDriver(self.service, coord2)
        coord2.on_tool_batch_hook = driver2._on_tool_batch
        asyncio.run(driver2.start(rec.goal_id))
        final = self.store.get(rec.goal_id)
        self.assertEqual(final.status, "paused")
        self.assertIn("artifact_missing", final.blocked_reason or "")


if __name__ == "__main__":
    unittest.main()
