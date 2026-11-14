"""A live daemon that drops the engine (its hub socket was re-created) is reattached."""

import asyncio
import functools
import json
import os
import tempfile
import time
import types

import pytest
from kollabor_engine.daemon_pool import DaemonHandle

from kollabor_rpc import RpcError


@pytest.mark.asyncio
async def test_handle_reattaches_after_the_daemon_drops_it():
    attaches = []

    async def daemon(reader, writer):
        attach = json.loads(await reader.readline())
        attaches.append(attach["action"])
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        if len(attaches) == 1:  # the watchdog restart: drop the first attacher
            writer.close()
            return
        line = await reader.readline()
        request = json.loads(line)
        reply = {"action": "rpc_reply", "request_id": request["request_id"], "result": {"ok": True}}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "zircon.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s1", "zircon", launcher=None)
    handle.pid = os.getpid()  # a live daemon process
    queue = handle.subscribe()
    try:
        await handle.connect(path)
        for _ in range(50):
            if len(attaches) == 2:
                break
            await asyncio.sleep(0.1)
        assert attaches == ["attach", "attach"]
        await asyncio.sleep(0.1)
        assert await handle.rpc.call("state.ping", {}, timeout=5) == {"ok": True}
        assert queue.empty()  # no daemon_closed: the chat never saw a dead daemon
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


async def _serve_after_refusals(refusals: int):
    """A daemon that drops its first attacher, refuses the next ``refusals``, then answers."""
    attaches = []

    async def daemon(reader, writer):
        await reader.readline()
        attaches.append("attach")
        if 1 < len(attaches) <= 1 + refusals:  # its socket is not taking anyone back yet
            writer.close()
            return
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        if len(attaches) == 1:
            writer.close()
            return
        request = json.loads(await reader.readline())
        reply = {"action": "rpc_reply", "request_id": request["request_id"], "result": {"ok": True}}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "lapis.sock")
    return await asyncio.start_unix_server(daemon, path), path, attaches


def _fast_reattach(handle):
    """The same retry policy on a test clock: 2 quick tries, then every 50 ms."""
    handle._reattach = functools.partial(
        DaemonHandle._reattach, handle, attempts=2, delay=0.01, slow_delay=0.05
    )


@pytest.mark.asyncio
async def test_a_live_daemon_is_retried_past_the_quick_tries():
    # #541: giving up after the quick tries left a handle whose every call
    # failed "client closed" while the daemon kept running.
    server, path, attaches = await _serve_after_refusals(refusals=5)
    handle = DaemonHandle("s2", "lapis", launcher=None)
    handle.pid = os.getpid()
    _fast_reattach(handle)
    queue = handle.subscribe()
    try:
        await handle.connect(path)
        for _ in range(100):
            if len(attaches) == 7 and handle.rpc is not None and not handle.rpc._closed:
                break
            await asyncio.sleep(0.02)
        assert len(attaches) == 7  # first attach, 5 refusals, taken back
        assert await handle.rpc.call("state.ping", {}, timeout=5) == {"ok": True}
        assert queue.empty()
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


@pytest.mark.asyncio
async def test_a_read_failure_that_is_not_a_connection_error_reattaches():
    server, path, attaches = await _serve_after_refusals(refusals=0)
    handle = DaemonHandle("s3", "lapis", launcher=None)
    handle.pid = os.getpid()
    _fast_reattach(handle)
    try:
        await handle.connect(path)

        async def broken_read():
            raise RuntimeError("the reader blew up")

        handle._reader.readline = broken_read  # before the read loop's first read
        for _ in range(100):
            if len(attaches) == 2 and handle.rpc is not None and not handle.rpc._closed:
                break
            await asyncio.sleep(0.02)
        assert await handle.rpc.call("state.ping", {}, timeout=5) == {"ok": True}
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


