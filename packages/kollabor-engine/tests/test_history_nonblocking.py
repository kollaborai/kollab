"""Opening a session's conversation reads this disk, never the daemon's RPC.

The engine answers /history from its warm mirror, or -- cold -- from the
conversation files the daemon has already written here, and reconciles with
the daemon in the background afterward. A busy or far agent cannot keep the
web UI waiting, and the reconciliation result arrives as history_updated.
"""

import asyncio
import json
import pathlib
import time
from types import SimpleNamespace

import kollabor_engine.session as session_module
import pytest
from kollabor_engine.history_store import read_history_from_disk
from kollabor_engine.routes import sessions as sessions_route
from kollabor_engine.server import get_session_registry
from kollabor_engine.session import EngineSession


class SilentState:
    """A daemon that never answers get_conversation (the busy-agent case)."""

    def get_permission_state(self):
        return SimpleNamespace(approval_mode="CONFIRM_ALL")

    def get_active_profile(self):
        return SimpleNamespace(name="openai-oauth", model="gpt", provider="openai")

    def get_system_info(self):
        return SimpleNamespace(cwd="/nowhere")

    async def get_conversation(self, since=None, anchor=None):
        await asyncio.sleep(3600)


def _daemon(state):
    events: asyncio.Queue = asyncio.Queue()
    return SimpleNamespace(
        alive=True,
        pid=0,
        identity="lapis",
        agent_name="",
        state=state,
        subscribe=lambda: events,
        unsubscribe=lambda queue: None,
        fan_out=lambda event: None,
    )


@pytest.fixture
def registry():
    sessions = get_session_registry()
    before = dict(sessions)
    sessions.clear()
    yield sessions
    sessions.clear()
    sessions.update(before)


@pytest.fixture
def conversations(tmp_path, monkeypatch):
    """A workspace whose conversations folder holds one daemon-written log.

    The workspace itself must exist (EngineSession validates it); the
    project-data mapping points at it so the daemon's log lands in the
    folder the engine would read on a real machine.
    """
    workspace = tmp_path / "proj"
    workspace.mkdir()
    conversations_dir = workspace / "conversations"
    conversations_dir.mkdir()
    import kollabor_config.config_utils as config_utils

    monkeypatch.setattr(
        config_utils,
        "get_config_directory",
        lambda: workspace.parent,  # <parent>/.kollab-style root -> projects/
    )
    # get_project_data_dir builds <config_root>/projects/<encoded>/conversations;
    # point "encoded" at our prepared conversations dir instead: patch one
    # level up is enough -- encode the workspace to a name we create.
    encoded = config_utils.encode_project_path(pathlib.Path(workspace))
    projects_dir = workspace.parent / "projects" / encoded
    projects_dir.mkdir(parents=True, exist_ok=True)
    if conversations_dir.exists():  # move the prepared dir under the real root
        for child in conversations_dir.iterdir():
            (projects_dir / "conversations" / child.name).parent.mkdir(exist_ok=True)
            child.rename(projects_dir / "conversations" / child.name)
        conversations_dir.rmdir()
    (projects_dir / "conversations").mkdir(exist_ok=True)
    return projects_dir / "conversations", str(workspace)


def _write_jsonl(path, messages, session_id="conv-1"):
    with path.open("w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(
                json.dumps(
                    {
                        "type": message["role"],
                        "message": message,
                        "timestamp": message.get("timestamp", ""),
                        "sessionId": session_id,
                    }
                )
                + "\n"
            )


