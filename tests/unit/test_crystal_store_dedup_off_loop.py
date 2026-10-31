"""Dreaming's dedup pass keeps the full pass's merges and runs off the event loop (#546)."""

import asyncio
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
        threads.append(threading.get_ident())
        return merge_pass(*args)

    store._merge_pass = recorded
    assert await store.deduplicate_off_loop() == 1
    assert threads and threads[0] != threading.get_ident()
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
    assert merged == 1  # 005 into 004; 002 changed, so it stays as written
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-002", "crys-004"]
    assert store.get_by_id("crys-002").body == "A new body, edited meanwhile."
    assert await store.deduplicate_off_loop() == 1
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-004"]
