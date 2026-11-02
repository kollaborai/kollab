"""A streamed turn keeps one id per row across the post-run history merge (#666).

The reloaded history names every row ``history-<index>``. A streamed turn used
to keep provisional ``user-<session>-<n>`` ids, so when the page merged the
reloaded history after the turn, that merge (which dedupes by id) re-added the
finished turn under its history ids and the window drew it twice until a full
reload. The engine now renames the streamed rows to their history ids at turn
completion, which makes the merge a no-op.
"""

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor_engine.routes import messages


def _state_messages():
    """Rows a page holds before typing: two rows, canonical history ids."""
    return [
        {"id": "history-0", "role": "user", "content": "welcome prompt"},
        {"id": "history-1", "role": "assistant", "content": "welcome reply"},
    ]


def _history():
    """The stored conversation after the typed turn landed."""
    return [
        {"role": "user", "content": "welcome prompt"},
        {"role": "assistant", "content": "welcome reply"},
        {"role": "user", "content": "PROOF666 one"},
        {"role": "assistant", "content": "MOCK666 control reply."},
    ]


def _streamed(messages):
    """Rows the turn appended, with the provisional ids the stream used."""
    return messages + [
        {"id": "user-s1-2", "role": "user", "content": "PROOF666 one"},
        {"id": "assistant-s1-3", "role": "assistant",
         "content": [{"type": "text", "text": "MOCK666 control reply."}]},
    ]


class TestAlignStreamedIds:
    def test_renames_the_turns_rows_to_their_history_ids(self):
        rows = _streamed(_state_messages())

        messages._assistant_align_streamed_ids(rows, _history())

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "history-2", "history-3",
        ]

    def test_pairing_skips_trailing_system_rows(self):
        rows = _streamed(_state_messages())
        history = _history() + [
            {"role": "system", "content": "[hub channel: user -> *]"},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "history-2", "history-3",
        ]

    def test_indexes_are_absolute_not_relative_to_the_tail(self):
        rows = _streamed(_state_messages())
        history = [
            {"role": "user", "content": "welcome prompt"},
            {"role": "assistant", "content": "welcome reply"},
            {"role": "user", "content": "PROOF666 one"},
            {"role": "assistant", "content": "MOCK666 control reply."},
            {"role": "user", "content": "PROOF666 two"},
            {"role": "assistant", "content": "MOCK666 control reply."},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "history-4", "history-5",
        ]

    def test_rows_only_this_turn_appended_are_renamed(self):
        rows = [
            {"id": "history-0", "role": "user", "content": "old"},
            {"id": "user-s1-1", "role": "user", "content": "PROOF666 one"},
            {"id": "assistant-s1-2", "role": "assistant", "content": "reply"},
        ]
        history = [
            {"role": "user", "content": "old"},
            {"role": "user", "content": "PROOF666 one"},
            {"role": "assistant", "content": "reply"},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "history-2",
        ]

    def test_keeps_provisional_ids_when_history_lacks_the_turn(self):
        rows = _streamed(_state_messages())

        messages._assistant_align_streamed_ids(rows, _history()[:2])

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "user-s1-2", "assistant-s1-3",
        ]

    def test_keeps_provisional_ids_on_a_role_mismatch(self):
        rows = _streamed(_state_messages())
        history = [
            {"role": "user", "content": "welcome prompt"},
            {"role": "user", "content": "PROOF666 one"},
            {"role": "assistant", "content": "MOCK666 control reply."},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "user-s1-2", "assistant-s1-3",
        ]

    def test_noop_when_every_row_already_has_a_history_id(self):
        rows = _state_messages()

        messages._assistant_align_streamed_ids(rows, _history())

        assert [row["id"] for row in rows] == ["history-0", "history-1"]

    def test_synthetic_error_rows_are_left_alone(self):
        rows = _streamed(_state_messages())
        rows.insert(2, {"id": "history-2-error", "role": "assistant",
                        "content": "boom"})

        messages._assistant_align_streamed_ids(rows, _history())

        assert rows[2]["id"] == "history-2-error"
        assert [row["id"] for row in rows[3:]] == ["history-2", "history-3"]


class _Controller:
    """The slice of RunController the turn path touches."""

    def __init__(self, state):
        self.state = dict(state) if isinstance(state, dict) else {}
        self.cancelled_event = asyncio.Event()
        self.errors: list[str] = []

    def add_error(self, message):
        self.errors.append(str(message))

    def append_text(self, text):
        pass

    def append_reasoning(self, text):
        pass

    def add_tool_result(self, tool_id, result):
        pass


