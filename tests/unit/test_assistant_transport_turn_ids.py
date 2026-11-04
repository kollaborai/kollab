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
        """Two earlier rounds, distinct content; this turn pairs the tail by
        content and takes those absolute stored indexes, not page-relative
        ones."""
        rows = [
            {"id": "history-0", "role": "user", "content": "welcome prompt"},
            {"id": "history-1", "role": "assistant", "content": "welcome reply"},
            {"id": "user-s1-4", "role": "user", "content": "PROOF666 two"},
            {
                "id": "assistant-s1-5",
                "role": "assistant",
                "content": [{"type": "text", "text": "MOCK666 control reply two."}],
            },
        ]
        history = [
            {"role": "user", "content": "welcome prompt"},
            {"role": "assistant", "content": "welcome reply"},
            {"role": "user", "content": "PROOF666 one"},
            {"role": "assistant", "content": "MOCK666 control reply one."},
            {"role": "user", "content": "PROOF666 two"},
            {"role": "assistant", "content": "MOCK666 control reply two."},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "history-4", "history-5",
        ]

    def test_refuses_ambiguous_duplicate_rounds(self):
        """Duplicate rounds where this turn's text matches the earlier one
        and the tail holds a DIFFERENT round: a tail claim would map this
        turn onto rows it did not stream, so the aligner refuses and leaves
        the provisional ids for the next full reload."""
        rows = _streamed(_state_messages())
        history = [
            {"role": "user", "content": "welcome prompt"},
            {"role": "assistant", "content": "welcome reply"},
            {"role": "user", "content": "PROOF666 one"},
            {"role": "assistant", "content": "MOCK666 control reply."},
            {"role": "user", "content": "PROOF666 two"},
            {"role": "assistant", "content": "MOCK666 control reply."},
        ]

        assert messages._assistant_align_streamed_ids(rows, history) is False
        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "user-s1-2", "assistant-s1-3",
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

    def test_tool_turn_streamed_single_row_pairs_final_reply(self):
        """A streamed tool turn is ONE row (cards + final text); the stored
        turn is calls row, tool rows, final reply (ground truth from the
        rig: roles system, user, assistant, tool, assistant). The reverse
        pairing must skip the stored rows the streamed turn does not carry
        as its own row and claim the FINAL reply for the streamed row: the
        merge then adds only the calls row, whose tool row attaches to the
        card. User keeps its own index; the tool row keeps history-6.
        """
        rows = [
            {"id": "history-0", "role": "user", "content": "welcome prompt"},
            {"id": "history-1", "role": "assistant", "content": "welcome reply"},
            {"id": "history-2", "role": "user", "content": "PROOF666 one"},
            {"id": "history-3", "role": "assistant", "content": "MOCK666 control reply."},
            {"id": "user-s1-4", "role": "user", "content": "runtool"},
            {"id": "assistant-s1-5", "role": "assistant",
             "content": [{"type": "tool-call", "toolCallId": "terminal_0",
                          "toolName": "terminal"},
                         {"type": "text", "text": "done"}]},
        ]
        history = _history() + [
            {"role": "user", "content": "runtool"},
            {"role": "assistant", "content": "Running it.",
             "metadata": {"tool_calls": [{"id": "terminal_0", "type": "function",
                "function": {"name": "terminal", "arguments": {"command": "echo one"}}}]}},
            {"role": "tool", "content": "[terminal] one",
             "metadata": {"tool_call_id": "terminal_0"}},
            {"role": "assistant", "content": "done"},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "history-2", "history-3",
            "history-4", "history-7",
        ]

    def test_native_tool_turn_ending_on_the_tool_batch(self):
        """A native turn that ends on its tool rows still aligns.

        The daemon writes role="tool" rows for native calls too (no XML
        metadata anywhere); a refresh landing between the batch and the final
        reply leaves the mirror ending on the tool row.
        """
        rows = [
            {"id": "history-0", "role": "user", "content": "welcome prompt"},
            {"id": "history-1", "role": "assistant", "content": "welcome reply"},
            {"id": "history-2", "role": "user", "content": "PROOF666 one"},
            {"id": "history-3", "role": "assistant", "content": "MOCK666 control reply."},
            {"id": "user-s1-4", "role": "user", "content": "runtool"},
            {"id": "assistant-s1-5", "role": "assistant",
             "content": [{"type": "tool-call", "toolCallId": "call_1",
                          "toolName": "terminal"},
                         {"type": "text", "text": "Running it."}]},
        ]
        history = _history() + [
            {"role": "user", "content": "runtool"},
            {"role": "assistant", "content": "Running it.",
             "metadata": {"tool_calls": [{"id": "call_1", "type": "function",
                "function": {"name": "terminal", "arguments": {"command": "ls"}}}]}},
            {"role": "tool", "content": "[terminal] a.txt",
             "metadata": {"tool_call_id": "call_1"}},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        assert [row["id"] for row in rows] == [
            "history-0", "history-1", "history-2", "history-3",
            "history-4", "history-5",
        ]

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

    async def add_tool_call(self, name, tool_id):
        class _ToolController:
            def append_args_text(self, text):
                pass

        return _ToolController()

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

    def __init__(self, history, replies=None, tool_round=None):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._history = history
        self.replies = replies if replies is not None else ["MOCK666 control reply."]
        # tool_round: ("Running it.", <call id>) emits a streamed tool round
        # (text, tool_start card) before the final reply, so the turn's
        # single assistant row carries the call card the stored history
        # keeps as a separate row.
        self.tool_round = tool_round
        self.refresh_history = AsyncMock(return_value=history)

    def subscribe(self):
        return self.queue

    def unsubscribe(self, queue):
        pass

    async def send_message(self, content, **kwargs):
        for text in self.replies:
            if self.tool_round is not None and text == self.replies[-1]:
                call_text, call_id = self.tool_round
                self.queue.put_nowait({
                    "type": "token", "request_id": "r1", "text": call_text,
                })
                self.queue.put_nowait({
                    "type": "tool_start", "request_id": "r1",
                    "tool_id": call_id, "tool_name": "terminal",
                    "input": {"command": "echo one"},
                })
            self.queue.put_nowait({
                "type": "token", "request_id": "r1", "text": text,
            })
        self.queue.put_nowait({
            "type": "turn_complete", "request_id": "r1", "stop_reason": "end_turn",
        })
        return {"accepted": True, "request_id": "r1"}


async def _run_turn(
    monkeypatch, history, state_messages, replies=None,
    message="PROOF666 one", tool_round=None, refresh=None,
):
    controllers, runners = _fake_assistant_stream(monkeypatch)
    session = _Session(history, replies=replies, tool_round=tool_round)
    if refresh is not None:
        session.refresh_history = refresh
    monkeypatch.setattr(messages, "get_session_registry", lambda: {"s1": session})
    body = messages.AssistantRequest(
        commands=[{
            "type": "add-message",
            "message": {
                "id": "client-1",
                "role": "user",
                "parts": [{"type": "text", "text": message}],
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


def _xml_tool_history():
    """A stored XML tool turn after ``web_history`` shaped it (#666 P2).

    Tool rows sit between the call row and the final reply; the streamed
    turn carries the call on the assistant row's parts instead of a row.
    """
    return _history() + [
        {"role": "user", "content": "runtool"},
        {"role": "assistant", "content": "Running it.",
         "metadata": {"tool_calls": [{"id": "terminal_0", "type": "function",
            "function": {"name": "terminal", "arguments": {"command": "echo one"}}}]}},
        {"role": "tool", "content": "[terminal] one",
         "metadata": {"tool_call_id": "terminal_0"}},
        {"role": "assistant", "content": "done"},
    ]


def _native_tool_history():
    """The same turn shape with the daemon's native metadata (no XML keys).

    ``web_history`` passes a native tool turn through as it is; the align
    must not depend on XML metadata being present.
    """
    return _history() + [
        {"role": "user", "content": "runtool"},
        {"role": "assistant", "content": "Running it.",
         "metadata": {"tool_calls": [{"id": "call_1", "type": "function",
            "function": {"name": "terminal", "arguments": {"command": "ls"}}}]}},
        {"role": "tool", "content": "[terminal] a.txt",
         "metadata": {"tool_call_id": "call_1"}},
        {"role": "assistant", "content": "done"},
    ]


def _tool_base_state():
    """Rows the page already shows under canonical ids before the tool turn."""
    return [
        {"id": "history-0", "role": "user", "content": "welcome prompt"},
        {"id": "history-1", "role": "assistant", "content": "welcome reply"},
        {"id": "history-2", "role": "user", "content": "PROOF666 one"},
        {"id": "history-3", "role": "assistant", "content": "MOCK666 control reply."},
    ]


def _tool_streamed():
    return _tool_base_state() + [
        {"id": "user-s1-4", "role": "user", "content": "runtool"},
        {"id": "assistant-s1-5", "role": "assistant",
         "content": [{"type": "tool-call", "toolCallId": "terminal_0",
                      "toolName": "terminal"},
                     {"type": "text", "text": "Running it."}]},
        {"id": "assistant-s1-6", "role": "assistant",
         "content": [{"type": "text", "text": "done"}]},
    ]


@pytest.mark.asyncio
async def test_xml_tool_turn_publishes_history_ids(monkeypatch):
    """Route-level: a tool turn's rows still get their stored history ids.

    The fake turn streams one assistant row (text, call card, final text)
    matching the stored shape call-row, tool row(s), final row: the rename
    straddles the tool row (history-7 after history-5) and the wiring passes
    the mirror through untouched.
    """
    state = await _run_turn(
        monkeypatch, _xml_tool_history(), _tool_base_state(),
        replies=["done"], message="runtool",
        tool_round=("Running it.", "terminal_0"),
    )

    assert [row["id"] for row in state["messages"]] == [
        "history-0", "history-1", "history-2", "history-3",
        "history-4", "history-7",
    ]
    assert state["synced"] == {"history": 8, "messages": 6}


@pytest.mark.asyncio
async def test_native_tool_turn_publishes_history_ids(monkeypatch):
    """Route-level: native tool rows (no XML metadata) skip the same way."""
    state = await _run_turn(
        monkeypatch, _native_tool_history(), _tool_base_state(),
        replies=["done"], message="runtool",
        tool_round=("Running it.", "call_1"),
    )

    assert [row["id"] for row in state["messages"]] == [
        "history-0", "history-1", "history-2", "history-3",
        "history-4", "history-7",
    ]
    assert state["synced"] == {"history": 8, "messages": 6}


@pytest.mark.asyncio
async def test_refresh_failure_claims_no_sync(monkeypatch):
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

    assert final["synced"] is None


@pytest.mark.asyncio
async def test_failed_refresh_then_stale_retry_never_claims_sync(monkeypatch):
    """A stale retry snapshot must not be claimed as synced.

    The first refresh (turn_complete branch) raises; the retry returns a
    mirror missing the current turn. The stream never reconciled its
    provisional ids against that snapshot, so claiming it would let the
    runtime merge append the turn's canonical rows alongside the
    provisional ones: the double draw. No claim means the page takes its
    next full load, which always reconciles.
    """
    controllers, runners = _fake_assistant_stream(monkeypatch)
    stale = _history()[:2]  # the turn's rows are missing from the mirror

    session = _Session(stale)
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
    assert final["synced"] is None


@pytest.mark.asyncio
async def test_publish_precedes_refresh_history_round_trips(monkeypatch):
    """The page receives the turn's rows before any history refresh awaits.

    #675 P2: the turn-end publish used to sit behind up to two
    ``refresh_history`` round trips, so rows appeared only after the daemon
    answered. The publish now goes out first; the refreshed snapshot only
    decides the sync claim afterwards (alignment mutates the published list
    in place, so the claim and the shown ids stay consistent).
    """
    order = []
    real_set = messages._assistant_set_messages

    def _recording_set(controller, msgs):
        order.append("publish")
        real_set(controller, msgs)

    monkeypatch.setattr(messages, "_assistant_set_messages", _recording_set)

    async def _refresh(*, stale_raises=False):
        order.append("refresh")
        return _history()

    state = await _run_turn(
        monkeypatch, _history(), _state_messages(), refresh=_refresh
    )

    last_publish = len(order) - 1 - order[::-1].index("publish")
    first_refresh = order.index("refresh")
    assert last_publish < first_refresh, f"publish order wrong: {order}"
    # End state is unchanged: ids renamed against the snapshot, claim set.
    assert [row["id"] for row in state["messages"]] == [
        "history-0", "history-1", "history-2", "history-3",
    ]
    assert state["synced"] == {"history": 4, "messages": 4}
