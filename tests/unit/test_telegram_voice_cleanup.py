"""Telegram voice transcription keeps temporary audio private and cleans it up."""

import asyncio
import logging
import os
import sys
import tempfile
import textwrap
import time
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from plugins.hub.messaging_bridge import TelegramBridge

_REAL_PROCESS_CHILDREN = psutil.Process.children


@pytest.fixture(autouse=True)
def _isolate_process_tree_enumeration(monkeypatch):
    monkeypatch.setattr(psutil.Process, "children", lambda *_args, **_kwargs: [])


class _TelegramResponse:
    content = b"synthetic audio"

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True, "result": {"file_path": "voice.ogg"}}


class _Process:
    def __init__(self, outcome="success"):
        self.outcome = outcome
        self.pid = 12345
        self.returncode = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.killed = False
        self.waited = False

    async def communicate(self, _input=None):
        self.started.set()
        if self.outcome == "timeout":
            raise asyncio.TimeoutError
        if self.outcome == "error":
            raise OSError("request https://api.telegram.org/bot123:synthetic-secret/file")
        if self.outcome == "cancel":
            await self.release.wait()
        return b"synthetic transcript", b""

    def terminate(self):
        self.killed = True
        self.release.set()

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
async def test_authorized_voice_poll_uses_successful_transcription():
    bridge = TelegramBridge(token="123:synthetic-secret", chat_id="456", user_id="789")
    response = Mock()
    response.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 10,
                "message": {
                    "chat": {"id": 456},
                    "from": {"id": 789},
                    "date": 1,
                    "voice": {"file_id": "synthetic-file-id"},
                },
            }
        ],
    }
    bridge._client = AsyncMock()
    bridge._client.get.return_value = response
    bridge._transcribe_voice = AsyncMock(return_value="[voice] synthetic successful transcript")

    messages = await bridge.poll()

    assert [message.text for message in messages] == ["[voice] synthetic successful transcript"]
    bridge._transcribe_voice.assert_awaited_once_with("synthetic-file-id")


@pytest.mark.asyncio
async def test_unauthorized_voice_poll_does_not_download_media():
    bridge = TelegramBridge(token="123:synthetic-secret", chat_id="456", user_id="789")
    response = Mock()
    response.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 10,
                "message": {
                    "chat": {"id": 999},
                    "from": {"id": 789},
                    "date": 1,
                    "voice": {"file_id": "unauthorized-file-id"},
                },
            }
        ],
    }
    bridge._client = AsyncMock()
    bridge._client.get.return_value = response
    bridge._transcribe_voice = AsyncMock(return_value="[voice] private transcript")

    messages = await bridge.poll()

    assert messages == []
    bridge._client.get.assert_awaited_once()
    bridge._transcribe_voice.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_repeated_cancellation_during_spawn_reaps_child(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    process = _Process("cancel")
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    async def delayed_spawn(*_args, **_kwargs):
        spawn_started.set()
        await release_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    await spawn_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release_spawn.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed and process.waited
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_cancellation_during_failed_spawn_still_raises_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    async def delayed_failure(*_args, **_kwargs):
        spawn_started.set()
        await release_spawn.wait()
        raise FileNotFoundError(2, "No such file or directory", "whisper")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_failure)
    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    await spawn_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release_spawn.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process group lifecycle")
