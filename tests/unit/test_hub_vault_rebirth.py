"""Regression tests for safe hub rebirth context."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.hub.task_ledger import TaskLedger
from plugins.hub.vault import AgentVault, sanitize_rebirth_text


class TestHubVaultRebirth(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.project_vaults = self.tmpdir / "project-vaults"
        self.global_vaults = self.tmpdir / "global-vaults"
        self.vault = self._open_vault()

    def _open_vault(self):
        """Open koordinator's vault from disk, as a fresh process would."""
        with (
            patch("plugins.hub.vault.get_vaults_dir", return_value=self.project_vaults),
            patch(
                "plugins.hub.vault.get_global_vaults_dir",
                return_value=self.global_vaults,
            ),
        ):
            return AgentVault("koordinator")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rebirth_archives_stream_instead_of_replaying_task_directives(self):
        self.vault.append_stream(
            "received",
            "[task 11764730 complete - QA needed]\n"
            "directive: Verify the task ledger after restart.",
            from_agent="aquamarine",
            to_agent="koordinator",
        )
        self.vault.append_stream(
            "sent",
            "Please send one fresh-card evidence report for 4dbb0da1.",
            from_agent="koordinator",
            to_agent="lapis",
        )
        self.vault.append_stream(
            "session_end",
            "agent koordinator shutting down",
            from_agent="koordinator",
        )
        self.vault.save_working_memory(
            "# working memory for koordinator\n"
            "known peers: aquamarine, lapis\n\n"
            "messages received from agents: 1\n"
            "  <- aquamarine: [task 528fa87d complete - QA needed]\n\n"
            "useful note: source-local runtime is required\n"
        )

        context = self.vault.get_rebirth_context()

        self.assertIn("archived hub activity", context)
        self.assertIn("current TaskLedger", context)
        self.assertIn("source-local runtime is required", context)
        self.assertNotIn("last session activity (verbatim)", context)
        self.assertNotIn("11764730", context)
        self.assertNotIn("4dbb0da1", context)
        self.assertNotIn("528fa87d", context)
        self.assertNotIn("directive:", context)

    def test_rebirth_marks_crystals_read_only_and_filters_task_cron(self):
        class FakeCrystalStore:
            def get_injection_context(self, budget):
                return (
                    "crystallized memories (2 entries):\n"
                    "[crys-001] task-cron reminder was stale\n"
                    "[crys-002] durable provider note\n"
                )

        context = self.vault.get_rebirth_context(crystal_store=FakeCrystalStore())

        self.assertIn("read-only historical knowledge", context)
        self.assertIn("durable provider note", context)
        self.assertNotIn("task-cron", context)

    def test_sanitize_rebirth_text_drops_archived_control_sections(self):
        text = (
            "messages sent to other agents: 2\n"
            "  -> aquamarine: [task reminder: 11764730] verify it\n\n"
            "known peers: aquamarine\n"
            "SESSION 53 - stale reminder persistence\n"
            "No response/action is needed unless a fresh directive arrives.\n"
            "keep this engineering note\n"
        )

        safe = sanitize_rebirth_text(text)

        self.assertIn("known peers: aquamarine", safe)
        self.assertIn("keep this engineering note", safe)
        self.assertNotIn("11764730", safe)
        self.assertNotIn("task-cron", safe)
        self.assertNotIn("stale reminder", safe)
        self.assertNotIn("fresh directive", safe)

    def test_rebirth_lists_exactly_the_active_ledger_cards_after_restart(self):
        tasks_dir = str(self.tmpdir / "tasks")
        ledger = TaskLedger(tasks_dir)
        active = ledger.create(
            "lapis",
            "koordinator",
            "Ship the docs inventory\nsecond line of detail",
            priority=3,
        )
        standby = ledger.create(
            "lapis", "koordinator", "Hold the release gate", status="standby"
        )
        in_qa = ledger.create("zircon", "koordinator", "Review the compaction fix")
        ledger.request_qa(in_qa.id, "ready")

        finished = []
        done = ledger.create("lapis", "koordinator", "Finished work")
        ledger.complete(done.id, "ok")
        finished.append(done)
        closed = ledger.create("lapis", "koordinator", "Approved work")
        ledger.request_qa(closed.id, "ready")
        ledger.qa_approve(closed.id, "lapis")
        finished.append(closed)
        cancelled = ledger.create("lapis", "koordinator", "Dropped work")
        ledger.cancel(cancelled.id)
        finished.append(cancelled)
        obsolete = ledger.create("lapis", "koordinator", "Superseded work")
        ledger.terminalize(
            obsolete.id, status="obsolete", reason="superseded", actor="test"
        )
        finished.append(obsolete)
        other = ledger.create("lapis", "sapphire", "Someone else's card")

        # Restart: nothing in memory survives, only the files on disk.
        self.vault.append_stream(
            "session_end", "agent koordinator shutting down", from_agent="koordinator"
        )
        reborn_vault = self._open_vault()
        reborn_ledger = TaskLedger(tasks_dir)

        context = reborn_vault.get_rebirth_context(task_ledger=reborn_ledger)

        for card in (active, standby, in_qa):
            self.assertIn(card.id, context)
        for card in (*finished, other):
            self.assertNotIn(card.id, context)
        self.assertIn("active TaskLedger cards (3)", context)
        self.assertIn(f"[{active.id}] active p3 from lapis: Ship the docs inventory", context)
        self.assertIn(f"[{standby.id}] standby", context)
        self.assertIn(f"[{in_qa.id}] qa_review", context)
        # compact: titles only, never the rest of the directive
        self.assertNotIn("second line of detail", context)

    def test_rebirth_says_none_when_the_ledger_has_no_active_card(self):
        ledger = TaskLedger(str(self.tmpdir / "tasks"))
        done = ledger.create("lapis", "koordinator", "Finished work")
        ledger.complete(done.id, "ok")

        context = self._open_vault().get_rebirth_context(task_ledger=ledger)

        self.assertIn("active TaskLedger cards: none", context)
        self.assertNotIn(done.id, context)

    def test_rebirth_does_not_claim_none_when_the_ledger_is_unreadable(self):
        class BrokenLedger:
            def get_active_for(self, identity):
                raise OSError("disk gone")

        context = self.vault.get_rebirth_context(task_ledger=BrokenLedger())

        self.assertIn("active TaskLedger cards: unavailable", context)
        self.assertNotIn("cards: none", context)

    def test_rebirth_survives_a_card_with_a_null_directive(self):
        ledger = TaskLedger(str(self.tmpdir / "tasks"))
        card = ledger.create("lapis", "koordinator", "placeholder")
        path = Path(ledger._tasks_dir) / f"{card.id}.json"
        data = json.loads(path.read_text())
        data["directive"] = None
        path.write_text(json.dumps(data))

        context = self.vault.get_rebirth_context(task_ledger=ledger)

        self.assertIn(f"[{card.id}] active", context)

    def test_rebirth_caps_the_ledger_listing(self):
        ledger = TaskLedger(str(self.tmpdir / "tasks"))
        for n in range(12):
            ledger.create("lapis", "koordinator", f"card number {n}")

        context = self.vault.get_rebirth_context(task_ledger=ledger)

        self.assertIn("active TaskLedger cards (12)", context)
        self.assertEqual(context.count("from lapis:"), 10)
        self.assertIn("+2 more", context)

    def test_rebirth_without_a_ledger_omits_the_section(self):
        context = self.vault.get_rebirth_context()

        self.assertNotIn("active TaskLedger cards", context)


if __name__ == "__main__":
    unittest.main()
