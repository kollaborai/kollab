"""The engine's history mirror fetches only new messages, and stays exactly the daemon's history."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kollabor_engine.history_xml_tools import web_history
from kollabor_engine.session import EngineSession

from kollabor.state.snapshots import ConversationSnapshot, MessageDto


class Daemon:
    """A daemon's state service: the real tail rule, counting the messages it sends."""

    def __init__(self, knows_tail=True):
        self.messages = []
        self.sent = []
        self.knows_tail = knows_tail

    async def get_conversation(self, since=None, anchor=None):
        snapshot = ConversationSnapshot(messages=list(self.messages), message_count=len(self.messages))
        if self.knows_tail:
            snapshot.tail(since, anchor)
        self.sent.append(len(snapshot.messages))
        return snapshot


def _session(daemon):
    session = EngineSession.__new__(EngineSession)
    session.session_id = "lapis@box"
    session.daemon = SimpleNamespace(state=daemon)
    session.history = []
    session._raw_history = []
    session._history_anchor = ""
    session._history_seeded = False
    session._history_lock = asyncio.Lock()
    return session


def _say(daemon, *texts):
    for text in texts:
        role = "user" if len(daemon.messages) % 2 == 0 else "assistant"
        daemon.messages.append(MessageDto(role=role, content=text, timestamp="2026-10-09T00:00:00"))


def _full(daemon):
    return web_history([m.to_dict() for m in daemon.messages])


@pytest.mark.asyncio
async def test_each_refresh_sends_only_what_is_new():
    daemon = Daemon()
    session = _session(daemon)
    _say(daemon, "uname -n", "box")
    assert await session.refresh_history() == _full(daemon)
    _say(daemon, "and the date?", "Friday")
    assert await session.refresh_history() == _full(daemon)
    assert await session.refresh_history() == _full(daemon)
    assert daemon.sent == [2, 2, 0]


@pytest.mark.asyncio
async def test_a_rewritten_history_is_fetched_whole():
    daemon = Daemon()
    session = _session(daemon)
    _say(daemon, "one", "two", "three", "four")
    await session.refresh_history()
    daemon.messages[1] = MessageDto(role="assistant", content="two, packed")  # edited in place
    _say(daemon, "five")
    assert await session.refresh_history() == _full(daemon)
    daemon.messages = []  # /clear
    _say(daemon, "fresh")
    assert await session.refresh_history() == _full(daemon)
    assert daemon.sent == [4, 5, 1]


@pytest.mark.asyncio
async def test_a_daemon_too_old_to_know_still_gives_the_whole_history():
    daemon = Daemon(knows_tail=False)
    session = _session(daemon)
    _say(daemon, "one", "two")
    assert await session.refresh_history() == _full(daemon)
    _say(daemon, "three")
    assert await session.refresh_history() == _full(daemon)
    assert daemon.sent == [2, 3]


@pytest.mark.asyncio
async def test_a_tail_that_cannot_be_joined_is_not_used():
    """A far daemon that answers with a ``since`` past what is held: keep the mirror, refetch whole next time."""
    daemon = Daemon()
    session = _session(daemon)
    _say(daemon, "one", "two")
    before = await session.refresh_history()

    async def lying(since=None, anchor=None):
        return ConversationSnapshot(messages=[MessageDto(role="user", content="x")], since=9, anchor="y")

    session.daemon = SimpleNamespace(state=SimpleNamespace(get_conversation=lying))
    assert await session.refresh_history() == before
    session.daemon = SimpleNamespace(state=daemon)
    _say(daemon, "three")
    assert await session.refresh_history() == _full(daemon)
    assert daemon.sent == [2, 3]


@pytest.mark.asyncio
async def test_initialization_provenance_is_display_only_and_survives_tail_refresh(monkeypatch, tmp_path):
    daemon = Daemon()
    session = _session(daemon)
    session.workspace = str(tmp_path)
    _say(daemon, "injected context", "ready")
    original_get = daemon.get_conversation

    async def native_snapshot(since=None, anchor=None):
        snapshot = await original_get(since, anchor)
        snapshot.session_id = "native-resumed-session"
        return snapshot

    daemon.get_conversation = native_snapshot
    conversations_dir = tmp_path / "conversations"
    project_dir = Mock(return_value=conversations_dir)
    monkeypatch.setattr("kollabor_engine.session.get_conversations_dir", project_dir)

    def mark(directory, native_id, first):
        assert directory == conversations_dir
        assert native_id == "native-resumed-session"
        assert first["content"] == "injected context"
        assert not first["metadata"].get("system_initialization")
        return {**first, "metadata": {**first["metadata"], "system_initialization": True}}

    marker = Mock(side_effect=mark)
    monkeypatch.setattr("kollabor_engine.session.mark_system_initialization", marker)
    first_display = await session.refresh_history()
    anchor = session._history_anchor
    assert first_display[0]["metadata"]["system_initialization"] is True
    assert first_display[0]["content"] == session._raw_history[0]["content"]
    assert session._raw_history[0]["metadata"] == {}
    assert daemon.messages[0].metadata == {}
    assert await session.refresh_history() == first_display
    assert session._history_anchor == anchor
    _say(daemon, "human request", "answer")
    displayed = await session.refresh_history()
    assert displayed[0]["metadata"]["system_initialization"] is True
    assert len(displayed) == len(session._raw_history) == 4
    assert session._raw_history[0]["metadata"] == {}
    assert daemon.sent == [2, 0, 2]
    assert marker.call_count == 3
    project_dir.assert_called_with(tmp_path)


@pytest.mark.asyncio
async def test_unavailable_provenance_leaves_history_visible(monkeypatch):
    daemon = Daemon()
    session = _session(daemon)
    _say(daemon, "my ordinary request")

    async def snapshot(**kwargs):
        return ConversationSnapshot(messages=daemon.messages, session_id="native")

    daemon.get_conversation = snapshot
    monkeypatch.setattr("kollabor_engine.session.mark_system_initialization", Mock(side_effect=OSError("unavailable")))
    assert await session.refresh_history() == _full(daemon)
    assert session._raw_history[0]["metadata"] == {}


@pytest.mark.asyncio
async def test_conversation_uid_change_refetches_and_announces_even_identical_history():
    class ScopedDaemon(Daemon):
        def __init__(self):
            super().__init__()
            self.uid = 'conversation-a'
        async def get_conversation(self, since=None, anchor=None, expected_conversation_uid=None):
            snapshot = ConversationSnapshot(messages=list(self.messages), message_count=len(self.messages),
                                            metadata={'conversation_uid': self.uid})
            if expected_conversation_uid in (None, self.uid):
                snapshot.tail(since, anchor)
            self.sent.append(len(snapshot.messages))
            return snapshot
    daemon = ScopedDaemon()
    session = _session(daemon)
    events = []
    session.daemon.fan_out = events.append
    _say(daemon, 'same text')
    await session.refresh_history()
    assert session._history_conversation_uid == 'conversation-a'
    daemon.uid = 'conversation-b'
    await session.refresh_history(notify=False)
    assert session._history_conversation_uid == 'conversation-b'
    assert daemon.sent == [1, 1]
    assert events == [{'type': 'history_updated', 'conversation_uid': 'conversation-b'}]
    session._schedule_refresh = lambda: None
    session.last_turn_error = None
    assert (await session.get_history_nonblocking())['conversation_uid'] == 'conversation-b'


@pytest.mark.asyncio
async def test_empty_live_conversation_never_reseeds_old_disk_history():
    daemon = Daemon()
    session = _session(daemon)
    await session.refresh_history()
    session._disk_conversation_dir = lambda: (_ for _ in ()).throw(AssertionError('old disk seed used'))
    session._schedule_refresh = lambda: None
    session.last_turn_error = None
    snapshot = await session.get_history_nonblocking()
    assert snapshot['history'] == [] and snapshot['source'] == 'mirror'
