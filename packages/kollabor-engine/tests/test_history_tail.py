"""The engine's history mirror fetches only new messages, and stays exactly the daemon's history."""

import asyncio
from types import SimpleNamespace

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