@pytest.mark.asyncio
async def test_cold_open_seeds_from_disk_without_the_daemon(registry, conversations):
    """A busy daemon + cold mirror: /history answers from disk, sub-second."""
    conversations_dir, workspace_root = conversations
    _write_jsonl(
        conversations_dir / "conv-1.jsonl",
        [
            {"role": "user", "content": "hello from disk", "timestamp": "t1"},
            {"role": "assistant", "content": "hi from disk", "timestamp": "t2"},
        ],
    )
    state = SilentState()
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.conversation_id = "conv-1"  # discovery supplies this validated log id
    session.created_at = None
    session.daemon = _daemon(state)
    session._event_task = None  # no event monitoring in this test
    registry[session.session_id] = session

    started = time.monotonic()
    result = await asyncio.wait_for(
        sessions_route.get_history(session.session_id), timeout=2
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert [m["content"] for m in result["history"]] == [
        "hello from disk",
        "hi from disk",
    ]
    assert result["source"] == "disk" and result["stale"] is True
    assert state is session.daemon.state


@pytest.mark.asyncio
async def test_background_refresh_reconciles_and_emits_history_updated(
    registry, conversations
):
    """The disk seed is marked stale; the later refresh answers and emits."""
    conversations_dir, workspace_root = conversations
    _write_jsonl(
        conversations_dir / "conv-1.jsonl",
        [{"role": "user", "content": "seed", "timestamp": "t1"}],
    )
    state = SilentState()

    async def get_conversation(since=None, anchor=None):
        # MessageDto.to_dict() wire shape, as the real state service returns.
        return SimpleNamespace(
            messages=[
                {
                    "role": "user",
                    "content": "seed",
                    "timestamp": "t1",
                    "metadata": {},
                    "thinking": None,
                },
                {
                    "role": "assistant",
                    "content": "from daemon",
                    "timestamp": "t2",
                    "metadata": {},
                    "thinking": None,
                },
            ],
            session_id="conv-1",
            started_at="t0",
            message_count=2,
            metadata={},
            anchor="",
            since=None,
        )

    state.get_conversation = get_conversation
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.conversation_id = "conv-1"
    session.created_at = None
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session

    emitted: asyncio.Queue = asyncio.Queue()

    def fan_out(event):
        emitted.put_nowait(event)

    session.daemon.fan_out = fan_out

    result = await asyncio.wait_for(
        sessions_route.get_history(session.session_id), timeout=2
    )
    assert result["source"] == "disk"

    event = await asyncio.wait_for(emitted.get(), timeout=2)
    assert event["type"] == "history_updated"
    # The refresh is done only when its task handle is cleared -- the event
    # is emitted in the same finally, so by now the daemon's answer has
    # replaced the seed wholesale.
    assert session._history_refresh_task is None
    assert [m["content"] for m in session.history] == ["seed", "from daemon"]
    assert session.conversation_id == "conv-1"


@pytest.mark.asyncio
async def test_warm_mirror_answers_without_disk_or_daemon(registry, conversations):
    """A warm mirror is authoritative: no disk read, and still never blocks."""
    _, workspace_root = conversations
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.history = [{"role": "assistant", "content": "warm"}]
    session.daemon = _daemon(SilentState())
    session._event_task = None
    registry[session.session_id] = session

    result = await asyncio.wait_for(
        sessions_route.get_history(session.session_id), timeout=2
    )
    assert result["history"] == [{"role": "assistant", "content": "warm"}]
    assert result["source"] == "mirror" and result["stale"] is False


def test_large_jsonl_reads_only_a_tail_window(tmp_path):
    """A 2 MB log with a small limit reads a bounded window, not the file."""
    path = tmp_path / "big.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        filler = "x" * 512
        for index in range(4000):  # ~2 MB of old, far-past messages
            handle.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": f"old-{index}-{filler}"},
                        "timestamp": "",
                    }
                )
                + "\n"
            )
        for index in range(250):  # the recent tail a UI asks for
            handle.write(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": f"new-{index}"},
                        "timestamp": "",
                    }
                )
                + "\n"
            )

    messages = read_history_from_disk(tmp_path, "big", limit=200)
    assert len(messages) == 200
    assert messages[-1]["content"] == "new-249"
    assert all(not m["content"].startswith("old-") for m in messages)


def test_snapshot_fallback_when_no_jsonl(tmp_path):
    """A resumed conversation with only a snapshot still opens from disk."""
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "conv-2_snapshot.json").write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "from snapshot"},
                    {"role": "tool", "content": "not a chat message"},
                ]
            }
        ),
        encoding="utf-8",
    )
    messages = read_history_from_disk(tmp_path, "conv-2")
    assert messages == [
        {
            "role": "user",
            "content": "from snapshot",
            "timestamp": "",
            "metadata": {},
            "thinking": None,
        }
    ]


