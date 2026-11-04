"""The crystal summary cache stays bounded as memories change."""

import asyncio
import sys
import threading
from collections import Counter, OrderedDict

import pytest

from plugins.hub import crystal_store
from plugins.hub.text_utils import summary_tokens


def _cached_summary_count(store):
    cache = store._summary_words
    return len(cache) if isinstance(cache, dict) else cache.cache_info().currsize


def test_cache_is_bounded_across_merges_edits_and_deletes(tmp_path):
    store = crystal_store.CrystalStore(tmp_path)

    # Each incoming summary matches the same memory, but is a distinct key.
    for n in range(8292):
        store.add_entry(f"**Shared hub routing memory {n}** Same issue repeated")
    assert store.count() == 1
    # One live memory keeps only the minimum allowance, not every incoming summary (#567).
    assert _cached_summary_count(store) <= 8

    entry = store.get_all()[0]
    store.update_entry(entry.id, body="Updated body", summary="Edited routing memory")
    store.add_entry("**Edited routing memory** Extra detail")
    assert store.count() == 1
    assert store.delete_entry(entry.id) is not None
    assert crystal_store.CrystalStore(tmp_path).count() == 0
    assert _cached_summary_count(store) <= 8


def test_cache_scales_with_the_live_vault_under_pressure(tmp_path):
    entries = [
        crystal_store.CrystalEntry(
            id=f"crys-{n + 1:03d}", date="2026-10-01", keywords=[f"topic{n}"],
            summary=f"memory {n} about routing and sessions", body="body",
        )
        for n in range(100)
    ]
    (tmp_path / "crystallized.md").write_text("\n".join(entry.to_block() for entry in entries))
    store = crystal_store.CrystalStore(tmp_path)
    assert store.count() == 100

    # A scan over the live vault keeps every live summary cached.
    for entry in entries:
        store._words(entry.summary)
    assert _cached_summary_count(store) == 100
    # A power of two at least twice the live vault: 2 * 100 rounds up to 256.
    assert store._summary_words_limit == 256

    # 5,000 transient summaries pass through the same vault and evict its working set.
    for n in range(5000):
        store._words(f"transient probe number {n}")
    assert _cached_summary_count(store) == 256
    assert entries[0].summary not in store._summary_words
    # An evicted live summary is tokenized again, to the same tokens.
    assert store._words(entries[0].summary) == summary_tokens(entries[0].summary)


def test_eviction_recomputes_the_same_tokens_and_caches_are_per_store(tmp_path, monkeypatch):
    calls = []

    def counted_tokens(summary):
        calls.append(summary)
        return summary_tokens(summary)

    monkeypatch.setattr(crystal_store, "summary_tokens", counted_tokens)
    first = crystal_store.CrystalStore(tmp_path)
    second = crystal_store.CrystalStore(tmp_path)
    original = "Routing summaries preserve their matching behavior"
    expected = summary_tokens(original)
    assert first._words(original) == expected
    assert first._words(original) == expected
    assert calls.count(original) == 1

    for n in range(8192):
        first._words(f"A distinct summary number {n}")

    assert _cached_summary_count(first) <= 8
    assert first._words(original) == expected
    assert calls.count(original) == 2
    assert _cached_summary_count(second) == 0


def test_large_live_vault_keeps_its_working_set_and_shrinks_after_delete(tmp_path, monkeypatch):
    calls = []

    def counted_tokens(summary):
        calls.append(summary)
        return summary_tokens(summary)

    monkeypatch.setattr(crystal_store, "summary_tokens", counted_tokens)
    entries = [
        crystal_store.CrystalEntry(
            id=f"crys-{n:05d}", date="2026-10-01", keywords=[f"key{n}"],
            summary=f"Distinct memory summary number {n}", body="body",
        )
        for n in range(8300)
    ]
    (tmp_path / "crystallized.md").write_text("\n".join(entry.to_block() for entry in entries))
    store = crystal_store.CrystalStore(tmp_path)
    assert store.count() == 8300
    store._find_merge_target([], "xylophone thunder")
    first_scan_calls = len(calls)
    store._find_merge_target([], "xylophone thunder")
    assert len(calls) == first_scan_calls

    # Crossing a capacity boundary must also release excess cached summaries.
    smaller_vault = tmp_path / "smaller"
    smaller_vault.mkdir()
    (smaller_vault / "crystallized.md").write_text(
        "\n".join(entry.to_block() for entry in entries[:4097])
    )
    smaller = crystal_store.CrystalStore(smaller_vault)
    assert smaller.count() == 4097
    for n in range(9000):
        smaller._words(f"Previously considered memory {n}")
    assert _cached_summary_count(smaller) == 9000
    smaller.delete_entry(entries[4096].id)
    assert smaller.count() == 4096
    assert _cached_summary_count(smaller) <= 8192



