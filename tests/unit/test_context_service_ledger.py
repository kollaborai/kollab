"""Unit tests for the ContextService ledger.

Covers: sequential ctx_id, add/get, sorted all(), total_bytes
excludes evicted, count_pending, thread safety.
"""

import threading
from datetime import datetime

from kollabor_ai.context_service.hash_utils import compute_hash
from kollabor_ai.context_service.ledger import Ledger
from kollabor_ai.context_service.models import LedgerEntry


def _make_entry(
    ctx_id: str = "ctx-1",
    size: int = 10000,
    decision: str = "pending",
    kind: str = "file_read",
    tool: str = "read",
    label: str = "test.py",
) -> LedgerEntry:
    """Helper to create a LedgerEntry with sensible defaults."""
    return LedgerEntry(
        ctx_id=ctx_id,
        kind=kind,
        tool=tool,
        label=label,
        content_hash="abc123",
        size_bytes=size,
        message_uuid="uuid-1",
        added_at=datetime.now(),
        last_accessed_at=datetime.now(),
        decision=decision,
    )


# --- ctx_id sequential generation ---

def test_next_ctx_id_sequential():
    """ctx_id values increment sequentially."""
    ledger = Ledger()
    assert ledger.next_ctx_id() == "ctx-1"
    assert ledger.next_ctx_id() == "ctx-2"
    assert ledger.next_ctx_id() == "ctx-3"


def test_next_ctx_id_independent_of_add():
    """ctx_id counter increments even without calling add()."""
    ledger = Ledger()
    id1 = ledger.next_ctx_id()
    id2 = ledger.next_ctx_id()
    assert id1 == "ctx-1"
    assert id2 == "ctx-2"
    assert ledger.get("ctx-1") is None  # Never added


# --- add and get ---

def test_add_and_get():
    """Add an entry then retrieve it by ctx_id."""
    ledger = Ledger()
    entry = _make_entry()
    ledger.add(entry)
    assert ledger.get("ctx-1") is entry


def test_get_missing_returns_none():
    """Looking up a non-existent ctx_id returns None."""
    ledger = Ledger()
    assert ledger.get("ctx-999") is None


def test_add_multiple_entries():
    """Multiple entries coexist in the ledger."""
    ledger = Ledger()
    e1 = _make_entry(ctx_id="ctx-1", label="a.py")
    e2 = _make_entry(ctx_id="ctx-2", label="b.py")
    e3 = _make_entry(ctx_id="ctx-3", label="c.py")
    ledger.add(e1)
    ledger.add(e2)
    ledger.add(e3)
    assert ledger.get("ctx-1").label == "a.py"
    assert ledger.get("ctx-2").label == "b.py"
    assert ledger.get("ctx-3").label == "c.py"


def test_add_overwrites_duplicate_ctx_id():
    """Adding an entry with the same ctx_id overwrites the prior."""
    ledger = Ledger()
    e1 = _make_entry(ctx_id="ctx-1", label="original.py")
    e2 = _make_entry(ctx_id="ctx-1", label="replacement.py")
    ledger.add(e1)
    ledger.add(e2)
    assert ledger.get("ctx-1").label == "replacement.py"


# --- all() sorted ---

def test_all_sorted_by_ctx_id():
    """all() returns entries sorted by numeric ctx_id."""
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-10"))
    ledger.add(_make_entry(ctx_id="ctx-2"))
    ledger.add(_make_entry(ctx_id="ctx-5"))
    ledger.add(_make_entry(ctx_id="ctx-1"))
    entries = ledger.all()
    assert [e.ctx_id for e in entries] == [
        "ctx-1", "ctx-2", "ctx-5", "ctx-10"
    ]


def test_all_returns_copy():
    """all() returns a new list, not the internal dict values."""
    ledger = Ledger()
    ledger.add(_make_entry())
    entries1 = ledger.all()
    entries2 = ledger.all()
    assert entries1 is not entries2
    assert entries1 == entries2


def test_all_empty_ledger():
    """all() returns an empty list on a fresh ledger."""
    ledger = Ledger()
    assert ledger.all() == []


# --- total_bytes ---

def test_total_bytes_basic():
    """total_bytes sums size_bytes of all entries."""
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-1", size=10000))
    ledger.add(_make_entry(ctx_id="ctx-2", size=20000))
    assert ledger.total_bytes() == 30000


def test_total_bytes_excludes_evicted():
    """total_bytes does not count evicted entries."""
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-1", size=10000, decision="pending"))
    ledger.add(_make_entry(ctx_id="ctx-2", size=20000, decision="keep"))
    ledger.add(_make_entry(ctx_id="ctx-3", size=30000, decision="evicted"))
    assert ledger.total_bytes() == 30000  # 10k + 20k, not 60k