def test_torn_trailing_line_is_dropped_not_fatal(tmp_path):
    """A log written mid-append opens: the torn last line is skipped."""
    path = tmp_path / "torn.jsonl"
    good = json.dumps(
        {
            "type": "user",
            "message": {"role": "user", "content": "whole"},
            "timestamp": "",
        }
    )
    path.write_text(
        good + "\n" + '{"type":"user","message":{"role":"user","cont', encoding="utf-8"
    )
    messages = read_history_from_disk(tmp_path, "torn")
    assert [m["content"] for m in messages] == ["whole"]


@pytest.mark.asyncio
async def test_coalesced_refresh_single_task(registry, conversations):
    """Many opens during one refresh share the task instead of stacking."""
    _, workspace_root = conversations
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(SilentState())
    session._event_task = None
    registry[session.session_id] = session

    started: asyncio.Event = asyncio.Event()

    async def get_conversation(since=None, anchor=None):
        await started.wait()
        from types import SimpleNamespace as NS

        return NS(
            messages=[],
            session_id="",
            started_at="",
            message_count=0,
            metadata={},
            anchor="",
        )

    session.daemon.state.get_conversation = get_conversation

    first = session._schedule_refresh()
    await asyncio.sleep(0)  # let the task start
    running = session._history_refresh_task
    assert running is not None
    # A second and third request coalesce onto the same task.
    session._schedule_refresh()
    session._schedule_refresh()
    assert session._history_refresh_task is running
    started.set()
    await asyncio.wait_for(running, timeout=2)
    assert session._history_refresh_task is None
    assert first is None



class GrowingState(SilentState):
    """A daemon that answers get_conversation from a conversation that grows."""

    def __init__(self):
        self.messages = [_wire("assistant", "hello", "t0")]
        self.calls = 0

    async def get_conversation(self, since=None, anchor=None):
        self.calls += 1
        return SimpleNamespace(
            messages=list(self.messages),
            session_id="conv-1",
            started_at="t0",
            message_count=len(self.messages),
            metadata={},
            anchor="",
            since=None,
        )


def _wire(role, content, timestamp):
    return {
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "metadata": {},
        "thinking": None,
    }


async def _settled(session):
    """The coalesced refresh has finished (its handle is cleared in its finally)."""
    for _ in range(200):
        if session._history_refresh_task is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the background refresh never finished")


