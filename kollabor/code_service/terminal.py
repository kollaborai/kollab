"""A login shell on a PTY, hosted by the daemon of the session being used.

The engine's ``/code/terminal`` WebSocket is a bridge: every client message goes to ``message`` and the output comes
back through ``poll`` (a long poll), so a terminal on another computer reaches the same code over the device channel.
A shell outlives its link. Its output is kept in a ring of ``RING_BYTES``; a link that attaches again with the
``sessionId`` gets the ring replayed, and a shell nobody is attached to is ended after ``REAP_AFTER_SECONDS``. A link
the engine stopped polling (it died, or the channel dropped) is detached after ``LINK_IDLE_SECONDS``. Input, output
and tokens are never logged.
"""

import asyncio
import codecs
import contextlib
import json
import logging
import os
import secrets
import select
import signal
import struct
import subprocess
import sys
import threading
from collections import deque
from itertools import islice
from typing import Any

import psutil

from .reply import Reply
from .settings import DEFAULTS, CodeSettings

logger = logging.getLogger(__name__)

REAP_AFTER_SECONDS = 600.0
KILL_AFTER_SECONDS = 1.0
REAP_WAIT_SECONDS = 0.2
SUPERVISOR_WAIT_SECONDS = 4.0
SCAN_WAIT_SECONDS = 0.05
INITIAL_SCAN_WAIT_SECONDS = 0.2
LINK_IDLE_SECONDS = 90.0
# A daemon answers the requests of one connection in the order they came, so a poll that waited long would hold up
# every keystroke and every other call behind it. The wait is short and the engine polls again (routes/code.py).
MAX_POLL_SECONDS = 0.1
MAX_SESSIONS = 8
MAX_LINKS = 64
RING_BYTES = 256 * 1024
READ_BYTES = 65536
MAX_INPUT_BYTES = 64 * 1024
MAX_PENDING_INPUT_BYTES = 1024 * 1024
# Leave ample space for the RPC envelope and transport framing. A PTY read is
# at most READ_BYTES before decoding and remains below this budget when escaped.
MAX_POLL_JSON_BYTES = 1024 * 1024
MAX_POLL_MESSAGES = 128
MAX_DIMENSION = 500
DEFAULT_COLS = 80
DEFAULT_ROWS = 24
_SECRET_MARKERS = (
    "TOKEN",
    "SECRET",
    "API_KEY",
    "APIKEY",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "DATABASE_URL",
)
# Whole `_`-separated words that mark a secret; a substring test would hit PATH and PATTERN_DIR.
_SECRET_WORDS = frozenset({"PAT"})
# Never passed on, whatever the setting says: the engine's and the daemon's own variables.
_OWN_PREFIXES = ("KOLLAB_", "KOLLABOR_")

_SESSIONS: dict[str, _Session] = {}
_TEARDOWNS: set[asyncio.Task[None]] = set()
_SCAN_SLOTS = threading.BoundedSemaphore(MAX_SESSIONS)


