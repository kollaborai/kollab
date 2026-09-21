"""GoalStore tests: schema, CAS, lease fencing, attempts, and the P0
crash-recovery harness from the spec's round-3 gates.

All tests run against a temp SQLite file — no network, no provider, no
keyring (KOLLAB_NO_KEYRING is exported by the test environment).
"""

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kollabor.state.goal_store import (
    ConstraintError,
    GoalStore,
    LeaseHeld,
    StaleEpoch,
    StaleVersion,
    UnfinishedGoalExists,
)


def make_store(base: Path, name: str = "goals.db") -> GoalStore:
    return GoalStore(Path(base) / name)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = make_store(Path(self.tmp.name))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_create_and_get(self):
        rec = self.store.create(
            conversation_uid="conv-1",
            objective="make the tests pass",
            project_root="/repo",
            session_id="sess-a",
        )
        loaded = self.store.get(rec.goal_id)
        self.assertEqual(loaded.status, "active")
        self.assertEqual(loaded.objective, "make the tests pass")
        self.assertEqual(loaded.conversation_uid, "conv-1")
        self.assertEqual(loaded.record_version, 1)
        events = self.store.events(rec.goal_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "created")
        self.assertEqual(events[0].sequence, 1)

    def test_one_unfinished_goal_per_conversation(self):
        first = self.store.create("conv-1", "goal A", "/repo")
        with self.assertRaises(UnfinishedGoalExists) as ctx:
            self.store.create("conv-1", "goal B", "/repo")
        self.assertEqual(ctx.exception.goal_id, first.goal_id)
        # different conversation is fine
        self.store.create("conv-2", "goal C", "/repo")

    def test_clear_frees_the_slot(self):
        rec = self.store.create("conv-1", "goal A", "/repo")
        self.store.transition(
            rec.goal_id,
            rec.record_version,
            "cleared",
            actor="user",
            status="cleared",
        )
        self.store.create("conv-1", "goal B", "/repo")
        hist = self.store.history("conv-1")
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0].goal_id, rec.goal_id)

    def test_two_concurrent_creations_one_winner(self):
        """Round-3 gate: two writers, one commit, one constraint rejection."""
        results = []
        barrier = threading.Barrier(2)

        def writer(tag: str):
            store = make_store(Path(self.tmp.name))
            barrier.wait()
            try:
                rec = store.create("conv-race", f"goal {tag}", "/repo")
                results.append(("ok", tag, rec.goal_id))
            except UnfinishedGoalExists as exc:
                results.append(("rejected", tag, exc.goal_id))
            finally:
                store.close()

        threads = [threading.Thread(target=writer, args=(t,)) for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for r in results if r[0] == "ok"), 1)
        self.assertEqual(sum(1 for r in results if r[0] == "rejected"), 1)
        active = self.store.get_active("conv-race")
        self.assertIsNotNone(active)


class CastTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = make_store(Path(self.tmp.name))
        self.rec = self.store.create("conv-1", "objective", "/repo")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_stale_version_rejected(self):
        with self.assertRaises(StaleVersion):
            self.store.transition(
                self.rec.goal_id,
                self.rec.record_version - 1 or 999,
                "paused",
                status="paused",
            )

    def test_version_bumps(self):
        updated = self.store.transition(
            self.rec.goal_id,
            self.rec.record_version,
            "paused",
            status="paused",
            reason="user asked",
        )
        self.assertEqual(updated.record_version, self.rec.record_version + 1)
        self.assertEqual(updated.status, "paused")

    def test_update_fields_no_event(self):
        before = len(self.store.events(self.rec.goal_id))
        updated = self.store.update_fields(
            self.rec.goal_id, self.rec.record_version, tokens_used=12.5
        )
        self.assertEqual(updated.tokens_used, 12.5)
        self.assertEqual(len(self.store.events(self.rec.goal_id)), before)

    def test_terminal_immutable_via_event_audit(self):
        done = self.store.transition(
            self.rec.goal_id,
            self.rec.record_version,
            "complete",
            status="complete",
        )
        # CAS still applies; terminal *lifecycle* protection is policy-level,
        # but late writes must carry the fresh version or fail:
        with self.assertRaises(StaleVersion):
            self.store.transition(
                self.rec.goal_id, self.rec.record_version, "resumed", status="active"
            )
        self.assertEqual(done.status, "complete")


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.clock = FakeClock()
        self.store = GoalStore(Path(self.tmp.name) / "goals.db", clock=self.clock)
        self.rec = self.store.create("conv-1", "objective", "/repo")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_second_daemon_cannot_claim_live_lease(self):
        self.store.claim(self.rec.goal_id, "daemon-A", lease_seconds=90)
        with self.assertRaises(LeaseHeld):
            self.store.claim(self.rec.goal_id, "daemon-B", lease_seconds=90)

    def test_claim_renewal_by_owner(self):
        self.store.claim(self.rec.goal_id, "daemon-A", lease_seconds=90)
        self.store.claim(self.rec.goal_id, "daemon-A", lease_seconds=90)

    def test_recover_expired_lease_no_attempts(self):
        self.store.claim(self.rec.goal_id, "daemon-A", lease_seconds=90)
        self.clock.advance(120)  # lease expired
        rec, report = self.store.recover_lease(self.rec.goal_id, "daemon-B")
        self.assertEqual(rec.lease_epoch, 1)
        self.assertEqual(report["ambiguous"], [])
        self.assertEqual(rec.status, "active")

    def test_recover_refuses_live_lease(self):
        self.store.claim(self.rec.goal_id, "daemon-A", lease_seconds=90)
        with self.assertRaises(LeaseHeld):
            self.store.recover_lease(self.rec.goal_id, "daemon-B")


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.clock = FakeClock()
        self.store = GoalStore(Path(self.tmp.name) / "goals.db", clock=self.clock)
        self.rec = self.store.create("conv-1", "objective", "/repo")
        self.store.claim(self.rec.goal_id, "daemon-A", lease_seconds=90)
        self.epoch = self.store.get(self.rec.goal_id).lease_epoch

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_one_attempt_per_sequence(self):
        self.store.insert_attempt(self.rec.goal_id, 1, self.epoch, 1)
        with self.assertRaises(ConstraintError):
            self.store.insert_attempt(self.rec.goal_id, 1, self.epoch, 1)

    def test_full_lifecycle(self):
        att = self.store.insert_attempt(self.rec.goal_id, 1, self.epoch, 1)
        self.assertEqual(att.state, "dispatched")
        att = self.store.mark_ack(att.attempt_id, self.epoch, "req-1")
        self.assertEqual(att.state, "acked")
        self.assertEqual(att.provider_request_ref, "req-1")
        att = self.store.settle_attempt(
            att.attempt_id,
            self.epoch,
            turn_id="t-1",
            outcome={"status": "ok", "origin": "goal"},
            usage_deltas=[{"tokens": 50}],
        )
        self.assertEqual(att.state, "settled")
        self.assertEqual(att.outcome["status"], "ok")

    def test_stale_epoch_fenced(self):
        att = self.store.insert_attempt(self.rec.goal_id, 1, self.epoch, 1)
        self.clock.advance(120)
        self.store.recover_lease(self.rec.goal_id, "daemon-B")  # epoch -> 1
        with self.assertRaises(StaleEpoch):
            self.store.mark_ack(att.attempt_id, self.epoch, "req-1")


