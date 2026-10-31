"""A live daemon that drops the engine (its hub socket was re-created) is reattached."""

import asyncio
import functools
import json
import os
import tempfile

import pytest
from kollabor_engine.daemon_pool import DaemonHandle


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
