"""Unit tests for hub XML tool modules.

Tests: change_feed, scratchpad, session_state
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from plugins.hub.change_feed import ChangeFeed
from plugins.hub.scratchpad import SCRATCHPAD_MAX, Scratchpad
from plugins.hub.session_state import SessionState, SessionStateManager


class TestScratchpad(unittest.TestCase):
    """Tests for Scratchpad module."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.pad = Scratchpad(self.tmpdir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_and_get(self):
        self.pad.write("hello world")
        self.assertEqual(self.pad.get(), "hello world")

    def test_append_to_empty(self):
        self.pad.append("line 1")
        self.assertEqual(self.pad.get(), "line 1")

    def test_append_adds_lines(self):
        self.pad.write("line 1")
        self.pad.append("line 2")
        self.pad.append("line 3")
        content = self.pad.get()
        self.assertIn("line 1", content)
        self.assertIn("line 2", content)
        self.assertIn("line 3", content)

    def test_clear_wipes_content(self):
        self.pad.write("important notes")
        self.pad.clear()
        self.assertEqual(self.pad.get(), "")

    def test_get_when_empty(self):
        self.assertEqual(self.pad.get(), "")

    def test_max_chars_truncation(self):
        long_content = "x" * (SCRATCHPAD_MAX + 500)
        self.pad.write(long_content)
        result = self.pad.get()
        self.assertEqual(len(result), SCRATCHPAD_MAX)

    def test_append_truncates_oldest(self):
        # fill to near max
        self.pad.write("A" * (SCRATCHPAD_MAX - 10))
        # append something that pushes over
        self.pad.append("B" * 100)
        result = self.pad.get()
        self.assertEqual(len(result), SCRATCHPAD_MAX)
        # oldest should be gone, newest should be at end
        self.assertTrue(result.endswith("B" * 100))

    def test_overwrite_replaces_content(self):
        self.pad.write("old content")
        self.pad.write("new content")
        self.assertEqual(self.pad.get(), "new content")

    def test_get_path(self):
        self.assertEqual(self.pad.get_path(), self.tmpdir / "scratchpad.md")

    def test_max_chars_static(self):
        self.assertEqual(Scratchpad.max_chars(), 4000)


