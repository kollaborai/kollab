"""Real-pipe regression tests for MCP server diagnostics."""

import asyncio
import logging
import subprocess
import sys

import pytest

from kollabor_agent.mcp_integration import MCPRequestTimeoutError, MCPServerConnection


async def _wait_for_diagnostic_bytes(connection, count):
    async def wait():
        while connection._stderr_bytes < count:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout=5)


def test_noisy_server_does_not_block_concurrent_requests(tmp_path):
    server = tmp_path / "noisy_server.py"
    server.write_text(
        "import json, sys\n"
        "requests = []\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    requests.append(request)\n"
        "    if len(requests) == 2:\n"
        "        for request in reversed(requests):\n"
        "            sys.stderr.buffer.write(b'x' * 1048576)\n"
        "            sys.stderr.buffer.flush()\n"
        "            print(json.dumps({'id': request['id'], 'result': request['id']}), flush=True)\n",
        encoding="utf-8",
    )

    async def run():
        command = subprocess.list2cmdline([sys.executable, "-u", str(server)])
        connection = MCPServerConnection("noisy", command, request_timeout=10)
        assert await connection.connect()
        process = connection.process
        tasks = [
            asyncio.create_task(
                connection._send_request({"id": str(i), "method": "test"})
            )
            for i in range(2)
        ]
        try:
            done, _ = await asyncio.wait(tasks, timeout=3)
            assert len(done) == 2, "stderr backpressure blocked responses"
            assert [task.result()["result"] for task in tasks] == ["0", "1"]
            await _wait_for_diagnostic_bytes(connection, 2 * 1048576)
            assert connection._stderr_bytes == 2 * 1048576
        finally:
            reader = getattr(connection, "_stderr_task", None)
            if (
                any(not task.done() for task in tasks)
                and process
                and process.stderr
                and reader is None
            ):
                drain = asyncio.create_task(process.stderr.read())
            else:
                drain = None
            await asyncio.gather(*tasks, return_exceptions=True)
            await connection.close()
            if drain:
                await drain

    asyncio.run(run())


def test_noisy_startup_can_initialize(tmp_path):
    server = tmp_path / "starting_server.py"
    server.write_text(
        "import json, sys\n"
        "sys.stderr.buffer.write(b'x' * 1048576)\n"
        "sys.stderr.buffer.flush()\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'id': request['id'], 'result': {}}), flush=True)\n"
        "sys.stdin.readline()\n",
        encoding="utf-8",
    )

    async def run():
        command = subprocess.list2cmdline([sys.executable, "-u", str(server)])
        connection = MCPServerConnection("starting", command, startup_timeout=5)
        assert await connection.connect()
        process = connection.process
        reader = connection._stderr_task
        try:
            assert await connection.initialize()
            await _wait_for_diagnostic_bytes(connection, 1048576)
            assert connection._stderr_bytes == 1048576
        finally:
            await connection.close()
        assert process is not None and process.returncode is not None
        assert reader is not None and reader.done()
        assert connection._stderr_task is None

    asyncio.run(run())


def test_noisy_startup_failure_does_not_log_payload(tmp_path, caplog):
    server = tmp_path / "failed_server.py"
    server.write_text(
        "import sys\n"
        "sys.stdin.readline()\n"
        "sys.stderr.buffer.write(b'x' * 1048576 + b'SECRET-DIAGNOSTIC')\n"
        "sys.stderr.buffer.flush()\n",
        encoding="utf-8",
    )

    async def run():
        command = subprocess.list2cmdline([sys.executable, "-u", str(server)])
        connection = MCPServerConnection("failed", command, startup_timeout=5)
        assert await connection.connect()
        process = connection.process
        reader = connection._stderr_task
        try:
            assert not await connection.initialize()
            assert process is not None
            await asyncio.wait_for(process.wait(), timeout=5)
        finally:
            await connection.close()
        assert connection._stderr_bytes == 1048576 + len(b"SECRET-DIAGNOSTIC")
        assert reader is not None and reader.done()
        assert connection._stderr_task is None
        assert process.returncode is not None

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    assert "SECRET-DIAGNOSTIC" not in caplog.text


@pytest.mark.parametrize("cancel", [False, True])
def test_noisy_timeout_and_cancellation_clean_up_and_reconnect(tmp_path, cancel):
    server = tmp_path / "hanging_server.py"
    server.write_text(
        "import sys, time\n"
        "sys.stderr.buffer.write(b'x' * 1048576)\n"
        "sys.stderr.buffer.flush()\n"
        "sys.stdin.readline()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    async def run():
        command = subprocess.list2cmdline([sys.executable, "-u", str(server)])
        connection = MCPServerConnection("hanging", command, request_timeout=2)
        assert await connection.connect()
        process = connection.process
        reader = connection._stderr_task
        try:
            request = asyncio.create_task(
                connection._send_request({"id": "1", "method": "test"}, timeout=2)
            )
            if cancel:
                await _wait_for_diagnostic_bytes(connection, 1048576)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            else:
                with pytest.raises(MCPRequestTimeoutError):
                    await request
            assert process is not None and process.returncode is not None
            assert reader is not None and reader.done()
            assert connection._stderr_task is None
            assert connection._pending_requests == {}
            assert await connection.connect()
            assert connection._stderr_bytes == 0
        finally:
            await connection.close()
        assert connection.process is None
        assert connection._stderr_task is None

    asyncio.run(run())
