"""Rebirth-seam harness tests (aquamarine): compaction summary JSONL round-trip.

Covers koordinator's asked-for failure classes:
  [1] marker round-trip: COORDINATION STATE block survives write→load verbatim
  [2] multi-round last-wins: only the final round's summary replays
  [3] insertion order: summary lands BEFORE the post-compaction tail
  [4] resume→compact→resume cycle: block preserved again after round 2
  [5] no-tail case: summary-only file loads
  [6] OLD format (stats-only compaction records) still loads without content
  [7] GO-signal replay: unacked coordination in the block is re-deliverable

Runs against the REAL loader (conversation_manager._load_from_jsonl) with
synthetic session files — no daemon, no LLM.
"""

from __future__ import annotations

import json
import sys
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, "packages/kollabor-ai/src")
from kollabor_ai.conversation_manager import ConversationManager  # noqa: E402

COORD_BLOCK = (
    "=== COORDINATION STATE (verbatim, machine-extracted) ===\n"
    "[hub channel: koordinator -> aquamarine]\n"
    "GO signal: build aec.py — green light.\n"
    "---\n"
    "[hub channel: zircon -> aquamarine]\n"
    "delivery report: compaction fix shipped.\n"
    "=== END COORDINATION STATE ==="
)

SUMMARY_R1 = f"ROUND 1 SUMMARY — early work on voice mode.\n\n{COORD_BLOCK}"
SUMMARY_R2 = f"ROUND 2 SUMMARY — assembly complete, matrices green.\n\n{COORD_BLOCK}"


def rec_compaction(round_n: int, content: str | None, ts: str) -> dict[str, Any]:
    r = {
        "type": "context_compaction",
        "sessionId": "test-session",
        "uuid": str(uuidlib.uuid4()),
        "timestamp": ts,
        "compaction_round": round_n,
        "messages_summarized": 10 * round_n,
        "messages_kept": 8,
        "summary_length": len(content or ""),
        "pre_message_count": 100 * round_n,
        "post_message_count": 8,
        "tasks_preserved": 3,
    }
    if content is not None:
        r["content"] = content
        r["role"] = "user"
        r["subtype"] = "compaction_summary"
    return r


