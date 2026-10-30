"""The attach connects reach a real local_ipc server (#268, wave 1)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_config import local_ipc


@pytest.fixture
def sock(tmp_path, monkeypatch):
    # Windows only trusts endpoint folders inside USERPROFILE.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # macOS caps unix socket paths at 104 bytes; a short relative name
    # keeps the socket inside tmp_path.
    monkeypatch.chdir(tmp_path)
    return Path("lapis.sock")


async def _serve_once(path, seen):
    """Serve on path: record each connection, read its request, then close."""

    async def handler(reader, writer):
        seen.append(True)
        await reader.readline()
        writer.close()

    return await local_ipc.serve(handler, path)


@pytest.mark.asyncio
async def test_attach_client_connects_through_local_ipc(sock, capsys):
    from kollabor.attach_client import AttachClient

    seen = []
    endpoint = await _serve_once(sock, seen)
    try:
        await AttachClient(str(sock), "lapis", interactive=False).run()
    finally:
        endpoint.close()
    assert seen == [True]
    assert "attach failed: connection closed" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_attach_proxy_connects_through_local_ipc(sock):
    from kollabor.application import TerminalLLMChat

    seen, shown = [], []
    endpoint = await _serve_once(sock, seen)
    try:
        app = TerminalLLMChat.__new__(TerminalLLMChat)
        app._attach_to = "lapis"
        app._attach_socket = str(sock)
        app._startup_ready = asyncio.Event()
        app.running = True
        app.renderer = SimpleNamespace(
            message_coordinator=SimpleNamespace(display_message_sequence=shown.extend)
        )
        await app._initialize_attach_proxy()
    finally:
        endpoint.close()
    assert seen == [True]
    assert "attach failed" in shown[-1][1]