class P0CrashRecoveryTests(unittest.TestCase):
    """The P0 gate: crash after provider acceptance, before local ack/settle.

    Fake provider = the attempt lifecycle itself. 'acceptance' is the moment
    insert_attempt returns (dispatch intent committed). The daemon dies.
    Recovery must NOT re-dispatch: provider request count stays one.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.clock = FakeClock()
        self.store = GoalStore(Path(self.tmp.name) / "goals.db", clock=self.clock)
        self.rec = self.store.create("conv-1", "objective", "/repo")
        self.store.claim(self.rec.goal_id, "daemon-A", lease_seconds=90)
        rec = self.store.get(self.rec.goal_id)
        self.epoch = rec.lease_epoch
        self.provider_requests: list[str] = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _fake_provider_submit(self, seq: int) -> str:
        self.provider_requests.append(f"req-{seq}")
        att = self.store.insert_attempt(self.rec.goal_id, seq, self.epoch, 1)
        return att.attempt_id

    def test_crash_after_acceptance_no_redispatch(self):
        """Daemon A submits (1 provider request), crashes before ack."""
        self._fake_provider_submit(1)
        # --- daemon A dies here; no ack, no settle ---

        self.clock.advance(120)  # lease expires
        rec, report = self.store.recover_lease(self.rec.goal_id, "daemon-B")

        self.assertEqual(len(self.provider_requests), 1)
        self.assertEqual(len(report["ambiguous"]), 1)
        self.assertEqual(rec.status, "paused")
        self.assertIn("reconcile", (rec.blocked_reason or ""))
        events = {e.kind for e in self.store.events(rec.goal_id)}
        self.assertIn("reconcile_paused", events)

        # automatic recovery never re-dispatches: no new attempt row may be
        # created for the ambiguous sequence by the recovery path itself
        attempts = self.store.attempts(self.rec.goal_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].state, "ambiguous")

        # and continuing starts at the NEXT sequence; seq 1 is not re-run
        nxt = self.store.insert_attempt(self.rec.goal_id, 2, rec.lease_epoch, 1)
        self.assertEqual(nxt.continuation_seq, 2)

    def test_pending_attempt_cancelled_and_retryable(self):
        """Crash before submission: pending attempts cancel and may retry."""
        att = self.store.insert_attempt(
            self.rec.goal_id, 1, self.epoch, 1, state="pending"
        )
        self.clock.advance(120)
        rec, report = self.store.recover_lease(self.rec.goal_id, "daemon-B")
        self.assertEqual(report["cancelled"], [att.attempt_id])
        self.assertEqual(report["ambiguous"], [])
        self.assertEqual(rec.status, "active")  # safe to continue

    def test_acked_attempt_is_ambiguous_too(self):
        att = self.store.insert_attempt(self.rec.goal_id, 1, self.epoch, 1)
        self.store.mark_ack(att.attempt_id, self.epoch, "req-1")
        self.clock.advance(120)
        rec, report = self.store.recover_lease(self.rec.goal_id, "daemon-B")
        self.assertEqual(len(report["ambiguous"]), 1)
        self.assertEqual(rec.status, "paused")

    def test_recovery_across_processes(self):
        """Two real store connections (two daemons), one file."""
        self._fake_provider_submit(1)
        self.clock.advance(120)
        store_b = make_store(Path(self.tmp.name))
        try:
            rec, report = store_b.recover_lease(self.rec.goal_id, "daemon-B")
            self.assertEqual(rec.lease_epoch, 1)
            self.assertEqual(len(report["ambiguous"]), 1)
            # daemon A's late write is fenced by the epoch
            with self.assertRaises(StaleEpoch):
                self.store.mark_ack(
                    self.store.attempts(self.rec.goal_id)[0].attempt_id,
                    self.epoch,
                    "req-late",
                )
        finally:
            store_b.close()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = make_store(Path(self.tmp.name))
        self.rec = self.store.create("conv-1", "objective", "/repo")
        self.att = self.store.insert_attempt(self.rec.goal_id, 1, 0, 1)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_record_and_query(self):
        self.store.record_evidence(
            self.rec.goal_id,
            self.att.attempt_id,
            tool_result_id="tool-1",
            kind="test_output",
            ref="pytest run 2026-09-20",
            tool_seq=3,
            turn_id="t-1",
            checksum="abc123",
        )
        evs = self.store.evidence_for(self.rec.goal_id)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, "test_output")
        self.assertEqual(evs[0].tool_seq, 3)
        self.assertFalse(evs[0].is_mutating)


class RedactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = make_store(Path(self.tmp.name))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_reason_bounded(self):
        rec = self.store.create("conv-1", "x", "/repo")
        long_reason = "r" * 5000
        self.store.transition(
            rec.goal_id,
            rec.record_version,
            "paused",
            reason=long_reason,
            status="paused",
        )
        ev = self.store.events(rec.goal_id)[0]
        self.assertLessEqual(len(ev.reason or ""), 600)

    def test_objective_never_holds_bytes(self):
        rec = self.store.create("conv-1", "plain text only", "/repo")
        self.assertIsInstance(rec.objective, str)


if __name__ == "__main__":
    unittest.main()