@pytest.mark.skipif(sys.platform != "linux", reason="Linux child ownership")
async def test_cancellation_stops_real_transcriber_descendants(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    whisper = tmp_path / "whisper"
    writer_script = tmp_path / "writer.py"
    writer_pid = tmp_path / "writer.pid"
    writes = tmp_path / "writes"
    writer_script.write_text(
        textwrap.dedent(
            f"""\
            import pathlib, time
            path = pathlib.Path({str(writes)!r})
            while True:
                with path.open("a") as stream:
                    stream.write("x\\n")
                    stream.flush()
                time.sleep(0.02)
            """
        )
    )
    whisper.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            f"""\
            import subprocess, sys, time
            from pathlib import Path
            outdir = Path(sys.argv[sys.argv.index("--output_dir") + 1])
            audio = Path(sys.argv[1])
            (outdir / (audio.stem + ".txt")).write_text("partial transcript")
            child = subprocess.Popen([sys.executable, {str(writer_script)!r}])
            Path({str(writer_pid)!r}).write_text(str(child.pid))
            while True:
                time.sleep(1)
            """
        )
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    deadline = time.monotonic() + 5
    while (not writer_pid.exists() or not writes.exists()) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert writer_pid.exists() and writes.exists(), "transcriber descendant did not start"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    before = writes.read_text()
    await asyncio.sleep(0.15)
    assert writes.read_text() == before
    assert not list(tmp_path.glob("kollab-telegram-voice-*"))


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Linux child ownership")
async def test_transcription_does_not_depend_on_descendant_enumeration(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    whisper = tmp_path / "whisper"
    writer = tmp_path / "writer.py"
    started, outcome = tmp_path / "writer.started", tmp_path / "writer.outcome"
    writer.write_text(
        textwrap.dedent(
            f"""\
            import pathlib, sys, time
            pathlib.Path({str(started)!r}).touch()
            time.sleep(0.5)
            try:
                pathlib.Path(sys.argv[1]).write_text("late output")
            except OSError as exc:
                pathlib.Path({str(outcome)!r}).write_text(type(exc).__name__)
            else:
                pathlib.Path({str(outcome)!r}).write_text("written")
            """
        )
    )
    whisper.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            f"""\
            import pathlib, subprocess, sys, time
            audio = pathlib.Path(sys.argv[1])
            subprocess.Popen(
                [sys.executable, {str(writer)!r}, str(audio.parent / "late.txt")],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            while not pathlib.Path({str(started)!r}).exists():
                time.sleep(0.005)
            time.sleep(0.05)
            """
        )
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(
        psutil.Process,
        "children",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(psutil.AccessDenied()),
    )

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice] [voice message - whisper returned empty]"
    deadline = time.monotonic() + 2
    while not outcome.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert outcome.read_text() == "written"
    assert list(private.iterdir()) == []


@pytest.mark.asyncio
async def test_missing_whisper_log_keeps_safe_executable_context(monkeypatch, caplog):
    async def missing(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "whisper")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)
    caplog.set_level(logging.ERROR)
    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice message - transcription failed]"
    assert "whisper" in caplog.text
    assert "synthetic-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Linux child ownership")
async def test_success_waits_for_detached_transcript_writer(tmp_path, monkeypatch):
    monkeypatch.setattr(psutil.Process, "children", _REAL_PROCESS_CHILDREN)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    whisper = tmp_path / "whisper"
    writer = tmp_path / "writer.py"
    started = tmp_path / "writer.started"
    writer.write_text(
        textwrap.dedent(
            f"""\
            import pathlib, sys, time
            pathlib.Path({str(started)!r}).write_text("ready")
            time.sleep(0.25)
            pathlib.Path(sys.argv[1]).write_text("delayed transcript")
            """
        )
    )
    whisper.write_text(
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            f"""\
            import pathlib, subprocess, sys, time
            audio = pathlib.Path(sys.argv[1])
            subprocess.Popen(
                [sys.executable, {str(writer)!r}, str(audio.with_suffix(".txt"))],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            while not pathlib.Path({str(started)!r}).exists():
                time.sleep(0.005)
            time.sleep(0.05)
            """
        )
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice] delayed transcript"
    deadline = time.monotonic() + 2
    while list(tmp_path.glob("kollab-telegram-voice-*")) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert not list(tmp_path.glob("kollab-telegram-voice-*"))


@pytest.mark.asyncio
async def test_non_linux_transcription_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/python3" if name == "python3" else None)
    create_process = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice message - transcription failed]"
    create_process.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []
