"""Tests for peer credential checking in AgentSocketServer.

Verifies that:
1. Same-UID connections are accepted (happy path).
2. Cross-UID connections are rejected.
3. Unsupported platforms gracefully allow connections.
4. Socket retrieval failures degrade gracefully.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from plugins.hub.messenger import AgentSocketServer


@pytest.fixture
def short_socket_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect hub socket directory to a short /tmp path."""
    short_dir = Path(f"/tmp/khrpc-cred-{uuid.uuid4().hex[:8]}")
    short_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "plugins.hub.messenger.get_socket_dir",
        lambda: short_dir,
    )
    yield short_dir
    try:
        for f in short_dir.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        short_dir.rmdir()
    except OSError:
        pass


@pytest_asyncio.fixture
async def running_server(
    short_socket_dir: Path,
) -> AsyncIterator[AgentSocketServer]:
    """Start a minimal AgentSocketServer for credential testing."""

    async def _on_message(msg: Any) -> None:
        pass

    agent_id = f"t{uuid.uuid4().hex[:6]}"
    server = AgentSocketServer(agent_id=agent_id, on_message=_on_message)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def _connect_raw(socket_path: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a raw unix socket connection to the server."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    return reader, writer


async def _send_ping_and_read_pong(
    writer: asyncio.StreamWriter,
    reader: asyncio.StreamReader,
) -> dict[str, Any]:
    """Send a ping action and return the parsed response."""
    writer.write((json.dumps({"action": "ping"}) + "\n").encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.readline(), timeout=2.0)
    assert raw, "Expected pong response, got EOF"
    return json.loads(raw.decode())


@pytest.mark.asyncio
async def test_same_uid_connection_accepted(running_server: AgentSocketServer) -> None:
    """A connection from the same UID should not be rejected."""
    # The default _get_peer_credentials returns the real peer UID.
    # Since the test connects from the same process, UIDs match.
    reader, writer = await _connect_raw(str(running_server.socket_path))

    # Ping confirms we got past the credential check and into the normal
    # request loop.
    response = await _send_ping_and_read_pong(writer, reader)
    assert response["type"] == "pong"
    assert response["agent_id"] == running_server.agent_id

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_cross_uid_connection_rejected(running_server: AgentSocketServer) -> None:
    """A connection from a different UID should be rejected and closed."""
    our_uid = os.getuid()
    fake_uid = our_uid + 9999  # definitely not us

    with patch.object(
        running_server,
        "_get_peer_credentials",
        return_value=(12345, fake_uid),
    ):
        reader, writer = await _connect_raw(str(running_server.socket_path))

        # Server should close the connection. Attempting to read should
        # hit EOF quickly since the server rejected us.
        try:
            raw = await asyncio.wait_for(reader.read(1024), timeout=2.0)
            # If we get data, connection wasn't rejected - fail
            assert raw == b"", f"Expected EOF, got {raw!r}"
        except asyncio.TimeoutError:
            # Connection still open after 2s - server didn't reject
            pytest.fail("Server did not close connection for cross-UID peer")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_unsupported_platform_allows_connection(
    running_server: AgentSocketServer,
) -> None:
    """When _get_peer_credentials returns None, connection should be allowed."""
    with patch.object(
        running_server,
        "_get_peer_credentials",
        return_value=None,
    ):
        reader, writer = await _connect_raw(str(running_server.socket_path))

        # Should be able to ping - connection not rejected.
        response = await _send_ping_and_read_pong(writer, reader)
        assert response["type"] == "pong"
        assert response["agent_id"] == running_server.agent_id

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_get_peer_credentials_no_socket() -> None:
    """_get_peer_credentials returns None when socket is unavailable."""
    writer = MagicMock()
    writer.transport.get_extra_info.return_value = None

    result = AgentSocketServer._get_peer_credentials(writer)
    assert result is None


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="getsockopt mock only safe on Linux")
async def test_get_peer_credentials_oserror_linux() -> None:
    """_get_peer_credentials returns None on OSError from getsockopt (Linux)."""
    writer = MagicMock()
    mock_sock = MagicMock()
    mock_sock.getsockopt.side_effect = OSError("mock error")
    writer.transport.get_extra_info.return_value = mock_sock

    result = AgentSocketServer._get_peer_credentials(writer)
    assert result is None


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "darwin", reason="ctypes path only on macOS")
async def test_get_peer_credentials_macos_returns_uid() -> None:
    """On macOS, _get_peer_credentials returns (None, uid) for a real socket."""
    # Can't safely mock the ctypes path (segfault risk with fake fds).
    # Instead, verify it works against a real unix socket pair.
    import socket as sock_mod

    srv, cli = sock_mod.socketpair(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
    try:
        # Build a minimal writer-like object wrapping the server end.
        # _get_peer_credentials only needs transport.get_extra_info("socket").
        mock_transport = MagicMock()
        mock_transport.get_extra_info.return_value = srv

        mock_writer = MagicMock()
        mock_writer.transport = mock_transport

        result = AgentSocketServer._get_peer_credentials(mock_writer)
        # Should return (None, our_uid) on macOS
        assert result is not None
        _pid, uid = result
        assert uid == os.getuid()
    finally:
        srv.close()
        cli.close()
