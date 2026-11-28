"""Regression coverage for malformed task cards in rebirth context."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.hub.task_ledger import TaskLedger
from plugins.hub.vault import AgentVault


class TestMalformedTaskCardInRebirth(unittest.TestCase):
    def test_restart_does_not_report_none_when_only_active_card_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / "tasks"
            vaults_dir = root / "vaults"
            global_vaults_dir = root / "global-vaults"
            ledger = TaskLedger(str(tasks_dir))
            card = ledger.create("koordinator", "lapis", "resume the deployment")
            ledger._task_path(card.id).write_text("{", encoding="utf-8")

            # Reopen the vault as a fresh process would after the malformed card
            # was left on disk.
            with (
                patch("plugins.hub.vault.get_vaults_dir", return_value=vaults_dir),
                patch(
                    "plugins.hub.vault.get_global_vaults_dir",
                    return_value=global_vaults_dir,
                ),
            ):
                reborn_vault = AgentVault("lapis")
                context = reborn_vault.get_rebirth_context(task_ledger=ledger)

        self.assertIn("active TaskLedger cards: unavailable", context)
        self.assertNotIn("active TaskLedger cards: none", context)


if __name__ == "__main__":
    unittest.main()
