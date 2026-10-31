"""deduplicate() compares only entries added or changed since its last pass (#546)."""

import tempfile
from pathlib import Path

from plugins.hub.crystal_store import CrystalStore


def _word(i: int, salt: str) -> str:
    return salt + "".join(chr(97 + (i // 26**k) % 26) for k in range(3))


def _text(i: int) -> str:
    """An entry sharing no word with any other."""
    words = [_word(i, salt) for salt in ("qv", "zx", "jw", "kp", "yh", "fm")]
    return f"**{' '.join(words[:4])}** {' '.join(words[4:])}"


def _loaded_store(n: int) -> CrystalStore:
    vault = Path(tempfile.mkdtemp())
    writer = CrystalStore(vault)
    for i in range(n):
        writer.add_entry(_text(i))
    assert writer.count() == n
    return CrystalStore(vault)  # a restarted agent: everything on disk is checked


def _counting(store: CrystalStore) -> list:
    calls = []
    compare = store._summary_similarity

    def counted(a, b):
        calls.append(1)
        return compare(a, b)

    store._summary_similarity = counted
    return calls


def test_a_pass_compares_only_the_entries_added_since_the_last_one():
    store = _loaded_store(300)
    store.add_entry(_text(1000))
    store.add_entry(_text(1001))
    calls = _counting(store)
    assert store.deduplicate() == 0
    # Each new entry against the other 301; a full pass would be ~45,000.
    assert 0 < len(calls) <= 2 * 301
    calls.clear()
    assert store.deduplicate() == 0 and calls == []  # nothing changed since


def test_an_edited_entry_is_compared_again_and_merged():
    store = _loaded_store(3)
    first, second, _ = store.get_all()
    store.update_entry(second.id, body=first.body, summary=first.summary, keywords=list(first.keywords))
    assert store.deduplicate() == 1
    assert store.count() == 2
    assert store.get_by_id(first.id) is not None  # the earlier entry keeps the merge
