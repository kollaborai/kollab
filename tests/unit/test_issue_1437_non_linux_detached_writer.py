"""Non-Linux Telegram transcription must stop detached Whisper writers."""

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


@pytest.mark.asyncio
async def test_non_linux_stops_detached_writer_before_removing_run_directory(
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
        import os, pathlib, sys, time
        os.setsid()
        pathlib.Path({str(writer_pid)!r}).write_text(str(os.getpid()))
        time.sleep(30)
        pathlib.Path(sys.argv[1]).write_text("late transcript")
    """))
    whisper = bin_dir / "whisper"
    whisper.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(f"""
        import pathlib, subprocess, sys, time
        subprocess.Popen(
            [sys.executable, {str(writer)!r}, str(pathlib.Path(sys.argv[1]).with_suffix('.txt'))],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 3
        while not pathlib.Path({str(writer_pid)!r}).exists():
            assert time.monotonic() < deadline
            time.sleep(0.005)
    """))
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    pid = int(writer_pid.read_text())
    try:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            psutil.Process(pid).kill()
            psutil.wait_procs([psutil.Process(pid)], timeout=2)
    assert result == "[voice] [voice message - whisper returned empty]"
    assert list(private.iterdir()) == []
