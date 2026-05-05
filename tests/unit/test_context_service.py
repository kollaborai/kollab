"""Tests for the ContextService service layer.

Covers: ingest_heavy_item, set_decision, evict, file_read_hook,
curator triggering, context snapshot, confirmation injection,
entry_for_message, increment_turn.
"""

from kollabor_ai.context_service.service import ContextService


def _make_content(size_kb: int = 10) -> bytes:
    """Create dummy content of given size in KB."""
    return b"x" * (size_kb * 1024)


def _make_service(
    heavy_kb: int = 8,
    curate_kb: int = 300,
) -> ContextService:
    return ContextService(
        heavy_threshold_kb=heavy_kb,
        curate_threshold_kb=curate_kb,
    )


class TestIngestHeavyItem:
    """Tests for ingest_heavy_item."""

    def test_below_threshold_returns_none(self):
        svc = _make_service(heavy_kb=10)
        result = svc.ingest_heavy_item(
            kind="file_read",
            tool="read",
            label="small.py",
            content=_make_content(5),
            message_uuid="uuid-1",
        )
        assert result is None

    def test_at_threshold_creates_entry(self):
        svc = _make_service(heavy_kb=10)
        result = svc.ingest_heavy_item(
            kind="file_read",
            tool="read",
            label="exact.py",
            content=_make_content(10),
            message_uuid="uuid-1",
        )
        assert result is not None
        assert result.ctx_id == "ctx-1"
        assert result.kind == "file_read"
        assert result.size_bytes == 10 * 1024
        assert result.decision == "pending"

    def test_sequential_ctx_ids(self):
        svc = _make_service(heavy_kb=1)
        e1 = svc.ingest_heavy_item(
            "tool_result", "terminal", "cmd1",
            _make_content(2), "uuid-1",
        )
        e2 = svc.ingest_heavy_item(
            "tool_result", "terminal", "cmd2",
            _make_content(2), "uuid-2",
        )
        e3 = svc.ingest_heavy_item(
            "tool_result", "terminal", "cmd3",
            _make_content(2), "uuid-3",
        )
        assert e1.ctx_id == "ctx-1"
        assert e2.ctx_id == "ctx-2"
        assert e3.ctx_id == "ctx-3"

    def test_file_path_updates_file_tracker(self):
        svc = _make_service(heavy_kb=1)
        entry = svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(2), "uuid-1",
            file_path="test.py",
        )
        assert entry.file_path == "test.py"
        assert entry.file_version == 1

    def test_content_hash_computed(self):
        svc = _make_service(heavy_kb=1)
        content = _make_content(2)
        entry = svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            content, "uuid-1",
        )
        assert len(entry.content_hash) == 16
        from kollabor_ai.context_service.hash_utils import compute_hash
        assert entry.content_hash == compute_hash(content)


class TestSetDecision:
    """Tests for set_decision."""

    def _add_entry(self, svc, size_kb=10):
        return svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(size_kb), "uuid-1",
        )

    def test_keep_decision(self):
        svc = _make_service(heavy_kb=1)
        self._add_entry(svc)
        assert svc.set_decision("ctx-1", "keep", "actively editing")
        entry = svc.all_entries()[0]
        assert entry.decision == "keep"
        assert entry.decision_body == "actively editing"
        assert entry.decided_at is not None

    def test_summary_decision(self):
        svc = _make_service(heavy_kb=1)
        self._add_entry(svc)
        assert svc.set_decision("ctx-1", "summary", "compressed version")
        entry = svc.all_entries()[0]
        assert entry.decision == "summary"
        assert entry.decision_body == "compressed version"

    def test_invalid_decision_rejected(self):
        svc = _make_service(heavy_kb=1)
        self._add_entry(svc)
        assert not svc.set_decision("ctx-1", "invalid", "body")

    def test_empty_body_rejected(self):
        svc = _make_service(heavy_kb=1)
        self._add_entry(svc)
        assert not svc.set_decision("ctx-1", "keep", "   ")

    def test_nonexistent_entry_returns_false(self):
        svc = _make_service()
        assert not svc.set_decision("ctx-999", "keep", "reason")

    def test_overwrite_previous_decision(self):
        svc = _make_service(heavy_kb=1)
        self._add_entry(svc)
        svc.set_decision("ctx-1", "keep", "first reason")
        svc.set_decision("ctx-1", "summary", "second reason")
        entry = svc.all_entries()[0]
        assert entry.decision == "summary"
        assert entry.decision_body == "second reason"


