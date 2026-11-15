"""The daemon's terminal (kollabor/code_service/terminal.py): links, long polls, replay, the session cap (#987).

The engine bridges a browser socket to ``terminal.message`` / ``terminal.poll`` / ``terminal.detach``. Commands print
their marker with a quoted split (``12''3`` shows as ``123``), so the echo of the typed line can never satisfy a
check; only running the command does.
"""

import json
import re
import sys
import time
import uuid

import psutil
import pytest
import pytest_asyncio

from kollabor import code_service
from kollabor.code_service import terminal
from kollabor.code_service.settings import CodeSettings

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _shells(tmp_path):
    """HOME is tmp_path (conftest); an empty rc skips zsh's first-run wizard. Every shell ends with the test."""
    (tmp_path / ".zshrc").write_text("")
    yield
    await code_service.shutdown()


class Link:
    """What the engine does for one browser socket, minus the socket."""

    def __init__(self, cwd, settings: CodeSettings | None = None) -> None:
        self.settings = settings
        self.id = uuid.uuid4().hex
        self.cwd = str(cwd)
        self.ack: int | None = None

    async def call(self, op: str, **params):
        answer = await code_service.call(
            f"terminal.{op}", {"link": self.id, **params}, root=self.cwd, settings=self.settings
        )
        assert answer["status"] == 200, answer
        return answer["body"]["data"]

    async def send(self, **message):
        return (await self.call("message", raw=json.dumps(message)))["messages"]

    async def poll(self, wait: float = 5.0, *, advance: bool = True):
        params = {"wait": wait}
        if self.ack is not None:
            params["ack"] = self.ack
        data = await self.call("poll", **params)
        if advance:
            self.ack = data["ack"]
        return data["messages"]

    async def open(self, **extra) -> str:
        ready = (await self.send(type="open", cols=100, rows=30, **extra))[0]
        assert ready["type"] == "ready" and ready["sessionId"], ready
        return ready["sessionId"]

    async def until(self, marker: str) -> str:
        """Poll until ``marker`` shows up in the output; any message that is not output fails the test."""
        seen = ""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for message in await self.poll():
                if message["type"] != "output":
                    pytest.fail(f"expected output, got: {message}")
                seen += message["data"]
            if marker in seen:
                return seen
        pytest.fail(f"{marker!r} never showed up")


async def test_open_answers_ready_at_once_then_runs_the_shell_in_the_folder(tmp_path):
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="pwd; echo kollab-marker-12''3\n")

    output = await link.until("kollab-marker-123")

    assert str(tmp_path.resolve()) in output


async def test_a_folder_named_in_the_message_is_ignored(tmp_path):
    # the shell opens in the daemon's own folder, whatever a client sends
    link = Link(tmp_path)
    await link.open(workspace="/etc")
    await link.send(type="input", data="pwd; echo kollab-marker-12''4\n")

    output = await link.until("kollab-marker-124")

    assert str(tmp_path.resolve()) in output
    assert "/etc\r" not in output


async def test_a_reattach_replays_the_output_and_the_shell_keeps_running(tmp_path):
    first = Link(tmp_path)
    session_id = await first.open()
    await first.send(type="input", data="echo kollab-marker-12''6\n")
    await first.until("kollab-marker-126")
    await first.call("detach")  # the browser socket is gone; the shell is not

    again = Link(tmp_path)
    assert await again.send(type="open", sessionId=session_id) == [{"type": "ready", "sessionId": session_id}]
    assert "kollab-marker-126" in await again.until("kollab-marker-126")
    await again.send(type="input", data="echo kollab-after-''7\n")
    await again.until("kollab-after-7")


async def test_reattach_with_cursor_skips_already_received_output(tmp_path):
    first = Link(tmp_path)
    session_id = await first.open()
    await first.send(type="input", data="echo old-marker-1''26\n")
    await first.until("old-marker-126")
    cursor = first.ack
    await first.call("detach")

    again = Link(tmp_path)
    await again.open(sessionId=session_id, cursor=cursor)
    await again.send(type="input", data="echo new-marker-1''27\n")
    output = await again.until("new-marker-127")
    assert "old-marker-126" not in output