@pytest.mark.asyncio
async def test_an_oversized_frame_is_dropped_and_the_session_stays(monkeypatch):
    # A frame over the engine's read limit made every reattach meet it again
    # (nephrite, 2026-10-31: a reconnect every ~10 s). Drop that one frame.
    import kollabor_rpc.transport

    monkeypatch.setattr(kollabor_rpc.transport, "LARGE_BUFFER_LIMIT", 1024)
    attaches = []

    async def daemon(reader, writer):
        await reader.readline()
        attaches.append("attach")
        writer.write(b'{"type": "attach_ack"}\n')
        writer.write(b'{"type": "tool_result", "output": "' + b"x" * 4096 + b'"}\n')
        await writer.drain()
        request = json.loads(await reader.readline())
        reply = {"action": "rpc_reply", "request_id": request["request_id"], "result": {"ok": True}}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
        await reader.read()  # stay attached

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "nephrite.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s4", "nephrite", launcher=None)
    handle.pid = os.getpid()
    _fast_reattach(handle)
    try:
        await handle.connect(path)
        assert await handle.rpc.call("state.ping", {}, timeout=5) == {"ok": True}
        assert attaches == ["attach"]  # never dropped and reattached
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


@pytest.mark.asyncio
async def test_an_oversized_reply_fails_its_call_at_once(monkeypatch):
    # Kyanite on #543: dropping a too-big reply left its call to time out with
    # no cause, and refresh_history quietly kept stale history.
    import kollabor_rpc.transport

    monkeypatch.setattr(kollabor_rpc.transport, "LARGE_BUFFER_LIMIT", 1024)

    async def daemon(reader, writer):
        await reader.readline()
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        for result in ({"messages": "x" * 4096}, {"ok": True}):
            request = json.loads(await reader.readline())
            reply = {"action": "rpc_reply", "request_id": request["request_id"], "result": result}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        await reader.read()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "nephrite.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s6", "nephrite", launcher=None)
    handle.pid = os.getpid()
    try:
        await handle.connect(path)
        started = time.monotonic()
        with pytest.raises(RpcError, match="frame limit"):
            await handle.rpc.call("state.get_conversation", {}, timeout=5)
        assert time.monotonic() - started < 2  # failed, not timed out
        assert await handle.rpc.call("state.ping", {}, timeout=5) == {"ok": True}
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


@pytest.mark.asyncio
async def test_a_turn_in_flight_across_the_slow_retries_ends_on_its_own_completion():
    # Kyanite on #543: the daemon replays its recent events on every attach, so
    # after a long reattach the last turn's turn_complete reached the running
    # turn's stream first and ended it early.
    old_done = {"type": "turn_complete", "request_id": "old", "seq": 1}
    started = {"type": "tool_start", "request_id": "new", "seq": 2}
    new_done = {"type": "turn_complete", "request_id": "new", "seq": 3}
    attaches = []

    async def daemon(reader, writer):
        await reader.readline()
        attaches.append("attach")
        if 1 < len(attaches) <= 4:  # refused past the quick tries
            writer.close()
            return
        writer.write(b'{"type": "attach_ack"}\n')
        for event in [old_done, started] + ([new_done] if len(attaches) > 1 else []):
            writer.write((json.dumps(event) + "\n").encode())
        await writer.drain()
        if len(attaches) == 1:  # the socket restart, mid-turn
            writer.close()
            return
        await reader.read()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "koordinator.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s7", "koordinator", launcher=None)
    handle.pid = os.getpid()
    _fast_reattach(handle)
    queue = handle.subscribe()
    try:
        await handle.connect(path)
        seen = [await asyncio.wait_for(queue.get(), timeout=5) for _ in range(3)]
        assert [(e["type"], e["request_id"]) for e in seen] == [
            ("turn_complete", "old"),
            ("tool_start", "new"),
            ("turn_complete", "new"),
        ]
        assert len(attaches) == 5
        await asyncio.sleep(0.05)
        assert queue.empty()
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


@pytest.mark.asyncio
async def test_an_oversized_display_event_is_dropped_out_loud(monkeypatch):
    # Kyanite on #543 round 2: a >16 MiB tool_result vanished from the stream
    # and the reply looked complete.
    import kollabor_rpc.transport

    monkeypatch.setattr(kollabor_rpc.transport, "LARGE_BUFFER_LIMIT", 1024)
    big = {"type": "tool_result", "tool_id": "t1", "output": "x" * 4096, "seq": 1}
    done = {"type": "turn_complete", "request_id": "r1", "seq": 2}

    async def daemon(reader, writer):
        await reader.readline()
        writer.write(b'{"type": "attach_ack"}\n')
        writer.write((json.dumps(big) + "\n" + json.dumps(done) + "\n").encode())
        await writer.drain()
        await reader.read()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "jasper.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s7", "jasper", launcher=None)
    handle.pid = os.getpid()
    queue = handle.subscribe()
    try:
        await handle.connect(path)
        first = await asyncio.wait_for(queue.get(), 5)
        second = await asyncio.wait_for(queue.get(), 5)
        assert first["type"] == "error" and first["code"] == "frame_too_large"
        assert second["type"] == "turn_complete"
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


@pytest.mark.asyncio
async def test_an_oversized_terminal_frame_is_dropped_quietly(monkeypatch, caplog):
    # A terminal-only frame never reaches the chat, so dropping one must not
    # tell the chat its reply is incomplete (#544: such frames came every few s).
    import kollabor_rpc.transport

    monkeypatch.setattr(kollabor_rpc.transport, "LARGE_BUFFER_LIMIT", 1024)
    big = {"type": "active_area", "lines": ["x" * 4096], "seq": 1}
    done = {"type": "turn_complete", "request_id": "r1", "seq": 2}

    async def daemon(reader, writer):
        await reader.readline()
        writer.write(b'{"type": "attach_ack"}\n')
        writer.write((json.dumps(big) + "\n" + json.dumps(done) + "\n").encode())
        await writer.drain()
        await reader.read()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "jasper.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s8", "jasper", launcher=None)
    handle.pid = os.getpid()
    queue = handle.subscribe()
    try:
        with caplog.at_level("WARNING"):
            await handle.connect(path)
            first = await asyncio.wait_for(queue.get(), 5)
        assert first["type"] == "turn_complete"
        size = len(json.dumps(big)) + 1
        assert f"dropped an oversized active_area frame ({size} bytes)" in caplog.text
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


async def _pool_events(frames, count):
    """What a DaemonHandle passes on when the daemon writes ``frames``."""
    async def daemon(reader, writer):
        await reader.readline()
        writer.write(b'{"type": "attach_ack"}\n')
        writer.write("".join(json.dumps(frame) + "\n" for frame in frames).encode())
        await writer.drain()
        await reader.read()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "jasper.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s9", "jasper", launcher=None)
    handle.pid = os.getpid()
    queue = handle.subscribe()
    try:
        await handle.connect(path)
        return [await asyncio.wait_for(queue.get(), 5) for _ in range(count)]
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()


class _StreamSession:
    """A session for the message route that replays ``events``."""

    session_id = "s9"
    alive = True

    def __init__(self, events, admission=None):
        self.queue = asyncio.Queue()
        for event in events:
            self.queue.put_nowait(event)
        self.admission = admission or {"accepted": True, "request_id": "new"}
        self.sent = []

    def subscribe(self):
        return self.queue

    def unsubscribe(self, queue):
        pass

    async def send_message(self, *args, **kwargs):
        self.sent.append(kwargs)
        return self.admission


