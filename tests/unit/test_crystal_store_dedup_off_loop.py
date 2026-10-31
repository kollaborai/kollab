"""Dreaming's dedup pass keeps the full pass's merges and runs off the event loop (#546)."""

import asyncio
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from plugins.hub.crystal_store import CrystalStore

# Distinct summaries, keywords {a,b,c} {a,b,d} {a,d,e}: 1 and 2 match (2/4),
# 2 and 3 match (2/4), 1 and 3 do not. After 2 merges into 1, 1+2 vs 3 is 2/5.
_CHAIN = [
    ("crys-001", "a, b, c", "alpha bravo charlie delta"),
    ("crys-002", "a, b, d", "echo foxtrot golf hotel"),
    ("crys-003", "a, d, e", "india juliet kilo lima"),
]


def _vault(entries) -> Path:
    vault = Path(tempfile.mkdtemp())
    blocks = [
        f"---\nid: {entry_id}\ndate: 2026-10-01\nkeywords: {keywords}\nsummary: {summary}\n---\n{summary} body.\n"
        for entry_id, keywords, summary in entries
    ]
    (vault / "crystallized.md").write_text("\n".join(blocks))
    return vault


def test_a_chain_merges_in_list_order():
    store = CrystalStore(_vault(_CHAIN))
    assert store.deduplicate() == 1
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-003"]


def test_duplicates_already_on_disk_merge_after_a_restart():
    vault = _vault(_CHAIN[:2])  # saved by an agent that stopped before its next pass
    assert CrystalStore(vault).deduplicate() == 1
    assert [e.id for e in CrystalStore(vault).get_all()] == ["crys-001"]


@pytest.mark.asyncio
async def test_the_dreaming_pass_runs_in_a_worker_thread_with_the_same_result():
    store = CrystalStore(_vault(_CHAIN))
    threads = []
    merge_pass = store._merge_pass

    def recorded(*args):
        threads.append((threading.get_ident(), sys.getswitchinterval()))
        return merge_pass(*args)

    interval = sys.getswitchinterval()
    store._merge_pass = recorded
    assert await store.deduplicate_off_loop() == 1
    assert threads and threads[0][0] != threading.get_ident()
    # The pass gives the GIL back often, so the loop's syscalls never wait on it.
    assert threads[0][1] <= 0.0002 and sys.getswitchinterval() == interval
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-003"]
    assert [e.id for e in CrystalStore(store._path.parent).get_all()] == ["crys-001", "crys-003"]


def _hold_pass(store, during):
    """Pause the worker's pass until ``during()`` has run on the event loop."""
    started, release = threading.Event(), threading.Event()
    merge_pass = store._merge_pass

    def held(*args):
        started.set()
        release.wait(5)
        return merge_pass(*args)

    async def run():
        store._merge_pass = held
        task = asyncio.ensure_future(store.deduplicate_off_loop())
        await asyncio.to_thread(started.wait, 5)
        result = during()
        release.set()
        merged = await task
        store._merge_pass = merge_pass
        return merged, result

    return run()


@pytest.mark.asyncio
async def test_writes_during_every_pass_never_hold_back_the_merge():
    # Kyanite on #547 round 2: one unrelated write per pass threw every pass
    # away, so the duplicates never merged.
    store = CrystalStore(_vault(_CHAIN[:2]))
    merged, added = await _hold_pass(store, lambda: store.add_entry("**Zulu yankee** xray whiskey."))
    assert merged == 1
    assert [e.id for e in store.get_all()] == ["crys-001", added.id]
    assert [e.id for e in CrystalStore(store._path.parent).get_all()] == ["crys-001", added.id]
    merged, again = await _hold_pass(store, lambda: store.add_entry("**Victor uniform** tango sierra."))
    assert merged == 0 and [e.id for e in store.get_all()] == ["crys-001", added.id, again.id]


@pytest.mark.asyncio
async def test_a_merge_whose_entry_changed_during_the_pass_waits_for_the_next():
    pairs = _CHAIN[:2] + [
        ("crys-004", "x, y, z", "mike november oscar papa"),
        ("crys-005", "x, y, w", "quebec romeo sierra tango"),
    ]
    store = CrystalStore(_vault(pairs))
    edit = lambda: store.update_entry("crys-002", "A new body, edited meanwhile.", keywords=["a", "b", "d"])  # noqa: E731
    merged, _ = await _hold_pass(store, edit)
    assert merged == 0  # 002 changed: its merge and every one after it wait, 005 into 004 too
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-002", "crys-004", "crys-005"]
    assert store.get_by_id("crys-002").body == "A new body, edited meanwhile."
    assert await store.deduplicate_off_loop() == 2
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-004"]


@pytest.mark.asyncio
async def test_merges_after_a_skipped_one_wait_since_they_may_depend_on_it():
    # Kyanite on #547 round 3: the pass plans 001<-002 and 003<-004. With 001
    # edited, 002 stays, and a fresh pass merges 003 and 004 into 002 instead.
    entries = _CHAIN + [("crys-004", "a, d, e, x, y", "mike november oscar papa")]
    store = CrystalStore(_vault(entries))
    edit = lambda: store.update_entry("crys-001", "Quebec romeo sierra.", keywords=["m", "n", "o"])  # noqa: E731
    merged, _ = await _hold_pass(store, edit)
    assert merged == 0
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-002", "crys-003", "crys-004"]
    assert await store.deduplicate_off_loop() == 2
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-002"]


def _held(store):
    """A pass whose worker waits for ``release``; returns (task, started, release)."""
    started, release = threading.Event(), threading.Event()
    merge_pass = store._merge_pass

    def held(*args):
        started.set()
        release.wait(5)
        return merge_pass(*args)

    store._merge_pass = held
    return asyncio.ensure_future(store.deduplicate_off_loop()), started, release


@pytest.mark.asyncio
async def test_overlapping_passes_keep_the_interval_low_until_the_last_ends():
    # Kyanite on #547 round 3: per-pass save/restore put it back while B ran,
    # then left it at B's saved 0.2 ms for good.
    interval = sys.getswitchinterval()
    a, a_started, a_release = _held(CrystalStore(_vault(_CHAIN)))
    b, b_started, b_release = _held(CrystalStore(_vault(_CHAIN)))
    await asyncio.to_thread(a_started.wait, 5)
    await asyncio.to_thread(b_started.wait, 5)
    a_release.set()
    assert await a == 1
    assert sys.getswitchinterval() <= 0.0002  # B still runs
    b_release.set()
    assert await b == 1
    assert sys.getswitchinterval() == interval


@pytest.mark.asyncio
async def test_a_cancelled_pass_keeps_the_interval_low_while_its_worker_runs():
    interval = sys.getswitchinterval()
    task, started, release = _held(CrystalStore(_vault(_CHAIN)))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sys.getswitchinterval() <= 0.0002  # the worker thread still runs
    release.set()
    for _ in range(500):
        if sys.getswitchinterval() == interval:
            break
        await asyncio.sleep(0.01)
    assert sys.getswitchinterval() == interval