async def test_reattach_reports_a_gap_when_the_ring_has_wrapped(tmp_path):
    first = Link(tmp_path)
    session_id = await first.open()
    session = terminal._SESSIONS[session_id]
    session._ring_limit = 4
    session._publish("old!")
    session._publish("new!")
    link = terminal._Link()
    link.attach(session, 0)
    messages, _ = link.pending()
    assert messages[0] == {"type": "gap", "nextSeq": session.first_seq}
    assert messages[1]["data"] == "new!"
    assert messages[1]["seq"] == session.first_seq
    link.detach()


async def test_large_escaped_scrollback_is_paged_and_replayed(tmp_path):
    client = Link(tmp_path, CodeSettings(scrollback_kb=65536))
    sid = await client.open()
    session = terminal._SESSIONS[sid]
    text = "\x01" * terminal.READ_BYTES
    for _ in range(64):
        session._publish(text)
    link = terminal._Link()
    link.attach(session, 0)
    result = []
    while True:
        messages, nxt = link.pending()
        assert len(json.dumps(messages).encode()) <= 1024 * 1024
        assert link.pending() == (messages, nxt)  # lost reply is replayed
        result.extend(m for m in messages if m["type"] == "output")
        if nxt == session.next_seq:
            break
        assert nxt > link.seq
        link.seq = nxt
    assert [m["seq"] for m in result] == list(range(session.first_seq, session.next_seq))
    assert "".join(m["data"] for m in result) == text * 64
    link.detach()


async def test_paged_scrollback_keeps_gap_and_exit_order(tmp_path):
    client = Link(tmp_path)
    sid = await client.open()
    session = terminal._SESSIONS[sid]
    session._ring_limit = terminal.MAX_POLL_MESSAGES + 5
    for _ in range(terminal.MAX_POLL_MESSAGES * 2):
        session._publish("x")
    link = terminal._Link()
    link.attach(session, 0)
    try:
        session.exited = True  # simulate exit without allowing the fixture to lose the shell
        first, ack = link.pending()
        assert first[0] == {"type": "gap", "nextSeq": session.first_seq}
        assert len(first) == terminal.MAX_POLL_MESSAGES
        assert all(message["type"] != "exit" for message in first)
        link.seq = ack
        second, ack = link.pending()
        assert all(message["type"] == "output" for message in second)
        assert ack == session.next_seq
        link.seq = ack
        assert link.pending()[0] == [{"type": "exit", "code": None}]
    finally:
        session.exited = False
        link.detach()


async def test_a_poll_whose_answer_was_lost_is_sent_again(tmp_path):
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="echo kollab-marker-12''8\n")
    await link.until("kollab-marker-128")
    kept = link.ack

    await link.send(type="input", data="echo kollab-later-''9\n")
    await link.until("kollab-later-9")
    link.ack = kept  # the engine never saw the last answer, so it acknowledges the old position
    resent = "".join(m["data"] for m in await link.poll(advance=False))

    assert "kollab-later-9" in resent


async def test_a_poll_never_holds_the_connection_for_long_and_output_ends_it_early(tmp_path):
    # the daemon answers one connection's requests in order: a long poll would hold up every keystroke behind it
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="echo kollab-ready-''0\n")
    await link.until("kollab-ready-0")
    while await link.poll(wait=0.5):  # drain what follows the marker (the next prompt)
        pass
    started = time.monotonic()

    quiet = await link.poll(wait=20)

    assert quiet == [] and 0.05 <= time.monotonic() - started < 1.0
    await link.send(type="input", data="echo kollab-wake-''1\n")
    output = await link.until("kollab-wake-1")  # polls again and again until the output arrives
    assert "kollab-wake-1" in output


async def test_the_ninth_open_is_refused_with_an_error(tmp_path):
    for _ in range(terminal.MAX_SESSIONS):
        await Link(tmp_path).open()

    refused = (await Link(tmp_path).send(type="open"))[0]

    assert refused["type"] == "error"
    assert "limit" in refused["message"]


async def test_a_bad_link_id_is_refused_and_detach_forgets_the_link(tmp_path):
    bad = await code_service.call("terminal.poll", {"link": "short"}, root=str(tmp_path))
    link = Link(tmp_path)
    await link.open()
    await link.call("detach")

    assert bad["status"] == 400
    assert link.id not in terminal._LINKS