@pytest.mark.asyncio
async def test_an_oversized_turn_complete_still_ends_its_turn(monkeypatch):
    # Kyanite on #543 round 4: a >16 MiB turn_complete (a harness error with
    # its whole output) was dropped like any update, so the message stream
    # waited out its 600 s idle timeout and the reply never ended.
    from kollabor_engine.routes import messages

    import kollabor_rpc.transport

    monkeypatch.setattr(kollabor_rpc.transport, "LARGE_BUFFER_LIMIT", 1024)
    big = {"type": "turn_complete", "request_id": "new", "stop_reason": "error", "error": "x" * 4096, "seq": 3}
    after = {"type": "tool_start", "tool_id": "t2", "seq": 4}
    # The second copy is what a reattach replays: the seq fence holds it back.
    error, done, nxt = await _pool_events([big, big, after], 3)
    assert error["type"] == "error" and error["code"] == "frame_too_large"
    assert "turn ended" in error["message"]
    assert done == {"type": "turn_complete", "stop_reason": "error", "request_id": "new", "session_id": "s9"}
    assert nxt["type"] == "tool_start"
    # An id JSON escapes is read whole. Kyanite on #543 round 5: 'old"id' was lost,
    # so the completion named no request and ended whichever stream was open.
    odd = {**big, "request_id": 'old"id-\u00e9', "seq": 1}
    assert (await _pool_events([odd], 2))[1]["request_id"] == 'old"id-\u00e9'
    # A chat turn names its requests in request_ids (#551); the stream that sent
    # one is waiting on exactly that completion.
    chat = {"type": "turn_complete", "stop_reason": "error", "error": "x" * 4096, "request_ids": ["r1"], "seq": 1}
    answered = (await _pool_events([chat], 2))[1]
    assert answered["request_ids"] == ["r1"]
    assert not messages._foreign_completion(answered, {"r1"}, tagged=True)

    # The message route ends its stream on it instead of idling 600 s.
    monkeypatch.setattr(messages, "get_session_registry", lambda: types.SimpleNamespace(
        get=lambda sid: _StreamSession([error, done])))
    response = await messages.send_message("s9", messages.MessageRequest(content="hi", request_id="new"))

    async def drain():
        return [item async for item in response.body_iterator]

    sent = await asyncio.wait_for(drain(), 5)
    assert '"turn_complete"' in str(sent[-1])


async def _route_stream(monkeypatch, session, request_id=None):
    """What POST /sessions/{id}/message streams for ``session``'s events."""
    from kollabor_engine.routes import messages

    monkeypatch.setattr(messages, "get_session_registry", lambda: types.SimpleNamespace(get=lambda sid: session))
    response = await messages.send_message("s9", messages.MessageRequest(content="hi", request_id=request_id))

    async def drain():
        return [str(item) async for item in response.body_iterator]

    return await asyncio.wait_for(drain(), 5)


@pytest.mark.asyncio
async def test_a_message_sent_while_a_turn_runs_streams_its_own_reply(monkeypatch):
    # #551: B's stream ended on the untagged completion of turn A, still
    # running when B was sent, and B's reply never streamed.
    session = _StreamSession([
        {"type": "turn_complete", "stop_reason": "end_turn"},
        {"type": "token", "text": "B-OK", "request_ids": ["new"]},
        {"type": "turn_complete", "stop_reason": "end_turn", "request_ids": ["new"]},
    ])
    sent = await _route_stream(monkeypatch, session)
    assert "B-OK" in sent[-2] and '"turn_complete"' in sent[-1]
    # The client sent no id, so the engine made one for the daemon to stamp.
    assert len(session.sent[0]["request_id"]) == 32


@pytest.mark.asyncio
@pytest.mark.parametrize("theirs", [{}, {"request_id": "voice-A"}], ids=["typed", "voice"])
async def test_a_message_sent_while_an_unnamed_turn_runs_gets_none_of_it(monkeypatch, theirs):
    # Chalcedony on #554 round 5: a turn typed in the terminal (no ids) or a
    # voice turn (its own id only) streamed its text and permission prompt
    # into a message sent meanwhile.
    session = _StreamSession([
        {"type": "token", "text": "THEIRS", **theirs},
        {"type": "permission_request", "tool_id": "their-tool", **theirs},
        {"type": "turn_complete", "stop_reason": "end_turn", **theirs},
        {"type": "token", "text": "B-OK", "request_ids": ["new"]},
        {"type": "turn_complete", "stop_reason": "end_turn", "request_ids": ["new"]},
    ])
    sent = await _route_stream(monkeypatch, session)
    assert len(sent) == 2 and "B-OK" in sent[0] and '"turn_complete"' in sent[1]


