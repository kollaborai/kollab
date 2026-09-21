"""GoalService tests: state machine, guarded continuation, evidence audit,
budget/bounds, and the round-3 policy gates (spec section 12).
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from kollabor.goals.service import (
    BoundaryContext,
    GoalControl,
    GoalError,
    GoalService,
    TurnOutcome,
)
from kollabor.state.goal_store import GoalStore


def outcome_ok(goal_id: str, turn: str = "t") -> TurnOutcome:
    return TurnOutcome(turn_id=turn, origin="goal", status="ok", goal_id=goal_id)


def ctx(**kw) -> BoundaryContext:
    defaults = dict(
        queue_empty=True, pending_work=False, invalidated=False, provider_healthy=True
    )
    defaults.update(kw)
    return BoundaryContext(**defaults)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = GoalStore(Path(self.tmp.name) / "goals.db")
        self.service = GoalService(self.store, daemon_id="daemon-A")
        self.rec = self.service.create_goal(
            "conv-1", "make the tests pass", "/repo", session_id="s1"
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    # -- creation ------------------------------------------------------

    def test_empty_objective_rejected(self):
        with self.assertRaises(GoalError):
            self.service.create_goal("conv-1", "   ", "/repo")

    def test_objective_length_bounded(self):
        with self.assertRaises(GoalError):
            self.service.create_goal("conv-1", "x" * 4001, "/repo")

    def test_second_unfinished_goal_rejected_with_next_actions(self):
        with self.assertRaises(GoalError) as e:
            self.service.create_goal("conv-1", "another", "/repo")
        self.assertIn("/goal clear", str(e.exception))

    # -- pause / clear intents ----------------------------------------

    def test_pause_idle_applies_immediately(self):
        rec = self.service.pause(self.rec.goal_id)
        self.assertEqual(rec.status, "paused")
        self.assertFalse(rec.pause_requested)

    def test_pause_in_flight_records_intent(self):
        att = self.store.insert_attempt(self.rec.goal_id, 1, self.rec.lease_epoch, 1)
        self.store.mark_ack(att.attempt_id, self.rec.lease_epoch, "req")
        rec = self.service.pause(self.rec.goal_id)
        self.assertEqual(rec.status, "active")
        self.assertTrue(rec.pause_requested)

    def test_pause_intent_blocks_continuation_at_boundary(self):
        # round-3 gate: pause recorded immediately, applied at boundary
        self.store.insert_attempt(self.rec.goal_id, 1, self.rec.lease_epoch, 1)
        self.service.pause(self.rec.goal_id)
        nxt = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        self.assertIsNone(nxt)
        self.assertEqual(self.store.get(self.rec.goal_id).status, "paused")

    def test_clear_terminal_and_history(self):
        self.service.clear(self.rec.goal_id)
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.status, "cleared")
        hist = self.service.history("conv-1")
        self.assertEqual(len(hist), 1)
        # slot freed
        self.service.create_goal("conv-1", "new goal", "/repo")

    # -- resume / generation ------------------------------------------

    def test_resume_bumps_generation(self):
        self.service.pause(self.rec.goal_id)
        rec = self.service.resume(self.rec.goal_id)
        self.assertEqual(rec.status, "active")
        self.assertEqual(rec.execution_generation, self.rec.execution_generation + 1)

    def test_resume_budget_must_exceed_used(self):
        self.store.update_fields(
            self.rec.goal_id, self.rec.record_version, tokens_used=100.0
        )
        self.service.pause(self.rec.goal_id)
        with self.assertRaises(GoalError):
            self.service.resume(self.rec.goal_id, token_budget=50)
        rec = self.service.resume(self.rec.goal_id, token_budget=200)
        self.assertEqual(rec.token_budget, 200)

    def test_stale_generation_completion_rejected(self):
        # round-3 gate: control from pre-pause generation rejected
        self.service.pause(self.rec.goal_id)
        self.service.resume(self.rec.goal_id)
        old_gen = 1
        attempt = MagicMock()
        attempt.execution_generation = old_gen
        control = GoalControl(
            goal_id=self.rec.goal_id,
            expected_record_version=self.store.get(self.rec.goal_id).record_version,
            kind="complete",
            reason="done",
            evidence=[{"ref": "x", "claim": "y"}],
        )
        out = self.service.handle_goal_report(control, attempt)
        self.assertFalse(out["accepted"])
        self.assertIn("generation", out["detail"])

    # -- guarded continuation -----------------------------------------

    def test_user_input_wins(self):
        nxt = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx(queue_empty=False)
        )
        self.assertIsNone(nxt)
        self.assertEqual(self.store.get(self.rec.goal_id).status, "active")

    def test_pending_work_blocks_dispatch(self):
        nxt = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx(pending_work=True)
        )
        self.assertIsNone(nxt)

    def test_cancelled_outcome_pauses(self):
        out = TurnOutcome(turn_id="t", origin="goal", status="cancelled")
        nxt = self.service.maybe_continue(self.rec.goal_id, out, ctx())
        self.assertIsNone(nxt)
        self.assertEqual(self.store.get(self.rec.goal_id).status, "paused")

    def test_error_outcome_pauses_not_restarts(self):
        out = TurnOutcome(
            turn_id="t", origin="goal", status="error", error="provider 500"
        )
        nxt = self.service.maybe_continue(self.rec.goal_id, out, ctx())
        self.assertIsNone(nxt)
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.status, "paused")
        self.assertIn("provider 500", rec.last_reason or "")

    def test_pass_dispatches_one_attempt_with_seq(self):
        nxt = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.continuation_seq, 1)
        self.assertEqual(nxt.state, "dispatched")
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.turn_count, 1)
        self.assertEqual(rec.continuation_seq, 1)

    def test_token_budget_dispatch_gate(self):
        self.store.update_fields(
            self.rec.goal_id,
            self.rec.record_version,
            tokens_used=500.0,
            token_budget=500.0,
        )
        nxt = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        self.assertIsNone(nxt)
        self.assertEqual(self.store.get(self.rec.goal_id).status, "budget_limited")

    def test_auto_turn_limit_stops_without_budget(self):
        self.store.update_fields(
            self.rec.goal_id,
            self.rec.record_version,
            turn_count=40,
            continuation_seq=40,
        )
        nxt = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        self.assertIsNone(nxt)
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.status, "budget_limited")
        self.assertIn("auto_turn_limit", rec.blocked_reason or "")

    def test_auto_time_limit_stops(self):
        self.store.update_fields(
            self.rec.goal_id, self.rec.record_version, time_used_seconds=7200.0
        )
        nxt = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        self.assertIsNone(nxt)
        self.assertIn(
            "auto_time_limit",
            self.store.get(self.rec.goal_id).blocked_reason or "",
        )

    # -- blockers ------------------------------------------------------

    def test_three_same_blockers_blocks(self):
        for _ in range(3):
            self.service.note_blocker(self.rec.goal_id, "missing_api_key")
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.status, "blocked")
        self.assertIn("missing_api_key", rec.blocked_reason or "")

    def test_alternating_blockers_block_at_six(self):
        keys = ["a", "b"] * 3
        for k in keys:
            self.service.note_blocker(self.rec.goal_id, k)
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.status, "blocked")
        self.assertIn("alternating", rec.blocked_reason or "")

    def test_two_same_then_new_evidence_resets(self):
        self.service.note_blocker(self.rec.goal_id, "x")
        self.service.note_blocker(self.rec.goal_id, "x", new_evidence=True)
        self.service.note_blocker(self.rec.goal_id, "x")
        self.service.note_blocker(self.rec.goal_id, "x")
        # only 3 consecutive same-key at the tail would block; the reset
        # in the middle broke the run
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.status, "active")

    # -- accounting ----------------------------------------------------

    def test_per_request_usage_accumulates(self):
        att = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        out = TurnOutcome(
            turn_id="t1",
            origin="goal",
            status="ok",
            request_usage=[{"tokens": 30}, {"tokens": 25}],
        )
        self.service.finish_turn(
            self.rec.goal_id, att, out, request_usage=out.request_usage
        )
        rec = self.store.get(self.rec.goal_id)
        self.assertEqual(rec.tokens_used, 55.0)
        settled = self.store.get_attempt(att.attempt_id)
        self.assertEqual(settled.state, "settled")
        self.assertEqual(len(settled.usage_deltas), 2)

    # -- goal_report / evidence audit ----------------------------------

    def _seed_evidence(self, attempt, refs, mutating_after=None):
        for i, ref in enumerate(refs):
            self.service.note_tool_result(
                self.rec.goal_id,
                attempt.attempt_id,
                tool_result_id=f"tool-{i}",
                kind="test_output",
                ref=ref,
                tool_seq=i,
                turn_id="t1",
            )
        if mutating_after:
            self.service.note_tool_result(
                self.rec.goal_id,
                attempt.attempt_id,
                tool_result_id="tool-edit",
                kind="file_edit",
                ref=mutating_after,
                tool_seq=99,
                turn_id="t1",
                is_mutating=True,
            )

    def test_complete_held_until_batch_settles(self):
        att = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        self._seed_evidence(att, ["pytest green"])
        control = GoalControl(
            goal_id=self.rec.goal_id,
            expected_record_version=self.store.get(self.rec.goal_id).record_version,
            kind="complete",
            reason="tests pass",
            evidence=[{"ref": "pytest green", "claim": "suite green"}],
        )
        out = self.service.handle_goal_report(control, att)
        self.assertTrue(out["accepted"])
        self.assertEqual(out["action"], "complete_held")
        self.assertTrue(out["declared"])  # no checker registered
        # not complete yet
        self.assertEqual(self.store.get(self.rec.goal_id).status, "active")
        done = self.service.apply_pending_completion(self.rec.goal_id)
        self.assertIsNotNone(done)
        self.assertEqual(done.status, "complete")
        self.assertTrue(done.completion_declared)

    def test_invented_evidence_ref_rejected(self):
        att = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        control = GoalControl(
            goal_id=self.rec.goal_id,
            expected_record_version=self.store.get(self.rec.goal_id).record_version,
            kind="complete",
            reason="done",
            evidence=[{"ref": "hallucinated run", "claim": "x"}],
        )
        out = self.service.handle_goal_report(control, att)
        self.assertFalse(out["accepted"])
        self.assertIn("not produced by a goal tool", out["detail"])

    def test_stale_evidence_freshness(self):
        att = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        # evidence at seq 0, then a mutating edit at seq 99 afterwards
        self._seed_evidence(att, ["pytest green"], mutating_after="edit main.py")
        control = GoalControl(
            goal_id=self.rec.goal_id,
            expected_record_version=self.store.get(self.rec.goal_id).record_version,
            kind="complete",
            reason="done",
            evidence=[{"ref": "pytest green", "claim": "suite"}],
        )
        out = self.service.handle_goal_report(control, att)
        self.assertFalse(out["accepted"])
        self.assertIn("stale evidence", out["detail"])

    def test_progress_dedup(self):
        att = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        self._seed_evidence(att, ["read file"])
        control = GoalControl(
            goal_id=self.rec.goal_id,
            expected_record_version=self.store.get(self.rec.goal_id).record_version,
            kind="progress",
            reason="reading",
            evidence=[{"ref": "read file", "claim": "inspected"}],
        )
        first = self.service.handle_goal_report(control, att)
        self.assertTrue(first["accepted"])
        self.assertEqual(first["detail"], "1 new evidence refs")
        control2 = GoalControl(
            goal_id=self.rec.goal_id,
            expected_record_version=self.store.get(self.rec.goal_id).record_version,
            kind="progress",
            reason="reading again",
            evidence=[{"ref": "read file", "claim": "inspected again"}],
        )
        second = self.service.handle_goal_report(control2, att)
        self.assertFalse(second["accepted"])
        self.assertIn("deduplicated", second["detail"])

    def test_stale_version_control_rejected(self):
        att = self.service.maybe_continue(
            self.rec.goal_id, outcome_ok(self.rec.goal_id), ctx()
        )
        control = GoalControl(
            goal_id=self.rec.goal_id,
            expected_record_version=999,
            kind="progress",
            reason="x",
            evidence=[],
        )
        out = self.service.handle_goal_report(control, att)
        self.assertFalse(out["accepted"])

    # -- steering (9.3) ------------------------------------------------

    def test_goal_context_contains_counter_budget_policy(self):
        note = self.service.render_goal_context(self.store.get(self.rec.goal_id))
        self.assertIn("[goal context]", note)
        self.assertIn("make the tests pass", note)
        self.assertIn("turn 0/40", note)
        self.assertIn("goal_report", note)
        self.assertIn("not progress", note)


if __name__ == "__main__":
    unittest.main()