def _resize_vault(tmp_path, count):
    summaries = [
        "amber zephyr maple", "bamboo cobalt radar", "dandelion vector quartz",
        "embers orange willow", "indigo mountain planet", "jigsaw satin violet",
        "krypton magnet thistle", "lantern ocean walnut", "orchid rocket winter",
    ]
    entries = [
        crystal_store.CrystalEntry(
            id=f"crys-{n + 1:03d}", date="2026-10-01", keywords=[f"unique{n}"],
            summary=summaries[n], body="body",
        )
        for n in range(count)
    ]
    (tmp_path / "crystallized.md").write_text("\n".join(entry.to_block() for entry in entries))
    return entries


def _count_tokenizations(monkeypatch):
    counts = Counter()
    lock = threading.Lock()

    def counted_tokens(summary):
        with lock:
            counts[summary] += 1
        return summary_tokens(summary)

    monkeypatch.setattr(crystal_store, "summary_tokens", counted_tokens)
    return counts


def test_growth_keeps_the_scanned_live_working_set(tmp_path, monkeypatch):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 8)
    entries = _resize_vault(tmp_path, 4)
    counts = _count_tokenizations(monkeypatch)
    store = crystal_store.CrystalStore(tmp_path)

    # Four entries fit in a limit of 8; adding the fifth grows it to 16.
    store.add_entry("**quasar tundra nova**")
    assert store.count() == 5
    store.add_entry("**porcupine mercury xylophone**")
    assert store.count() == 6
    assert [counts[e.summary] for e in entries] == [1] * 4
    assert crystal_store.CrystalStore(tmp_path).count() == 6


def test_repeated_boundary_crossings_do_not_retokenize_live_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 8)
    entries = _resize_vault(tmp_path, 4)
    counts = _count_tokenizations(monkeypatch)
    store = crystal_store.CrystalStore(tmp_path)

    for _ in range(8):
        added = store.add_entry("**quasar tundra nova**")
        assert store.count() == 5
        assert store.delete_entry(added.id) is not None
        assert store.count() == 4
    assert [counts[e.summary] for e in entries] == [1] * 4
    assert [e.id for e in crystal_store.CrystalStore(tmp_path).get_all()] == [e.id for e in entries]


def test_sustained_deletions_trim_the_cache_but_keep_recent_live_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 8)
    entries = _resize_vault(tmp_path, 9)
    counts = _count_tokenizations(monkeypatch)
    store = crystal_store.CrystalStore(tmp_path)
    assert store.count() == 9
    for n in range(20):
        store._words(f"retired summary {n}")
    for entry in entries:
        store._words(entry.summary)
    assert _cached_summary_count(store) == 29

    for entry in entries[:6]:
        assert store.delete_entry(entry.id) is not None
        # Power-of-two sizing is bounded by four times the live vault,
        # subject to the minimum allowance for transient summaries.
        assert _cached_summary_count(store) <= max(8, 4 * store.count())
    assert store.count() == 3
    assert _cached_summary_count(store) <= 8
    for entry in entries[6:]:
        assert store._words(entry.summary) == summary_tokens(entry.summary)
        assert counts[entry.summary] == 1
    assert [e.id for e in crystal_store.CrystalStore(tmp_path).get_all()] == [e.id for e in entries[6:]]