def rec_user(text: str, ts: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": str(uuidlib.uuid4()),
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def rec_assistant(text: str, ts: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": str(uuidlib.uuid4()),
        "timestamp": ts,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def write_session(tmp_path: Path, records: list[dict]) -> Path:
    # loader reads {session_id}.jsonl (conversation_manager.py:546);
    # logger writes session ids like "2609252214-sigma-pulse" — no prefix
    f = tmp_path / "test-session.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return f


def make_mgr(tmp_path: Path) -> ConversationManager:
    # minimal config: _conversations_dir (conversation_manager.py:70) + get()
    class _Cfg:
        _conversations_dir = tmp_path

        def get(self, key: str, default=None):
            return default

    return ConversationManager(_Cfg())


def loaded(mgr: ConversationManager) -> list[dict]:
    assert mgr.load_session("test-session"), "load_session returned False"
    return mgr.messages


class TestMarkerRoundTrip:
    def test_coordination_block_survives_verbatim(self, tmp_path):
        recs = [
            rec_compaction(1, SUMMARY_R1, "2026-09-26T00:00:00+00:00"),
            rec_user("post-compaction turn", "2026-09-26T00:05:00+00:00"),
        ]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        assert any(
            COORD_BLOCK in (m.get("content") or "") for m in msgs
        ), "coordination block lost in round-trip"

    def test_go_signal_extractable_by_marker(self, tmp_path):
        recs = [rec_compaction(1, SUMMARY_R1, "2026-09-26T00:00:00+00:00")]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        summary = next(m for m in msgs if "COORDINATION STATE" in (m.get("content") or ""))
        content = summary["content"]
        start = content.index("=== COORDINATION STATE")
        end = content.index("=== END COORDINATION STATE") + len("=== END COORDINATION STATE")
        block = content[start:end]
        assert "GO signal: build aec.py" in block
        assert "[hub channel: koordinator -> aquamarine]" in block


class TestMultiRoundLastWins:
    def test_only_final_round_replays(self, tmp_path):
        recs = [
            rec_user("old turn summarized in r1", "2026-09-25T20:00:00+00:00"),
            rec_compaction(1, SUMMARY_R1, "2026-09-25T21:00:00+00:00"),
            rec_compaction(2, SUMMARY_R2, "2026-09-25T22:00:00+00:00"),
            rec_user("final tail", "2026-09-25T23:00:00+00:00"),
        ]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        r1 = [m for m in msgs if "ROUND 1 SUMMARY" in (m.get("content") or "")]
        r2 = [m for m in msgs if "ROUND 2 SUMMARY" in (m.get("content") or "")]
        assert not r1, "round-1 summary replayed despite round-2 superseding it"
        assert len(r2) == 1


class TestInsertionOrder:
    def test_summary_before_tail(self, tmp_path):
        recs = [
            rec_compaction(1, SUMMARY_R1, "2026-09-26T00:00:00+00:00"),
            rec_user("post turn", "2026-09-26T00:05:00+00:00"),
            rec_assistant("post reply", "2026-09-26T00:06:00+00:00"),
        ]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        idx_summary = next(
            i for i, m in enumerate(msgs) if "ROUND 1 SUMMARY" in (m.get("content") or "")
        )
        assert idx_summary == 0, "summary not first — tail must follow it"
        assert msgs[-1]["content"] == "post reply"

    def test_summary_only_file_loads(self, tmp_path):
        recs = [rec_compaction(1, SUMMARY_R1, "2026-09-26T00:00:00+00:00")]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        assert len(msgs) == 1
        assert "COORDINATION STATE" in msgs[0]["content"]


class TestResumeCompactResumeCycle:
    def test_round2_preserves_block_again(self, tmp_path):
        # cycle: load (r1 summary + tail) -> write back with r2 summary -> load
        f = write_session(
            tmp_path,
            [
                rec_compaction(1, SUMMARY_R1, "2026-09-26T00:00:00+00:00"),
                rec_user("mid turn", "2026-09-26T00:05:00+00:00"),
            ],
        )
        mgr = make_mgr(tmp_path)
        assert mgr.load_session("test-session")
        # simulate resumed session compacting again: append r2 record
        with open(f, "a") as fh:
            fh.write(json.dumps(rec_compaction(2, SUMMARY_R2, "2026-09-26T01:00:00+00:00")) + "\n")
            fh.write(json.dumps(rec_user("new tail", "2026-09-26T01:05:00+00:00")) + "\n")
        mgr2 = make_mgr(tmp_path)
        assert mgr2.load_session("test-session")
        msgs = mgr2.messages
        assert not any("ROUND 1 SUMMARY" in (m.get("content") or "") for m in msgs)
        assert any("ROUND 2 SUMMARY" in (m.get("content") or "") for m in msgs)
        assert any("COORDINATION STATE" in (m.get("content") or "") for m in msgs)


class TestLegacyFormat:
    def test_stats_only_record_still_loads(self, tmp_path):
        recs = [
            rec_user("old turn", "2026-09-25T20:00:00+00:00"),
            rec_compaction(1, None, "2026-09-25T21:00:00+00:00"),  # no content
            rec_user("tail", "2026-09-25T22:00:00+00:00"),
        ]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        assert len(msgs) == 2  # no summary injected; user/assistant pass through


class TestGoSignalReplay:
    def test_unacked_go_replayable_from_block_alone(self, tmp_path):
        """[b] replay of unacked coordination follows from the block: the
        reborn agent sees the GO text in-context without any API."""
        recs = [
            rec_compaction(1, SUMMARY_R1, "2026-09-26T00:00:00+00:00"),
            rec_assistant("ack of unrelated work", "2026-09-26T00:02:00+00:00"),
        ]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        # GO is present, and nothing marks it consumed — replay = presence
        block_msgs = [m for m in msgs if "GO signal" in (m.get("content") or "")]
        assert block_msgs, "GO signal not replayable from block alone"
        assert all(
            not (m.get("metadata") or {}).get("consumed") for m in block_msgs
        )


class TestBugPreCompactionDuplication:
    """EXPOSED BUG (aquamarine review 00:4x): pre-compaction user/assistant
    records survive alongside the summary that REPLACED them. Resumed
    context = summary + stale already-summarized history + tail. The comment
    at conversation_manager.py:879-883 promises tail-after-timestamp; the
    code prepends the summary to EVERYTHING. These tests encode the
    CORRECT behavior; they FAIL against the current loader — that's the
    point. Zircon: fix = only append user/assistant records whose
    timestamp > last compaction timestamp."""

    def test_pre_compaction_messages_dropped(self, tmp_path):
        recs = [
            rec_user("summarized-away turn 1", "2026-09-25T20:00:00+00:00"),
            rec_assistant("summarized-away reply 1", "2026-09-25T20:01:00+00:00"),
            rec_compaction(1, SUMMARY_R1, "2026-09-25T21:00:00+00:00"),
            rec_user("live tail", "2026-09-25T22:00:00+00:00"),
        ]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        stale = [m for m in msgs if "summarized-away" in (m.get("content") or "")]
        assert not stale, "pre-compaction messages leaked through compaction replay"

    def test_r1_window_not_leaked_in_r2_session(self, tmp_path):
        recs = [
            rec_user("very old turn", "2026-09-25T10:00:00+00:00"),
            rec_compaction(1, SUMMARY_R1, "2026-09-25T11:00:00+00:00"),
            rec_compaction(2, SUMMARY_R2, "2026-09-25T22:00:00+00:00"),
        ]
        mgr = make_mgr(tmp_path)
        write_session(tmp_path, recs)
        msgs = loaded(mgr)
        assert not any("very old turn" in (m.get("content") or "") for m in msgs)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