class TestEvict:
    """Tests for evict."""

    def test_evict_existing(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(2), "uuid-1",
        )
        assert svc.evict("ctx-1", "done with this")
        entry = svc.all_entries()[0]
        assert entry.decision == "evicted"
        assert entry.decision_body == "done with this"

    def test_evict_nonexistent(self):
        svc = _make_service()
        assert not svc.evict("ctx-999")

    def test_evicted_excluded_from_total_bytes(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(10), "uuid-1",
        )
        svc.ingest_heavy_item(
            "file_read", "read", "test2.py",
            _make_content(10), "uuid-2",
        )
        assert svc._ledger.total_bytes() == 20 * 1024
        svc.evict("ctx-1")
        assert svc._ledger.total_bytes() == 10 * 1024


class TestFileReadHook:
    """Tests for file_read_hook."""

    def test_fresh_file(self):
        svc = _make_service()
        content = _make_content(10)
        result = svc.file_read_hook("newfile.py", content)
        assert result["action"] == "fresh"
        assert result["content"] == content
        assert result["ledger_entry"] is None

    def test_stale_hit_after_ingest(self):
        svc = _make_service(heavy_kb=1)
        content = _make_content(10)
        # First read — ingest it
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            content, "uuid-1", file_path="test.py",
        )
        # Second read — same content
        result = svc.file_read_hook("test.py", content)
        assert result["action"] == "stale"
        text = result["content"].decode("utf-8")
        assert "stale hit" in text
        assert "ctx-1" in text

    def test_diff_on_changed_content(self):
        svc = _make_service(heavy_kb=1)
        old_content = _make_content(10)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            old_content, "uuid-1", file_path="test.py",
        )
        # New content — different hash
        new_content = b"y" * (10 * 1024)
        result = svc.file_read_hook("test.py", new_content)
        assert result["action"] == "diff"
        text = result["content"].decode("utf-8")
        assert "file changed" in text
        assert result["prior_ctx_id"] == "ctx-1"

    def test_force_bypasses_dedup(self):
        svc = _make_service(heavy_kb=1)
        content = _make_content(10)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            content, "uuid-1", file_path="test.py",
        )
        result = svc.file_read_hook("test.py", content, force=True)
        assert result["action"] == "force_fresh"
        assert result["content"] == content

    def test_stale_updates_read_count(self):
        svc = _make_service(heavy_kb=1)
        content = _make_content(5)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            content, "uuid-1", file_path="test.py",
        )
        assert svc.all_entries()[0].read_count == 1
        svc.file_read_hook("test.py", content)
        assert svc.all_entries()[0].read_count == 2
        svc.file_read_hook("test.py", content)
        assert svc.all_entries()[0].read_count == 3


class TestCurator:
    """Tests for curator triggering and injection."""

    def test_curator_not_triggered_below_threshold(self):
        svc = _make_service(curate_kb=1)
        # 5KB content, threshold 1KB for heavy, 1KB for curate
        # Actually curate_kb is 1KB total ledger size
        svc.ingest_heavy_item(
            "file_read", "read", "small.py",
            _make_content(1), "uuid-1",
        )
        assert not svc._curator_pending

    def test_curator_triggered_at_threshold(self):
        svc = _make_service(heavy_kb=1, curate_kb=5)
        # Add 3KB each = 6KB total, exceeds 5KB threshold
        svc.ingest_heavy_item(
            "file_read", "read", "a.py",
            _make_content(3), "uuid-1",
        )
        svc.ingest_heavy_item(
            "file_read", "read", "b.py",
            _make_content(3), "uuid-2",
        )
        assert svc._curator_pending

    def test_curator_injection_returns_none_when_not_pending(self):
        svc = _make_service()
        assert svc.build_curator_injection() is None

    def test_curator_injection_contains_pending_entries(self):
        svc = _make_service(heavy_kb=1, curate_kb=5)
        svc.ingest_heavy_item(
            "file_read", "read", "a.py",
            _make_content(3), "uuid-1",
        )
        svc.ingest_heavy_item(
            "file_read", "read", "b.py",
            _make_content(3), "uuid-2",
        )
        # Curator should be pending now
        injection = svc.build_curator_injection()
        assert injection is not None
        assert "curator" in injection
        assert "ctx-1" in injection
        assert "ctx-2" in injection
        # One-shot: second call returns None
        assert svc.build_curator_injection() is None

    def test_curator_throttled_by_turns(self):
        svc = _make_service(heavy_kb=1, curate_kb=5)
        svc.ingest_heavy_item(
            "file_read", "read", "a.py",
            _make_content(3), "uuid-1",
        )
        svc.ingest_heavy_item(
            "file_read", "read", "b.py",
            _make_content(3), "uuid-2",
        )
        assert svc._curator_pending
        # Consume it
        svc.build_curator_injection()
        # Add more — but turn count hasn't advanced enough
        svc.ingest_heavy_item(
            "file_read", "read", "c.py",
            _make_content(3), "uuid-3",
        )
        assert not svc._curator_pending  # Throttled


