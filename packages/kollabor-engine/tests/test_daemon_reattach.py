"""A live daemon that drops the engine (its hub socket was re-created) is reattached."""

import asyncio
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