@pytest.mark.asyncio
async def test_an_admission_that_echoes_no_id_ends_on_any_completion(monkeypatch):
    # A slash command or hub mention publishes an untagged completion of its
    # own; its stream must still end on it.
    session = _StreamSession(
        [{"type": "token", "text": "done"}, {"type": "turn_complete", "stop_reason": "end_turn"}],
        admission={"accepted": True, "reason": "slash command"},
    )
    sent = await _route_stream(monkeypatch, session, request_id="client-1")
    assert '"turn_complete"' in sent[-1]
    assert session.sent[0]["request_id"] == "client-1"


def test_a_tagged_request_ends_only_on_a_completion_naming_it():
    from kollabor_engine.routes.messages import _foreign_completion

    mine = {"new"}
    untagged = {"type": "turn_complete"}
    assert _foreign_completion(untagged, mine, tagged=True)  # a turn running when this was sent
    assert not _foreign_completion(untagged, mine)  # nothing is tagged: as before
    # One batch can answer two requests; a voice turn keeps its own id as well.
    assert not _foreign_completion({"type": "turn_complete", "request_ids": ["old", "new"]}, mine, tagged=True)
    assert _foreign_completion({"type": "turn_complete", "request_ids": ["old"]}, mine, tagged=True)
    assert not _foreign_completion(
        {"type": "turn_complete", "request_id": "voice-1", "request_ids": ["new"]}, mine, tagged=True
    )


def test_a_stream_gets_none_of_another_requests_output():
    # Variscite on #554 round 4: a message sent while turn A ran streamed A's
    # text, tool cards and permission prompt as its own.
    from kollabor_engine.daemon_pool import _normalize_event
    from kollabor_engine.routes.messages import _foreign_output

    mine = {"B"}
    assert _foreign_output({"type": "token", "request_ids": ["A"]}, mine)
    assert _foreign_output({"type": "tool_start", "request_ids": ["A"]}, mine)
    assert not _foreign_output({"type": "token", "request_ids": ["A", "B"]}, mine)  # one batch answers both
    assert not _foreign_output({"type": "token"}, mine)  # session-wide, or a daemon that tags nothing
    assert not _foreign_output({"type": "token", "request_ids": ["A"]}, set())  # a stream with no id
    prompt = _normalize_event(
        {"type": "permission_request", "request_ids": ["A"], "details": {"tool_name": "terminal"}}
    )
    assert prompt["request_ids"] == ["A"] and _foreign_output(prompt, mine)
    # Chalcedony on #554 round 5: a turn typed in the terminal names no request
    # and a voice turn only its own id, so both reached a tagged request.
    assert _foreign_output({"type": "token"}, mine, tagged=True)
    assert _foreign_output({"type": "permission_request", "tool_id": "t1"}, mine, tagged=True)
    assert _foreign_output({"type": "token", "request_id": "voice-A"}, mine)
    assert not _foreign_output({"type": "token", "request_id": "B"}, mine, tagged=True)  # an ACP turn names it so
    assert not _foreign_output({"type": "permission_granted", "tool_id": "t1"}, mine, tagged=True)  # by its tool
    assert not _foreign_output({"type": "daemon_closed"}, mine, tagged=True)  # session-wide
    # Danburite on #554 round 6: a parked card's answer names the turn that
    # showed it, and the later message that gives it must still close the card.
    answer = {"type": "permission_denied", "tool_id": "t1", "request_ids": ["A"]}
    assert not _foreign_output(answer, mine, tagged=True)


