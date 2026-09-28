"""JSONL round-trip: coordination state must survive compaction + reload.

Aquamarine's review gaps [A]+[B]: the compaction summary (with the
deterministic COORDINATION STATE block) never reached disk, and the loader
ignored it even if it had. This test guards the WHOLE chain:
write compaction (with a fake GO in the summarizable set) -> JSONL record
with content -> _load_from_jsonl replay -> block text survives.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.context_compaction_plugin import (  # noqa: E402
    ContextCompactionPlugin,
)

GO_BLOCK = """=== COORDINATION STATE (verbatim, machine-extracted) ===
[hub channel: koordinator -> zircon]
GO — build now. Full authority. Report when landed.
=== END COORDINATION STATE ==="""


def _make_logger(session_file: Path) -> MagicMock:
    logger = MagicMock()
    logger.session_file = session_file
    logger.session_id = "2609-test"
    return logger


def _plugin(session_file: Path) -> ContextCompactionPlugin:
    p = ContextCompactionPlugin.__new__(ContextCompactionPlugin)
    config = MagicMock()
    config.get = lambda key, default=None: {
        "plugins.context_compaction.log_compaction_events": True,
    }.get(key, default)
    p.config = config
    p._conversation_logger = _make_logger(session_file)
    p._compaction_round = 1
    return p


class TestCompactionSummaryPersists(unittest.TestCase):
    def test_summary_text_in_jsonl_record(self):
        with TemporaryDirectory() as d:
            sf = Path(d) / "session_2609-test.jsonl"
            p = _plugin(sf)
            asyncio.run(
                p._log_compaction_event(
                    messages_summarized=40,
                    messages_kept=12,
                    summary_length=len(GO_BLOCK),
                    pre_count=52,
                    post_count=13,
                    tasks_preserved=1,
                    summary_text=GO_BLOCK,
                )
            )
            self.assertTrue(sf.exists())
            records = [json.loads(line) for line in sf.read_text().splitlines()]
            rec = records[0]
            self.assertEqual(rec["type"], "context_compaction")
            self.assertEqual(rec["role"], "user")
            self.assertEqual(rec["subtype"], "compaction_summary")
            self.assertIn("GO — build now. Full authority.", rec["content"])
            self.assertIn("=== COORDINATION STATE", rec["content"])

    def test_no_summary_text_no_content_key(self):
        # legacy/empty summary -> record shape unchanged
        with TemporaryDirectory() as d:
            sf = Path(d) / "session_2609-test.jsonl"
            p = _plugin(sf)
            asyncio.run(
                p._log_compaction_event(
                    messages_summarized=40,
                    messages_kept=12,
                    summary_length=500,
                    pre_count=52,
                    post_count=13,
                )
            )
            rec = json.loads(sf.read_text().splitlines()[0])
            self.assertNotIn("content", rec)
            self.assertNotIn("subtype", rec)


class TestLoaderReplaysCompaction(unittest.TestCase):
    """conversation_manager._load_from_jsonl must surface the summary."""

    def _manager(self, tmp: Path):
        from kollabor_ai.conversation_manager import ConversationManager

        m = ConversationManager.__new__(ConversationManager)
        m.max_history = 100
        return m

    def _session_file(self, d: Path) -> Path:
        sf = Path(d) / "session_2609-test.jsonl"
        lines = [
            json.dumps(
                {
                    "type": "conversation_metadata",
                    "startTime": "2026-09-26T00:00:00Z",
                    "cwd": "/tmp",
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u1",
                    "timestamp": "2026-09-26T00:01:00Z",
                    "message": {
                        "role": "user",
                        "content": "pre-compaction turn 1",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "context_compaction",
                    "sessionId": "2609-test",
                    "uuid": "c1",
                    "timestamp": "2026-09-26T00:10:00Z",
                    "compaction_round": 1,
                    "messages_summarized": 40,
                    "messages_kept": 12,
                    "content": GO_BLOCK,
                    "role": "user",
                    "subtype": "compaction_summary",
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u2",
                    "timestamp": "2026-09-26T00:20:00Z",
                    "message": {
                        "role": "user",
                        "content": "post-compaction turn (survives, newer)",
                    },
                }
            ),
        ]
        sf.write_text("\n".join(lines) + "\n")
        return sf

    def test_round_trip_coordination_survives(self):
        with TemporaryDirectory() as d:
            sf = self._session_file(Path(d))
            m = self._manager(Path(d))
            result = m._load_from_jsonl(sf)
            msgs = result["messages"]
            contents = [x["content"] for x in msgs]
            # THE assertion: GO signal text survived compaction + reload
            self.assertTrue(
                any("GO — build now. Full authority." in c for c in contents),
                f"coordination block lost: {[c[:40] for c in contents]}",
            )
            # replayed summary + post-compaction turn
            self.assertTrue(
                any("post-compaction turn" in c for c in contents)
            )
            # NOTE: pre-compaction user/assistant turns ARE replayed by the
            # existing loader (it appends every user/assistant record it
            # sees). That is the pre-existing behavior and out of scope for
            # this patch — the caller-side history rebuild decides how the
            # JSONL context_window is consumed. The contract here is only:
            # the summary is present, FIRST among the coordination-relevant
            # records, and the post-compaction turns still follow it.
            idx_summary = next(
                i for i, c in enumerate(contents)
                if "GO — build now" in c
            )
            idx_post = next(
                i for i, c in enumerate(contents)
                if "post-compaction turn" in c
            )
            self.assertLess(idx_summary, idx_post)
            # summary comes FIRST, post-compaction turns after it
            roles = [x.get("metadata", {}) for x in msgs]
            self.assertTrue(
                any(m.get("context_compaction") for m in roles),
                "replayed summary must carry context_compaction metadata",
            )

    def test_last_round_wins(self):
        with TemporaryDirectory() as d:
            sf = Path(d) / "session_2609-test.jsonl"
            def rec(i, t):
                return json.dumps(
                    {
                        "type": "context_compaction",
                        "uuid": f"c{i}",
                        "timestamp": t,
                        "compaction_round": i,
                        "content": f"SUMMARY ROUND {i}",
                        "role": "user",
                    }
                )
            lines = [
                json.dumps(
                    {
                        "type": "conversation_metadata",
                        "startTime": "2026-09-26T00:00:00Z",
                        "cwd": "/tmp",
                    }
                ),
                rec(1, "2026-09-26T00:10:00Z"),
                rec(2, "2026-09-26T00:30:00Z"),
            ]
            sf.write_text("\n".join(lines) + "\n")
            m = self._manager(Path(d))
            result = m._load_from_jsonl(sf)
            contents = [x["content"] for x in result["messages"]]
            self.assertIn("SUMMARY ROUND 2", contents)
            self.assertNotIn("SUMMARY ROUND 1", contents)


if __name__ == "__main__":
    unittest.main()