class _Session:
    """One shell on a PTY, held in ``_SESSIONS`` from open until it is reaped."""

    def __init__(
        self,
        sid: str,
        proc: subprocess.Popen[bytes],
        fd: int,
        ring_bytes: int = RING_BYTES,
        reap_seconds: float = REAP_AFTER_SECONDS,
        shell_pid: int | None = None,
        pty_name: str | None = None,
    ) -> None:
        self.sid = sid
        self._ring_limit = ring_bytes
        self._reap_seconds = reap_seconds  # 0 = a shell nobody watches is never reaped
        self.pid = shell_pid if shell_pid is not None else proc.pid
        self._supervised = shell_pid is not None
        self._pty_name = pty_name
        self._pty_anchor = os.dup(fd) if pty_name is not None else None
        self._pty_identity = None
        if pty_name is not None:
            st = os.stat(pty_name)
            self._pty_identity = (st.st_dev & 0xFFFFFFFF, st.st_ino)
        self._proc = proc  # _watch reaps the child and sets its status here, so Popen never waits or warns
        self.fd: int | None = fd
        self.exited = False  # the shell ended by itself
        self.closed = False  # the server ended it: a close message, the idle reap, or shutdown
        self.exit_code: int | None = None
        self.attached = 0  # sockets streaming this shell
        self._loop = asyncio.get_running_loop()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._chunks: deque[tuple[int, str, int]] = deque()  # the ring: (sequence, text, its UTF-8 size)
        self._ring_bytes = 0
        self._next_seq = 0
        self._waiters: set[asyncio.Event] = set()
        self._pending = bytearray()
        self._reap_handle: asyncio.TimerHandle | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._teardown_task: asyncio.Task[None] | None = None
        self._scan_future: asyncio.Future[set[psutil.Process]] | None = None
        self._owned_processes: set[psutil.Process] = set()
        self._inspection_warnings: set[str] = set()
        try:
            self._shell_process: psutil.Process | None = psutil.Process(self.pid)
            self._owned_processes.add(self._shell_process)
            self._shell_started = self._shell_process.create_time()
        except psutil.Error as exc:
            self._shell_process = None
            self._shell_started = None
            logger.warning("Cannot inspect terminal process tree; terminal jobs may survive teardown: %s", exc)
        self._loop.add_reader(fd, self._on_readable)
        self._capture_task = self._loop.create_task(self._monitor_processes()) if pty_name is not None else None

    @property
    def first_seq(self) -> int:
        return self._chunks[0][0] if self._chunks else self._next_seq

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def chunks_from(self, seq: int):
        """Iterate retained chunks from ``seq`` without copying the whole ring."""
        start = max(seq, self.first_seq)
        return islice(self._chunks, start - self.first_seq, None)

    async def wait_for_change(self, seq: int) -> None:
        """Return at once when there is text after ``seq`` or the shell is over; otherwise wait for more."""
        if self._next_seq > seq or self.exited or self.closed:
            return
        waiter = asyncio.Event()
        self._waiters.add(waiter)
        try:
            await waiter.wait()
        finally:
            self._waiters.discard(waiter)

    def write_input(self, data: bytes) -> bool:
        """Queue ``data`` for the shell. False when it will not take input (it ended, or its backlog is full)."""
        if self.fd is None or len(self._pending) + len(data) > MAX_PENDING_INPUT_BYTES:
            return False
        self._pending.extend(data)
        self._flush_input()
        return True

    def _flush_input(self) -> None:
        while self._pending and self.fd is not None:
            try:
                written = os.write(self.fd, self._pending)
            except BlockingIOError:
                written = 0
            except OSError:  # the shell's side is gone; the reader reports the end
                self._pending.clear()
                break
            if written == 0:
                self._loop.add_writer(self.fd, self._flush_input)
                return
            del self._pending[:written]
        if self.fd is not None:
            self._loop.remove_writer(self.fd)

    def _on_readable(self) -> None:
        if self.fd is None:
            return
        try:
            data = os.read(self.fd, READ_BYTES)
        except BlockingIOError:
            return
        except OSError:  # EIO: the shell's side of the PTY has closed
            data = b""
        if not data:
            self._loop.remove_reader(self.fd)  # _watch re-arms it while the shell is still alive
            tail = self._decoder.decode(b"", final=True)
            if tail:
                self._publish(tail)
            self._ensure_watch()
            return
        text = self._decoder.decode(data)
        if text:
            self._publish(text)

    def _publish(self, text: str) -> None:
        size = len(text.encode("utf-8"))
        self._chunks.append((self._next_seq, text, size))
        self._next_seq += 1
        self._ring_bytes += size
        while self._ring_bytes > self._ring_limit and len(self._chunks) > 1:
            _, _, dropped = self._chunks.popleft()
            self._ring_bytes -= dropped
        self._wake()

    def _wake(self) -> None:
        for waiter in list(self._waiters):
            waiter.set()

    def _ensure_watch(self) -> None:
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = self._loop.create_task(self._watch())

    async def _monitor_processes(self) -> None:
        while not self.closed:
            await self._capture_async(SCAN_WAIT_SECONDS)
            await asyncio.sleep(0.05)

    def _collect_processes(self, known: set[psutil.Process]) -> set[psutil.Process]:
        """Capture only this shell's descendants and members of its Unix session.

        psutil Process handles retain creation time and check it before signalling. Session membership is scanned
        only while the original shell handle is alive; a numeric session id alone could later be reused.
        """
        owned = known.copy()
        if self._supervised:
            return owned
        shell = self._shell_process
        if shell is None:
            return owned
        processes = None
        try:
            processes = list(psutil.process_iter())
        except (psutil.Error, OSError) as exc:
            if "tree" not in self._inspection_warnings:
                logger.warning("Cannot enumerate terminal process tree; job discovery may be incomplete: %s", exc)
                self._inspection_warnings.add("tree")
        if processes is not None:
            try:
                # psutil's children(recursive=True) enumerates the system. Calling it for each known process makes
                # a single scan quadratic and blocks the event loop while other terminals are trying to close.
                shell_alive = shell.is_running() and self._proc.returncode is None
                by_parent: dict[int, list[psutil.Process]] = {}
                for process in processes:
                    try:
                        parent = process.ppid()
                        by_parent.setdefault(parent, []).append(process)
                        if (shell_alive and self._shell_started is not None
                                and process.create_time() >= self._shell_started
                                and os.getsid(process.pid) == self.pid and process.is_running()):
                            owned.add(process)
                    except (ProcessLookupError, PermissionError, psutil.Error):
                        continue
                # Walk the one snapshot from identity-checked previously owned processes, including detached ones.
                pending = list(owned)
                visited: set[int] = set()
                while pending:
                    parent = pending.pop()
                    if parent.pid in visited or not parent.is_running():
                        continue
                    visited.add(parent.pid)
                    for process in by_parent.get(parent.pid, []):
                        try:
                            if (process not in owned and parent.is_running() and process.is_running()
                                    and process.create_time() >= parent.create_time() and parent.is_running()):
                                owned.add(process)
                                pending.append(process)
                        except psutil.Error:
                            continue
            except (psutil.Error, OSError) as exc:
                if "tree" not in self._inspection_warnings:
                    logger.warning("Cannot inspect terminal process tree; jobs may survive teardown: %s", exc)
                    self._inspection_warnings.add("tree")
        if (self._pty_name is not None and self._pty_identity is not None and self._pty_anchor is not None
                and self._shell_started is not None):
            try:
                from ._terminal_darwin import pty_holders

                owned.update(pty_holders(self._pty_name, self._pty_identity, self._shell_started, processes))
            except (psutil.Error, OSError) as exc:
                if "pty" not in self._inspection_warnings:
                    logger.warning("Cannot inspect terminal PTY holders; jobs may survive teardown: %s", exc)
                    self._inspection_warnings.add("pty")
        return owned

    async def _capture_async(self, timeout: float) -> set[psutil.Process]:
        """Run at most one daemon scan per terminal without blocking the event loop."""
        future = self._scan_future
        if future is None:
            if not _SCAN_SLOTS.acquire(blocking=False):
                if "scan_capacity" not in self._inspection_warnings:
                    logger.warning("Terminal process scans are busy; jobs may survive teardown")
                    self._inspection_warnings.add("scan_capacity")
                return set()
            future = self._loop.create_future()
            self._scan_future = future
            known = self._owned_processes.copy()
            loop = self._loop

            def scan() -> None:
                try:
                    result = self._collect_processes(known)
                except Exception:
                    logger.exception("Terminal process scan failed; jobs may survive teardown")
                    result = known
                finally:
                    _SCAN_SLOTS.release()

                def deliver() -> None:
                    if not future.done():
                        future.set_result(result)

                try:
                    loop.call_soon_threadsafe(deliver)
                except RuntimeError:  # The daemon's event loop has already closed.
                    pass

            try:
                threading.Thread(target=scan, name="kollab-terminal-scan", daemon=True).start()
            except RuntimeError:
                _SCAN_SLOTS.release()
                self._scan_future = None
                logger.exception("Cannot start terminal process scan; jobs may survive teardown")
                return set()
        try:
            found = await asyncio.wait_for(asyncio.shield(future), timeout=max(0.0, timeout))
        except TimeoutError:
            if not future.done():
                return set()
            found = future.result()
        finally:
            if future.done() and self._scan_future is future:
                self._scan_future = None
        # Never apply a late scan after the PTY anchor was released and its device could be reused.
        if self._pty_name is not None and self._pty_anchor is None:
            return set()
        before = self._owned_processes.copy()
        self._owned_processes.update(found)
        return self._owned_processes - before

    def _signal_owned(self, sig: int, processes: set[psutil.Process] | None = None) -> None:
        # Send to individual identity-checked processes: a process group may have disappeared and its id been reused.
        for process in tuple(self._owned_processes if processes is None else processes):
            try:
                process.send_signal(sig)
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError) as exc:
                logger.warning("Cannot signal terminal process %s: %s", process.pid, exc)
        if processes is None and self._shell_process is None and self._proc.returncode is None:
            # Popen owns this child and checks its wait status before signalling it.
            with contextlib.suppress(ProcessLookupError):
                self._proc.send_signal(sig)

    async def _finish_teardown(self) -> None:
        """Escalate captured jobs even if the shell has already exited."""
        try:
            if self._supervised:
                await self._wait_for_watch(SUPERVISOR_WAIT_SECONDS)
                return
            # On macOS closing the master revokes slave descriptors, so discover holders first. An existing
            # monitor scan may predate close; spend the remaining budget on a fresh snapshot if it completed.
            scan_deadline = self._loop.time() + INITIAL_SCAN_WAIT_SECONDS
            await self._capture_async(INITIAL_SCAN_WAIT_SECONDS)
            if self._scan_future is None and self._loop.time() < scan_deadline:
                await self._capture_async(scan_deadline - self._loop.time())
            if self._scan_future is not None and not self._scan_future.done():
                logger.warning("Terminal process scan timed out; jobs may survive teardown")
            self._signal_owned(signal.SIGHUP)
            self._signal_owned(signal.SIGTERM)
            self._close_fd()
            deadline = self._loop.time() + KILL_AFTER_SECONDS
            while self._loop.time() < deadline:
                await asyncio.sleep(min(0.05, max(0, deadline - self._loop.time())))
                new_processes = await self._capture_async(min(SCAN_WAIT_SECONDS, deadline - self._loop.time()))
                if new_processes:
                    self._signal_owned(signal.SIGHUP, new_processes)
                    self._signal_owned(signal.SIGTERM, new_processes)
            self._signal_owned(signal.SIGKILL)
            await self._wait_for_watch(REAP_WAIT_SECONDS)
        finally:
            self._close_fd()
            if self._pty_anchor is not None:
                os.close(self._pty_anchor)
                self._pty_anchor = None

    async def _wait_for_watch(self, timeout: float) -> None:
        """Bound teardown; leave the watcher running to reap a child that exits later."""
        task = self._watch_task
        if task is not None:
            _, pending = await asyncio.wait({task}, timeout=timeout)
            if pending:
                logger.warning("Terminal shell did not exit within %.1f seconds; it may still be running", timeout)

    async def _watch(self) -> None:
        """Poll until the shell is reaped, independently of job teardown."""
        while True:
            try:
                done, status = os.waitpid(self._proc.pid, os.WNOHANG)
            except ChildProcessError:
                self._proc.returncode = 0
                self._shell_ended(None)
                return
            if done:
                code = os.waitstatus_to_exitcode(status)
                self._proc.returncode = code
                if self._supervised and self.closed and code == 125:
                    logger.warning("Terminal supervisor exited with status 125; job cleanup may be incomplete")
                self._shell_ended(code)
                return
            if not self.closed and self.fd is not None:
                self._loop.add_reader(self.fd, self._on_readable)
            await asyncio.sleep(0.05)

    def _shell_ended(self, code: int | None) -> None:
        self.exit_code = code
        self.exited = True
        self._close_fd()
        self._wake()
        if self.attached == 0 and not self.closed:
            self.schedule_reap()

    def _close_fd(self) -> None:
        if self.fd is None:
            return
        self._loop.remove_reader(self.fd)
        self._loop.remove_writer(self.fd)
        os.close(self.fd)
        self.fd = None
        self._pending.clear()

    def schedule_reap(self) -> None:
        self.cancel_reap()
        if self._reap_seconds > 0:
            self._reap_handle = self._loop.call_later(self._reap_seconds, self._reap_idle)

    def cancel_reap(self) -> None:
        if self._reap_handle is not None:
            self._reap_handle.cancel()
            self._reap_handle = None

    def _reap_idle(self) -> None:
        self._reap_handle = None
        if self.attached == 0:
            self.destroy()

    def destroy(self) -> asyncio.Task[None] | None:
        """End the terminal and return the independently escalating cleanup task."""
        if not self.closed:
            self.closed = True
            if self._capture_task is not None:
                self._capture_task.cancel()
            self.cancel_reap()
            _SESSIONS.pop(self.sid, None)
            if self._supervised:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.send_signal(signal.SIGTERM)
                self._close_fd()
            elif self.fd is not None:
                # Keep the master open for the bounded initial PTY-holder scan.
                self._loop.remove_reader(self.fd)
                self._loop.remove_writer(self.fd)
            if not self.exited:
                self._ensure_watch()
            self._teardown_task = self._loop.create_task(self._finish_teardown())
            _TEARDOWNS.add(self._teardown_task)
            self._teardown_task.add_done_callback(_TEARDOWNS.discard)
            self._wake()
        return self._teardown_task


