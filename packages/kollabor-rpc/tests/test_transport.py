"""Tests for the transport helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kollabor_rpc import (
    LARGE_BUFFER_LIMIT,
    open_unix_connection_with_large_buffer,
)


@pytest.fixture
def socket_path(tmp_path: Path) -> Path:
    """Per-test socket; use a short /tmp path to stay under macOS ~104 char limit."""
    import uuid

    return Path(f"/tmp/krpc-t{uuid.uuid4().hex[:8]}.sock")


def test_large_buffer_limit_is_16mb() -> None:
    """Sanity check: LARGE_BUFFER_LIMIT is exactly 16 MB."""
    assert LARGE_BUFFER_LIMIT == 16 * 1024 * 1024


@pytest.mark.asyncio
async def test_helper_reads_line_larger_than_64kb(socket_path: Path) -> None:
    """A single line > 64KB must not trigger the default buffer limit.

    This is the regression test for the phase 2 smoke test finding:
    state.save_conversation returned 67KB which exceeded the default
    asyncio.open_unix_connection() reader limit.
    """
    big_payload = {"data": "x" * 80_000}  # ~80KB string value
    big_line = json.dumps(big_payload).encode() + b"\n"
    assert len(big_line) > 64 * 1024  # Confirm it actually exceeds 64KB

    async def handle_connection(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Server writes one big line then closes
        writer.write(big_line)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    server = await asyncio.start_unix_server(handle_connection, path=str(socket_path))
    try:
        reader, writer = await open_unix_connection_with_large_buffer(str(socket_path))
        try:
            line = await reader.readline()
            decoded = json.loads(line.decode().strip())
            assert decoded == big_payload
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
    finally:
        server.close()
        await server.wait_closed()
        if socket_path.exists():
            try:
                socket_path.unlink()
            except OSError:
                pass