def test_only_this_requests_completion_ends_its_stream():
    # Kyanite on #543 round 2: an earlier turn's turn_complete, missed during
    # the outage, arrived after the reattach with a higher seq and ended the
    # stream of a request accepted meanwhile.
    from kollabor_engine.routes.messages import _foreign_completion

    mine = {"new"}
    assert _foreign_completion({"type": "turn_complete", "request_id": "old"}, mine)
    assert not _foreign_completion({"type": "turn_complete", "request_id": "new"}, mine)
    # A completion with no request id, or a stream with none, ends as before.
    assert not _foreign_completion({"type": "turn_complete"}, mine)
    assert not _foreign_completion({"type": "turn_complete", "request_id": "old"}, set())
    assert not _foreign_completion({"type": "tool_result", "request_id": "old"}, mine)
    # A cancelled pre-start client request never ends another stream.
    queued = {"type": "turn_complete", "input_source": "acp_client", "queued": True}
    assert _foreign_completion(queued, mine) and _foreign_completion(queued, set())


@pytest.mark.asyncio
async def test_an_old_turns_dropped_frame_fails_no_other_request(monkeypatch):
    # Tanzanite on #543 round 5: after a reattach, an old turn's oversized
    # completion replayed into a new request's stream. Its frame_too_large error
    # named no request, so the new reply was marked failed.
    from kollabor_engine.routes import messages

    import kollabor_rpc.transport

    monkeypatch.setattr(kollabor_rpc.transport, "LARGE_BUFFER_LIMIT", 1024)
    old = {"type": "turn_complete", "request_id": "old", "stop_reason": "error", "error": "x" * 4096, "seq": 3}
    error, stale = await _pool_events([old], 2)
    assert error["code"] == "frame_too_large" and error["request_id"] == stale["request_id"] == "old"
    assert messages._foreign_error(error, {"new"}) and not messages._foreign_error(error, {"old"})
    assert not messages._foreign_error({"type": "error", "message": "x"}, {"new"})  # names none: everyone's

    reply = [  # a daemon that echoes the id names it on its turn's events (#551)
        {"type": "token", "text": "fine", "request_id": "new"},
        {"type": "turn_complete", "request_id": "new", "stop_reason": "end_turn"},
    ]
    monkeypatch.setattr(messages, "get_session_registry", lambda: types.SimpleNamespace(
        get=lambda sid: _StreamSession([error, stale, *reply])))
    response = await messages.send_message("s9", messages.MessageRequest(content="hi", request_id="new"))

    async def drain():
        return [str(item) async for item in response.body_iterator]

    sent = "".join(await asyncio.wait_for(drain(), 5))
    assert "frame_too_large" not in sent and '"fine"' in sent and '"new"' in sent


@pytest.mark.asyncio
async def test_a_request_id_inside_a_tool_frame_names_no_request(monkeypatch):
    # Tourmaline on #543 round 6: an oversized tool frame's input held a
    # request_id of its own, read before the turn's, so another request got the error.
    from kollabor_tui.display_tap import DisplayTap, publish_semantic
    from kollabor_tui.turn_context import current_turn

    import kollabor_rpc.transport

    monkeypatch.setattr(kollabor_rpc.transport, "LARGE_BUFFER_LIMIT", 1024)
    tap = DisplayTap()
    events = tap.subscribe("probe")
    token = current_turn.set({"request_id": "old"})
    try:  # the tap stamps the turn after the event's own fields, so after the input
        publish_semantic(
            tap, "tool_start", tool_id="t", tool_name="mcp", tool_type="mcp",
            input={"request_id": "new", "body": "x" * 10000},
        )
    finally:
        current_turn.reset(token)
    error, = await _pool_events([events.get_nowait()], 1)
    assert error["code"] == "frame_too_large" and error["request_id"] == "old"

    # Untagged, with the nested key in the head and in the tail: it names none.
    nested = {"type": "tool_result", "output": {"request_id": "a"}, "pad": "x" * 10000,
              "more": {"request_id": "b"}}
    error, = await _pool_events([nested], 1)
    assert error["code"] == "frame_too_large" and "request_id" not in error