class TestContextSnapshot:
    """Tests for context snapshot injection."""

    def test_no_snapshot_when_not_requested(self):
        svc = _make_service()
        assert svc.build_context_snapshot() is None

    def test_snapshot_shows_entries(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(5), "uuid-1",
        )
        svc.request_context_snapshot()
        snap = svc.build_context_snapshot()
        assert snap is not None
        assert "ledger snapshot" in snap
        assert "ctx-1" in snap

    def test_snapshot_with_pending_filter(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "a.py",
            _make_content(2), "uuid-1",
        )
        svc.ingest_heavy_item(
            "tool_result", "terminal", "cmd",
            _make_content(2), "uuid-2",
        )
        svc.set_decision("ctx-1", "keep", "editing")
        svc.request_context_snapshot(filter_spec="pending")
        snap = svc.build_context_snapshot()
        assert snap is not None
        assert "ctx-1" not in snap  # keep, not pending
        assert "ctx-2" in snap

    def test_snapshot_one_shot(self):
        svc = _make_service()
        svc.request_context_snapshot()
        assert svc.build_context_snapshot() is not None
        assert svc.build_context_snapshot() is None

    def test_snapshot_empty_ledger(self):
        svc = _make_service()
        svc.request_context_snapshot()
        snap = svc.build_context_snapshot()
        assert "(empty)" in snap


class TestConfirmation:
    """Tests for confirmation injection."""

    def test_no_confirmation_when_not_pending(self):
        svc = _make_service()
        assert svc.build_confirmation_injection() is None

    def test_confirmation_after_decision(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(10), "uuid-1",
        )
        svc.set_decision("ctx-1", "keep", "actively editing")
        conf = svc.build_confirmation_injection()
        assert conf is not None
        assert "decisions recorded" in conf
        assert "ctx-1" in conf
        assert "keep" in conf

    def test_confirmation_shows_savings(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "big.py",
            _make_content(100), "uuid-1",
        )
        svc.set_decision("ctx-1", "summary", "short summary")
        conf = svc.build_confirmation_injection()
        assert "savings" in conf

    def test_confirmation_one_shot(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(5), "uuid-1",
        )
        svc.set_decision("ctx-1", "keep", "reason")
        assert svc.build_confirmation_injection() is not None
        assert svc.build_confirmation_injection() is None


class TestEntryForMessage:
    """Tests for entry_for_message lookup."""

    def test_finds_entry_by_uuid(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(2), "uuid-42",
        )
        entry = svc.entry_for_message("uuid-42")
        assert entry is not None
        assert entry.ctx_id == "ctx-1"

    def test_returns_none_for_unknown_uuid(self):
        svc = _make_service(heavy_kb=1)
        svc.ingest_heavy_item(
            "file_read", "read", "test.py",
            _make_content(2), "uuid-1",
        )
        assert svc.entry_for_message("uuid-999") is None


class TestIncrementTurn:
    """Tests for increment_turn."""

    def test_turn_count_increments(self):
        svc = _make_service()
        assert svc._ledger.turn_count == 0
        svc.increment_turn()
        assert svc._ledger.turn_count == 1
        svc.increment_turn()
        assert svc._ledger.turn_count == 2