class TestChangeFeed(unittest.TestCase):
    """Tests for ChangeFeed module."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.feed = ChangeFeed(hub_dir=self.tmpdir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_claim_file(self):
        result = self.feed.claim("lapis", "core/app.py")
        self.assertEqual(result["status"], "claimed")
        self.assertEqual(result["identity"], "lapis")
        self.assertEqual(result["path"], "core/app.py")

    def test_claim_conflict(self):
        self.feed.claim("lapis", "core/app.py")
        result = self.feed.claim("sapphire", "core/app.py")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["claimed_by"], "lapis")

    def test_claim_same_identity_reclaims(self):
        self.feed.claim("lapis", "core/app.py")
        result = self.feed.claim("lapis", "core/app.py", task="refactor")
        self.assertEqual(result["status"], "claimed")

    def test_release_file(self):
        self.feed.claim("lapis", "core/app.py")
        result = self.feed.release("lapis", "core/app.py")
        self.assertEqual(result["status"], "released")

    def test_release_not_claimed(self):
        result = self.feed.release("lapis", "core/app.py")
        self.assertEqual(result["status"], "not_claimed")

    def test_release_wrong_owner(self):
        self.feed.claim("lapis", "core/app.py")
        result = self.feed.release("sapphire", "core/app.py")
        self.assertEqual(result["status"], "not_owner")

    def test_release_all(self):
        self.feed.claim("lapis", "core/app.py")
        self.feed.claim("lapis", "core/io.py")
        result = self.feed.release_all("lapis")
        self.assertEqual(result["status"], "released_all")
        self.assertEqual(len(result["paths"]), 2)

    def test_get_all_claims(self):
        self.feed.claim("lapis", "core/app.py")
        self.feed.claim("sapphire", "core/io.py")
        result = self.feed.get_claims()
        self.assertEqual(result["count"], 2)

    def test_get_claims_by_identity(self):
        self.feed.claim("lapis", "core/app.py")
        self.feed.claim("sapphire", "core/io.py")
        result = self.feed.get_claims(identity="lapis")
        self.assertEqual(result["count"], 1)
        self.assertIn("core/app.py", result["claims"])

    def test_record_change(self):
        result = self.feed.record_change("lapis", "core/app.py", "edit")
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(result["entry"]["identity"], "lapis")
        self.assertEqual(result["entry"]["path"], "core/app.py")

    def test_get_recent(self):
        self.feed.record_change("lapis", "a.py")
        self.feed.record_change("sapphire", "b.py")
        result = self.feed.get_recent(limit=10)
        self.assertEqual(result["count"], 2)

    def test_get_changes_for_file(self):
        self.feed.record_change("lapis", "a.py")
        self.feed.record_change("sapphire", "b.py")
        self.feed.record_change("lapis", "a.py")
        result = self.feed.get_changes_for_file("a.py")
        self.assertEqual(result["count"], 2)

    def test_subscribe_glob(self):
        result = self.feed.subscribe("sapphire", "plugins/hub/*")
        self.assertEqual(result["status"], "subscribed")

    def test_subscribers_get_notified(self):
        self.feed.subscribe("sapphire", "plugins/hub/*")
        result = self.feed.record_change("lapis", "plugins/hub/vault.py")
        self.assertIn("sapphire", result["notified"])

    def test_unsubscribe(self):
        self.feed.subscribe("sapphire", "plugins/hub/*")
        result = self.feed.unsubscribe("sapphire")
        self.assertEqual(result["status"], "unsubscribed_all")

    def test_get_subscriptions(self):
        self.feed.subscribe("sapphire", "plugins/hub/*")
        result = self.feed.get_subscriptions("sapphire")
        self.assertEqual(len(result), 1)
        result2 = self.feed.get_subscriptions("lapis")
        self.assertEqual(len(result2), 0)

    def test_claims_persist_to_disk(self):
        self.feed.claim("lapis", "core/app.py")
        claims_path = Path(self.tmpdir) / "lane_claims.json"
        self.assertTrue(claims_path.exists())
        data = json.loads(claims_path.read_text())
        self.assertIn("core/app.py", data)

    def test_feed_persists_to_disk(self):
        self.feed.record_change("lapis", "core/app.py")
        feed_path = Path(self.tmpdir) / "change_feed.jsonl"
        self.assertTrue(feed_path.exists())
        lines = feed_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)


class TestSessionState(unittest.TestCase):
    """Tests for SessionState module."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.manager = SessionStateManager()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load(self):
        state = SessionState(
            identity="lapis",
            open_files=["core/app.py", "core/io.py"],
            investigation_notes="found the bug",
            focus_file="core/app.py",
        )
        self.manager.save_state(self.tmpdir, state)
        loaded = self.manager.load_state(self.tmpdir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.identity, "lapis")
        self.assertEqual(loaded.open_files, ["core/app.py", "core/io.py"])
        self.assertEqual(loaded.investigation_notes, "found the bug")
        self.assertEqual(loaded.focus_file, "core/app.py")

    def test_load_when_no_state(self):
        result = self.manager.load_state(self.tmpdir)
        self.assertIsNone(result)

    def test_clear_state(self):
        state = SessionState(identity="lapis")
        self.manager.save_state(self.tmpdir, state)
        result = self.manager.clear_state(self.tmpdir)
        self.assertTrue(result)
        self.assertIsNone(self.manager.load_state(self.tmpdir))

    def test_update_state(self):
        state = SessionState(identity="lapis", open_files=["a.py"])
        self.manager.save_state(self.tmpdir, state)
        updated = self.manager.update_state(
            self.tmpdir,
            {
                "open_files": ["a.py", "b.py"],
                "focus_file": "b.py",
            },
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.open_files, ["a.py", "b.py"])
        self.assertEqual(updated.focus_file, "b.py")

    def test_saved_at_timestamp(self):
        state = SessionState(identity="lapis")
        before = time.time()
        self.manager.save_state(self.tmpdir, state)
        after = time.time()
        loaded = self.manager.load_state(self.tmpdir)
        self.assertGreaterEqual(loaded.saved_at, before)
        self.assertLessEqual(loaded.saved_at, after)
        self.assertTrue(len(loaded.saved_at_human) > 0)

    def test_to_dict_roundtrip(self):
        state = SessionState(
            identity="lapis",
            open_files=["a.py"],
            claimed_lanes=["core/app.py"],
            pending_promises=[{"to": "sapphire", "what": "review"}],
            last_command="rg bug core/",
            focus_file="a.py",
            investigation_notes="looking into it",
        )
        data = state.to_dict()
        restored = SessionState.from_dict(data)
        self.assertEqual(restored.identity, state.identity)
        self.assertEqual(restored.open_files, state.open_files)
        self.assertEqual(restored.claimed_lanes, state.claimed_lanes)
        self.assertEqual(restored.pending_promises, state.pending_promises)
        self.assertEqual(restored.last_command, state.last_command)
        self.assertEqual(restored.focus_file, state.focus_file)
        self.assertEqual(restored.investigation_notes, state.investigation_notes)

    def test_injection_prompt_format(self):
        state = SessionState(
            identity="lapis",
            open_files=["core/app.py"],
            focus_file="core/app.py",
            investigation_notes="checking render loop",
            last_command="rg render_loop core/",
        )
        prompt = state.to_injection_prompt()
        self.assertIn("--- previous session state ---", prompt)
        self.assertIn("core/app.py", prompt)
        self.assertIn("checking render loop", prompt)
        self.assertIn("rg render_loop core/", prompt)
        self.assertIn("pick up where you left off", prompt)

    def test_injection_prompt_empty_state(self):
        state = SessionState()
        prompt = state.to_injection_prompt()
        self.assertIn("--- previous session state ---", prompt)

    def test_merge_with_current(self):
        old = SessionState(
            identity="lapis",
            open_files=["a.py", "b.py"],
            investigation_notes="old notes",
            claimed_lanes=["core/app.py"],
        )
        current = SessionState(
            identity="sapphire",
            open_files=["c.py"],
            investigation_notes="new notes",
        )
        merged = old.merge_with_current(current)
        # current wins on identity
        self.assertEqual(merged.identity, "sapphire")
        # lists are unioned
        self.assertIn("a.py", merged.open_files)
        self.assertIn("c.py", merged.open_files)
        # old notes appended
        self.assertIn("new notes", merged.investigation_notes)
        self.assertIn("old notes", merged.investigation_notes)

    def test_get_injection_prompt_no_state(self):
        result = self.manager.get_injection_prompt(self.tmpdir)
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()