def _fake_assistant_stream(monkeypatch):
    """Replace assistant_stream with a runner that records the final state."""
    module = types.ModuleType("assistant_stream")
    serialization = types.ModuleType("assistant_stream.serialization")

    class AssistantTransportResponse:
        def __init__(self, stream):
            self.body_iterator = stream

        async def stream_response(self, send):
            pass

    controllers: list[_Controller] = []
    runners: list[asyncio.Task] = []

    def create_run(run_callback, state=None):
        controller = _Controller(state)
        controllers.append(controller)
        runners.append(asyncio.ensure_future(run_callback(controller)))

        async def chunks():
            if False:  # the wire layer is not under test; no chunks emitted
                yield SimpleNamespace(type="")

        return chunks()

    module.RunController = object
    module.create_run = create_run
    serialization.AssistantTransportResponse = AssistantTransportResponse
    module.serialization = serialization
    monkeypatch.setitem(sys.modules, "assistant_stream", module)
    monkeypatch.setitem(sys.modules, "assistant_stream.serialization", serialization)
    return controllers, runners


class _Session:
    alive = True
    session_id = "s1"

    def __init__(self, history):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._history = history
        self.refresh_history = AsyncMock(return_value=history)

    def subscribe(self):
        return self.queue

    def unsubscribe(self, queue):
        pass

    async def send_message(self, content, **kwargs):
        self.queue.put_nowait({
            "type": "token", "request_id": "r1", "text": "MOCK666 control reply.",
        })
        self.queue.put_nowait({
            "type": "turn_complete", "request_id": "r1", "stop_reason": "end_turn",
        })
        return {"accepted": True, "request_id": "r1"}


async def _run_turn(monkeypatch, history, state_messages):
    controllers, runners = _fake_assistant_stream(monkeypatch)
    session = _Session(history)
    monkeypatch.setattr(messages, "get_session_registry", lambda: {"s1": session})
    body = messages.AssistantRequest(
        commands=[{
            "type": "add-message",
            "message": {
                "id": "client-1",
                "role": "user",
                "parts": [{"type": "text", "text": "PROOF666 one"}],
            },
        }],
        state={"messages": state_messages},
    )
    await messages.assistant_transport("s1", body)
    await asyncio.gather(*runners)
    return controllers[0].state


@pytest.mark.asyncio
async def test_turn_complete_publishes_history_ids(monkeypatch):
    state = await _run_turn(monkeypatch, _history(), _state_messages())

    assert [row["id"] for row in state["messages"]] == [
        "history-0", "history-1", "history-2", "history-3",
    ]
    assert state["synced"] == {"history": 4, "messages": 4}


@pytest.mark.asyncio
async def test_refresh_failure_keeps_the_old_behavior(monkeypatch):
    controllers, runners = _fake_assistant_stream(monkeypatch)

    session = _Session(_history())
    session.refresh_history = AsyncMock(side_effect=RuntimeError("daemon gone"))
    monkeypatch.setattr(messages, "get_session_registry", lambda: {"s1": session})
    body = messages.AssistantRequest(
        commands=[{
            "type": "add-message",
            "message": {
                "id": "client-1",
                "role": "user",
                "parts": [{"type": "text", "text": "PROOF666 one"}],
            },
        }],
        state={"messages": _state_messages()},
    )
    await messages.assistant_transport("s1", body)
    await asyncio.gather(*runners)

    final = controllers[0].state
    assert [row["id"] for row in final["messages"]] == [
        "history-0", "history-1", "user-s1-2", "assistant-s1-3",
    ]
    assert "synced" not in final


@pytest.mark.asyncio
async def test_refresh_failure_with_stale_mirror_still_sets_synced(monkeypatch):
    """A refresh that raises but leaves a cached mirror keeps the bookkeeping."""
    controllers, runners = _fake_assistant_stream(monkeypatch)
    stale = _history()[:2]  # the turn's rows are missing from the mirror

    session = _Session(stale)
    # The first refresh (in the turn_complete branch) raises; the second one
    # (inside _assistant_set_synced) returns the stale mirror: provisional ids
    # stay, the merge re-adds the turn, a full reload still reconciles.
    session.refresh_history = AsyncMock(
        side_effect=[RuntimeError("daemon busy"), stale]
    )
    monkeypatch.setattr(messages, "get_session_registry", lambda: {"s1": session})
    body = messages.AssistantRequest(
        commands=[{
            "type": "add-message",
            "message": {
                "id": "client-1",
                "role": "user",
                "parts": [{"type": "text", "text": "PROOF666 one"}],
            },
        }],
        state={"messages": _state_messages()},
    )
    await messages.assistant_transport("s1", body)
    await asyncio.gather(*runners)

    final = controllers[0].state
    assert [row["id"] for row in final["messages"]] == [
        "history-0", "history-1", "user-s1-2", "assistant-s1-3",
    ]
