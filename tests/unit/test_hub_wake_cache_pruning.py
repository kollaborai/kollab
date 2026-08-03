"""Wake-dedup caches must keep insertion order aligned with timestamps.

_prune_hub_wake_cache evicts from the front and breaks at the first
non-expired entry. Re-assigning an existing OrderedDict key updates the
value but keeps the original position -- so a refreshed entry at the front
stopped pruning dead, the cache grew without bound, and stale entries
behind it outlived the 120s TTL and silently rejected real messages as
duplicate fingerprints.
"""

import collections

from plugins.hub.plugin import HubPlugin


def _cache(*pairs):
    return collections.OrderedDict(pairs)


def _prune(cache, now, ttl=120.0):
    """Mirror of _prune_hub_wake_cache's per-cache loop."""
    cutoff = now - ttl
    while cache:
        _, ts = next(iter(cache.items()))
        if ts >= cutoff:
            break
        cache.popitem(last=False)


def test_touch_moves_refreshed_key_to_the_end():
    cache = _cache(("a", 100.0), ("b", 200.0))
    HubPlugin._touch_wake_cache(cache, "a", 300.0)
    assert list(cache.keys()) == ["b", "a"]
    assert cache["a"] == 300.0


def test_refresh_in_place_would_stall_pruning():
    """Characterizes the bug: plain assignment leaves the front stale-ordered."""
    cache = _cache(("old", 0.0), ("mid", 1.0))
    cache["old"] = 1000.0  # the old, buggy write
    _prune(cache, now=1000.0)
    assert "mid" in cache, "expired entry survived — pruning stopped at the front"


def test_touch_lets_pruning_reach_expired_entries():
    cache = _cache(("old", 0.0), ("mid", 1.0))
    HubPlugin._touch_wake_cache(cache, "old", 1000.0)
    _prune(cache, now=1000.0)
    assert list(cache.keys()) == ["old"], "expired 'mid' should have been evicted"


def test_cache_does_not_grow_unbounded_under_repeated_refresh():
    """The skip_fingerprint path re-registers the same fingerprint forever.

    The invariant is that size plateaus at the TTL window, not that it hits
    a particular number -- so compare two checkpoints instead of guessing a
    constant.
    """
    cache = _cache()
    now = 0.0
    sizes = {}
    for i in range(500):
        now += 1.0
        HubPlugin._touch_wake_cache(cache, "same-fingerprint", now)
        HubPlugin._touch_wake_cache(cache, f"unique-{i}", now)
        _prune(cache, now=now)
        if i in (249, 499):
            sizes[i] = len(cache)

    assert sizes[249] == sizes[499], (
        f"cache still growing: {sizes[249]} -> {sizes[499]} entries"
    )
    assert sizes[499] < 200, f"plateau of {sizes[499]} is far above the 120s window"


def test_touch_is_a_plain_insert_for_new_keys():
    cache = _cache(("a", 1.0))
    HubPlugin._touch_wake_cache(cache, "b", 2.0)
    assert list(cache.items()) == [("a", 1.0), ("b", 2.0)]
