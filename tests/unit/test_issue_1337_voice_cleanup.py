"""Cancellation must not wait for a descendant to close inherited pipes."""

import asyncio
import tempfile
from unittest.mock import AsyncMock

import pytest

from plugins.hub.messaging_bridge import TelegramBridge


class _Response:
    content = b"synthetic audio"

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True, "result": {"file_path": "voice.ogg"}}


class _OwnerProcess:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.pipe_open = asyncio.Event()
        self.communication_started = asyncio.Event()
        self.returncode = -15
        self.stdout = None
        self.stderr = None

    async def communicate(self, _input=None):
        self.run_dir.mkdir()
        self.communication_started.set()
        await self.pipe_open.wait()
        return b"", b""

    def terminate(self):
        pass

    async def wait(self):
        self.run_dir.rmdir()


@pytest.mark.asyncio
async def test_cancellation_finishes_when_descendant_keeps_pipe_open(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    run_dir = tmp_path / "kollab-telegram-voice-owned"
    process = _OwnerProcess(run_dir)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    bridge = TelegramBridge(token="synthetic-secret", chat_id="456")
    bridge._client = AsyncMock()
    bridge._client.get.return_value = _Response()

    task = asyncio.create_task(bridge._transcribe_voice("synthetic-file-id"))
    try:
        await process.communication_started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=2)

        assert not process.pipe_open.is_set()
        assert not run_dir.exists()
    finally:
        process.pipe_open.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
