"""Deep Thought IPC over kollabor_config.local_ipc: a real server and client."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from plugins.deep_thought import socket_server


@pytest.mark.asyncio
async def test_ipc(tmp_path, monkeypatch):
    # Windows only trusts endpoint folders inside USERPROFILE; macOS caps AF_UNIX
    # paths near 104 bytes, and pytest's tmp_path is longer.
    home = Path(tempfile.mkdtemp(dir=tmp_path if os.name == "nt" else "/tmp"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(home))
    server = socket_server.ThoughtServer("ipc")
    server.socket_path = home / "ipc.sock"
    client = None
    try:
        await server.start(expected_count=1)
        client = socket_server.ThoughtClient(str(server.socket_path), "child-1", "tree")
        assert await client.connect()
        await client.send_chunk("reasoning")
        await client.send_done("final answer")
        results = await server.wait_all(timeout=5)
        assert results["child-1"]["chunks"] == ["reasoning"]
        assert results["child-1"]["content"] == "final answer"
    finally:
        if client is not None:
            await client.close()
        await server.stop()
        shutil.rmtree(home, ignore_errors=True)
