"""Telegram voice transcription keeps temporary audio private and cleans it up."""

import asyncio
import logging
import os
import tempfile
import textwrap
import time
from unittest.mock import AsyncMock

import pytest

from kollabor_config import proc_ctl
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
        self.pid = 12345
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.killed = False
        self.waited = False

    async def communicate(self):
        self.started.set()
        if self.outcome == "timeout":
            raise asyncio.TimeoutError
        if self.outcome == "error":
            raise OSError("request https://api.telegram.org/bot123:synthetic-secret/file")
        if self.outcome == "cancel":
            await self.release.wait()
        return b"synthetic transcript", b""

    def kill(self):
        self.killed = True
        self.release.set()

    async def wait(self):
        self.waited = True


def _bridge():
    bridge = TelegramBridge(token="123:synthetic-secret", chat_id="456")
    bridge._client = AsyncMock()
    bridge._client.get = AsyncMock(return_value=_TelegramResponse())
    return bridge


def _patch_process_kill(monkeypatch, process):
    monkeypatch.setattr(
        proc_ctl,
        "kill_tree",
        lambda pid, sig: process.kill(),
    )


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
    _patch_process_kill(monkeypatch, process)
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
    _patch_process_kill(monkeypatch, process)
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
    _patch_process_kill(monkeypatch, process)
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
    _patch_process_kill(monkeypatch, process)
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
@pytest.mark.skipif(os.name == "nt", reason="POSIX process group lifecycle")
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
    while (
        (not writer_pid.exists() or not writes.exists())
        and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.02)
    assert writer_pid.exists() and writes.exists(), "transcriber descendant did not start"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    before = writes.read_text()
    await asyncio.sleep(0.15)
    assert writes.read_text() == before
    assert not list(tmp_path.glob("kollab-telegram-voice-*"))
