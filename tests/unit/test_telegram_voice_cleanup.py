"""Telegram voice transcription keeps temporary audio private and cleans it up."""

import asyncio
import logging
import tempfile
from unittest.mock import AsyncMock

import pytest

from plugins.hub.messaging_bridge import TelegramBridge


class _TelegramResponse:
    content = b"synthetic audio"

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True, "result": {"file_path": "voice.ogg"}}


class _Process:
    def __init__(self, outcome="success"):
        self.outcome = outcome
        self.started = asyncio.Event()
        self.killed = False
        self.waited = False

    async def communicate(self):
        self.started.set()
        if self.outcome == "timeout":
            raise asyncio.TimeoutError
        if self.outcome == "error":
            raise OSError("request https://api.telegram.org/bot123:synthetic-secret/file")
        if self.outcome == "cancel":
            await asyncio.Event().wait()
        return b"synthetic transcript", b""

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


def _bridge():
    bridge = TelegramBridge(token="123:synthetic-secret", chat_id="456")
    bridge._client = AsyncMock()
    bridge._client.get = AsyncMock(return_value=_TelegramResponse())
    return bridge


@pytest.mark.asyncio
async def test_success_removes_audio_and_does_not_log_transcript(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    process = _Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice] synthetic transcript"
    assert list(tmp_path.iterdir()) == []
    assert "synthetic transcript" not in caplog.text
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
async def test_timeout_removes_audio_and_reaps_child(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    process = _Process("timeout")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice message - transcription timed out]"
    assert process.killed and process.waited
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_subprocess_error_removes_audio_and_reaps_child(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    process = _Process("error")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice message - transcription failed]"
    assert process.killed and process.waited
    assert list(tmp_path.iterdir()) == []
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
async def test_cancellation_reaps_child_before_removing_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    process = _Process("cancel")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    await process.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed and process.waited
    assert list(tmp_path.iterdir()) == []
