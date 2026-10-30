"""waiter_line asks a halt waiter's status socket, on both platforms."""

import asyncio
import shutil
import tempfile
import threading
from pathlib import Path

import pytest
from kollabor_engine.hub_bridge import WAITER_FALLBACK, waiter_line

from kollabor_config import local_ipc


@pytest.fixture
def sock_path(tmp_path):
    # unix socket paths are capped near 104 bytes on macOS, so fall back to a short dir
    if len(str(tmp_path / "w.sock")) <= 100:
        yield tmp_path / "w.sock"
        return
    short = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        yield short / "w.sock"
    finally:
        shutil.rmtree(short, ignore_errors=True)


def _serve_halt_reply(sock_path: Path) -> None:
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def handler(reader, writer):
        await reader.readline()
        writer.write(b'{"halted": "Network halted by test."}\n')
        await writer.drain()
        writer.close()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(local_ipc.serve(handler, sock_path))
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(5)


def test_waiter_line_returns_the_halted_line(sock_path):
    _serve_halt_reply(sock_path)

    assert waiter_line(sock_path) == "Network halted by test."


def test_waiter_line_falls_back_when_nothing_listens(sock_path):
    assert waiter_line(sock_path.with_name("nothing.sock")) == WAITER_FALLBACK
