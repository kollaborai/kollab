"""Dreaming's dedup pass (#546): one full ordered pass, on the event loop a few
milliseconds at a time, started over when the store is written to meanwhile."""

import asyncio
import random
import tempfile
import threading
from dataclasses import replace
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


def _vault(entries, root=None) -> Path:
    vault = Path(tempfile.mkdtemp(dir=root))
    blocks = [
        f"---\nid: {entry_id}\ndate: 2026-10-01\nkeywords: {keywords}\nsummary: {summary}\n---\n{summary} body.\n"
        for entry_id, keywords, summary in entries
    ]
    (vault / "crystallized.md").write_text("\n".join(blocks))
    return vault


def _state(store):
    return [(e.id, list(e.keywords), e.summary, e.body) for e in store.get_all()]


def _every_pair(store, entries):
    """The pass before the index (#547 round 6): every later entry, in list order."""
    from plugins.hub.crystal_store import DEDUP_THRESHOLD, SUMMARY_DEDUP_THRESHOLD, _jaccard, token_overlap

    def words(entry):
        return store._words(entry.summary) if entry.summary else frozenset()

    i = 0
    while i < len(entries):
        j = i + 1
        while j < len(entries):
            a, b = entries[i], entries[j]
            if token_overlap(words(a), words(b)) > SUMMARY_DEDUP_THRESHOLD or (
                _jaccard(frozenset(a.keywords), frozenset(b.keywords)) > DEDUP_THRESHOLD
            ):
                store._merge_into(a, entries.pop(j))
            else:
                j += 1
        i += 1


def test_the_indexed_pass_merges_exactly_as_comparing_every_pair(tmp_path):
    # Sunstone on #547 round 6: comparing every pair after each dreaming write
    # took 4 s on 2,609 entries. The index skips pairs that share nothing, and
    # must keep the merges of the full ordered pass, chains included.
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    for seed in range(150):
        rng = random.Random(seed)
        entries = [
            (f"crys-{n:03d}", ", ".join(rng.sample("abcdefgh", rng.randint(1, 3))),
             " ".join(rng.sample(names, rng.randint(2, 5))))
            for n in range(1, 41)
        ]
        indexed, reference = CrystalStore(_vault(entries, tmp_path)), CrystalStore(_vault(entries, tmp_path))
        indexed.deduplicate()
        expected = reference.get_all()
        _every_pair(reference, expected)
        assert _state(indexed) == [(e.id, list(e.keywords), e.summary, e.body) for e in expected], seed


@pytest.mark.asyncio
async def test_a_pass_after_writes_merges_as_comparing_every_pair(tmp_path):
    # Only a pair with an entry written since the last pass can match, so the
    # pass after a dreaming write compares only those; the merges must still be
    # the full ordered pass's.
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    for seed in range(100):
        rng = random.Random(seed)

        def summary():
            return " ".join(rng.sample(names, rng.randint(2, 5)))

        entries = [(f"crys-{n:03d}", ", ".join(rng.sample("abcdefgh", rng.randint(1, 3))), summary())
                   for n in range(1, 31)]
        store = CrystalStore(_vault(entries, tmp_path))
        await store.deduplicate_in_slices(slice_s=1)
        for _ in range(rng.randint(1, 4)):
            if rng.random() < 0.5:
                store.add_entry(summary() + ".", rng.sample("abcdefgh", rng.randint(1, 3)))
            else:
                store.update_entry(rng.choice(store.get_all()).id, summary() + " edited.")
        expected = [replace(e, keywords=list(e.keywords)) for e in store.get_all()]
        _every_pair(store, expected)
        await store.deduplicate_in_slices(slice_s=1)
        assert _state(store) == [(e.id, list(e.keywords), e.summary, e.body) for e in expected], seed


def test_entries_that_share_no_word_or_keyword_are_never_compared(monkeypatch):
    import plugins.hub.crystal_store as crystal_store

    entries = [(f"crys-{n:04d}", f"k{n}, j{n}", f"w{n} x{n} y{n} z{n}") for n in range(1, 2610)]
    store, compared = CrystalStore(_vault(entries)), []
    overlap = crystal_store.token_overlap
    monkeypatch.setattr(crystal_store, "token_overlap", lambda a, b: compared.append(1) or overlap(a, b))
    assert store.deduplicate() == 0
    assert compared == []