@pytest.mark.asyncio
async def test_a_user_message_taken_by_the_daemon_warms_the_mirror(
    registry, conversations
):
    """A window that opens mid-turn finds the message being answered.

    Whoever sent it (another window, the agent's terminal), the daemon announces
    it with user_message; the mirror follows without telling the open windows.
    """
    _, workspace_root = conversations
    state = GrowingState()
    events: asyncio.Queue = asyncio.Queue()
    announced: list = []
    daemon = _daemon(state)
    daemon.subscribe = lambda: events
    daemon.fan_out = announced.append
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = daemon
    registry[session.session_id] = session
    await session.refresh_history()  # the warm mirror: one greeting
    session._event_task = asyncio.create_task(session._track_events())
    try:
        state.messages.append(_wire("user", "slowreply please", "t1"))
        events.put_nowait({"type": "user_message"})
        for _ in range(200):
            if len(session.history) == 2:
                break
            await asyncio.sleep(0.01)
        await _settled(session)

        result = await sessions_route.get_history(session.session_id)
        assert [m["content"] for m in result["history"]] == [
            "hello",
            "slowreply please",
        ]
        assert result["source"] == "mirror" and result["stale"] is False
        assert [e for e in announced if e["type"] == "history_updated"] == []
    finally:
        session._event_task.cancel()
        await asyncio.gather(session._event_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_turn_complete_announces_rows_found_after_an_inflight_stale_refresh(
    registry, conversations
):
    """A terminal's final refresh must notify even if a prior background read was stale."""
    _, workspace_root = conversations
    state = GrowingState()
    state.messages = [_wire("user", "before", "t0")]
    release_old_snapshot = asyncio.Event()
    old_snapshot_started = asyncio.Event()
    initial = list(state.messages)
    calls = 0

    async def get_conversation(since=None, anchor=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            old_snapshot_started.set()
            await release_old_snapshot.wait()
            messages = initial
        else:
            messages = list(state.messages)
        return SimpleNamespace(
            messages=messages,
            session_id="conv-1",
            started_at="t0",
            message_count=len(messages),
            metadata={},
            anchor="",
            since=None,
        )

    state.get_conversation = get_conversation
    events: asyncio.Queue = asyncio.Queue()
    announced: list = []
    daemon = _daemon(state)
    daemon.subscribe = lambda: events
    daemon.fan_out = announced.append
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = daemon
    registry[session.session_id] = session
    await session.refresh_history()
    session._event_task = asyncio.create_task(session._track_events())
    try:
        events.put_nowait({"type": "user_message"})
        await asyncio.wait_for(old_snapshot_started.wait(), timeout=2)
        state.messages.extend(
            [
                _wire("user", "spoken transcript", "t1"),
                _wire("assistant", "spoken reply", "t2"),
            ]
        )
        events.put_nowait({"type": "turn_complete"})
        await asyncio.sleep(0)  # let the tracker wait behind the in-flight refresh
        release_old_snapshot.set()

        for _ in range(200):
            if session.total_turns == 1 and session.history[-1]["content"] == "spoken reply":
                break
            await asyncio.sleep(0.01)

        assert [message["content"] for message in session.history] == [
            "before",
            "spoken transcript",
            "spoken reply",
        ]
        assert [event for event in announced if event["type"] == "history_updated"] == [
            {"type": "history_updated"}
        ]
    finally:
        release_old_snapshot.set()
        session._event_task.cancel()
        await asyncio.gather(session._event_task, return_exceptions=True)
        if session._history_refresh_task is not None:
            await asyncio.gather(session._history_refresh_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_voice_cut_that_lands_late_reloads_the_open_windows(registry, conversations):
    """A talk-over's cut queued on a turn still unwinding lands after its RPC
    answered: the daemon says voice_reply_cut, and open windows reload."""
    _, workspace_root = conversations
    state = GrowingState()
    state.messages.append(_wire("assistant", "Let me check the weather.", "t1"))
    events: asyncio.Queue = asyncio.Queue()
    announced: list = []
    daemon = _daemon(state)
    daemon.subscribe = lambda: events
    daemon.fan_out = announced.append
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = daemon
    registry[session.session_id] = session
    await session.refresh_history()
    session._event_task = asyncio.create_task(session._track_events())
    try:
        state.messages[-1] = _wire("assistant", "Let me", "t1")  # the cut landed
        events.put_nowait({"type": "voice_reply_cut", "turn_id": "turn-1"})
        for _ in range(200):
            if session.history[-1]["content"] == "Let me":
                break
            await asyncio.sleep(0.01)
        await _settled(session)
        assert session.history[-1]["content"] == "Let me"
        assert [e for e in announced if e["type"] == "history_updated"] != []
    finally:
        session._event_task.cancel()
        await asyncio.gather(session._event_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_refresh_asked_for_during_one_in_flight_runs_again(
    registry, conversations
):
    """The refresh in flight may have asked the daemon before the change.

    A request that arrives while one runs makes it go once more, so the mirror
    never settles on an answer older than the request.
    """
    _, workspace_root = conversations
    state = GrowingState()
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session

    asked: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()
    answer = state.get_conversation

    async def late_first_answer(since=None, anchor=None):
        snapshot = await answer(since, anchor)  # the daemon's answer as of now
        if state.calls == 1:
            asked.set()
            await release.wait()  # ... delivered late
        return snapshot

    state.get_conversation = late_first_answer

    session._schedule_refresh(notify=False)
    await asked.wait()
    state.messages.append(_wire("user", "typed after the first ask", "t1"))
    session._schedule_refresh(notify=False)
    release.set()
    await _settled(session)

    assert state.calls == 2
    assert [m["content"] for m in session.history] == [
        "hello",
        "typed after the first ask",
    ]


def test_disk_history_preserves_content_blocks_and_tool_metadata(tmp_path):
    path = tmp_path / "conv.jsonl"
    path.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}, {"type": "tool_use", "id": "t1"}],
                "tool_calls": [{"id": "t1", "name": "terminal"}],
                "metadata": {"acp": {"session": "s1"}},
                "thinking": "plan",
                "snapshot": {"turn": 3},
            },
            "timestamp": "t3",
        }) + "\n",
        encoding="utf-8",
    )
    messages = read_history_from_disk(tmp_path, "conv")
    assert messages[0]["content"] == "hello"
    assert messages[0]["content_blocks"][1]["id"] == "t1"
    assert messages[0]["tool_calls"][0]["name"] == "terminal"
    assert messages[0]["metadata"]["acp"]["session"] == "s1"
    assert messages[0]["snapshot"] == {"turn": 3}


