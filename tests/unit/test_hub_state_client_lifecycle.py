"""Regression tests for HubStateClient connection teardown."""

import asyncio
import socket
import tempfile
from pathlib import Path
from typing import Any

import pytest

from kollabor.state.hub_client import HubStateClient


class _CloseFailingWriter:
    def __init__(self) -> None:
        self.wait_closed_called = False

    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        raise OSError("already closed")

    async def wait_closed(self) -> None:
        self.wait_closed_called = True


@pytest.mark.asyncio
async def test_connect_waits_for_writer_even_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = asyncio.StreamReader()
    writer = _CloseFailingWriter()
    # The owner guard requires a real socket file this user owns; AF_UNIX
    # bind needs a short path on macOS.
    with tempfile.TemporaryDirectory(prefix="hsc-", dir="/tmp") as root:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        socket_path = Path(root) / "peer.sock"
        server.bind(str(socket_path))
        try:
            await _exercise_connect(monkeypatch, socket_path, reader, writer)
        finally:
            server.close()
    assert writer.wait_closed_called


async def _exercise_connect(monkeypatch, socket_path, reader, writer) -> None:
    async def open_connection(_socket_path: str) -> tuple[Any, Any]:
        return reader, writer

    monkeypatch.setattr(
        HubStateClient,
        "discover_peer_socket",
        staticmethod(lambda _identity: socket_path),
    )
    monkeypatch.setattr(
        "kollabor_rpc.open_unix_connection_with_large_buffer", open_connection
    )

    async with HubStateClient.connect("peer"):
        pass
