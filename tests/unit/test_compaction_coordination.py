"""Regression tests for compaction coordination-state loss (2026-09-26).

Three incident classes from tonight (malmazan's directive):
  1. GO signals needing re-issue (nephrite ×2) — a GO delivered mid-turn
     lands in the pending agent-HUD queue, is drained as a MERGED block
     without hub metadata, and was then LLM-summarized probabilistically.
  2. Delivery reports crossing compaction (bismuth ×2) — compaction fired
     with undrained coordination in flight.
  3. Coordinator board rebuilds — task/state lines lost to summaries.

Fixes under test:
  - containment-based hub detection (merged-HUD blocks)
  - readiness gate (defer while queued HUD / ledger pending)
  - deterministic coordination block (verbatim extraction, no LLM)
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.context_compaction_plugin import (  # noqa: E402
    ContextCompactionPlugin,
    _TASK_INDICATORS,
)


@dataclass
class _Msg:
    role: str = "user"
    content: Any = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


GO_MERGED_HUD = """[context:budget]
+ 91K / 200K tokens (45%)

[hub:pending_replies]
+ 1 pending (0m old)
    koordinator: 1568a8aed23b (4m old)

[hub channel: koordinator -> zircon]
GO — build now. Full authority. Report when landed.

  [hub wake instruction]
  classification: wake (actionable). Handle this once if actionable."""

DELIVERY_REPORT = """[hub channel: bismuth -> zircon]
SHIPPED: /voicemodels v1.2 — installer cascade NEW state, dispatch rewired,
pip-hint deleted. Wizard verified through download-complete. Report follows."""

BOARD_STATE = """[hub channel: koordinator -> *]
BOARD REBUILD: lane assignments —
  zircon: compaction fix (readiness gate + coordination block)
  aquamarine: session/rebirth half
  sapphire: TUI review
Carry forward; ledger is source of truth."""


class TestHubDetectionContainment(unittest.TestCase):
    """Incident class 1: merged-HUD blocks must be detected as hub."""

    def _plugin(self):
        p = ContextCompactionPlugin.__new__(ContextCompactionPlugin)
        return p

    def test_merged_hud_block_is_hub_message(self):
        # The block does NOT start with [hub — containment is required.
        self.assertFalse(GO_MERGED_HUD.startswith("[hub channel:"))
        self.assertTrue(
            self._plugin()._is_hub_message(_Msg(content=GO_MERGED_HUD))
        )

    def test_merged_hud_block_is_hub_task(self):
        # intended wake GO with imperative — preserved as task
        self.assertTrue(
            self._plugin()._is_hub_task(_Msg(content=GO_MERGED_HUD))
        )

    def test_plain_prefix_still_detected(self):
        self.assertTrue(
            self._plugin()._is_hub_message(_Msg(content=DELIVERY_REPORT))
        )

    def test_non_hub_content_not_flagged(self):
        self.assertFalse(
            self._plugin()._is_hub_message(
                _Msg(content="regular user message about [hubble] telescope")
            )
        )

    def test_human_elsewhere_not_a_task(self):
        m = _Msg(
            content=(
                "[hub channel: malmazan -> *]\nhello\n"
                "(the human is typing in koordinator's window. do NOT relay, "
                "repeat, or respond to koordinator about this message)"
            ),
            metadata={"hub_message": True, "hub_is_intended": True},
        )
        self.assertFalse(self._plugin()._is_hub_task(m))


class TestCoordinationExtraction(unittest.TestCase):
    """Incident class 3: coordination lines survive verbatim."""

    def test_go_in_merged_hud_extracted_verbatim(self):
        blocks = ContextCompactionPlugin._extract_coordination_lines(
            [_Msg(content=GO_MERGED_HUD)]
        )
        joined = "\n".join(blocks)
        self.assertIn("GO — build now. Full authority.", joined)
        self.assertIn("[hub channel: koordinator -> zircon]", joined)

    def test_delivery_report_extracted_whole(self):
        blocks = ContextCompactionPlugin._extract_coordination_lines(
            [_Msg(content=DELIVERY_REPORT)]
        )
        self.assertEqual(len(blocks), 1)
        self.assertIn("SHIPPED: /voicemodels v1.2", blocks[0])
        self.assertIn("pip-hint deleted", blocks[0])

    def test_board_rebuild_extracted(self):
        blocks = ContextCompactionPlugin._extract_coordination_lines(
            [_Msg(content=BOARD_STATE)]
        )
        joined = "\n".join(blocks)
        self.assertIn("BOARD REBUILD", joined)
        self.assertIn("zircon: compaction fix", joined)
        self.assertIn("aquamarine: session/rebirth half", joined)

    def test_mixed_message_only_hub_parts_extracted(self):
        mixed = (
            "long technical analysis paragraph\n\n" + DELIVERY_REPORT +
            "\n\nmore analysis after"
        )
        blocks = ContextCompactionPlugin._extract_coordination_lines(
            [_Msg(content=mixed)]
        )
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("long technical analysis", blocks[0])

    def test_no_hub_content_yields_empty(self):
        blocks = ContextCompactionPlugin._extract_coordination_lines(
            [_Msg(content="purely technical message")]
        )
        self.assertEqual(blocks, [])


class TestReadinessGate(unittest.TestCase):
    """Incident class 2: compaction defers while coordination in flight."""

    def _make(self, queued_hud=None, ledger_pending=None):
        p = ContextCompactionPlugin.__new__(ContextCompactionPlugin)

        class _Svc:
            _pending_agent_hud = queued_hud

        p._llm_service = _Svc()

        class _Ledger:
            def __init__(self, pend):
                self._pend = pend

            def pending_replies(self):
                return self._pend

        class _Hub:
            _task_ledger = (
                _Ledger(ledger_pending) if ledger_pending is not None else None
            )

        class _Bus:
            @staticmethod
            def get_service(name):
                return _Hub() if name == "hub_plugin" else None

        p.event_bus = _Bus()
        return p

    def test_queued_hud_defers(self):
        # a GO sitting in the HUD queue — compacting now would summarize it
        p = self._make(queued_hud=[object()])
        self.assertTrue(p._coordination_pending())

    def test_empty_queue_and_no_ledger_no_defer(self):
        p = self._make(queued_hud=[])
        self.assertFalse(p._coordination_pending())

    def test_none_service_no_defer(self):
        p = self._make(queued_hud=None)
        p._llm_service = None
        self.assertFalse(p._coordination_pending())

    def test_pending_replies_defer(self):
        p = self._make(queued_hud=[], ledger_pending=[{"task_id": "t1"}])
        self.assertTrue(p._coordination_pending())

    def test_resolved_replies_no_defer(self):
        p = self._make(queued_hud=[], ledger_pending=[])
        self.assertFalse(p._coordination_pending())


class TestTaskIndicatorsSanity(unittest.TestCase):
    def test_indicators_cover_tonight_language(self):
        low = (DELIVERY_REPORT + BOARD_STATE).lower()
        hits = [i for i in _TASK_INDICATORS if i in low]
        self.assertTrue(hits)  # at least one matches — 1970s heuristic OK


if __name__ == "__main__":
    unittest.main()
