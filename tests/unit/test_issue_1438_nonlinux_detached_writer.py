"""Non-Linux transcription must preserve files used by detached writers."""

import asyncio
import os
import sys
import tempfile
import textwrap
from unittest.mock import AsyncMock

import pytest

from plugins.hub.messaging_bridge import TelegramBridge


@pytest.mark.asyncio
async def test_nonlinux_detached_writer_can_finish_before_private_dir_cleanup(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "platform", "darwin")
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    started, outcome = tmp_path / "started", tmp_path / "outcome"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    writer = tmp_path / "writer.py"
    writer.write_text(
        textwrap.dedent(
            """\
            import pathlib, sys, time
            started, outcome, target = map(pathlib.Path, sys.argv[1:])
            started.write_text("ready")
            time.sleep(0.3)
            try:
                target.write_text("late transcript")
            except OSError as error:
                outcome.write_text(type(error).__name__)
            else:
                outcome.write_text("written")
            """
        )
    )
    whisper = bin_dir / "whisper"
    whisper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import pathlib, subprocess, sys, time
            audio = pathlib.Path(sys.argv[1])
            subprocess.Popen(
                [sys.executable, {str(writer)!r}, {str(started)!r},
                 {str(outcome)!r}, str(audio.with_suffix('.txt'))],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            while not pathlib.Path({str(started)!r}).exists():
                time.sleep(0.005)
            """
        )
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    bridge = TelegramBridge(token="123:secret", chat_id="456")
    bridge._client = AsyncMock()
    response = type(
        "Response",
        (),
        {
            "content": b"synthetic audio",
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "ok": True,
                "result": {"file_path": "voice.ogg"},
            },
        },
    )()
    bridge._client.get = AsyncMock(return_value=response)

    await bridge._transcribe_voice("synthetic-file-id")
    async with asyncio.timeout(3):
        while not outcome.exists():
            await asyncio.sleep(0.01)

    assert outcome.read_text() == "written", (
        f"detached writer raced cleanup: got {outcome.read_text()}"
    )
