"""Unit tests for the agent notification system (env queue).

Covers:
  - SYMBOLS table lookup
  - push / drain / peek / clear / size semantics
  - collapse_key dedup increments count + timestamp
  - max_size eviction rebuilds collapse index
  - render_env_block formats flat list with counts
  - empty render returns empty string
"""

from kollabor_ai.notifications import (
    SYMBOLS,
    EnvEvent,
    EnvKind,
    EnvQueue,
    render_env_block,
)


def _mk(kind: EnvKind, symbol_key: str, message: str, collapse_key=None) -> EnvEvent:
    return EnvEvent(
        kind=kind,
        symbol=SYMBOLS[symbol_key],
        message=message,
        collapse_key=collapse_key,
    )


def test_symbols_table_covers_spec():
    expected = {
        "capability",
        "joined",
        "changed",
        "file",
        "task",
        "action",
        "message",
        "external",
    }
    assert set(SYMBOLS.keys()) == expected


def test_push_and_drain():
    q = EnvQueue()
    q.push(_mk(EnvKind.PEER_ONLINE, "joined", "lapis joined"))
    q.push(_mk(EnvKind.PEER_STATE, "changed", "lapis -> waiting"))
    assert q.size() == 2

    drained = q.drain()
    assert len(drained) == 2
    assert q.size() == 0
    assert q.drain() == []


def test_peek_does_not_clear():
    q = EnvQueue()
    q.push(_mk(EnvKind.MESSAGE, "message", "hi"))
    snap = q.peek()
    assert len(snap) == 1
    assert q.size() == 1


def test_clear_returns_count():
    q = EnvQueue()
    q.push(_mk(EnvKind.PEER_ONLINE, "joined", "a"))
    q.push(_mk(EnvKind.PEER_ONLINE, "joined", "b"))
    q.push(_mk(EnvKind.PEER_ONLINE, "joined", "c"))
    assert q.clear() == 3
    assert q.size() == 0


def test_collapse_key_deduplicates():
    q = EnvQueue()
    for _ in range(4):
        q.push(
            _mk(
                EnvKind.FILE_CHANGED,
                "file",
                "plugin.py (by lapis)",
                collapse_key="file:plugin.py",
            )
        )
    drained = q.drain()
    assert len(drained) == 1
    assert drained[0].count == 4


def test_collapse_does_not_cross_keys():
    q = EnvQueue()
    q.push(_mk(EnvKind.FILE_CHANGED, "file", "a.py", collapse_key="file:a.py"))
    q.push(_mk(EnvKind.FILE_CHANGED, "file", "b.py", collapse_key="file:b.py"))
    q.push(_mk(EnvKind.FILE_CHANGED, "file", "a.py", collapse_key="file:a.py"))
    drained = q.drain()
    assert len(drained) == 2
    counts = sorted(e.count for e in drained)
    assert counts == [1, 2]


def test_max_size_evicts_oldest_and_preserves_collapse_index():
    q = EnvQueue(max_size=3)
    for i in range(5):
        q.push(
            _mk(
                EnvKind.MESSAGE,
                "message",
                f"msg-{i}",
                collapse_key=f"m:{i}",
            )
        )
    snap = q.peek()
    # oldest two evicted, newest three retained
    assert [e.message for e in snap] == ["msg-2", "msg-3", "msg-4"]

    # collapse index still works after rebuild
    q.push(_mk(EnvKind.MESSAGE, "message", "msg-4", collapse_key="m:4"))
    snap = q.peek()
    assert len(snap) == 3
    assert snap[-1].count == 2


def test_render_empty_returns_empty_string():
    assert render_env_block([]) == ""


def test_render_single_event():
    events = [_mk(EnvKind.PEER_ONLINE, "joined", "peridot joined")]
    out = render_env_block(events)
    assert out.splitlines()[0] == "[env: 1 event]"
    assert "+ peridot joined" in out


class _FakeBus:
    def __init__(self, queue=None):
        self._queue = queue

    def get_service(self, name):
        return self._queue if name == "env_queue" else None


def test_producer_no_bus_is_noop():
    from kollabor_ai.notifications.producer import push_env

    # must not raise
    push_env(None, "joined", "x")


def test_producer_missing_queue_is_noop():
    from kollabor_ai.notifications.producer import push_env

    bus = _FakeBus(queue=None)
    push_env(bus, "joined", "x")  # no exception


def test_producer_pushes_when_queue_present():
    from kollabor_ai.notifications.producer import push_env

    q = EnvQueue()
    bus = _FakeBus(queue=q)
    push_env(bus, "joined", "lapis joined", kind="peer_online")
    events = q.drain()
    assert len(events) == 1
    assert events[0].kind == EnvKind.PEER_ONLINE
    assert events[0].symbol == SYMBOLS["joined"]


def test_producer_pushes_tool_grant():
    from kollabor_ai.notifications.producer import push_env

    q = EnvQueue()
    bus = _FakeBus(queue=q)
    push_env(bus, "capability", "+tool:file-read", kind="tool_grant")
    events = q.drain()
    assert len(events) == 1
    assert events[0].kind == EnvKind.TOOL_GRANT
    assert events[0].symbol == SYMBOLS["capability"]
    assert events[0].message == "+tool:file-read"


def test_producer_pushes_tool_revoke():
    from kollabor_ai.notifications.producer import push_env

    q = EnvQueue()
    bus = _FakeBus(queue=q)
    push_env(bus, "capability", "-tool:file-read", kind="tool_revoke")
    events = q.drain()
    assert len(events) == 1
    assert events[0].kind == EnvKind.TOOL_REVOKE
    assert events[0].symbol == SYMBOLS["capability"]
    assert events[0].message == "-tool:file-read"


def test_notifications_tag_patterns_do_not_collide():
    """The clear pattern handles the clear variant, query handles bare."""
    import re

    clear_pat = re.compile(r"<notifications\s+clear\s*/>", re.IGNORECASE)
    query_pat = re.compile(r"<notifications\s*/>", re.IGNORECASE)

    clear_sample = "<notifications clear/>"
    query_sample = "<notifications/>"

    assert clear_pat.search(clear_sample) is not None
    assert query_pat.search(clear_sample) is None
    assert clear_pat.search(query_sample) is None
    assert query_pat.search(query_sample) is not None


def test_producer_unknown_symbol_or_kind_is_noop():
    from kollabor_ai.notifications.producer import push_env

    q = EnvQueue()
    bus = _FakeBus(queue=q)
    push_env(bus, "unknown_symbol", "x")
    push_env(bus, "joined", "x", kind="unknown_kind")
    assert q.size() == 0


def test_render_multiple_events_with_count():
    events = [
        _mk(EnvKind.PERMISSION, "capability", "trust:full (was confirm_all)"),
        _mk(
            EnvKind.FILE_CHANGED,
            "file",
            "plugin.py (by lapis)",
            collapse_key="file:plugin.py",
        ),
    ]
    events[1].count = 3
    out = render_env_block(events)
    lines = out.splitlines()
    assert lines[0] == "[env: 2 events]"
    assert lines[1] == f"  {SYMBOLS['capability']} trust:full (was confirm_all)"
    assert lines[2] == f"  {SYMBOLS['file']} plugin.py (by lapis) x3"