@pytest.mark.asyncio
async def test_unchanged_refresh_does_not_emit_history_updated(registry, conversations):
    _, workspace_root = conversations
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(SilentState())
    session._event_task = None
    registry[session.session_id] = session
    emitted = asyncio.Queue()
    session.daemon.fan_out = emitted.put_nowait

    async def empty_refresh(*args, **kwargs):
        return session.history

    session.refresh_history = empty_refresh
    session._schedule_refresh()
    await asyncio.wait_for(session._history_refresh_task, timeout=2)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(emitted.get(), timeout=0.05)


def _answer(*texts):
    """What get_conversation returns: the daemon's whole conversation."""
    return SimpleNamespace(
        messages=[
            {"role": "user" if index % 2 == 0 else "assistant", "content": text, "timestamp": f"t{index}"}
            for index, text in enumerate(texts)
        ],
        session_id="conv-1",
        started_at="t0",
        message_count=len(texts),
        metadata={},
        anchor="",
        since=None,
    )


def _seeded_session(registry, conversations, state):
    conversations_dir, workspace_root = conversations
    _write_jsonl(
        conversations_dir / "conv-1.jsonl",
        [{"role": "user", "content": "seed", "timestamp": "t0"}],
    )
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.conversation_id = "conv-1"
    session.created_at = None
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session
    return session


@pytest.mark.asyncio
async def test_every_open_before_the_daemon_answers_reports_the_seed(registry, conversations):
    """A second page opening while the refresh runs gets the seed too, and the page must know."""
    release = asyncio.Event()
    state = SilentState()

    async def get_conversation(since=None, anchor=None):
        await release.wait()
        return _answer("seed", "from daemon")

    state.get_conversation = get_conversation
    session = _seeded_session(registry, conversations, state)

    first = await session.get_history_nonblocking()
    second = await session.get_history_nonblocking()
    assert (first["source"], first["stale"]) == ("disk", True)
    assert (second["source"], second["stale"]) == ("disk", True)

    release.set()
    await asyncio.wait_for(session._history_refresh_task, timeout=2)
    third = await session.get_history_nonblocking()
    assert (third["source"], third["stale"]) == ("mirror", False)
    assert [m["content"] for m in third["history"]] == ["seed", "from daemon"]
    await asyncio.wait_for(session._history_refresh_task, timeout=2)


@pytest.mark.asyncio
async def test_whatever_replaces_the_seed_announces_it_once(registry, conversations):
    """A turn ending before the background refresh lands still tells the page to drop its seed."""
    state = SilentState()

    async def get_conversation(since=None, anchor=None):
        return _answer("seed", "from daemon")

    state.get_conversation = get_conversation
    session = _seeded_session(registry, conversations, state)
    emitted = []
    session.daemon.fan_out = emitted.append
    session._schedule_refresh = lambda: None  # only the direct refresh runs

    assert (await session.get_history_nonblocking())["stale"] is True
    await session.refresh_history()  # what turn_complete does
    assert emitted == [{"type": "history_updated"}]
    await session.refresh_history()
    assert emitted == [{"type": "history_updated"}]


def _snapshot_omock():
    async def get_conversation(since=None, anchor=None):
        return SimpleNamespace(
            messages=[],
            since=999,
            anchor="",
            session_id="",
            metadata={},
        )
    return get_conversation


