"""Telegram transcription fails closed without Linux child ownership."""

import sys
from unittest.mock import AsyncMock

import pytest

from plugins.hub.messaging_bridge import TelegramBridge


class _Response:
    content = b"synthetic audio"

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True, "result": {"file_path": "voice.ogg"}}


@pytest.mark.asyncio
async def test_non_linux_transcription_fails_without_starting_whisper(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/python3" if name == "python3" else None
    )
    bridge = TelegramBridge(token="synthetic-token", chat_id="456")
    bridge._client = AsyncMock()
    bridge._client.get = AsyncMock(return_value=_Response())
    create_process = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", create_process)

    result = await bridge._transcribe_voice("synthetic-file-id")

    assert result == "[voice message - transcription failed]"
    create_process.assert_not_awaited()
