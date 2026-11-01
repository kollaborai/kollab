"""The crystal summary cache stays bounded as memories change."""

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
    assert _cached_summary_count(store) <= 8192

    entry = store.get_all()[0]
    store.update_entry(entry.id, body="Updated body", summary="Edited routing memory")
    store.add_entry("**Edited routing memory** Extra detail")
    assert store.count() == 1
    assert store.delete_entry(entry.id) is not None
    assert crystal_store.CrystalStore(tmp_path).count() == 0
    assert _cached_summary_count(store) <= 8192


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

    assert _cached_summary_count(first) <= 8192
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
