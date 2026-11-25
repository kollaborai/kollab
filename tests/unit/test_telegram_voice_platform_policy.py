"""Telegram transcription off Linux runs whisper directly and keeps its files private."""

import asyncio
import logging
import os
import sys
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


def _bridge():
    bridge = TelegramBridge(token="123:synthetic-secret", chat_id="456")
    bridge._client = AsyncMock()
    bridge._client.get = AsyncMock(return_value=_Response())
    return bridge


@pytest.mark.asyncio
async def test_non_linux_missing_whisper_keeps_safe_executable_context(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(sys, "platform", "darwin")
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    caplog.set_level(logging.ERROR)

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice message - transcription failed]"
    assert "whisper" in caplog.text.lower()
    assert "synthetic-secret" not in caplog.text
    assert list(private.iterdir()) == []


@pytest.mark.asyncio
async def test_non_linux_cancellation_kills_and_reaps_whisper(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    whisper = bin_dir / "whisper"
    whisper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, time\n"
        "pathlib.Path(os.environ['SYNTHETIC_PID_FILE']).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    whisper.chmod(0o700)
    pid_file = tmp_path / "whisper.pid"
    monkeypatch.setenv("SYNTHETIC_PID_FILE", str(pid_file))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    async with asyncio.timeout(5):
        while not pid_file.exists() or not pid_file.read_text():
            await asyncio.sleep(0.01)
    pid = int(pid_file.read_text())
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert list(private.iterdir()) == []
