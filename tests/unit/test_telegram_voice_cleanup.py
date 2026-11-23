"""Telegram audio cannot create artifacts or launch an uncontained transcriber."""

import asyncio
import logging
import os
import tempfile
from unittest.mock import AsyncMock, Mock

import pytest

from plugins.hub.messaging_bridge import TelegramBridge

NOTICE = "[voice message - transcription unavailable; please send text]"


def _update(update_id=10, *, media="voice", chat_id=456, user_id=789, text=""):
    message = {
        "chat": {"id": chat_id},
        "from": {"id": user_id, "first_name": "Synthetic sender"},
        "date": 1234567890,
    }
    if media:
        message[media] = {"file_id": "synthetic-file-id"}
    if text:
        message["text"] = text
    return {"update_id": update_id, "message": message}


def _bridge(updates):
    bridge = TelegramBridge(token="123:synthetic-secret", chat_id="456", user_id="789")

    async def get(url, **_kwargs):
        # Support the old download path so the baseline reaches the spawn guard.
        if url.endswith("/getUpdates"):
            data = {"ok": True, "result": updates}
        else:
            data = {"ok": True, "result": {"file_path": "voice.ogg"}}
        response = Mock()
        response.json.return_value = data
        response.content = b"synthetic private audio"
        return response

    bridge._client = AsyncMock()
    bridge._client.get.side_effect = get
    return bridge


@pytest.fixture
def voice_resources(tmp_path, monkeypatch):
    """Trap the capability before any executable can fork or retain a pipe."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    spawn = AsyncMock(side_effect=FileNotFoundError(2, "No such file", "whisper"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    directory = Mock(wraps=tempfile.mkdtemp)
    monkeypatch.setattr(tempfile, "mkdtemp", directory)
    audio_file = Mock(wraps=tempfile.NamedTemporaryFile)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", audio_file)
    return spawn, directory, audio_file


def _assert_no_voice_resources(resources):
    spawn, directory, audio_file = resources
    spawn.assert_not_awaited()
    directory.assert_not_called()
    audio_file.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("media", ["voice", "audio"])
async def test_audio_cannot_start_pipe_holders_or_detached_writers(media, voice_resources):
    # This denies the common producer of every descendant case: no process gets
    # audio/output paths, regardless of its exit status, session or pipe handling.
    bridge = _bridge([_update(media=media)])

    messages = await bridge.poll()

    _assert_no_voice_resources(voice_resources)
    assert [message.text for message in messages] == [NOTICE]
    assert messages[0].sender_id == "789"
    assert bridge._offset == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("media", ["voice", "audio"])
async def test_audio_is_not_downloaded_or_persisted(media, voice_resources, tmp_path):
    bridge = _bridge([_update(media=media)])

    messages = await bridge.poll()

    assert bridge._client.get.await_count == 1
    assert bridge._client.get.await_args.args[0].endswith("/getUpdates")
    assert list(tmp_path.iterdir()) == []
    assert [message.text for message in messages] == [NOTICE]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "timeout", "error", "cancel"])
async def test_audio_notice_and_following_text_do_not_depend_on_whisper(
    failure, voice_resources
):
    spawn, _, _ = voice_resources
    spawn.side_effect = {
        "missing": FileNotFoundError(2, "No such file", "whisper"),
        "timeout": asyncio.TimeoutError(),
        "error": OSError("synthetic private audio and synthetic-secret"),
        "cancel": asyncio.CancelledError(),
    }[failure]
    bridge = _bridge([_update(), _update(11, media="", text="hello")])

    messages = await bridge.poll()

    assert [message.text for message in messages] == [NOTICE, "hello"]
    assert bridge._offset == 12
    _assert_no_voice_resources(voice_resources)


@pytest.mark.asyncio
async def test_concurrent_audio_polls_have_no_artifacts_or_cleanup_tasks(voice_resources):
    first = _bridge([_update()])
    second = _bridge([_update(media="audio")])
    before = asyncio.all_tasks()

    first_messages, second_messages = await asyncio.gather(first.poll(), second.poll())

    assert [message.text for message in first_messages] == [NOTICE]
    assert [message.text for message in second_messages] == [NOTICE]
    assert asyncio.all_tasks() <= before
    _assert_no_voice_resources(voice_resources)


@pytest.mark.asyncio
async def test_unavailable_notice_keeps_diagnostics_without_private_audio(
    voice_resources, caplog
):
    caplog.set_level(logging.INFO)
    bridge = _bridge([_update()])

    messages = await bridge.poll()

    assert [message.text for message in messages] == [NOTICE]
    assert "synthetic private audio" not in caplog.text
    assert "synthetic-secret" not in caplog.text
    assert "synthetic-file-id" not in caplog.text
    _assert_no_voice_resources(voice_resources)


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id,user_id", [(999, 789), (456, 999)])
async def test_unauthorized_audio_cannot_download_or_start_transcription(
    chat_id, user_id, voice_resources
):
    bridge = _bridge([_update(chat_id=chat_id, user_id=user_id)])

    assert await bridge.poll() == []
    assert bridge._offset == 11
    assert bridge._client.get.await_count == 1
    _assert_no_voice_resources(voice_resources)


@pytest.mark.asyncio
async def test_cancelled_poll_cannot_leave_voice_work_running(voice_resources):
    bridge = _bridge([_update()])
    entered = asyncio.Event()

    async def wait_for_updates(*_args, **_kwargs):
        entered.set()
        await asyncio.Future()

    bridge._client.get.side_effect = wait_for_updates
    task = asyncio.create_task(bridge.poll())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    _assert_no_voice_resources(voice_resources)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Synthetic POSIX executable")
async def test_installed_whisper_cannot_run_or_create_descendants(tmp_path, monkeypatch):
    # A real executable proves that PATH contents cannot restore the capability.
    whisper = tmp_path / "whisper"
    marker = tmp_path / "whisper-started"
    whisper.write_text('#!/bin/sh\nprintf started > "${0}-started"\n')
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    bridge = _bridge([_update()])

    messages = await bridge.poll()

    assert not marker.exists(), "Whisper must never get a chance to fork a detached writer"
    assert list(private.iterdir()) == []
    assert [message.text for message in messages] == [NOTICE]