@pytest.mark.asyncio
async def test_sliced_pass_overlaps_a_writer_and_preserves_hot_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 8)
    entries = _resize_vault(tmp_path, 4)
    entries[0].keywords = ["a", "b", "c"]
    entries[1].keywords = ["a", "b", "d"]
    (tmp_path / "crystallized.md").write_text("\n".join(e.to_block() for e in entries))

    counts = Counter()
    counts_lock = threading.Lock()
    reading, writer_done = threading.Event(), threading.Event()
    loop_thread = threading.current_thread()

    def paused_tokens(summary):
        with counts_lock:
            counts[summary] += 1
        if summary == entries[0].summary and threading.current_thread() is loop_thread and not reading.is_set():
            reading.set()
            assert writer_done.wait(5), "writer blocked while the pass tokenized"
        return summary_tokens(summary)

    monkeypatch.setattr(crystal_store, "summary_tokens", paused_tokens)
    store = crystal_store.CrystalStore(tmp_path)
    assert store.count() == 4
    wrote, errors = [], []

    def write_during_pass():
        try:
            assert reading.wait(5), "pass did not reach tokenization"
            wrote.append(store.add_entry("**zulu yankee xray whiskey**"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer_done.set()

    writer = threading.Thread(target=write_during_pass, daemon=True)
    writer.start()
    merged = await store.deduplicate_in_slices(slice_s=0)
    writer.join(5)
    assert not writer.is_alive()
    assert errors == []
    assert merged == 1
    assert [e.id for e in store.get_all()] == [entries[0].id, entries[2].id, entries[3].id, wrote[0].id]
    assert [e.id for e in crystal_store.CrystalStore(tmp_path).get_all()] == [e.id for e in store.get_all()]
    # The first summary has two concurrent misses; all other live summaries
    # are tokenized just once even though the writer caused growth and a retry.
    assert counts[entries[0].summary] == 2
    assert [counts[e.summary] for e in entries[1:]] == [1] * 3



def _bulk_cache_vault(tmp_path, count=2049):
    # Each group of 16 merges by keyword. Summaries are unique, so the pass
    # naturally fills the cache with the larger pre-dedup working set.
    entries = [
        crystal_store.CrystalEntry(
            id=f"crys-{n + 1:05d}", date="2026-10-01", keywords=[f"group_{n // 16}"],
            summary=f"amber_{n} bamboo_{n} cobalt_{n} delta_{n}", body="body",
        )
        for n in range(count)
    ]
    (tmp_path / "crystallized.md").write_text("\n".join(e.to_block() for e in entries))
    return crystal_store.CrystalStore(tmp_path)


def _assert_cache_container_compact(store):
    retained = sys.getsizeof(store._summary_words)
    compact = sys.getsizeof(OrderedDict(store._summary_words))
    assert retained <= 2 * compact, f"retained={retained}, compact={compact}"


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython container sizing")
@pytest.mark.parametrize("sliced", [False, True], ids=["synchronous", "sliced"])
def test_bulk_dedup_releases_cache_backing_storage(tmp_path, monkeypatch, sliced):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 512)
    store = _bulk_cache_vault(tmp_path)
    assert store.count() == 2049
    assert store._summary_words_limit == 8192
    resize = store._resize_summary_cache
    observed = []

    def observe_resize():
        with store._summary_words_lock:
            previous = list(store._summary_words.items())
        resize()
        with store._summary_words_lock:
            observed.append((previous[-store._summary_words_limit:], list(store._summary_words.items())))

    monkeypatch.setattr(store, "_resize_summary_cache", observe_resize)
    merged = asyncio.run(store.deduplicate_in_slices(slice_s=0)) if sliced else store.deduplicate()
    assert merged == 1920
    assert store.count() == 129
    assert store._summary_words_limit == len(store._summary_words) == 512
    assert observed and observed[-1][0] == observed[-1][1]  # LRU order and tokens survived
    _assert_cache_container_compact(store)
    cached_summary, expected = next(reversed(store._summary_words.items()))
    monkeypatch.setattr(crystal_store, "summary_tokens", lambda _: pytest.fail("hot summary retokenized"))
    assert store._words(cached_summary) == expected
    assert crystal_store.CrystalStore(tmp_path).count() == 129


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython container sizing")
def test_successive_small_shrinks_compact_again_from_the_last_high_water(tmp_path, monkeypatch):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 64)
    store = _bulk_cache_vault(tmp_path)
    assert store.deduplicate() == 1920
    assert store.count() == 129
    assert store._summary_words_limit == len(store._summary_words) == 512

    # Two capacity halvings occur through ordinary deletions: 512 -> 256 -> 128.
    # The second cumulative shrink must compact again after the bulk compaction.
    for entry in store.get_all()[:65]:
        assert store.delete_entry(entry.id)
    assert store.count() == 64
    assert store._summary_words_limit == len(store._summary_words) == 128
    _assert_cache_container_compact(store)
    assert crystal_store.CrystalStore(tmp_path).count() == 64


