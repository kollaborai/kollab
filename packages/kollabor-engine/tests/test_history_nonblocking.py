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
