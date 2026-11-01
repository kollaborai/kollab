"""Dreaming's dedup pass (#546): one full ordered pass, on the event loop a few
milliseconds at a time, started over when the store is written to meanwhile."""

import asyncio
import tempfile
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


def _state(store):
    return [(e.id, list(e.keywords), e.summary, e.body) for e in store.get_all()]


def test_a_chain_merges_in_list_order():
    store = CrystalStore(_vault(_CHAIN))
    assert store.deduplicate() == 1
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-003"]


def test_duplicates_already_on_disk_merge_after_a_restart():
    vault = _vault(_CHAIN[:2])  # saved by an agent that stopped before its next pass
    assert CrystalStore(vault).deduplicate() == 1
    assert [e.id for e in CrystalStore(vault).get_all()] == ["crys-001"]


@pytest.mark.asyncio
async def test_the_dreaming_pass_gives_the_loop_back_and_merges_as_a_full_pass():
    # #546: run whole on the loop, a pass over 2,608 entries took 105 s, the agent
    # answered nothing and peers deleted its socket as dead.
    entries = [(f"crys-{n:03d}", f"k{n // 2}, j{n // 2}", f"w{n} x{n} y{n} z{n}") for n in range(1, 300)]
    whole = CrystalStore(_vault(entries))
    assert whole.deduplicate() > 100
    store, ticks = CrystalStore(_vault(entries)), 0

    async def other_work():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(other_work())
    merged = await store.deduplicate_in_slices(slice_s=0)
    task.cancel()
    assert merged == len(entries) - len(whole.get_all()) and _state(store) == _state(whole)
    assert ticks > 10  # the loop ran other work during the pass
    assert _state(CrystalStore(store._path.parent)) == _state(whole)  # and it was saved


@pytest.mark.asyncio
async def test_a_store_unchanged_since_a_pass_found_nothing_skips_the_next():
    store, passes = CrystalStore(_vault(_CHAIN)), []
    steps = store._merge_steps
    store._merge_steps = lambda entries: (passes.append(len(entries)), steps(entries))[1]
    assert await store.deduplicate_in_slices(slice_s=0) == 1
    assert await store.deduplicate_in_slices(slice_s=0) == 0  # the merge was a write: another pass
    assert await store.deduplicate_in_slices(slice_s=0) == 0 and passes == [3, 2]  # nothing since
    store.update_entry("crys-003", body="a write")
    assert await store.deduplicate_in_slices(slice_s=0) == 0 and passes == [3, 2, 2]


@pytest.mark.asyncio
async def test_a_write_during_the_pass_starts_it_over_on_the_entries_as_they_are_now():
    # Morganite on #547 round 5: an entry edited during a pass sat out the next
    # one, so 002 never merged into 001 as a full pass merges it.
    entries = _CHAIN + [("crys-004", "a, d, e, x, y", "mike november oscar papa")]
    assert CrystalStore(_vault(entries)).deduplicate() == 2  # before the edit: 002 into 001, 004 into 003
    store = CrystalStore(_vault(entries))
    body = "echo foxtrot golf hotel, now about something else"

    async def edit():  # runs when the pass first gives the loop back
        store.update_entry("crys-002", body=body)

    task = asyncio.create_task(edit())
    merged = await store.deduplicate_in_slices(slice_s=0)
    await task
    reference = CrystalStore(_vault(entries))
    reference.update_entry("crys-002", body=body)
    # The edit gave 002 new keywords: a full pass over the entries as they are
    # now merges only 004 into 003, and the edit is kept.
    assert merged == reference.deduplicate() == 1
    assert _state(store) == _state(reference)


@pytest.mark.asyncio
async def test_writes_during_every_pass_leave_the_entries_whole_for_the_next_cycle():
    # Morganite on #547 round 5: edits during every pass held back an unrelated
    # merge for good. Now no pass applies half its merges: one that sees no
    # write merges them all.
    store = CrystalStore(_vault(_CHAIN))

    async def write_every_pass():
        for n in range(3):
            store.update_entry("crys-003", body=f"edit {n}")
            await asyncio.sleep(0)

    task = asyncio.create_task(write_every_pass())
    assert await store.deduplicate_in_slices(slice_s=0, attempts=3) == 0
    await task
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-002", "crys-003"]
    assert await store.deduplicate_in_slices(slice_s=0) == 1
    assert [e.id for e in store.get_all()] == ["crys-001", "crys-003"]