@pytest.mark.asyncio
async def test_refresh_history_can_report_staleness_for_the_synced_caller(
    registry, conversations, monkeypatch
):
    """Ordinary callers get the cached mirror when the daemon is slow or
    erroring; the synced watermark must refuse it (issue #666).

    A watermark set from a stale mirror misses the turn the window just ran,
    so the next history reload replays that turn as a tail next to the
    transient rows it already rendered and the turn draws twice."""
    from kollabor_engine.session import HistoryRefreshUnavailable

    _, workspace_root = conversations
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(SilentState())  # never answers -> timeout
    registry[session.session_id] = session
    monkeypatch.setattr(
        session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02
    )
    cached = await session.refresh_history()
    assert cached == []
    with pytest.raises(HistoryRefreshUnavailable):
        await session.refresh_history(stale_raises=True)


@pytest.mark.asyncio
async def test_stale_raises_covers_an_unjoinable_tail(registry, conversations):
    """A tail past what the mirror holds cannot be joined; for the synced
    caller that is staleness, not a usable history (issue #666)."""
    from kollabor_engine.session import HistoryRefreshUnavailable

    _, workspace_root = conversations
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    state = SimpleNamespace(
        get_permission_state=lambda: SimpleNamespace(approval_mode="CONFIRM_ALL"),
        get_active_profile=lambda: SimpleNamespace(
            name="openai-oauth", model="gpt", provider="openai"
        ),
        get_system_info=lambda: SimpleNamespace(cwd="/nowhere"),
        get_conversation=_snapshot_omock(),
    )
    session.daemon = _daemon(state)
    registry[session.session_id] = session
    cached = await session.refresh_history()
    assert cached == []
    with pytest.raises(HistoryRefreshUnavailable):
        await session.refresh_history(stale_raises=True)


class SlowRemoteState(SilentState):
    """An agent on another computer: every answer crosses the relay.

    It answers, but later than the open path waits for a busy local daemon
    (HISTORY_REFRESH_TIMEOUT_SECONDS, cut to 0.02 s in these tests).
    """

    def __init__(self, *texts):
        self.texts = texts
        self.calls = 0

    async def get_conversation(self, since=None, anchor=None, **kwargs):
        self.calls += 1
        await asyncio.sleep(0.2)
        return _answer(*self.texts)


def _remote_session(registry, conversations, state):
    _, workspace_root = conversations
    session = EngineSession(
        session_id="koordinator@devbox", profile=None, workspace=workspace_root
    )
    session.device = "devbox"  # remote: no disk of its own to seed from
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session
    return session


@pytest.mark.asyncio
async def test_a_slow_remote_agent_history_reaches_the_page(
    registry, conversations, monkeypatch
):
    """Switching back to a remote chat showed "No conversation messages yet"
    (issue #927): every refresh gave up before the relay answered, and the
    never-filled mirror was handed out as the agent's whole conversation."""
    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    state = SlowRemoteState("check the servers", "all four are up")
    session = _remote_session(registry, conversations, state)
    emitted = []
    session.daemon.fan_out = emitted.append

    # Opening the chat waits only briefly, and the relay is slower.
    assert await session.refresh_history() == []

    first = await session.get_history_nonblocking()
    assert first["history"] == []
    assert first["stale"] is True, "an unconfirmed empty mirror is not an empty chat"

    await asyncio.wait_for(session._history_refresh_task, timeout=2)
    assert {"type": "history_updated"} in emitted
    second = await session.get_history_nonblocking()
    assert second["stale"] is False
    assert [m["content"] for m in second["history"]] == [
        "check the servers",
        "all four are up",
    ]
    await asyncio.wait_for(session._history_refresh_task, timeout=2)


@pytest.mark.asyncio
async def test_a_remote_agent_with_no_messages_settles_as_empty(
    registry, conversations, monkeypatch
):
    """Once the daemon confirms an empty conversation the page leaves its
    syncing state: the empty answer is announced and no longer stale."""
    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    session = _remote_session(registry, conversations, SlowRemoteState())
    emitted = []
    session.daemon.fan_out = emitted.append

    assert (await session.get_history_nonblocking())["stale"] is True
    await asyncio.wait_for(session._history_refresh_task, timeout=2)
    assert emitted == [{"type": "history_updated"}]
    settled = await session.get_history_nonblocking()
    assert (settled["history"], settled["stale"]) == ([], False)
    await asyncio.wait_for(session._history_refresh_task, timeout=2)


