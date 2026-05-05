"""Transport helpers for kollabor_rpc.

The default asyncio StreamReader buffer is 64KB which is too small for
real state snapshots (daemon conversation history with expanded system
prompts commonly exceeds 64KB). This module provides a helper that
creates connections with a larger limit (16MB).

The 16MB cap is deliberate -- it's large enough that real state payloads
always fit, but small enough to bound memory use if a malicious or buggy
peer spams oversized frames.
"""

from __future__ import annotations

import asyncio

# 16 megabytes. Fits any realistic state payload including full conversation
# history with a large system prompt. Chosen to be large enough that we
# don't have to think about chunking, small enough to bound memory use.
LARGE_BUFFER_LIMIT = 16 * 1024 * 1024


async def open_unix_connection_with_large_buffer(
    path: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a unix connection with a 16MB StreamReader buffer.

    Drop-in replacement for asyncio.open_unix_connection that avoids
    the default 64KB readline buffer limit. Used by RpcClient and any
    other kollabor_rpc consumer that reads large NDJSON frames.

    Args:
        path: Path to the unix socket.

    Returns:
        (reader, writer) tuple just like asyncio.open_unix_connection.
    """
    reader = asyncio.StreamReader(limit=LARGE_BUFFER_LIMIT)
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_unix_connection(lambda: protocol, path=path)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return reader, writer
