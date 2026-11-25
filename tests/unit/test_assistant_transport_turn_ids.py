"""A streamed turn keeps one id per row across the post-run history merge (#666).
The streamed display IDs stay mounted after completion. Once the engine
proves a match, it publishes each row's canonical historySourceIndex so a
page can reconcile the stored turn without showing it twice.
"""

import asyncio
import json
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


def _assert_turn_mapping(rows, previous_ids, user, assistant):
    """Previously shown rows retain their IDs. Each new row keeps its stream
    ID. Only a proven match carries a stored index."""
    assert [row["id"] for row in rows[:-2]] == previous_ids
    assert all("historySourceIndex" not in row.get("metadata", {}).get("custom", {}) for row in rows[:-2])
    assert [
        (row["id"], row.get("metadata", {}).get("custom", {}).get("historySourceIndex"))
        for row in rows[-2:]
    ] == [user, assistant]


class TestAlignStreamedIds:
    def test_maps_stable_turn_ids_to_their_history_indexes(self):
        rows = _streamed(_state_messages())

        messages._assistant_align_streamed_ids(rows, _history())

        _assert_turn_mapping(
            rows, ["history-0", "history-1"],
            ("user-s1-2", 2), ("assistant-s1-3", 3),
        )

    def test_pairing_skips_trailing_system_rows(self):
        rows = _streamed(_state_messages())
        history = _history() + [
            {"role": "system", "content": "[hub channel: user -> *]"},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        _assert_turn_mapping(
            rows, ["history-0", "history-1"],
            ("user-s1-2", 2), ("assistant-s1-3", 3),
        )

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

        _assert_turn_mapping(
            rows, ["history-0", "history-1"],
            ("user-s1-4", 4), ("assistant-s1-5", 5),
        )

    def test_refuses_ambiguous_repeated_user_content(self):
        """Do not pick the newest matching user row by position alone."""
        rows = _streamed(_state_messages())
        history = _history() + [
            {"role": "user", "content": "PROOF666 one"},
            {"role": "user", "content": "PROOF666 one"},
            {"role": "assistant", "content": "MOCK666 control reply."},
        ]

        assert messages._assistant_align_streamed_ids(rows, history) is False
        _assert_turn_mapping(
            rows, ["history-0", "history-1"],
            ("user-s1-2", None), ("assistant-s1-3", None),
        )

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
        _assert_turn_mapping(
            rows, ["history-0", "history-1"],
            ("user-s1-2", None), ("assistant-s1-3", None),
        )

    def test_only_new_rows_receive_history_indexes(self):
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

        _assert_turn_mapping(
            rows, ["history-0"],
            ("user-s1-1", 1), ("assistant-s1-2", 2),
        )

    def test_keeps_provisional_ids_when_history_lacks_the_turn(self):
        rows = _streamed(_state_messages())

        messages._assistant_align_streamed_ids(rows, _history()[:2])

        _assert_turn_mapping(
            rows, ["history-0", "history-1"],
            ("user-s1-2", None), ("assistant-s1-3", None),
        )

    def test_keeps_provisional_ids_on_a_role_mismatch(self):
        rows = _streamed(_state_messages())
        history = [
            {"role": "user", "content": "welcome prompt"},
            {"role": "user", "content": "PROOF666 one"},
            {"role": "assistant", "content": "MOCK666 control reply."},
        ]

        messages._assistant_align_streamed_ids(rows, history)

        _assert_turn_mapping(
            rows, ["history-0", "history-1"],
            ("user-s1-2", None), ("assistant-s1-3", None),
        )

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

        _assert_turn_mapping(
            rows, ["history-0", "history-1", "history-2", "history-3"],
            ("user-s1-4", 4), ("assistant-s1-5", 7),
        )

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

        _assert_turn_mapping(
            rows, ["history-0", "history-1", "history-2", "history-3"],
            ("user-s1-4", 4), ("assistant-s1-5", 5),
        )

    def test_synthetic_error_rows_are_left_alone(self):
        rows = _streamed(_state_messages())
        rows.insert(2, {"id": "history-2-error", "role": "assistant",
                        "content": "boom"})

        messages._assistant_align_streamed_ids(rows, _history())

        assert rows[2]["id"] == "history-2-error"
        _assert_turn_mapping(
            rows[3:], [],
            ("user-s1-2", 2), ("assistant-s1-3", 3),
        )


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
async def test_turn_complete_publishes_history_indexes(monkeypatch):
    state = await _run_turn(monkeypatch, _history(), _state_messages())

    _assert_turn_mapping(
        state["messages"], ["history-0", "history-1"],
        ("user-s1-2", 2), ("assistant-s1-3", 3),
    )
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
async def test_xml_tool_turn_publishes_history_indexes(monkeypatch):
    """A tool turn's rows retain their display IDs and receive stored indexes.

    The fake turn streams one assistant row (text, call card, final text)
    matching the stored shape call-row, tool row(s), final row: the mapping
    straddles the tool row (history-7 after history-5) and the wiring passes
    the mirror through untouched.
    """
    state = await _run_turn(
        monkeypatch, _xml_tool_history(), _tool_base_state(),
        replies=["done"], message="runtool",
        tool_round=("Running it.", "terminal_0"),
    )

    _assert_turn_mapping(
        state["messages"], ["history-0", "history-1", "history-2", "history-3"],
        ("user-s1-4", 4), ("assistant-s1-5", 7),
    )
    assert state["synced"] == {"history": 8, "messages": 6}


@pytest.mark.asyncio
async def test_native_tool_turn_publishes_history_indexes(monkeypatch):
    """Route-level: native tool rows (no XML metadata) skip the same way."""
    state = await _run_turn(
        monkeypatch, _native_tool_history(), _tool_base_state(),
        replies=["done"], message="runtool",
        tool_round=("Running it.", "call_1"),
    )

    _assert_turn_mapping(
        state["messages"], ["history-0", "history-1", "history-2", "history-3"],
        ("user-s1-4", 4), ("assistant-s1-5", 7),
    )
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
    _assert_turn_mapping(
        final["messages"], ["history-0", "history-1"],
        ("user-s1-2", None), ("assistant-s1-3", None),
    )

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
    _assert_turn_mapping(
        final["messages"], ["history-0", "history-1"],
        ("user-s1-2", None), ("assistant-s1-3", None),
    )
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

    # The FIRST publish still precedes the refresh round trips (#675), and
    # the aligned rows are re-published after alignment so the wire carries
    # the source indexes (#767 — the encode-time snapshot of the early chunk
    # alone would leave the client without source indexes).
    first_refresh = order.index("refresh")
    assert order[0] == "publish" and first_refresh > 0, f"order wrong: {order}"
    assert "publish" in order[first_refresh + 1:], (
        f"aligned rows never re-published after refresh: {order}"
    )
    # The engine publishes both source indexes and the matching watermark.
    _assert_turn_mapping(
        state["messages"], ["history-0", "history-1"],
        ("user-s1-2", 2), ("assistant-s1-3", 3),
    )
    assert state["synced"] == {"history": 4, "messages": 4}


@pytest.mark.asyncio
async def test_wire_does_not_guess_between_identical_turns(monkeypatch):
    """Ambiguous content matches must keep provisional ids and clear sync."""
    history = _history() + [
        {"role": "user", "content": "PROOF666 one"},
        {"role": "assistant", "content": "MOCK666 control reply."},
    ]
    session = _Session(history)

    async def _refresh(*, stale_raises=False):
        # Let the real encoder serialize the provisional publish first.
        await asyncio.sleep(0.05)
        return history

    session.refresh_history = _refresh
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
        state={
            "messages": _state_messages(),
            "synced": {"history": 2, "messages": 2},
        },
    )

    response = await messages.assistant_transport("s1", body)
    sent: list = []

    async def _send(message):
        sent.append(message)

    await response.stream_response(_send)

    wire_text = "".join(
        (part.get("body") or b"").decode()
        if isinstance(part.get("body"), bytes)
        else (part.get("body") or "")
        for part in sent
    )
    messages_sets: list = []
    synced_sets: list = []
    for line in wire_text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[len("data: "):])
        if chunk.get("type") != "update-state":
            continue
        for op in chunk.get("operations", []):
            if op.get("type") != "set":
                continue
            if op.get("path") == ["messages"]:
                messages_sets.append(op["value"])
            elif op.get("path") == ["synced"]:
                synced_sets.append(op["value"])

    assert messages_sets, "no messages update ever reached the wire"
    _assert_turn_mapping(
        messages_sets[-1], ["history-0", "history-1"],
        ("user-s1-2", None), ("assistant-s1-3", None),
    )
    assert synced_sets and synced_sets[-1] is None, (
        "ambiguous alignment must clear the prior sync watermark; "
        f"wire carried {synced_sets}"
    )
    assert not any(
        value == {"history": len(history), "messages": 4}
        for value in synced_sets
    ), f"ambiguous alignment claimed the full snapshot: {synced_sets}"


