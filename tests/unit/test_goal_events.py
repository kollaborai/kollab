"""Goal attached-client event tests (spec 10.2 round-3 gate).

Proves the full delivery path: GoalService transition -> state publisher
-> publish_semantic -> DisplayTap -> subscriber queue, plus the create /
pause / resume lifecycle coverage. No polling, no Hub inference.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

from kollabor.goals.service import GoalService
from kollabor.state.goal_store import GoalStore
from kollabor_tui.display_tap import DisplayTap, publish_semantic


def make_tap_bus() -> tuple[DisplayTap, Any]:
    """An event-bus-like source whose get_service resolves the tap."""
    tap = DisplayTap()
    bus = SimpleNamespace(
        get_service=lambda name: tap if name == "display_tap" else None
    )
    return tap, bus


class GoalStateEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = GoalStore(Path(self.tmp.name) / "goals.db")
        self.service = GoalService(self.store, daemon_id="daemon-A")
        self.tap, self.bus = make_tap_bus()

        def publisher(**fields: Any) -> None:
            publish_semantic(self.bus, "goal.state_changed", **fields)

        self.service.set_state_publisher(publisher)
        self.queue = self.tap.subscribe("test-goal-client")
        self.events: list[dict] = []

    def tearDown(self):
        self.tap.unsubscribe("test-goal-client")
        self.store.close()
        self.tmp.cleanup()

    def _goal_events(self) -> list[dict]:
        import queue as _queue

        while True:
            try:
                self.events.append(self.queue.get_nowait())
            except _queue.Empty:
                break
        return [e for e in self.events if e.get("type") == "goal.state_changed"]

    def test_string_source_resolves_nothing(self):
        # regression: the literal "goal_service" source never reached the
        # tap (resolve_tap needs a bus/renderer/tap)
        publish_semantic("goal_service", "goal.state_changed", goal_id="x")
        self.assertEqual(self._goal_events(), [])

    def test_create_pause_resume_events_delivered(self):
        rec = self.service.create_goal("conv-1", "objective", "/repo")
        self.service.pause(rec.goal_id)
        self.service.resume(rec.goal_id)

        delivered = self._goal_events()
        kinds = [e["kind"] for e in delivered]
        self.assertIn("created", kinds)
        self.assertIn("paused", kinds)
        self.assertIn("generation_bumped", kinds)
        self.assertIn("resumed", kinds)

        created = next(e for e in delivered if e["kind"] == "created")
        self.assertEqual(created["goal_id"], rec.short_id)
        self.assertEqual(created["status"], "active")
        self.assertEqual(created["conversation_uid"], "conv-1")
        self.assertEqual(created["objective"], "objective")

        paused = next(e for e in delivered if e["kind"] == "paused")
        self.assertEqual(paused["status"], "paused")

    def test_terminal_events_delivered(self):
        rec = self.service.create_goal("conv-1", "objective", "/repo")
        self.service.clear(rec.goal_id)
        kinds = [e["kind"] for e in self._goal_events()]
        self.assertIn("cleared", kinds)
        cleared = next(e for e in self._goal_events() if e["kind"] == "cleared")
        self.assertEqual(cleared["status"], "cleared")

    def test_publisher_failure_never_raises(self):
        def boom(**fields: Any) -> None:
            raise RuntimeError("tap broke")

        self.service.set_state_publisher(boom)
        rec = self.service.create_goal("conv-2", "survives", "/repo")
        self.assertEqual(self.store.get(rec.goal_id).status, "active")


if __name__ == "__main__":
    unittest.main()