@pytest.mark.asyncio
async def test_a_waiting_caller_never_inherits_the_background_wait(
    registry, conversations, monkeypatch
):
    """The background refresh may wait seconds on the relay with the history
    lock held; opening the chat or ending a turn still returns within its
    own short budget, with the cached mirror (#927)."""
    from kollabor_engine.session import HistoryRefreshUnavailable

    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    release = asyncio.Event()
    state = SlowRemoteState("hello")

    async def get_conversation(since=None, anchor=None, **kwargs):
        await release.wait()
        return _answer("hello")

    state.get_conversation = get_conversation
    session = _remote_session(registry, conversations, state)

    await session.get_history_nonblocking()  # starts the background refresh
    await asyncio.sleep(0.01)
    started = time.monotonic()
    assert await session.refresh_history() == []
    with pytest.raises(HistoryRefreshUnavailable):
        await session.refresh_history(stale_raises=True)
    assert time.monotonic() - started < 0.5

    release.set()
    await asyncio.wait_for(session._history_refresh_task, timeout=2)
    assert [m["content"] for m in session.history] == ["hello"]


@pytest.mark.asyncio
async def test_an_unconfirmed_mirror_retries_until_the_remote_answers(
    registry, conversations, monkeypatch
):
    """The relay can outlast the state client's own RPC timeout (an agent on
    another computer took ~19 s live). The page polls the session list, not
    history, so one failed background answer left it syncing forever; the
    refresh tries again while the daemon has never answered (#927)."""
    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(
        session_module,
        "HISTORY_UNCONFIRMED_RETRY_DELAYS_SECONDS",
        (0.01, 0.01, 0.01),
        raising=False,
    )
    state = SlowRemoteState("check the servers", "all four are up")
    answers = iter([asyncio.TimeoutError(), asyncio.TimeoutError()])

    async def get_conversation(since=None, anchor=None, **kwargs):
        state.calls += 1
        failure = next(answers, None)
        if failure is not None:
            raise failure  # the state client's own 10 s RPC timeout
        return _answer("check the servers", "all four are up")

    state.get_conversation = get_conversation
    session = _remote_session(registry, conversations, state)
    emitted = []
    session.daemon.fan_out = emitted.append

    assert (await session.get_history_nonblocking())["stale"] is True  # one ask only
    await _settled(session)
    assert state.calls == 3
    assert emitted == [{"type": "history_updated"}]
    assert [m["content"] for m in session.history] == [
        "check the servers",
        "all four are up",
    ]


class TurnEndingState(SilentState):
    """A daemon whose turn ends while an older history answer is in flight.

    Each answer is the conversation as it stood when the call arrived; the
    first call is held until ``release`` is set.
    """

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0
        self.release = asyncio.Event()
        self.delay = 0.0

    async def get_conversation(self, since=None, anchor=None, **kwargs):
        self.calls += 1
        snapshot = _answer(*self.texts)
        if self.calls == 1:
            await self.release.wait()
        await asyncio.sleep(self.delay)
        return snapshot


@pytest.mark.asyncio
async def test_a_turn_end_that_finds_the_lock_busy_is_announced_later(
    registry, conversations, monkeypatch
):
    """Review finding on #960: an older background answer holding the lock
    made turn_complete's notifying refresh give up after its short budget,
    and nothing asked again, so the finished reply never reached the page."""
    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    _, workspace_root = conversations
    state = TurnEndingState("old")
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session
    emitted = []
    session.daemon.fan_out = emitted.append

    session._schedule_refresh(notify=False)  # a user_message warms the mirror
    await asyncio.sleep(0.01)  # its answer is in flight, holding the lock
    state.texts.append("new reply")  # the turn ends
    assert await session.refresh_history(notify=True) == []  # lock busy
    state.release.set()
    await _settled(session)

    assert [m["content"] for m in session.history] == ["old", "new reply"]
    assert {"type": "history_updated"} in emitted