@pytest.mark.asyncio
async def test_wire_republishes_aligned_rows_after_refresh(monkeypatch):
    """Drive the real assistant_stream encoder. It snapshots the early publish
    before refresh. A later messages chunk must carry the proven indexes as
    well as the original display IDs (#767)."""
    session = _Session(_history())

    async def _refresh(*, stale_raises=False):
        # Suspend long enough for the consumer to encode the early publish
        # chunk before alignment runs (production: a real daemon round trip;
        # add_operations flushes via call_soon, so sleep(0) is too short and
        # would let alignment mutate before the encode — never red).
        await asyncio.sleep(0.05)
        return _history()

    session.refresh_history = _refresh
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

    response = await messages.assistant_transport("s1", body)
    sent: list = []

    async def _send(message):
        sent.append(message)

    await response.stream_response(_send)

    wire_text = "".join(
        (part.get("body") or b"").decode()
        if isinstance(part.get("body"), bytes)
        else (part.get("body") or "")
        for part in sent
    )
    messages_sets: list = []
    for line in wire_text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[len("data: "):])
        if chunk.get("type") != "update-state":
            continue
        for op in chunk.get("operations", []):
            if op.get("type") == "set" and op.get("path") == ["messages"]:
                messages_sets.append(op["value"])

    assert messages_sets, "no messages update ever reached the wire"
    # The same display IDs reach the client before and after refresh. The last
    # update must also carry the canonical positions proven by the engine.
    _assert_turn_mapping(
        messages_sets[-1], ["history-0", "history-1"],
        ("user-s1-2", 2), ("assistant-s1-3", 3),
    )


