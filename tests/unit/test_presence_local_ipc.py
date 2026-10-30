"""Hub presence liveness checks, run against a real local IPC server."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from kollabor_config import local_ipc
from plugins.hub.presence import PresenceManager


async def _close_at_once(reader, writer):
    writer.close()


@pytest.fixture
def home_in_tmp(tmp_path, monkeypatch):
    # Windows only trusts endpoint folders inside USERPROFILE; macOS caps AF_UNIX
    # paths near 104 bytes, and pytest's tmp_path is longer.
    with tempfile.TemporaryDirectory(dir=tmp_path if os.name == "nt" else "/tmp") as d:
        monkeypatch.setenv("HOME", d)
        monkeypatch.setenv("USERPROFILE", d)
        yield Path(d)


@pytest.mark.asyncio
async def test_socket_responds_async(home_in_tmp):
    served = home_in_tmp / "agent.sock"
    endpoint = await local_ipc.serve(_close_at_once, served)
    try:
        assert await PresenceManager._socket_responds_async(str(served)) is True
        missing = home_in_tmp / "missing.sock"
        assert await PresenceManager._socket_responds_async(str(missing)) is False
    finally:
        endpoint.close()


@pytest.mark.asyncio
async def test_socket_responds(home_in_tmp):
    served = home_in_tmp / "agent.sock"
    endpoint = await local_ipc.serve(_close_at_once, served)
    try:
        # The blocking check runs in a thread so the server loop can answer it.
        responds = PresenceManager._socket_responds
        assert await asyncio.to_thread(responds, str(served)) is True
        missing = home_in_tmp / "missing.sock"
        assert await asyncio.to_thread(responds, str(missing)) is False
    finally:
        endpoint.close()