@pytest.mark.asyncio
async def test_a_write_from_another_thread_is_never_lost_to_the_pass(monkeypatch):
    # Sunstone on #547 round 6: a thread's add_entry finished after the pass's
    # last check and before its commit, which then dropped it from memory and disk.
    vault = _vault(_CHAIN[:2])  # crys-001 and crys-002 merge
    store, wrote = CrystalStore(vault), []
    store.get_all()
    steps = store._merge_steps

    def steps_then_a_thread_writes(*args):
        yield from steps(*args)
        if not wrote:  # between the pass's last step and its commit
            writer = threading.Thread(
                target=lambda: wrote.append(store.add_entry("Zulu yankee xray whiskey victor.", ["zulu"]))
            )
            writer.start()
            writer.join()

    monkeypatch.setattr(store, "_merge_steps", steps_then_a_thread_writes)
    assert await store.deduplicate_in_slices() == 1
    kept = ["crys-001", wrote[0].id]
    assert [e.id for e in store.get_all()] == kept
    assert [e.id for e in CrystalStore(vault).get_all()] == kept


def test_a_first_load_never_erases_a_write_saved_during_it(monkeypatch):
    # Jadeite on #547 round 7: a first load read the old vault, a writer loaded,
    # added and saved meanwhile, and the old read then replaced its entry.
    vault = _vault(_CHAIN[:2])
    store, wrote = CrystalStore(vault), []
    parse, reading, resume = store._parse_file, threading.Event(), threading.Event()

    def first_parse_pauses():
        entries = parse()
        if not reading.is_set():
            reading.set()
            resume.wait(5)
        return entries

    monkeypatch.setattr(store, "_parse_file", first_parse_pauses)
    loader = threading.Thread(target=store.get_all)
    loader.start()
    assert reading.wait(5)
    writer = threading.Thread(
        target=lambda: wrote.append(store.add_entry("Zulu yankee xray whiskey victor.", ["zulu"]))
    )
    writer.start()
    writer.join(0.2)  # the old code loads again by itself and saves here
    resume.set()
    loader.join(5)
    writer.join(5)
    kept = ["crys-001", "crys-002", wrote[0].id]
    assert [e.id for e in store.get_all()] == kept
    assert [e.id for e in CrystalStore(vault).get_all()] == kept
    assert store._next_id == 4


@pytest.mark.asyncio
async def test_the_dreaming_pass_loads_the_vault_on_the_loop():
    # Howlite on #547 round 8: loaded in a worker thread, the vault kept a
    # reader on the loop waiting behind the load's lock for about 100 ms.
    store, loaded_on = CrystalStore(_vault(_CHAIN)), []
    parse = store._parse_file
    store._parse_file = lambda: (loaded_on.append(threading.current_thread()), parse())[1]
    assert await store.deduplicate_in_slices() == 1
    assert loaded_on == [threading.current_thread()]


def _dreaming_plugin(add_entry):
    """A hub plugin whose dream writes three insights through ``add_entry``."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from plugins.hub.plugin import HubPlugin

    insights = [f"Insight {n}: the relay hands each peer its messages in the order they were sent." for n in range(3)]
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity="lapis", state="idle", current_task=None)
    plugin._vault = MagicMock()
    plugin._presence = None
    plugin._crystal_store = SimpleNamespace(add_entry=add_entry, deduplicate_in_slices=AsyncMock(return_value=0))
    plugin._build_dreaming_prompt = lambda entries: "prompt"
    plugin._dreaming_llm_call = AsyncMock(return_value="\n\n".join(insights))
    return plugin, insights


def test_dreaming_writes_its_insights_in_order_off_the_loop():
    # #562: the batch runs in one worker thread, in order, so the loop is not held.
    writes = []
    plugin, insights = _dreaming_plugin(lambda text: writes.append((text, threading.current_thread())))

    async def dream():
        await plugin._dream([])
        return threading.current_thread()

    loop_thread = asyncio.run(dream())
    assert [text for text, _ in writes] == insights
    assert all(thread is not loop_thread for _, thread in writes)


def test_a_dream_cancelled_during_its_writes_lands_them_before_shutdown_returns():
    # #562: a cancelled dream leaves its worker thread writing, so shutdown waits
    # for the batch and nothing lands after that wait returns.
    import time

    writes = []

    async def main():
        loop = asyncio.get_running_loop()

        def add_entry(text):
            if not writes:
                loop.call_soon_threadsafe(dream.cancel)  # shutdown, as the first write runs
            time.sleep(0.05)
            writes.append(text)

        plugin, _ = _dreaming_plugin(add_entry)
        dream = asyncio.ensure_future(plugin._dream([]))
        await asyncio.gather(dream, return_exceptions=True)
        await plugin._settle_crystal_write()  # what shutdown does next
        settled = len(writes)
        await asyncio.sleep(0.3)  # a write still running would land here
        return settled

    assert asyncio.run(main()) == len(writes) == 3


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
    # A keyword they all share: every pair is compared, and the pass still gives the loop back.
    entries = [(f"crys-{n:03d}", f"all, k{n // 2}, j{n // 2}", f"w{n} x{n} y{n} z{n}") for n in range(1, 300)]
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
    store._merge_steps = lambda entries, *rest: (passes.append(len(entries)), steps(entries, *rest))[1]
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
    store.get_all()  # loaded: the pass starts at once, as the writes expect

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