async def _events_across_images(images, count):
    """The (request_id, seq) the engine hands on while successive daemon images serve it.

    ``images`` is a list of ``(agent_id, events)``. Each image acks with its agent_id and
    sends its events; every image but the last then drops the attacher, as the exec that
    /restart ends in does (same pid, same socket path; #968).
    """
    import shutil

    attaches = []

    async def daemon(reader, writer):
        await reader.readline()
        attaches.append("attach")
        agent_id, events = images[min(len(attaches), len(images)) - 1]
        writer.write((json.dumps({"type": "attach_ack", "agent_id": agent_id}) + "\n").encode())
        for event in events:
            writer.write((json.dumps(event) + "\n").encode())
        await writer.drain()
        if len(attaches) < len(images):
            writer.close()
            return
        await reader.read()

    folder = tempfile.mkdtemp(dir="/tmp")
    server = await asyncio.start_unix_server(daemon, os.path.join(folder, "opal.sock"))
    handle = DaemonHandle("s9", "opal", launcher=None)
    handle.pid = os.getpid()
    _fast_reattach(handle)
    queue = handle.subscribe()
    seen = []
    try:
        await handle.connect(os.path.join(folder, "opal.sock"))
        for _ in range(count):
            try:
                seen.append(await asyncio.wait_for(queue.get(), timeout=2))
            except asyncio.TimeoutError:
                break
        await asyncio.sleep(0.05)
        while not queue.empty():
            seen.append(queue.get_nowait())
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()
        shutil.rmtree(folder, ignore_errors=True)
    return [(e["request_id"], e["seq"]) for e in seen]


def _done(request_id, seq):
    return {"type": "turn_complete", "request_id": request_id, "seq": seq}


@pytest.mark.asyncio
async def test_a_restarted_daemon_image_numbers_its_events_from_one_again():
    # #968: /restart execs the daemon (same pid, same socket) and the new image's
    # display tap counts from seq 1 again. Its attach_ack names the image by agent_id.
    # The fence that drops a reattach's replays belongs to one image; kept across the
    # exec it dropped every event of the restarted agent, so the chat got no reply.
    before = [_done("old", n) for n in (1, 2, 3, 4, 5)]
    after = [_done("new-1", 1), _done("new-2", 2)]
    seen = await _events_across_images([("image-one", before), ("image-two", after)], 7)
    assert seen == [("old", 1), ("old", 2), ("old", 3), ("old", 4), ("old", 5),
                    ("new-1", 1), ("new-2", 2)]


@pytest.mark.asyncio
async def test_the_same_daemon_image_replaying_after_a_reattach_is_still_fenced():
    # The #543 fence stays for one image: an ack naming the same agent_id again is the
    # same process replaying its recent events, which the engine already passed on.
    first = [_done("a", 1), _done("a", 2)]
    replay = [_done("a", 1), _done("a", 2), _done("b", 3)]
    seen = await _events_across_images([("image-one", first), ("image-one", replay)], 3)
    assert seen == [("a", 1), ("a", 2), ("b", 3)]


@pytest.mark.asyncio
async def test_a_restart_announcement_is_for_terminal_windows_only():
    # The daemon tells its attached windows a restart is coming (#968) so a terminal
    # window waits and attaches again. The engine reattaches by itself and its chats
    # say it their own way: the web UI never receives the announcement.
    announcement = {"type": "daemon_restarting", "identity": "citrine", "seq": 1}
    after = {"type": "tool_start", "request_id": "r1", "seq": 2}

    async def daemon(reader, writer):
        await reader.readline()
        writer.write(b'{"type": "attach_ack"}\n')
        for event in (announcement, after):
            writer.write((json.dumps(event) + "\n").encode())
        await writer.drain()
        await reader.read()

    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "citrine.sock")
    server = await asyncio.start_unix_server(daemon, path)
    handle = DaemonHandle("s8", "citrine", launcher=None)
    handle.pid = os.getpid()
    queue = handle.subscribe()
    try:
        await handle.connect(path)
        seen = await asyncio.wait_for(queue.get(), timeout=5)
        assert seen["type"] == "tool_start"  # the announcement was dropped at the boundary
        await asyncio.sleep(0.05)
        assert queue.empty()
    finally:
        handle._closed = True
        handle._read_task.cancel()
        server.close()