async def test_an_idle_link_is_swept_and_its_shell_is_left_to_the_reaper(tmp_path):
    link = Link(tmp_path)
    session_id = await link.open()
    terminal._LINKS[link.id].touched -= terminal.LINK_IDLE_SECONDS + 1

    terminal._sweep()

    assert link.id not in terminal._LINKS
    assert terminal._SESSIONS[session_id].attached == 0


async def test_the_shell_gets_no_kollab_or_secret_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="env; echo kollab-end-$((40+2))\n")

    output = await link.until("kollab-end-42")

    assert "TERM=xterm-256color" in output  # the environment was printed
    assert "KOLLAB_" not in output
    assert "MY_TOKEN" not in output


async def test_the_shell_sees_secret_named_variables_when_the_setting_is_on(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")
    link = Link(tmp_path, CodeSettings(pass_secret_env=True))
    await link.open()
    await link.send(type="input", data="env; echo kollab-end-$((40+2))\n")

    output = await link.until("kollab-end-42")

    assert "MY_TOKEN=2" in output
    assert "KOLLAB_" not in output


def test_shell_env_drops_kollab_and_secret_names(monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")

    env = terminal._shell_env()

    assert "KOLLAB_X" not in env and "MY_TOKEN" not in env
    assert env["TERM"] == "xterm-256color"


def test_shell_env_drops_common_credential_names_and_keeps_ordinary_ones(monkeypatch):
    # #987 review: these slipped through the four-marker rule.
    secrets = (
        "DATABASE_URL",
        "GITHUB_PAT",
        "PAT",
        "OPENAI_APIKEY",
        "DB_PASSWD",
        "AWS_CREDENTIALS",
        "SSH_PRIVATE_KEY",
        "AWS_ACCESS_KEY_ID",
    )
    for name in secrets:
        monkeypatch.setenv(name, "x")
    for name in ("PATTERN_DIR", "EDITOR_PATH_HINT", "PATHEXT_NOTE"):
        monkeypatch.setenv(name, "ok")

    env = terminal._shell_env()

    assert not [name for name in secrets if name in env]
    assert env["PATTERN_DIR"] == "ok" and env["EDITOR_PATH_HINT"] == "ok" and env["PATHEXT_NOTE"] == "ok"
    assert "PATH" in env


def test_shell_env_passes_common_credential_names_when_the_setting_is_on(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT", "p")
    monkeypatch.setenv("DATABASE_URL", "d")

    env = terminal._shell_env(CodeSettings(pass_secret_env=True))

    assert env["GITHUB_PAT"] == "p" and env["DATABASE_URL"] == "d"


def test_shell_env_keeps_secret_names_when_the_setting_is_on_but_never_kollab_names(monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("KOLLABOR_HUB_BRIDGE_TOKEN", "3")
    monkeypatch.setenv("MY_TOKEN", "2")

    env = terminal._shell_env(CodeSettings(pass_secret_env=True))

    assert env["MY_TOKEN"] == "2"
    assert "KOLLAB_X" not in env and "KOLLABOR_HUB_BRIDGE_TOKEN" not in env


async def test_the_shell_sits_on_a_real_terminal_and_leads_its_process_group(tmp_path):
    """The shell's stdio is a PTY device, and the shell is its own process group leader (job control needs both)."""
    link = Link(tmp_path)
    await link.open()
    await link.send(
        type="input", data="echo kollab-tty-$$-$(ps -o pgid= -p $$ | tr -d ' ')-$(tty)-kollab-end-''$((6*7))\n"
    )

    output = await link.until("kollab-end-42")

    found = re.search(r"kollab-tty-(\d+)-(\d+)-(/dev/ttys\d+|/dev/pts/\d+)-kollab-end-42", output)
    assert found, output
    shell_pid, process_group, _device = found.groups()
    assert shell_pid == process_group


async def test_linux_supervisor_uses_its_source_file_for_isolated_python(monkeypatch, tmp_path):
    import os
    from types import SimpleNamespace

    commands = []

    class Session:
        def __init__(self, _sid, _proc, fd, **_kwargs):
            self.fd = fd

    def fake_popen(argv, **kwargs):
        commands.append(argv)
        os.write(kwargs["pass_fds"][0], b"4242\n")
        return SimpleNamespace(pid=4241, returncode=None)

    monkeypatch.setattr(terminal.sys, "platform", "linux")
    monkeypatch.setattr(terminal, "_set_winsize", lambda *_args: None)
    monkeypatch.setattr(terminal, "_login_shell", lambda *_args: "/bin/sh")
    monkeypatch.setattr(terminal, "_shell_env", lambda *_args: {})
    monkeypatch.setattr(terminal.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(terminal, "_Session", Session)
    session = terminal._spawn(str(tmp_path), 80, 24)
    try:
        expected = os.path.join(os.path.dirname(terminal.__file__), "_terminal_supervisor.py")
        assert commands[0][2] == expected
    finally:
        os.close(session.fd)


async def test_ctrl_c_interrupts_a_running_command_and_the_shell_prompts_again(tmp_path):
    link = Link(tmp_path)
    await link.open()
    started = time.monotonic()
    await link.send(type="input", data="echo kollab-sleeping-''$((2*3)); sleep 30\n")
    await link.until("kollab-sleeping-6")
    time.sleep(0.5)  # let sleep take the foreground; a Ctrl-C that lands before that would reach the shell

    await link.send(type="input", data="\x03")
    await link.send(type="input", data="echo kollab-after-''$((5*5))\n")
    await link.until("kollab-after-25")

    assert time.monotonic() - started < 20  # a sleep that ignored Ctrl-C would hold this test for its 30 seconds


async def test_a_shell_that_exits_ends_the_stream_with_an_exit_message(tmp_path):
    link = Link(tmp_path)
    await link.open()
    await link.send(type="input", data="exit 3\n")

    kinds = []
    deadline = time.monotonic() + 20
    while "exit" not in kinds and time.monotonic() < deadline:
        kinds += [message["type"] for message in await link.poll()]

    assert kinds[-1] == "exit"


@pytest.mark.parametrize("mode", ["close", "idle", "shutdown"])
async def test_teardown_ends_background_job_after_shell_exits(tmp_path, mode):
    """A job in its own shell process group must not outlive any terminal teardown path."""
    import asyncio
    import contextlib
    import os
    import shlex
    import sys

    link = Link(tmp_path, CodeSettings(shell="/bin/sh", login=False))
    sid = await link.open()
    session = terminal._SESSIONS[sid]
    marker = tmp_path / "job.pid"
    heartbeat = tmp_path / "job-heartbeat"
    program = (
        "import os,signal,time\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "while True:\n"
        f"    open({str(heartbeat)!r}, 'a').write('x')\n"
        "    time.sleep(0.02)\n"
    )
    child_pid = None
    process = None
    try:
        await link.send(type="input", data=f"{shlex.quote(sys.executable)} -c {shlex.quote(program)} &\n")
        for _ in range(200):
            if marker.exists() and marker.read_text().strip() and heartbeat.exists():
                child_pid = int(marker.read_text())
                process = psutil.Process(child_pid)
                break
            await asyncio.sleep(0.02)
        assert child_pid is not None, "background job did not start"
        assert os.getsid(child_pid) == session.pid
        if mode == "close":
            await link.send(type="close")
        elif mode == "idle":
            await link.call("detach")
            session._reap_seconds = 0.01
            session.schedule_reap()
        else:
            await terminal.shutdown_terminals()
        for _ in range(200):
            if not _job_is_running(process):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail(f"background job survived {mode}")
        size = heartbeat.stat().st_size
        await asyncio.sleep(0.15)
        assert heartbeat.stat().st_size == size, "background job continued to do work"
    finally:
        # psutil verifies the recorded process identity before signalling it.
        if process is not None:
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()



async def test_closing_one_terminal_does_not_signal_another(tmp_path):
    import asyncio

    first = Link(tmp_path, CodeSettings(shell="/bin/sh", login=False))
    other = Link(tmp_path, CodeSettings(shell="/bin/sh", login=False))
    try:
        async with asyncio.timeout(10):
            first_id = await first.open()
            other_id = await other.open()
            first_session = terminal._SESSIONS[first_id]
            await first.send(type="close")
            task = first_session.destroy()
            if task is not None:
                await asyncio.shield(task)
            other_session = terminal._SESSIONS[other_id]
            assert other_session.pid not in {process.pid for process in first_session._owned_processes}
            assert not other_session.exited
            await other.send(type="input", data="echo still-alive-91''23\n")
            assert "still-alive-9123" in await other.until("still-alive-9123")
    except TimeoutError:
        pytest.fail("Closing one terminal or checking its neighbor exceeded 10 seconds")


async def test_teardown_does_not_wait_forever_for_an_unreaped_shell(tmp_path, monkeypatch):
    import asyncio
    import contextlib

    link = Link(tmp_path, CodeSettings(shell="/bin/sh", login=False))
    sid = await link.open()
    await link.send(type="input", data="echo shell-ready-57''9\n")
    await link.until("shell-ready-579")
    session = terminal._SESSIONS[sid]
    monkeypatch.setattr(session, "_collect_processes", lambda known: known)
    monkeypatch.setattr(session, "_signal_owned", lambda *_args: None)
    monkeypatch.setattr(terminal, "KILL_AFTER_SECONDS", 0.05)
    try:
        task = session.destroy()
        assert task is not None
        async with asyncio.timeout(1.0):
            await asyncio.shield(task)
    finally:
        # This is the exact shell started by this test; Popen guards against signalling a reused PID.
        with contextlib.suppress(ProcessLookupError):
            session._proc.kill()
        if session._watch_task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(session._watch_task, 3)
        if session._teardown_task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(session._teardown_task, 3)


def _job_is_running(process):
    """Whether the test process can still execute."""
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def test_job_liveness_does_not_count_a_zombie():
    import asyncio
    import os

    import psutil

    pid = os.fork()
    if pid == 0:
        os._exit(0)
    process = psutil.Process(pid)
    try:
        for _ in range(100):
            if process.status() == psutil.STATUS_ZOMBIE:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("test child did not become a zombie")
        assert not _job_is_running(process)
    finally:
        os.waitpid(pid, 0)


@pytest.mark.parametrize("mode", ["close", "idle", "shutdown"])
@pytest.mark.parametrize("delay", [0.0, 0.01, 0.04, 0.06])
@pytest.mark.parametrize("close_fds", [
    False,
    pytest.param(True, marks=pytest.mark.xfail(
        sys.platform == "darwin", strict=True,
        reason="macOS cannot discover a detached child that closes every inherited PTY descriptor between scans",
    )),
])
async def test_teardown_stops_fast_detached_descendant(tmp_path, mode, delay, close_fds):
    import asyncio
    import contextlib
    import shlex
    import sys

    import psutil

    marker = tmp_path / "detached.pid"
    heartbeat = tmp_path / "heartbeat"
    program = tmp_path / "spawn_job.py"
    program.write_text(
        "import os, signal, time\n"
        f"time.sleep({delay!r})\n"
        "if os.fork(): os._exit(0)\n"
        "os.setsid()\n"
        f"if {close_fds!r}: os.closerange(0, 256)\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "while True:\n"
        f"    open({str(heartbeat)!r}, 'a').write('x')\n"
        "    time.sleep(0.02)\n"
    )
    link = Link(tmp_path, CodeSettings(shell="/bin/sh", login=False))
    session_id = await link.open()
    session = terminal._SESSIONS[session_id]
    process = None
    try:
        await link.send(type="input", data=f"{shlex.quote(sys.executable)} {shlex.quote(str(program))} &\n")
        for _ in range(250):
            if marker.exists() and marker.read_text().strip() and heartbeat.exists():
                process = psutil.Process(int(marker.read_text()))
                break
            await asyncio.sleep(0.01)
        assert process is not None, "detached child did not start"
        assert _job_is_running(process)
        if mode == "close":
            await link.send(type="close")
            await session.destroy()
        elif mode == "idle":
            await link.call("detach")
            session._reap_seconds = 0.01
            session.schedule_reap()
            for _ in range(200):
                if session.closed:
                    await session.destroy()
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("idle reaper did not run")
        else:
            await terminal.shutdown_terminals()
        for _ in range(200):
            if not _job_is_running(process):
                break
            await asyncio.sleep(0.01)
        assert not _job_is_running(process), "detached child survived terminal teardown"
        size = heartbeat.stat().st_size
        await asyncio.sleep(0.15)
        assert heartbeat.stat().st_size == size, "detached child continued to do work"
    finally:
        if process is not None:
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()  # psutil checks the captured process identity before signalling


async def test_slow_process_enumeration_does_not_block_terminal_close(tmp_path, monkeypatch):
    import asyncio

    link = Link(tmp_path, CodeSettings(shell="/bin/sh", login=False))
    sid = await link.open()
    await link.send(type="input", data="echo ready-scan-62''1\n")
    await link.until("ready-scan-621")
    session = terminal._SESSIONS[sid]

    def slow_scan():
        time.sleep(2.0)
        return iter(())

    monkeypatch.setattr(terminal.psutil, "process_iter", slow_scan)
    monkeypatch.setattr(terminal, "KILL_AFTER_SECONDS", 0.05)
    started = time.monotonic()
    task = session.destroy()
    assert task is not None
    async with asyncio.timeout(3):
        await task
    assert time.monotonic() - started < 1.2, "process enumeration blocked the event loop during terminal close"


async def test_late_process_scan_is_never_signalled_after_close(monkeypatch):
    import asyncio
    import os
    import signal
    import threading

    # Construct the PTY-scanning lifecycle directly: Linux terminals normally use a supervisor instead.
    session = object.__new__(terminal._Session)
    session.sid = f"late-scan-{uuid.uuid4().hex}"
    session._loop = asyncio.get_running_loop()
    session._scan_future = None
    session._capture_task = None
    session._watch_task = None
    session._teardown_task = None
    session._reap_handle = None
    session._waiters = set()
    session._owned_processes = set()
    session._inspection_warnings = set()
    session._pty_name = "/test/pty"
    session._pty_anchor = os.open(os.devnull, os.O_RDONLY)  # Only this test-owned descriptor is closed.
    session._shell_process = object()
    session._supervised = False
    session.fd = None
    session.exited = True
    session.closed = False
    started = asyncio.Event()
    release = threading.Event()
    sent = []

    class Unrelated:
        pid = -1

        def send_signal(self, sig):
            sent.append(sig)

    unrelated = Unrelated()

    def late_scan(known):
        session._loop.call_soon_threadsafe(started.set)
        if not release.wait(timeout=15):
            raise AssertionError("test did not release the blocked scan")
        return known | {unrelated}

    monkeypatch.setattr(session, "_collect_processes", late_scan)
    monkeypatch.setattr(terminal, "INITIAL_SCAN_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(terminal, "KILL_AFTER_SECONDS", 0.05)
    task = session.destroy()
    assert task is not None
    try:
        async with asyncio.timeout(10):
            await started.wait()
            pending = session._scan_future
            assert pending is not None and not pending.done()
            await asyncio.shield(task)
            assert session.closed and session._pty_anchor is None
            assert not pending.done(), "the test must finish teardown before releasing the scan"
            release.set()
            assert unrelated in await asyncio.shield(pending), "the late scan did not return its result"
            assert await session._capture_async(1) == set(), "a scan completed after close was applied"
            session._signal_owned(signal.SIGKILL)
            assert unrelated not in session._owned_processes
            assert not sent
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if session._pty_anchor is not None:
            os.close(session._pty_anchor)
            session._pty_anchor = None



async def test_supervised_teardown_does_not_schedule_pty_scan(monkeypatch):
    session = object.__new__(terminal._Session)
    session._supervised = True
    session._watch_task = None
    session._pty_anchor = None
    session.fd = None

    async def unexpected_scan(_timeout):
        pytest.fail("supervised teardown must not schedule a PTY-holder scan")

    monkeypatch.setattr(session, "_capture_async", unexpected_scan)
    await session._finish_teardown()



async def test_shutdown_does_not_wait_forever_for_a_stalled_teardown(monkeypatch):
    import asyncio
    import contextlib

    stalled = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(terminal, "_SESSIONS", {})
    monkeypatch.setattr(terminal, "_TEARDOWNS", {stalled})
    monkeypatch.setattr(terminal, "SUPERVISOR_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(terminal, "INITIAL_SCAN_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(terminal, "KILL_AFTER_SECONDS", 0.05)
    monkeypatch.setattr(terminal, "REAP_WAIT_SECONDS", 0.05)
    try:
        async with asyncio.timeout(1):
            await terminal.shutdown_terminals()
    finally:
        stalled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stalled
        terminal._TEARDOWNS.clear()
