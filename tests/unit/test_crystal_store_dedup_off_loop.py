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

    def recorded(entries):
        threads.append(threading.get_ident())
        return merge_pass(entries)

    store._merge_pass = recorded
    assert await store.deduplicate_off_loop() == 1
    assert threads and threads[0] != threading.get_ident()
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-003"]
    assert [e.id for e in CrystalStore(store._path.parent).get_all()] == ["crys-001", "crys-003"]


@pytest.mark.asyncio
async def test_an_entry_added_during_the_pass_is_kept_and_the_merge_waits():
    store = CrystalStore(_vault(_CHAIN[:2]))
    started, release = threading.Event(), threading.Event()
    merge_pass = store._merge_pass

    def held(entries):
        started.set()
        release.wait(5)
        return merge_pass(entries)

    store._merge_pass = held
    task = asyncio.ensure_future(store.deduplicate_off_loop())
    await asyncio.to_thread(started.wait, 5)
    added = store.add_entry("**Zulu yankee xray whiskey** victor uniform tango sierra.")
    release.set()
    assert await task == 0
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-002", added.id]

    store._merge_pass = merge_pass
    assert await store.deduplicate_off_loop() == 1
    assert [e.id for e in store.get_all()] == ["crys-001", added.id]