def test_small_live_boundary_oscillations_never_rebuild_the_cache(tmp_path, monkeypatch):
    entries = [
        crystal_store.CrystalEntry(
            id=f"crys-{n + 1:05d}", date="2026-10-01", keywords=[f"unique_{n}"],
            summary=f"amber_{n} bamboo_{n} cobalt_{n} delta_{n}", body="body",
        )
        for n in range(8192)
    ]
    (tmp_path / "crystallized.md").write_text("\n".join(e.to_block() for e in entries))
    counts = _count_tokenizations(monkeypatch)
    store = crystal_store.CrystalStore(tmp_path)
    assert store.count() == 8192
    cache = store._summary_words

    for _ in range(3):
        added = store.add_entry("**quasar tundra nova**")
        assert store.count() == 8193
        assert store._summary_words is cache
        assert store.delete_entry(added.id)
        assert store.count() == 8192
        assert store._summary_words is cache
    assert counts[entries[0].summary] == 1
    assert counts[entries[-1].summary] == 1
    assert crystal_store.CrystalStore(tmp_path).count() == 8192



def test_repeated_growth_then_cumulative_shrink_keeps_hot_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 8)
    entries = _resize_vault(tmp_path, 9)
    counts = _count_tokenizations(monkeypatch)
    store = crystal_store.CrystalStore(tmp_path)
    assert store.count() == 9
    for n in range(30):
        store._words(f"retired_{n}")
    for entry in entries[-2:]:
        store._words(entry.summary)
    original_cache = store._summary_words
    original_items = list(original_cache.items())
    for entry in entries[:7]:
        assert store.delete_entry(entry.id)
    assert store._summary_words_limit == 8
    assert store._summary_words is not original_cache
    assert list(store._summary_words.items()) == original_items[-8:]

    for cycle in range(3):
        old_cache = store._summary_words
        added = [store.add_entry(f"**vector_{cycle}_{n} spiral_{cycle}_{n} nylon_{cycle}_{n}**")
                 for n in range(7)]
        assert store.count() == 9
        assert store._summary_words_limit == 32
        assert store._summary_words is old_cache
        marker = f"hot_marker_{cycle}"
        expected = store._words(marker)
        before_shrink = list(store._summary_words.items())
        for entry in added:
            assert store.delete_entry(entry.id)
        assert store.count() == 2
        assert store._summary_words_limit == 8
        assert store._summary_words is not old_cache
        assert list(store._summary_words.items()) == before_shrink[-8:]
        assert store._words(marker) == expected
        assert counts[marker] == 1



@pytest.mark.asyncio
async def test_writer_compacts_while_sliced_pass_is_tokenizing(tmp_path, monkeypatch):
    monkeypatch.setattr(crystal_store, "SUMMARY_WORD_CACHE_MIN_SIZE", 8)
    entries = _resize_vault(tmp_path, 9)
    reading, writer_done = threading.Event(), threading.Event()
    loop_thread = threading.current_thread()

    def paused_tokens(summary):
        if summary == entries[0].summary and threading.current_thread() is loop_thread and not reading.is_set():
            reading.set()
            assert writer_done.wait(5), "writer blocked during tokenization"
        return summary_tokens(summary)

    monkeypatch.setattr(crystal_store, "summary_tokens", paused_tokens)
    store = crystal_store.CrystalStore(tmp_path)
    assert store.count() == 9
    for n in range(30):
        store._words(f"retired_{n}")
    old_cache = store._summary_words
    rebuilt, errors = [], []

    def delete_during_pass():
        try:
            assert reading.wait(5), "pass did not start tokenization"
            for entry in entries[:7]:
                assert store.delete_entry(entry.id)
            rebuilt.append(store._summary_words is not old_cache)
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer_done.set()

    writer = threading.Thread(target=delete_during_pass, daemon=True)
    writer.start()
    assert await store.deduplicate_in_slices(slice_s=0) == 0
    writer.join(5)
    assert not writer.is_alive()
    assert errors == []
    assert rebuilt == [True]
    assert store._summary_words_limit == 8
    assert len(store._summary_words) <= 8
    assert [e.id for e in store.get_all()] == [e.id for e in entries[-2:]]
    assert [e.id for e in crystal_store.CrystalStore(tmp_path).get_all()] == [e.id for e in entries[-2:]]