@pytest.mark.asyncio
async def test_a_turn_end_the_daemon_answers_late_is_announced_later(
    registry, conversations, monkeypatch
):
    """A remote daemon answers the turn's notifying refresh after the short
    budget; the reply still reaches the page once it lands (#927)."""
    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    _, workspace_root = conversations
    state = TurnEndingState("old")
    state.release.set()
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session
    emitted = []
    session.daemon.fan_out = emitted.append
    assert [m["content"] for m in await session.refresh_history()] == ["old"]

    state.texts.append("new reply")
    state.delay = 0.2  # the relay is slower than the short budget
    assert [m["content"] for m in await session.refresh_history(notify=True)] == ["old"]
    await _settled(session)

    assert [m["content"] for m in session.history] == ["old", "new reply"]
    assert emitted == [{"type": "history_updated"}]


class LateFirstAnswerState(SilentState):
    """The first answer arrives late and already holds the finished turn."""

    def __init__(self):
        self.texts = ["old"]
        self.calls = 0
        self.first_delay = 0.0

    async def get_conversation(self, since=None, anchor=None, **kwargs):
        self.calls += 1
        if self.calls == 2:
            await asyncio.sleep(self.first_delay)
        return _answer(*self.texts)


@pytest.mark.asyncio
async def test_a_dropped_turn_end_is_announced_by_the_refresh_that_absorbs_it(
    registry, conversations, monkeypatch
):
    """Review round 2 on #960: the in-flight background answer already held
    the finished reply and stored it silently; the queued notifying refresh
    then found nothing new against that mirror, so no history_updated ever
    told the window, which still showed what it fetched at turn end."""
    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    _, workspace_root = conversations
    state = LateFirstAnswerState()
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session
    emitted = []
    session.daemon.fan_out = emitted.append
    assert [m["content"] for m in await session.refresh_history()] == ["old"]

    state.texts.append("new reply")  # the turn ends on the daemon
    state.first_delay = 0.08
    session._schedule_refresh(notify=False)  # a user_message warms the mirror
    await asyncio.sleep(0.01)
    shown = await session.refresh_history(notify=True)  # turn_complete, lock busy
    assert [m["content"] for m in shown] == ["old"]
    await _settled(session)

    assert [m["content"] for m in session.history] == ["old", "new reply"]
    assert emitted == [{"type": "history_updated"}]


@pytest.mark.asyncio
async def test_a_turn_end_that_lands_mid_publication_is_announced(
    registry, conversations, monkeypatch
):
    """Review round 3 on #960: the raw mirror was replaced before the display
    provenance lookup ran in a thread, so a turn end giving up during that
    lookup recorded the new mirror as what its window holds while returning
    the old chat, and no history_updated followed."""
    import threading

    monkeypatch.setattr(session_module, "HISTORY_REFRESH_TIMEOUT_SECONDS", 0.02)
    gate = threading.Event()
    gate.set()

    def provenance(conversations_dir, native_session_id, first):
        gate.wait(5)
        return first

    monkeypatch.setattr(session_module, "mark_system_initialization", provenance)
    _, workspace_root = conversations
    state = LateFirstAnswerState()
    session = EngineSession(session_id="lapis", profile=None, workspace=workspace_root)
    session.daemon = _daemon(state)
    session._event_task = None
    registry[session.session_id] = session
    emitted = []
    session.daemon.fan_out = emitted.append
    assert [m["content"] for m in await session.refresh_history()] == ["old"]

    state.texts.append("new reply")  # the turn ends on the daemon
    gate.clear()  # the next publication waits in its provenance lookup
    try:
        session._schedule_refresh(notify=False)  # a user_message warms the mirror
        await asyncio.sleep(0.05)
        shown = await session.refresh_history(notify=True)  # turn_complete gives up
    finally:
        gate.set()
    await _settled(session)

    assert [m["content"] for m in shown] == ["old"]
    assert [m["content"] for m in session.history] == ["old", "new reply"]
    assert {"type": "history_updated"} in emitted