def test_total_bytes_empty_ledger():
    """total_bytes returns 0 for an empty ledger."""
    ledger = Ledger()
    assert ledger.total_bytes() == 0


# --- count_pending ---

def test_count_pending_mixed():
    """count_pending only counts entries with decision='pending'."""
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-1", decision="pending"))
    ledger.add(_make_entry(ctx_id="ctx-2", decision="keep"))
    ledger.add(_make_entry(ctx_id="ctx-3", decision="pending"))
    ledger.add(_make_entry(ctx_id="ctx-4", decision="summary"))
    ledger.add(_make_entry(ctx_id="ctx-5", decision="evicted"))
    assert ledger.count_pending() == 2


def test_count_pending_none_pending():
    """count_pending returns 0 when all entries have decisions."""
    ledger = Ledger()
    ledger.add(_make_entry(ctx_id="ctx-1", decision="keep"))
    ledger.add(_make_entry(ctx_id="ctx-2", decision="summary"))
    assert ledger.count_pending() == 0


def test_count_pending_empty():
    """count_pending returns 0 for an empty ledger."""
    ledger = Ledger()
    assert ledger.count_pending() == 0


# --- thread safety ---

def test_concurrent_adds():
    """Concurrent adds from multiple threads don't corrupt the ledger."""
    ledger = Ledger()
    n_threads = 10
    n_per_thread = 50
    errors: list = []

    def worker(thread_idx: int) -> None:
        try:
            for i in range(n_per_thread):
                ctx_id = ledger.next_ctx_id()
                entry = _make_entry(
                    ctx_id=ctx_id,
                    label=f"thread{thread_idx}_item{i}",
                    size=1000,
                )
                ledger.add(entry)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(t,))
        for t in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    entries = ledger.all()
    assert len(entries) == n_threads * n_per_thread

    # All ctx_ids should be unique
    ctx_ids = [e.ctx_id for e in entries]
    assert len(set(ctx_ids)) == len(ctx_ids), "Duplicate ctx_ids detected"


def test_concurrent_reads_and_writes():
    """Reads during concurrent writes don't raise errors."""
    ledger = Ledger()

    # Pre-populate
    for i in range(20):
        ctx_id = ledger.next_ctx_id()
        ledger.add(_make_entry(ctx_id=ctx_id, size=1000))

    errors: list = []

    def writer() -> None:
        try:
            for i in range(50):
                ctx_id = ledger.next_ctx_id()
                ledger.add(_make_entry(ctx_id=ctx_id, size=2000))
        except Exception as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(50):
                ledger.all()
                ledger.total_bytes()
                ledger.count_pending()
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer),
        threading.Thread(target=writer),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"


# --- hash_utils ---

def test_compute_hash_deterministic():
    """Same content produces same hash."""
    data = b"hello world"
    assert compute_hash(data) == compute_hash(data)


def test_compute_hash_length():
    """Hash is 16 hex characters (8 bytes)."""
    h = compute_hash(b"test content")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_compute_hash_different_content():
    """Different content produces different hashes."""
    h1 = compute_hash(b"foo")
    h2 = compute_hash(b"bar")
    assert h1 != h2


def test_compute_hash_empty():
    """Empty content still produces a valid hash."""
    h = compute_hash(b"")
    assert len(h) == 16


# --- models ---

def test_ledger_entry_defaults():
    """LedgerEntry has correct defaults for optional fields."""
    entry = LedgerEntry(
        ctx_id="ctx-1",
        kind="file_read",
        tool="read",
        label="test.py",
        content_hash="abc",
        size_bytes=100,
        message_uuid="uuid-1",
        added_at=datetime.now(),
        last_accessed_at=datetime.now(),
    )
    assert entry.decision == "pending"
    assert entry.decision_body == ""
    assert entry.decided_at is None
    assert entry.read_count == 1
    assert entry.ttl_seconds is None
    assert entry.file_path is None
    assert entry.file_lines is None
    assert entry.file_version is None
    assert entry.prior_ctx_id is None
    assert entry.hub_shared is False
    assert entry.hub_holders == []


def test_file_version_latest():
    """FileVersion.latest returns the most recent entry."""
    from kollabor_ai.context_service.models import FileVersion

    e1 = _make_entry(ctx_id="ctx-1")
    e2 = _make_entry(ctx_id="ctx-2")
    fv = FileVersion(path="test.py", versions=[e1, e2])
    assert fv.latest is e2
    assert fv.latest_hash == e2.content_hash


def test_file_version_latest_empty():
    """FileVersion.latest returns None when no versions."""
    from kollabor_ai.context_service.models import FileVersion

    fv = FileVersion(path="test.py")
    assert fv.latest is None
    assert fv.latest_hash is None