from plugins.hub.nudge_engine import AgentTracker, NudgeEngine


class TestNudgeEngine(unittest.TestCase):
    """Tests for NudgeEngine - context-aware tool reminders."""

    def setUp(self):
        self.engine = NudgeEngine(cooldown=0)  # no cooldown for tests

    def test_nudge_unclaimed_file_edit(self):
        self.engine.observe_response(
            identity="lapis",
            response="I edited core/app.py",
            edited_files=["core/app.py"],
        )
        nudge = self.engine.evaluate("lapis")
        self.assertIsNotNone(nudge)
        self.assertIn("lane_claim", nudge)
        self.assertIn("core/app.py", nudge)

    def test_no_nudge_after_claim(self):
        self.engine.observe_response(
            identity="lapis",
            response="editing",
            edited_files=["core/app.py"],
            claimed_files=["core/app.py"],
        )
        nudge = self.engine.evaluate("lapis")
        self.assertIsNone(nudge)

    def test_nudge_scratchpad_neglect(self):
        for i in range(5):
            self.engine.observe_response(
                identity="lapis",
                response=f"working hard turn {i}",
            )
        nudge = self.engine.evaluate("lapis")
        self.assertIsNotNone(nudge)
        self.assertIn("scratchpad", nudge)

    def test_no_nudge_when_scratchpad_used(self):
        for i in range(5):
            self.engine.observe_response(
                identity="lapis",
                response=f"working turn {i}",
                used_scratchpad=(i == 3),
            )
        nudge = self.engine.evaluate("lapis")
        # turns_since_scratchpad reset at turn 3, only 1 turn since
        self.assertIsNone(nudge)

    def test_nudge_file_watch_when_peers_active(self):
        self.engine.observe_response(
            identity="lapis",
            response="turn 1",
        )
        self.engine.observe_response(
            identity="lapis",
            response="turn 2",
        )
        self.engine.observe_response(
            identity="lapis",
            response="turn 3",
        )
        self.engine.observe_file_watches("lapis", has_watches=False)
        nudge = self.engine.evaluate("lapis", peers_online=2)
        self.assertIsNotNone(nudge)
        self.assertIn("file_watch", nudge)

    def test_no_nudge_when_has_watches(self):
        for i in range(3):
            self.engine.observe_response(
                identity="lapis",
                response=f"turn {i}",
            )
        self.engine.observe_file_watches("lapis", has_watches=True)
        nudge = self.engine.evaluate("lapis", peers_online=2)
        self.assertIsNone(nudge)

    def test_nudge_task_without_checkpoint(self):
        self.engine.observe_task_assignment("lapis", has_task=True)
        for i in range(8):
            self.engine.observe_response(
                identity="lapis",
                response=f"working turn {i}",
            )
        nudge = self.engine.evaluate("lapis")
        self.assertIsNotNone(nudge)
        self.assertIn("task_checkpoint", nudge)

    def test_no_nudge_when_empty(self):
        self.engine.observe_response(
            identity="lapis",
            response="just started",
        )
        nudge = self.engine.evaluate("lapis")
        self.assertIsNone(nudge)

    def test_tracker_counters(self):
        tracker = AgentTracker(identity="test")
        tracker.turns_since_scratchpad = 0
        tracker.turns_since_scratchpad += 1
        self.assertEqual(tracker.turns_since_scratchpad, 1)

    def test_cooldown_respected(self):
        engine = NudgeEngine(cooldown=9999)
        engine.observe_response(
            identity="lapis",
            response="edited file",
            edited_files=["core/app.py"],
        )
        nudge1 = engine.evaluate("lapis")
        self.assertIsNotNone(nudge1)
        # Second nudge should be blocked by cooldown
        nudge2 = engine.evaluate("lapis")
        self.assertIsNone(nudge2)


if __name__ == "__main__":
    unittest.main()