class _Link:
    """One engine socket's view of a shell: which shell it streams (if any) and how far into the ring it has read.

    ``seq`` moves only when the engine acknowledges what it received (``poll``'s ``ack``), so a reply lost on the
    way is sent again by the next poll.
    """

    def __init__(self) -> None:
        self.session: _Session | None = None
        self.seq = 0
        self.touched = asyncio.get_running_loop().time()
        self._kicked = asyncio.Event()

    def kick(self) -> None:
        """Wake the poll in flight: a message changed what this link has to say."""
        self._kicked.set()

    def attach(self, session: _Session, cursor: int | None = None) -> None:
        self.detach()
        self.session = session
        self.seq = min(cursor, session.next_seq) if cursor is not None else session.first_seq
        session.attached += 1
        session.cancel_reap()

    def detach(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            session.attached -= 1
            if session.attached == 0 and not session.closed:
                session.schedule_reap()

    async def wait(self, timeout: float) -> None:
        """Return at once when there is text to send, the shell is over, or a message kicked; else after ``timeout``."""
        session = self.session
        ahead = session is not None and (session.next_seq > self.seq or session.exited or session.closed)
        if self._kicked.is_set() or ahead:
            self._kicked.clear()
            return
        waits = [asyncio.ensure_future(self._kicked.wait())]
        if session is not None:
            waits.append(asyncio.ensure_future(session.wait_for_change(self.seq)))
        try:
            await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in waits:
                task.cancel()
        self._kicked.clear()

    def pending(self) -> tuple[list[dict[str, Any]], int]:
        """What the engine has not been sent yet and the sequence to acknowledge; once the shell is over and its
        text is read, the exit message."""
        session = self.session
        if session is None:
            return [], self.seq
        start = max(self.seq, session.first_seq)
        messages: list[dict[str, Any]] = []
        if self.seq < session.first_seq:
            messages.append({"type": "gap", "nextSeq": session.first_seq})
        size = len(json.dumps(messages))
        nxt = start
        for seq, text, _ in session.chunks_from(self.seq):
            message = {"type": "output", "data": text, "seq": seq}
            message_size = len(json.dumps(message)) + 2  # array separator
            if len(messages) >= MAX_POLL_MESSAGES or size + message_size > MAX_POLL_JSON_BYTES:
                break
            messages.append(message)
            size += message_size
            nxt = seq + 1
        if nxt == start and start == session.next_seq and (session.exited or session.closed):
            messages.append({"type": "exit", "code": session.exit_code})
        return messages, nxt


def _error(message: str) -> dict[str, Any]:
    return {"type": "error", "message": message}


def _dimension(value: object) -> int | None:
    """A terminal size from the client, clamped to 1..MAX_DIMENSION; None when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = int(value)
    except ValueError, OverflowError:  # NaN or infinity, which Python's json accepts
        return None
    return max(1, min(MAX_DIMENSION, number))


def _login_shell(configured: str = "") -> str:
    """The shell program: the setting ``terminal.shell`` when it is executable, else $SHELL, else zsh, else sh."""
    for candidate in (configured, os.environ.get("SHELL", ""), "/bin/zsh", "/bin/sh"):
        # An absolute path only: a bare name would be looked up in the project folder, where a clone can plant it.
        if candidate and os.path.isabs(candidate) and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


def _looks_secret(upper: str) -> bool:
    if any(marker in upper for marker in _SECRET_MARKERS):
        return True
    return any(word in _SECRET_WORDS for word in upper.split("_"))


def _env_verdict(name: str, settings: CodeSettings) -> str | None:
    """Why a variable does not reach a new shell (``denied``, ``kollab`` or ``secret``), or None when it passes."""
    upper = name.upper()
    if upper in {item.upper() for item in settings.env_deny}:
        return "denied"
    if upper in {item.upper() for item in settings.env_allow}:
        return None
    if not settings.pass_kollab_env and upper.startswith(_OWN_PREFIXES):
        return "kollab"
    if not settings.pass_secret_env and _looks_secret(upper):
        return "secret"
    return None


def _shell_env(settings: CodeSettings = DEFAULTS) -> dict[str, str]:
    """The daemon's environment for a shell, filtered by the settings, with TERM set and ``env_set`` laid on top.

    Defaults: ``KOLLAB_*`` / ``KOLLABOR_*`` and names carrying a secret marker are dropped. Each has a switch
    (``terminal.pass_kollab_env``, ``terminal.pass_secret_env``), and ``env_allow`` / ``env_deny`` decide by exact name.
    """
    env = {name: value for name, value in os.environ.items() if _env_verdict(name, settings) is None}
    env["TERM"] = "xterm-256color"
    env.update(dict(settings.env_set))
    return env


def env_report(settings: CodeSettings = DEFAULTS) -> dict[str, Any]:
    """The names a new shell would not get, grouped by reason. Names only: values never leave the environment."""
    hidden: dict[str, list[str]] = {"secret": [], "kollab": [], "denied": []}
    for name in sorted(os.environ):
        verdict = _env_verdict(name, settings)
        if verdict is not None:
            hidden[verdict].append(name)
    return {
        "hidden": hidden,
        "count": sum(len(names) for names in hidden.values()),
        "shell": os.path.basename(_login_shell(settings.shell)),
    }


# The shell starts from this one-line program, not from a fork inside the multi-threaded engine (a fork there can
# deadlock before exec). os.login_tty makes the PTY (fd 0) the controlling terminal, so Ctrl-C reaches the shell's
# foreground job. Python ignores SIGPIPE and SIGXFSZ and exec keeps ignored signals, so both are reset first.
# -I ignores PYTHON* variables and never puts the working directory on sys.path.
_SHELL_LAUNCHER = (
    "import os, signal, sys\n"
    "os.login_tty(0)\n"
    "signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n"
    "signal.signal(signal.SIGXFSZ, signal.SIG_DFL)\n"
    "os.execv(sys.argv[1], [sys.argv[1]] + sys.argv[2:])\n"
)


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    # imported here, not at the top: both are Unix-only and the module must still import on Windows (#268)
    import fcntl
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _spawn(cwd: str, cols: int, rows: int, settings: CodeSettings = DEFAULTS) -> _Session:
    """Start the shell on a new PTY. subprocess does the fork-and-exec, so no Python code runs in a forked child."""
    shell = _login_shell(settings.shell)
    env = _shell_env(settings)
    env["PWD"] = cwd
    master, slave = os.openpty()
    ready_read = ready_write = None
    proc = None
    shell_pid = None
    pty_name = os.ttyname(slave) if sys.platform == "darwin" else None
    try:
        _set_winsize(slave, cols, rows)
        if sys.platform.startswith("linux"):
            ready_read, ready_write = os.pipe()
            supervisor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_terminal_supervisor.py")
            argv = [sys.executable, "-I", supervisor,
                    str(ready_write), shell, *(["-l"] if settings.login else [])]
            proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                                    close_fds=True, pass_fds=(ready_write,))
            os.close(ready_write)
            ready_write = None
            if not select.select([ready_read], [], [], 5)[0]:
                raise OSError("terminal supervisor did not start")
            answer = os.read(ready_read, 64).strip()
            if not answer.isdigit():
                raise OSError("terminal supervisor could not start")
            shell_pid = int(answer)
        else:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-c", _SHELL_LAUNCHER, shell, *(["-l"] if settings.login else [])],
                stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env, close_fds=True,
            )
    except BaseException:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=SUPERVISOR_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=REAP_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    logger.error("Terminal launcher did not exit after being killed")
        os.close(master)
        raise
    finally:
        os.close(slave)
        if ready_read is not None:
            os.close(ready_read)
        if ready_write is not None:
            os.close(ready_write)
    os.set_inheritable(master, False)
    os.set_blocking(master, False)
    return _Session(
        secrets.token_urlsafe(16), proc, master,
        ring_bytes=settings.scrollback_kb * 1024,
        reap_seconds=settings.idle_reap_minutes * 60,
        shell_pid=shell_pid, pty_name=pty_name,
    )


def _open_session(
    msg: dict[str, Any], link: _Link, cwd: str, settings: CodeSettings = DEFAULTS
) -> list[dict[str, Any]]:
    session_id = msg.get("sessionId")
    if session_id is not None:
        session = _SESSIONS.get(session_id) if isinstance(session_id, str) else None
        if session is None or session.closed:
            return [_error("That terminal session is no longer running.")]
        cursor = msg.get("cursor")
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            cursor = None
        link.attach(session, cursor)
        return [{"type": "ready", "sessionId": session.sid}]
    if not settings.terminal_enabled:
        return [_error("Terminals are turned off for this agent. Setting: kollabor.code.terminal.enabled")]
    if not hasattr(os, "openpty"):  # Windows has no PTY; ConPTY is planned (docs/specs/code-shell-windows.md)
        return [
            _error(
                "This computer cannot run a web terminal yet: it needs a Unix PTY. "
                "Setting: kollabor.code.terminal.enabled turns the view off."
            )
        ]
    if sum(1 for live in _SESSIONS.values() if not (live.exited or live.closed)) >= settings.max_shells:
        return [
            _error(
                f"Too many terminals are open; the limit is {settings.max_shells}. "
                "Setting: kollabor.code.terminal.max_shells"
            )
        ]
    cols = _dimension(msg.get("cols")) or DEFAULT_COLS
    rows = _dimension(msg.get("rows")) or DEFAULT_ROWS
    try:
        session = _spawn(cwd, cols, rows, settings)
    except OSError:
        logger.exception("could not start a terminal shell")
        return [_error("The shell could not start.")]
    _SESSIONS[session.sid] = session
    link.attach(session)
    if settings.start_command and not settings.read_only:  # a read-only agent's shell takes no input, this included
        session.write_input((settings.start_command + "\n").encode("utf-8", errors="replace"))
    return [{"type": "ready", "sessionId": session.sid}]


def _input_message(msg: dict[str, Any], link: _Link, settings: CodeSettings = DEFAULTS) -> list[dict[str, Any]]:
    if settings.read_only:
        return [
            _error("Read only is on for this agent, so the terminal takes no input. Setting: kollabor.code.read_only")
        ]
    session = link.session
    if session is None:
        return [_error("No terminal is open.")]
    if session.exited or session.closed:
        return [_error("That terminal has ended.")]
    data = msg.get("data")
    if not isinstance(data, str):
        return [_error("Input must be text.")]
    raw = data.encode("utf-8", errors="replace")
    if len(raw) > MAX_INPUT_BYTES:
        return [_error("That input is larger than 64 KB.")]
    if not session.write_input(raw):
        return [_error("The terminal is not taking input right now.")]
    return []


def _resize_message(msg: dict[str, Any], link: _Link) -> None:
    session = link.session
    cols = _dimension(msg.get("cols"))
    rows = _dimension(msg.get("rows"))
    if session is None or session.fd is None or cols is None or rows is None:
        return
    with contextlib.suppress(OSError):
        _set_winsize(session.fd, cols, rows)


def _on_message(raw: object, link: _Link, cwd: str, settings: CodeSettings = DEFAULTS) -> list[dict[str, Any]]:
    if not isinstance(raw, str):
        return [_error("The message is not JSON.")]
    try:
        msg = json.loads(raw)
    except ValueError:
        return [_error("The message is not JSON.")]
    if not isinstance(msg, dict):
        return [_error("The message is not an object.")]
    kind = msg.get("type")
    if kind == "open":
        return _open_session(msg, link, cwd, settings)
    if kind == "input":
        return _input_message(msg, link, settings)
    if kind == "resize":
        _resize_message(msg, link)
    elif kind == "close":
        session = link.session
        link.detach()
        if session is not None:
            session.destroy()
    else:
        return [_error("Unknown message type.")]
    return []


# --- links: the engine's sockets, by id ---

_LINKS: dict[str, _Link] = {}
_sweeper: asyncio.TimerHandle | None = None


def _link(link_id: object, *, create: bool = True) -> _Link | None:
    if not isinstance(link_id, str) or not 8 <= len(link_id) <= 64:
        return None
    link = _LINKS.get(link_id)
    if link is None and create:
        if len(_LINKS) >= MAX_LINKS:
            return None
        link = _LINKS[link_id] = _Link()
        _arm_sweeper()
    if link is not None:
        link.touched = asyncio.get_running_loop().time()
    return link


def _arm_sweeper() -> None:
    global _sweeper
    if _sweeper is None and _LINKS:
        _sweeper = asyncio.get_running_loop().call_later(LINK_IDLE_SECONDS / 3, _sweep)


def _sweep() -> None:
    """Detach links nobody has polled for ``LINK_IDLE_SECONDS``: their shells then wait out the idle reap."""
    global _sweeper
    _sweeper = None
    now = asyncio.get_running_loop().time()
    for link_id, link in list(_LINKS.items()):
        if now - link.touched > LINK_IDLE_SECONDS:
            link.detach()
            del _LINKS[link_id]
    _arm_sweeper()


def _bad(message: str, status: int = 400) -> Reply:
    return Reply({"success": False, "error": {"code": "bad_request", "message": message}}, status)


async def serve(op: str, cwd: str, params: dict[str, Any], settings: CodeSettings = DEFAULTS) -> Reply:
    """``message`` (one client message in, the immediate answers out), ``poll`` (wait for output), ``detach`` or
    ``env_report`` (the variable names a new shell would not get)."""
    if op == "env_report":
        return Reply({"success": True, "data": env_report(settings)})
    link = _link(params.get("link"), create=op != "detach")
    if op == "detach":
        if link is not None:
            link.detach()
            _LINKS.pop(str(params.get("link")), None)
        return Reply({"success": True, "data": {}})
    if link is None:
        return _bad("That terminal link is not valid, or too many are open.")
    if op == "message":
        answers = _on_message(params.get("raw"), link, cwd, settings)
        link.kick()
        return Reply({"success": True, "data": {"messages": answers}})
    if op == "poll":
        ack = params.get("ack")
        if isinstance(ack, int) and not isinstance(ack, bool) and link.session is not None:
            link.seq = max(link.seq, min(ack, link.session.next_seq))
        wait = params.get("wait")
        seconds = float(wait) if isinstance(wait, int | float) and not isinstance(wait, bool) else 0.0
        await link.wait(max(0.0, min(MAX_POLL_SECONDS, seconds)))
        link.touched = asyncio.get_running_loop().time()
        messages, nxt = link.pending()
        return Reply({"success": True, "data": {"messages": messages, "ack": nxt}})
    return _bad("That terminal operation is not available.", 404)


async def shutdown_terminals() -> None:
    """Daemon shutdown: end every terminal shell and wait until each one is gone."""
    global _sweeper
    if _sweeper is not None:
        _sweeper.cancel()
        _sweeper = None
    _LINKS.clear()
    tasks: list[asyncio.Task[None]] = []
    for session in list(_SESSIONS.values()):
        task = session.destroy()
        if task is not None:
            tasks.append(task)
    tasks.extend(task for task in _TEARDOWNS if task not in tasks)
    if tasks:
        limit = max(SUPERVISOR_WAIT_SECONDS, INITIAL_SCAN_WAIT_SECONDS + KILL_AFTER_SECONDS + REAP_WAIT_SECONDS) + 0.5
        done, pending = await asyncio.wait(tasks, timeout=limit)
        if pending:
            logger.warning("Terminal shutdown timed out; %d cleanup task(s) may still be running", len(pending))
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                logger.error("Terminal cleanup failed: %s", task.exception())
    _SESSIONS.clear()
