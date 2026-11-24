"""Telegram voice transcription keeps temporary audio private and cleans it up."""

import asyncio
import logging
import os
import tempfile
import textwrap
import time
from unittest.mock import AsyncMock, Mock

import psutil
import pytest

from kollabor_config import proc_ctl
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
async def test_cancellation_during_failed_spawn_still_raises_cancelled(
    tmp_path, monkeypatch
):
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


def _install_detached_whisper(tmp_path, temp_root, monkeypatch):
    whisper = tmp_path / "whisper"
    descendant_script = tmp_path / "descendant.py"
    descendant_started = tmp_path / "descendant.started"
    descendant_script.write_text(
        textwrap.dedent(
            f"""\
            import os, pathlib, sys, time
            os.setsid()
            pathlib.Path({str(descendant_started)!r}).write_text("ready")
            output = pathlib.Path(sys.argv[1]) / "descendant-output.txt"
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                with output.open("a") as stream:
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
            import pathlib, subprocess, sys, time
            audio = pathlib.Path(sys.argv[1])
            subprocess.Popen(
                [sys.executable, {str(descendant_script)!r}, str(audio.parent)]
            )
            time.sleep(0.5)
            """
        )
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return descendant_started


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group lifecycle")
async def test_cancellation_defers_cleanup_for_detached_pipe_writer(tmp_path, monkeypatch):
    temp_root = tmp_path / "temporary"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    descendant_started = _install_detached_whisper(tmp_path, temp_root, monkeypatch)

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    deadline = time.monotonic() + 5
    while not descendant_started.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert descendant_started.exists(), "detached descendant did not start"
    assert not task.done(), "transcriber exited before cancellation"
    task.cancel()
    _, pending = await asyncio.wait({task}, timeout=0.25)
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not pending, "cancellation waited for detached descendant pipe EOF"
        run_dirs = list(temp_root.glob("kollab-telegram-voice-*"))
        assert len(run_dirs) == 1, "artifact cleanup raced the detached writer"
        output = run_dirs[0] / "descendant-output.txt"
        before = output.read_text()
        await asyncio.sleep(0.1)
        assert output.read_text() != before, "detached writer exited before its pipe closed"
    finally:
        await asyncio.sleep(0.9)

    assert list(temp_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group lifecycle")
async def test_timeout_defers_cleanup_for_detached_pipe_writer(tmp_path, monkeypatch):
    temp_root = tmp_path / "temporary"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    descendant_started = _install_detached_whisper(tmp_path, temp_root, monkeypatch)
    original_wait_for = asyncio.wait_for
    timeout_requested = asyncio.Event()

    async def short_timeout(awaitable, timeout):
        if timeout == 60:
            deadline = time.monotonic() + 5
            while not descendant_started.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            timeout_requested.set()
            raise asyncio.TimeoutError
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", short_timeout)
    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    await timeout_requested.wait()
    _, pending = await asyncio.wait({task}, timeout=0.25)
    try:
        assert await task == "[voice message - transcription timed out]"
        assert descendant_started.exists(), "detached descendant did not start"
        assert not pending, "timeout waited for detached descendant pipe EOF"
        run_dirs = list(temp_root.glob("kollab-telegram-voice-*"))
        assert len(run_dirs) == 1, "artifact cleanup raced the detached writer"
        output = run_dirs[0] / "descendant-output.txt"
        before = output.read_text()
        await asyncio.sleep(0.1)
        assert output.read_text() != before, "detached writer exited before its pipe closed"
    finally:
        await asyncio.sleep(0.9)

    assert list(temp_root.iterdir()) == []


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
@pytest.mark.skipif(os.name == "nt", reason="POSIX process group lifecycle")
async def test_cancellation_kills_pipe_holder_after_transcriber_exits(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    whisper = tmp_path / "whisper"
    child_pid = tmp_path / "child.pid"
    whisper.write_text(
        "#!/bin/sh\n"
        f"sleep 120 & echo $! > {str(child_pid)!r}\n"
        "exit 0\n"
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    deadline = time.monotonic() + 5
    while not child_pid.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert child_pid.exists(), "pipe-holding descendant did not start"
    task.cancel()
    try:
        _, pending = await asyncio.wait({task}, timeout=10)
        assert not pending, "cancel did not return while a descendant held the pipes"
        assert task.cancelled()
        assert not list(tmp_path.glob("kollab-telegram-voice-*"))
    finally:
        if child_pid.exists():
            try:
                os.kill(int(child_pid.read_text()), 9)
            except ProcessLookupError:
                pass
        if not task.done():
            await asyncio.wait({task}, timeout=10)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group lifecycle")
async def test_cancellation_blocks_detached_writer_before_artifact_cleanup(
    tmp_path, monkeypatch
):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    whisper = tmp_path / "whisper"
    writer_script = tmp_path / "writer.py"
    whisper_pid = tmp_path / "whisper.pid"
    writer_started = tmp_path / "writer.started"
    writer_pid = tmp_path / "writer.pid"
    outcome = tmp_path / "writer.outcome"
    writer_script.write_text(
        textwrap.dedent(
            f"""\
            import os, pathlib, sys, time
            pathlib.Path({str(whisper_pid)!r}).write_text(str(os.getppid()))
            pathlib.Path({str(writer_started)!r}).write_text("ready")
            time.sleep(0.3)
            target = pathlib.Path(sys.argv[1])
            try:
                target.write_text("late output")
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
            child = subprocess.Popen(
                [sys.executable, {str(writer_script)!r},
                 str(audio.parent / "late.txt")],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            pathlib.Path({str(writer_pid)!r}).write_text(str(child.pid))
            time.sleep(0.5)
            """
        )
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    deadline = time.monotonic() + 5
    while not writer_started.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert writer_started.exists(), "detached writer did not start"
    assert writer_pid.exists(), "transcriber did not record detached writer"
    root_pid = int(whisper_pid.read_text())
    child_pid = int(writer_pid.read_text())

    def children(process, recursive=False):
        if process.pid == root_pid:
            return [psutil.Process(child_pid)]
        return []

    monkeypatch.setattr(psutil.Process, "children", children)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)

    await asyncio.sleep(0.45)
    assert outcome.read_text() == "PermissionError"
    assert list(private.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process group lifecycle")
async def test_timeout_kills_pipe_holder_after_transcriber_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    whisper = tmp_path / "whisper"
    child_pid = tmp_path / "child.pid"
    whisper.write_text(
        "#!/bin/sh\n"
        f"sleep 120 & echo $! > {str(child_pid)!r}\n"
        "exit 0\n"
    )
    whisper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    try:
        _, pending = await asyncio.wait({task}, timeout=75)
        assert not pending, "60 s timeout did not return while a descendant held the pipes"
        assert task.result() == "[voice message - transcription timed out]"
        assert child_pid.exists(), "pipe-holding descendant did not start"
        assert not list(tmp_path.glob("kollab-telegram-voice-*"))
    finally:
        if child_pid.exists():
            try:
                os.kill(int(child_pid.read_text()), 9)
            except ProcessLookupError:
                pass
        if not task.done():
            await asyncio.wait({task}, timeout=10)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX detached child lifecycle")
async def test_success_protects_artifacts_until_detached_writer_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(psutil.Process, "children", _REAL_PROCESS_CHILDREN)
    temp_root = tmp_path / "temporary"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    whisper = tmp_path / "whisper"
    writer = tmp_path / "writer.py"
    started = tmp_path / "writer.started"
    outcome = tmp_path / "writer.outcome"
    writer.write_text(
        textwrap.dedent(
            f"""\
            import pathlib, sys, time
            pathlib.Path({str(started)!r}).write_text("ready")
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
                [sys.executable, {str(writer)!r},
                 str(audio.parent / "late.txt")],
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
    task = asyncio.create_task(_bridge()._transcribe_voice("synthetic-file-id"))
    try:
        result = await task
        assert result == "[voice] [voice message - whisper returned empty]"
        run_dirs = list(temp_root.glob("kollab-telegram-voice-*"))
        assert len(run_dirs) == 1, "successful communication removed artifacts while detached writer ran"
        deadline = time.monotonic() + 2
        while not outcome.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert outcome.exists(), "detached writer did not finish"
        assert outcome.read_text() == "PermissionError"
        deadline = time.monotonic() + 2
        while list(temp_root.iterdir()) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert list(temp_root.iterdir()) == []
    finally:
        if not task.done():
            task.cancel()
