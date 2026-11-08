"""#880: an agent on another device answers over the relay, so its state calls get a longer budget."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from kollabor_engine import daemon_pool

from kollabor.state.remote import RemoteStateService


async def _adopt(row_extra):
    # Short dir: unix socket paths are capped near 104 bytes.
    sock_dir = tempfile.mkdtemp(prefix="k880-", dir="/tmp")
    sock = str(Path(sock_dir) / "k.sock")

    async def agent(reader, writer):
        json.loads(await reader.readline())
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        while await reader.readline():
            pass

    server = await asyncio.start_unix_server(agent, path=sock)
    pool = daemon_pool.DaemonPool()
    try:
        handle = await pool.adopt(
            "s880",
            {
                "identity": "koordinator",
                "agent": "default",
                "daemon_pid": 0,
                "socket_path": sock,
                **row_extra,
            },
        )
        timeout = handle.state._timeout
        await handle.close(stop_process=False)
        return timeout
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_an_agent_on_another_device_gets_the_remote_state_budget():
    assert await _adopt({"device": "alarchy-home"}) >= 60.0


@pytest.mark.asyncio
async def test_a_local_agent_keeps_the_default_state_budget():
    assert await _adopt({}) == RemoteStateService.DEFAULT_TIMEOUT
