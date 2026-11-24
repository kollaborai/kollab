"""Exercise the real CLI boundary with synthetic, escaping writer processes."""

import asyncio
import os
import sys
import tempfile
import textwrap
from unittest.mock import AsyncMock

import psutil
import pytest

from kollabor_config import proc_ctl
from plugins.hub.messaging_bridge import TelegramBridge

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux child ownership"),
]


@pytest.fixture(autouse=True)
def _isolate_old_tree_scans(monkeypatch):
    # The old implementation samples descendants. Do not scan the host in its
    # red run: an empty sample is precisely the race this suite must close.
    monkeypatch.setattr(psutil.Process, "children", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(proc_ctl, "kill_tree", lambda pid, sig: os.killpg(pid, sig))


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


def _install(tmp_path, monkeypatch, source):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    executable = tmp_path / "whisper"
    executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(source))
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return private


async def _ready(path):
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("generations", [1, 2])
@pytest.mark.parametrize("hold_pipes", [False, True])
async def test_success_owns_fast_detached_writers(
    tmp_path, monkeypatch, generations, hold_pipes
):
    """No observation window: the CLI exits immediately after starting a writer."""
    outcome = tmp_path / "outcome"
    writer = tmp_path / "writer.py"
    writer.write_text(textwrap.dedent(f"""
        import os, pathlib, sys, time
        os.setsid()
        if {generations} == 2 and os.fork():
            os._exit(0)
        time.sleep(0.25)
        try:
            pathlib.Path(sys.argv[1]).write_text("delayed transcript")
            pathlib.Path(sys.argv[1]).with_name("extra.txt").write_text("partial")
            pathlib.Path({str(outcome)!r}).write_text("written")
        except OSError as exc:
            pathlib.Path({str(outcome)!r}).write_text(type(exc).__name__)
    """))
    private = _install(tmp_path, monkeypatch, f"""
        import pathlib, subprocess, sys
        subprocess.Popen(
            [sys.executable, {str(writer)!r}, str(pathlib.Path(sys.argv[1]).with_suffix('.txt'))],
            stdin=subprocess.DEVNULL,
            stdout=None if {hold_pipes!r} else subprocess.DEVNULL,
            stderr=None if {hold_pipes!r} else subprocess.DEVNULL,
        )
    """)

    result = await _bridge()._transcribe_voice("synthetic-file-id")
    await _ready(outcome)
    assert result == "[voice] delayed transcript"
    assert outcome.read_text() == "written"
    assert list(private.iterdir()) == []


@pytest.mark.parametrize("finish", ["cancel", "timeout"])
@pytest.mark.parametrize("root_exits", [False, True])
@pytest.mark.parametrize("hold_pipes", [False, True])
async def test_abort_reaps_escaped_writers_before_cleanup(
    tmp_path, monkeypatch, finish, root_exits, hold_pipes
):
    started = tmp_path / "started"
    writer = tmp_path / "writer.py"
    writer.write_text(textwrap.dedent(f"""
        import os, pathlib, sys, time
        os.setsid()
        pathlib.Path({str(started)!r}).write_text(str(os.getpid()))
        output = pathlib.Path(sys.argv[1]).with_name("partial.txt")
        while True:
            output.write_text("synthetic partial output")
            time.sleep(0.01)
    """))
    private = _install(tmp_path, monkeypatch, f"""
        import pathlib, subprocess, sys, time
        subprocess.Popen(
            [sys.executable, {str(writer)!r}, sys.argv[1]],
            stdin=subprocess.DEVNULL,
            stdout=None if {hold_pipes!r} else subprocess.DEVNULL,
            stderr=None if {hold_pipes!r} else subprocess.DEVNULL,
        )
        while not pathlib.Path({str(started)!r}).exists():
            time.sleep(0.005)
        if not {root_exits!r}:
            time.sleep(30)
    """)
    original_wait_for = asyncio.wait_for

    async def short_deadline(awaitable, timeout):
        if timeout == 60:
            await _ready(started)
            return await original_wait_for(awaitable, 0.15)
        return await original_wait_for(awaitable, timeout)

    if finish == "timeout":
        monkeypatch.setattr(asyncio, "wait_for", short_deadline)
    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    await _ready(started)
    pid = int(started.read_text())
    try:
        if finish == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await original_wait_for(asyncio.shield(task), 3)
        else:
            assert await original_wait_for(task, 3) == "[voice message - transcription timed out]"
        assert not psutil.pid_exists(pid), "the escaped writer was not reaped"
        assert list(private.iterdir()) == []
    finally:
        # A red run must not leave the deliberately unbounded writer behind.
        if psutil.pid_exists(pid):
            psutil.Process(pid).kill()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_concurrent_transcriptions_have_independent_artifacts(tmp_path, monkeypatch):
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    private = _install(tmp_path, monkeypatch, f"""
        import pathlib, sys, time
        audio = pathlib.Path(sys.argv[1])
        assert audio.parent.stat().st_mode & 0o777 == 0o700
        assert audio.stat().st_mode & 0o077 == 0
        (pathlib.Path({str(barrier)!r}) / audio.parent.name).touch()
        deadline = time.monotonic() + 3
        while len(list(pathlib.Path({str(barrier)!r}).iterdir())) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        audio.with_suffix('.txt').write_text(audio.read_text())
    """)
    first, second = _bridge(), _bridge()
    first_response, second_response = _Response(), _Response()
    first_response.content = b"first transcript"
    second_response.content = b"second transcript"
    first._client.get.return_value = first_response
    second._client.get.return_value = second_response

    results = await asyncio.gather(
        first._transcribe_voice("first"), second._transcribe_voice("second")
    )

    assert results == ["[voice] first transcript", "[voice] second transcript"]
    assert len(list(barrier.iterdir())) == 2
    assert list(private.iterdir()) == []


@pytest.mark.parametrize("media", ["voice", "audio"])
async def test_authorized_poll_forwards_real_success(tmp_path, monkeypatch, media):
    private = _install(tmp_path, monkeypatch, """
        import pathlib, sys
        pathlib.Path(sys.argv[1]).with_suffix('.txt').write_text("successful transcript")
    """)
    bridge = _bridge()
    update = _Response()
    update.json = lambda: {"ok": True, "result": [{
        "update_id": 1, "message": {
            "chat": {"id": 456}, "date": 1,
            media: {"file_id": "synthetic-file-id"},
        },
    }]}
    bridge._client.get.side_effect = [update, _Response(), _Response()]

    messages = await bridge.poll()

    assert [message.text for message in messages] == ["[voice] successful transcript"]
    assert list(private.iterdir()) == []


async def test_nonzero_cli_discards_partial_transcript(tmp_path, monkeypatch, caplog):
    private = _install(tmp_path, monkeypatch, """
        import pathlib, sys
        pathlib.Path(sys.argv[1]).with_suffix('.txt').write_text("private transcript")
        print("synthetic-secret private transcript", file=sys.stderr)
        sys.exit(1)
    """)

    result = await _bridge()._transcribe_voice("synthetic-file-id")

    assert result == "[voice message - transcription failed]"
    assert list(private.iterdir()) == []
    assert "private transcript" not in caplog.text
    assert "synthetic-secret" not in caplog.text
