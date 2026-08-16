"""Regression tests for safe hub rebirth context."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.hub.vault import AgentVault, sanitize_rebirth_text


class TestHubVaultRebirth(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.project_vaults = self.tmpdir / "project-vaults"
        self.global_vaults = self.tmpdir / "global-vaults"
        with (
            patch("plugins.hub.vault.get_vaults_dir", return_value=self.project_vaults),
            patch(
                "plugins.hub.vault.get_global_vaults_dir",
                return_value=self.global_vaults,
            ),
        ):
            self.vault = AgentVault("koordinator")

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


if __name__ == "__main__":
    unittest.main()