@pytest.mark.asyncio
async def test_turn_complete_publishes_before_history_refresh_resolves(monkeypatch):
    """Bismuth probe (#675 P2): a gated refresh must not hold the turn.

    The completion path awaited up to two history-refresh RPCs (~3s worst
    case) before it published anything, so the page spun on a slow daemon
    before it saw the completed rows. The completed turn must reach the
    client while the refresh is still unresolved; the aligned republish
    then reconciles ids and only afterwards claims the watermark (#666:
    never claim synced for rows the page has not been shown)."""
    controllers, runners = _fake_assistant_stream(monkeypatch)
    session = _Session(_history())
    gate = asyncio.Event()

    async def gated_refresh(*args, **kwargs):
        await gate.wait()
        return _history()

    session.refresh_history = gated_refresh
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

    loop = asyncio.get_event_loop()
    deadline = loop.time() + 1.0
    published = None
    while loop.time() < deadline:
        rows = controllers[0].state.get("messages") or []
        last = rows[-1] if rows else {}
        # Progress publishes carry no status (the client derives "running");
        # only the turn-complete publish stamps the completed status.
        if last.get("status") == {"type": "complete", "reason": "stop"}:
            published = rows
            break
        await asyncio.sleep(0.01)

    try:
        assert published is not None, (
            "turn completion waited on the gated history refresh before "
            "publishing; the page must see the completed status immediately"
        )
    finally:
        gate.set()
        await asyncio.wait_for(runners[0], 2.0)

    # The aligned republish lands the canonical indexes, and only then is the
    # synced watermark claimed.
    state = controllers[0].state
    _assert_turn_mapping(
        state["messages"], ["history-0", "history-1"],
        ("user-s1-2", 2), ("assistant-s1-3", 3),
    )
    assert state["synced"] == {"history": 4, "messages": 4}


def test_align_refuses_stale_suffix_only_match():
    """A bare tail match is not identity (#675 P3a).

    A refresh snapshot that missed this turn can still end on a stale
    answer whose text is the tail of the streamed answer ("42" vs
    "Answer: 42"): pairing it would map this turn onto the stale rows
    and claim a synced watermark over rows the page has not been shown.
    The aligner must refuse and keep the provisional ids; only a turn
    whose streamed row carries its own call cards proves a merged tool
    round, where the stored final text legitimately is a strict tail."""
    rows = [
        {"id": "history-0", "role": "user", "content": "welcome prompt"},
        {"id": "history-1", "role": "assistant", "content": "welcome reply"},
        {"id": "user-s1-2", "role": "user", "content": "ask again"},
        {"id": "assistant-s1-3", "role": "assistant",
         "content": [{"type": "text", "text": "Answer: 42"}]},
    ]
    history = [
        {"role": "user", "content": "welcome prompt"},
        {"role": "assistant", "content": "welcome reply"},
        {"role": "user", "content": "ask again"},
        {"role": "assistant", "content": "42"},
    ]

    assert messages._assistant_align_streamed_ids(rows, history) is False
    _assert_turn_mapping(
        rows, ["history-0", "history-1"],
        ("user-s1-2", None), ("assistant-s1-3", None),
    )
