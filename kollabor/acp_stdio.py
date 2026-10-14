"""Reserve stdout for a protocol and route all ordinary output to stderr."""
from __future__ import annotations

import asyncio
import io
import os
import sys
from typing import Any


def isolated_stdout() -> io.TextIOWrapper:
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.set_inheritable(protocol_fd, False)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(protocol_fd, "w", encoding="utf-8", buffering=1)


async def acp_streams(protocol: Any) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=50 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    class Protocol(asyncio.streams.FlowControlMixin):
        def __init__(self):
            super().__init__(loop=loop)
            self.closed = loop.create_future()

        def connection_lost(self, exc):
            super().connection_lost(exc)
            if not self.closed.done():
                self.closed.set_result(None)

        def _get_close_waiter(self, stream):
            return self.closed

    flow = Protocol()
    transport, _ = await loop.connect_write_pipe(lambda: flow, protocol)
    writer = asyncio.StreamWriter(transport, flow, None, loop)
    return reader, writer
