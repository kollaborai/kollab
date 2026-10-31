"""A live daemon that drops the engine (its hub socket was re-created) is reattached."""

import asyncio
import functools
import json
import os
import tempfile
import time

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
