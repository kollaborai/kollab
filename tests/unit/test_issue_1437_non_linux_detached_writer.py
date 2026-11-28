"""Non-Linux Telegram transcription keeps detached writers' files and stops attached writers on cancel."""

import asyncio
import os
import sys
import tempfile
import textwrap
from unittest.mock import AsyncMock

import psutil
import pytest

from plugins.hub.messaging_bridge import TelegramBridge


class _Response:
    content = b"synthetic audio"

    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True, "result": {"file_path": "voice.ogg"}}


def _bridge():
    bridge = TelegramBridge(token="synthetic-secret", chat_id="456")
    bridge._client = AsyncMock()
    bridge._client.get.return_value = _Response()
    return bridge


def _is_gone(pid: int) -> bool:
    # A killed writer can linger as a zombie until the process that adopted it reaps it.
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _kill_if_running(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass


@pytest.mark.asyncio
async def test_non_linux_normal_completion_keeps_detached_writer_and_directory(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "platform", "darwin")
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    writer_pid = tmp_path / "writer.pid"
    writer = tmp_path / "writer.py"
    writer.write_text(textwrap.dedent(f"""
        import os, pathlib, time
        os.setsid()
        pathlib.Path({str(writer_pid)!r}).write_text(str(os.getpid()))
        time.sleep(30)
    """))
    whisper = bin_dir / "whisper"
    whisper.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(f"""
        import pathlib, subprocess, sys, time
        subprocess.Popen(
            [sys.executable, {str(writer)!r}],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 3
        while not pathlib.Path({str(writer_pid)!r}).exists():
            assert time.monotonic() < deadline
            time.sleep(0.005)
    """))
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice] [voice message - whisper returned empty]"
    pid = int(writer_pid.read_text())
    try:
        assert not _is_gone(pid)
        run_dirs = list(private.iterdir())
        assert len(run_dirs) == 1
        assert (run_dirs[0] / "voice.ogg").exists()
    finally:
        _kill_if_running(pid)


@pytest.mark.asyncio
async def test_non_linux_cancellation_stops_writer_still_attached_to_whisper(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "platform", "darwin")
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    writer_pid = tmp_path / "writer.pid"
    writer = tmp_path / "writer.py"
    writer.write_text(textwrap.dedent(f"""
        import os, pathlib, time
        os.setsid()
        pathlib.Path({str(writer_pid)!r}).write_text(str(os.getpid()))
        time.sleep(60)
    """))
    whisper = bin_dir / "whisper"
    whisper.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(f"""
        import subprocess, sys, time
        subprocess.Popen(
            [sys.executable, {str(writer)!r}],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(60)
    """))
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    async with asyncio.timeout(5):
        while not writer_pid.exists() or not writer_pid.read_text():
            await asyncio.sleep(0.01)
    pid = int(writer_pid.read_text())
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(5):
            while not _is_gone(pid):
                await asyncio.sleep(0.01)
        run_dirs = list(private.iterdir())
        assert len(run_dirs) == 1
        assert (run_dirs[0] / "voice.ogg").exists()
    finally:
        _kill_if_running(pid)
