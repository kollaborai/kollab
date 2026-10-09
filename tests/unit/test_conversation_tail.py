"""A reader that keeps a copy of the conversation gets only what it lacks (ConversationSnapshot.tail)."""

import asyncio

from kollabor.state.remote import RemoteStateService
from kollabor.state.snapshots import ConversationSnapshot, MessageDto


def _messages(*texts):
    return [MessageDto(role="user" if n % 2 == 0 else "assistant", content=text) for n, text in enumerate(texts)]


def _snapshot(messages, since=None, anchor=None):
    return ConversationSnapshot(messages=list(messages), message_count=len(messages)).tail(since, anchor)


def test_a_reader_with_the_same_first_messages_gets_only_the_rest():
    first = _snapshot(_messages("a", "b", "c"))
    assert (len(first.messages), first.since) == (3, None) and first.anchor

    grown = _snapshot(_messages("a", "b", "c", "d", "e"), since=3, anchor=first.anchor)
    assert [m.content for m in grown.messages] == ["d", "e"]
    assert (grown.since, grown.message_count) == (3, 5)
    assert grown.anchor == _snapshot(_messages("a", "b", "c", "d", "e")).anchor

    same = _snapshot(_messages("a", "b", "c", "d", "e"), since=5, anchor=grown.anchor)
    assert (same.messages, same.since) == ([], 5)


def test_any_change_in_the_readers_part_sends_everything():
    held = _snapshot(_messages("a", "b", "c"))
    for changed in (
        _messages("a", "B", "c", "d"),  # a tool output packed in place, deep in the history
        _messages("summary", "d"),  # compaction
        [],  # /clear
    ):
        reply = _snapshot(changed, since=3, anchor=held.anchor)
        assert (len(reply.messages), reply.since) == (len(changed), None)
    for since, anchor in ((3, ""), (3, "0" * 64), (4, held.anchor), (True, held.anchor), (-1, held.anchor)):
        assert _snapshot(_messages("a", "b", "c"), since, anchor).since is None


def test_since_and_anchor_survive_the_wire_and_a_bad_since_is_dropped():
    reply = _snapshot(_messages("a", "b", "c"), since=2, anchor=_snapshot(_messages("a", "b")).anchor)
    back = ConversationSnapshot.from_dict(reply.to_dict())
    assert (back.since, back.anchor, [m.content for m in back.messages]) == (2, reply.anchor, ["c"])
    for bad in ("2", True, -1, 2.0):
        assert ConversationSnapshot.from_dict({**reply.to_dict(), "since": bad}).since is None
    assert ConversationSnapshot.from_dict({"messages": []}).since is None  # a daemon too old to know


def test_the_client_sends_since_and_anchor_only_when_it_holds_a_copy():
    asked = []

    class Rpc:
        async def call(self, method, params, timeout=None):
            asked.append((method, params))
            return {"messages": []}

    async def run():
        state = RemoteStateService(Rpc())
        await state.get_conversation()
        await state.get_conversation(since=3, anchor="x")

    asyncio.run(run())
    assert asked == [
        ("state.get_conversation", {}),
        ("state.get_conversation", {"since": 3, "anchor": "x"}),
    ]
