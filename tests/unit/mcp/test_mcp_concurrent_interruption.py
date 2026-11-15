"""Concurrency and shutdown behavior for MCP stdio requests."""

import asyncio
import json
import unittest

from kollabor_agent.mcp_integration import (
    MCPRequestInterruptedError,
    MCPRequestTimeoutError,
    MCPServerConnection,
)


class _QueuedStdout:
    def __init__(self):
        self.chunks = asyncio.Queue()

    async def read(self, _size):
        return await self.chunks.get()

    def send(self, message):
        self.chunks.put_nowait((json.dumps(message) + "\n").encode())

    def eof(self):
        self.chunks.put_nowait(b"")

    def close(self):
        pass


class _Stdin:
    def __init__(self, drain_gate=None):
        self.writes = []
        self.drain_gate = drain_gate
        self.closing = False

    def write(self, data):
        self.writes.append(json.loads(data))

    async def drain(self):
        if self.drain_gate is not None:
            await self.drain_gate.wait()

    def is_closing(self):
        return self.closing

    def close(self):
        self.closing = True


class _Stderr:
    async def read(self):
        return b""

    def close(self):
        pass


class _Process:
    def __init__(self, drain_gate=None):
        self.stdin = _Stdin(drain_gate)
        self.stdout = _QueuedStdout()
        self.stderr = _Stderr()
        self.returncode = None

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class TestMCPConcurrentInterruption(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = MCPServerConnection("test", "fake-server")
        self.connection.initialized = True
        self.process = _Process()
        self.connection.process = self.process

    async def _wait_for_writes(self, count):
        while len(self.process.stdin.writes) < count:
            await asyncio.sleep(0)

    async def test_late_response_after_cancel_does_not_dispatch_again(self):
        first = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "side-effect", "method": "tools/call"}
            )
        )
        second = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "peer", "method": "tools/call"}
            )
        )
        await self._wait_for_writes(2)

        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        with self.assertRaisesRegex(
            MCPRequestInterruptedError, "outcome may be unknown"
        ):
            await second

        self.connection._dispatch_incoming_message(
            {"jsonrpc": "2.0", "id": "side-effect", "result": {"ok": True}}
        )
        self.assertEqual([item["id"] for item in self.process.stdin.writes], ["side-effect", "peer"])
        self.assertEqual(self.connection._pending_requests, {})
        self.assertEqual(self.process.returncode, 0)

    async def test_timeout_before_peer_write_reports_not_written(self):
        gate = asyncio.Event()
        self.process.stdin.drain_gate = gate
        first = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "slow", "method": "tools/call"},
                timeout=0.02,
            )
        )
        second = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "queued", "method": "tools/call"},
                timeout=1,
            )
        )
        await self._wait_for_writes(1)

        with self.assertRaises(MCPRequestTimeoutError) as timeout_error:
            await first
        self.assertIn("outcome may be unknown", str(timeout_error.exception))
        with self.assertRaisesRegex(MCPRequestInterruptedError, "request was not written"):
            await second

        self.assertEqual([item["id"] for item in self.process.stdin.writes], ["slow"])
        self.assertEqual(self.connection._pending_requests, {})
        self.assertEqual(self.process.returncode, 0)

    async def test_timeout_after_write_marks_peer_outcome_unknown(self):
        first = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "slow", "method": "tools/call"},
                timeout=0.02,
            )
        )
        second = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "peer", "method": "tools/call"},
                timeout=1,
            )
        )
        await self._wait_for_writes(2)

        with self.assertRaises(MCPRequestTimeoutError) as timeout_error:
            await first
        self.assertIn("outcome may be unknown", str(timeout_error.exception))
        with self.assertRaisesRegex(
            MCPRequestInterruptedError, "outcome may be unknown"
        ):
            await second

        self.assertEqual([item["id"] for item in self.process.stdin.writes], ["slow", "peer"])
        self.assertEqual(self.connection._pending_requests, {})
        self.assertEqual(self.process.returncode, 0)

    async def test_call_tool_returns_peer_interruption_to_caller(self):
        peer = asyncio.create_task(
            self.connection.call_tool("side-effect", {"value": 1})
        )
        canceling = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "cancel", "method": "tools/call"}
            )
        )
        await self._wait_for_writes(2)
        canceling.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await canceling

        result = await peer
        self.assertIn("interrupted", result["error"])
        self.assertIn("outcome may be unknown", result["error"])
        self.assertEqual(len(self.process.stdin.writes), 2)

    async def test_server_eof_explicitly_interrupts_written_requests(self):
        first = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "eof-1", "method": "tools/call"}
            )
        )
        second = asyncio.create_task(
            self.connection._send_request(
                {"jsonrpc": "2.0", "id": "eof-2", "method": "tools/call"}
            )
        )
        await self._wait_for_writes(2)
        self.process.stdout.eof()

        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(all(isinstance(item, MCPRequestInterruptedError) for item in results))
        self.assertTrue(all("outcome may be unknown" in str(item) for item in results))
        self.assertFalse(self.connection.initialized)
        self.assertEqual(self.connection._pending_requests, {})
        await self.connection.close()
        self.assertEqual(self.process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
