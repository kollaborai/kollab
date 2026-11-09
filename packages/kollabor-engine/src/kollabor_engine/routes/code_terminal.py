"""Terminal WebSocket for the floating terminal panel: a login shell on a PTY, started in the chat's folder.

``/code/terminal`` is outside the HTTP bearer middleware (WebSockets do not pass through it), so it checks its own
token: the first message must be ``{"type": "auth", "token": ...}`` within ``AUTH_TIMEOUT_SECONDS``, else the socket
closes with 4401. A shell outlives its socket. Its output is kept in a ring of ``RING_BYTES``; a socket that attaches
again with the ``sessionId`` gets the ring replayed, and a shell nobody is attached to is ended after
``REAP_AFTER_SECONDS``. Input, output and the token are never logged.
"""

import asyncio
import codecs
import contextlib
import json
import logging
import os
import secrets
import signal
import struct
import subprocess
import sys
from collections import deque
from itertools import islice
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect  # type: ignore[import-not-found]

from ..auth import validate_token
from ..workspace_folder import folder_or_400

logger = logging.getLogger(__name__)
router = APIRouter(tags=["code-terminal"])

AUTH_TIMEOUT_SECONDS = 5.0
REAP_AFTER_SECONDS = 600.0
KILL_AFTER_SECONDS = 1.0
MAX_SESSIONS = 8
RING_BYTES = 256 * 1024
READ_BYTES = 65536
MAX_INPUT_BYTES = 64 * 1024
MAX_PENDING_INPUT_BYTES = 1024 * 1024
MAX_DIMENSION = 500
DEFAULT_COLS = 80
DEFAULT_ROWS = 24
CLOSE_UNAUTHORIZED = 4401
_SECRET_MARKERS = ("TOKEN", "SECRET", "API_KEY", "PASSWORD")

_SESSIONS: dict[str, _Session] = {}


class _Session:
    """One shell on a PTY, held in ``_SESSIONS`` from open until it is reaped."""

    def __init__(self, sid: str, proc: subprocess.Popen[bytes], fd: int) -> None:
        self.sid = sid
        self.pid = proc.pid
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
        self._kill_at: float | None = None
        self._loop.add_reader(fd, self._on_readable)

    @property
    def first_seq(self) -> int:
        return self._chunks[0][0] if self._chunks else self._next_seq

    @property
    def next_seq(self) -> int:
        return self._next_seq

    def chunks_from(self, seq: int) -> tuple[list[str], int]:
        """The ring's text from sequence ``seq`` (older text may be gone) and the sequence to ask for next."""
        start = max(seq, self.first_seq)
        texts = [text for _, text, _ in islice(self._chunks, start - self.first_seq, None)]
        return texts, self._next_seq

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
        while self._ring_bytes > RING_BYTES and len(self._chunks) > 1:
            _, _, dropped = self._chunks.popleft()
            self._ring_bytes -= dropped
        self._wake()

    def _wake(self) -> None:
        for waiter in list(self._waiters):
            waiter.set()

    def _ensure_watch(self) -> None:
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = self._loop.create_task(self._watch())

    async def _watch(self) -> None:
        """Poll until the shell is gone, then record its exit code.

        While it lives, re-arm the PTY reader (a read can report the end before the shell has opened its side of the
        PTY), and SIGKILL it once a close has outlived ``KILL_AFTER_SECONDS``.
        """
        while True:
            try:
                done, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:  # reaped by someone else; the exit code is lost
                self._proc.returncode = 0  # the answer Popen itself gives for a child whose status is gone
                self._shell_ended(None)
                return
            if done:
                code = os.waitstatus_to_exitcode(status)
                self._proc.returncode = code  # reaped here: Popen must not wait again or warn that it still runs
                self._shell_ended(code)
                return
            if self.closed:
                if self._kill_at is not None and self._loop.time() >= self._kill_at:
                    self._kill_at = None
                    _signal_group(self.pid, signal.SIGKILL)
            elif self.fd is not None:
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
        self._reap_handle = self._loop.call_later(REAP_AFTER_SECONDS, self._reap_idle)

    def cancel_reap(self) -> None:
        if self._reap_handle is not None:
            self._reap_handle.cancel()
            self._reap_handle = None

    def _reap_idle(self) -> None:
        self._reap_handle = None
        if self.attached == 0:
            self.destroy()

    def destroy(self) -> asyncio.Task[None] | None:
        """End the shell now. Returns the task that finishes once it is gone, or None if it had already ended."""
        if not self.closed:
            self.closed = True
            self.cancel_reap()
            _SESSIONS.pop(self.sid, None)
            self._kill_at = self._loop.time() + KILL_AFTER_SECONDS
            self._close_fd()
            if not self.exited:
                _signal_group(self.pid, signal.SIGHUP)
                self._ensure_watch()
            self._wake()
        return None if self.exited else self._watch_task


class _Link:
    """One socket's view of a shell: which shell it streams (if any) and how far into the ring it has sent."""

    def __init__(self) -> None:
        self.session: _Session | None = None
        self.seq = 0

    def attach(self, session: _Session) -> None:
        self.detach()
        self.session = session
        self.seq = session.first_seq  # a new or reattaching socket starts at the oldest text kept
        session.attached += 1
        session.cancel_reap()

    def detach(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            session.attached -= 1
            if session.attached == 0 and not session.closed:
                session.schedule_reap()

    async def wait(self) -> None:
        session = self.session
        if session is None:
            await asyncio.Event().wait()  # nothing attached: only a message from the socket can wake this link
        else:
            await session.wait_for_change(self.seq)

    def drain(self) -> list[dict[str, Any]]:
        """What the socket has not been sent yet; once the shell is over and its text is sent, the exit message."""
        session = self.session
        if session is None:
            return []
        texts, self.seq = session.chunks_from(self.seq)
        messages: list[dict[str, Any]] = [{"type": "output", "data": text} for text in texts]
        if not texts and (session.exited or session.closed):
            messages.append({"type": "exit", "code": session.exit_code})
            self.detach()
        return messages


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


def _login_shell() -> str:
    """The user's shell: $SHELL when it is executable, else zsh, else sh."""
    for candidate in (os.environ.get("SHELL", ""), "/bin/zsh", "/bin/sh"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


def _shell_env() -> dict[str, str]:
    """The engine's environment without KOLLAB_* names or names carrying a secret marker, with TERM set."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("KOLLAB_") and not any(marker in name.upper() for marker in _SECRET_MARKERS)
    }
    env["TERM"] = "xterm-256color"
    return env


# The shell starts from this one-line program, not from a fork inside the multi-threaded engine (a fork there can
# deadlock before exec). os.login_tty makes the PTY (fd 0) the controlling terminal, so Ctrl-C reaches the shell's
# foreground job. Python ignores SIGPIPE and SIGXFSZ and exec keeps ignored signals, so both are reset first.
# -I ignores PYTHON* variables and never puts the working directory on sys.path.
_SHELL_LAUNCHER = (
    "import os, signal, sys\n"
    "os.login_tty(0)\n"
    "signal.signal(signal.SIGPIPE, signal.SIG_DFL)\n"
    "signal.signal(signal.SIGXFSZ, signal.SIG_DFL)\n"
    "os.execv(sys.argv[1], [sys.argv[1], '-l'])\n"
)


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    # imported here, not at the top: both are Unix-only and the module must still import on Windows (#268)
    import fcntl
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _signal_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def _spawn(cwd: str, cols: int, rows: int) -> _Session:
    """Start the shell on a new PTY. subprocess does the fork-and-exec, so no Python code runs in a forked child."""
    shell = _login_shell()
    env = _shell_env()
    env["PWD"] = cwd
    master, slave = os.openpty()
    try:
        _set_winsize(slave, cols, rows)
        # No start_new_session: os.login_tty calls setsid itself, and setsid fails in a process group leader.
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _SHELL_LAUNCHER, shell],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd,
            env=env,
            close_fds=True,
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)  # the shell has its own copy; ours would keep the PTY open after the shell ends
    os.set_inheritable(master, False)
    os.set_blocking(master, False)
    return _Session(secrets.token_urlsafe(16), proc, master)


def _open_session(msg: dict[str, Any], link: _Link) -> list[dict[str, Any]]:
    session_id = msg.get("sessionId")
    if session_id is not None:
        session = _SESSIONS.get(session_id) if isinstance(session_id, str) else None
        if session is None or session.closed:
            return [_error("That terminal session is no longer running.")]
        link.attach(session)
        return [{"type": "ready", "sessionId": session.sid}]
    workspace = msg.get("workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        return [_error("Pick a folder for the terminal first.")]
    try:
        cwd = folder_or_400(workspace)
    except HTTPException as refused:
        return [_error(str(refused.detail))]
    if sum(1 for live in _SESSIONS.values() if not (live.exited or live.closed)) >= MAX_SESSIONS:
        return [_error(f"Too many terminals are open; the limit is {MAX_SESSIONS}.")]
    cols = _dimension(msg.get("cols")) or DEFAULT_COLS
    rows = _dimension(msg.get("rows")) or DEFAULT_ROWS
    try:
        session = _spawn(cwd, cols, rows)
    except OSError:
        logger.exception("could not start a terminal shell")
        return [_error("The shell could not start.")]
    _SESSIONS[session.sid] = session
    link.attach(session)
    return [{"type": "ready", "sessionId": session.sid}]


def _input_message(msg: dict[str, Any], link: _Link) -> list[dict[str, Any]]:
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


def _on_message(raw: str, link: _Link) -> list[dict[str, Any]]:
    try:
        msg = json.loads(raw)
    except ValueError:
        return [_error("The message is not JSON.")]
    if not isinstance(msg, dict):
        return [_error("The message is not an object.")]
    kind = msg.get("type")
    if kind == "open":
        return _open_session(msg, link)
    if kind == "input":
        return _input_message(msg, link)
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


async def _authenticated(ws: WebSocket) -> bool:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except TimeoutError, WebSocketDisconnect:
        return False
    try:
        message = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(message, dict) or message.get("type") != "auth":
        return False
    token = message.get("token")
    if not isinstance(token, str):
        return False
    try:
        return validate_token(token)
    except TypeError:  # secrets.compare_digest refuses text with non-ASCII characters
        return False


@router.websocket("/code/terminal")
async def code_terminal_ws(ws: WebSocket) -> None:
    """Terminal socket. Send ``auth`` first, then ``open``, ``input``, ``resize`` or ``close``.

    The server answers ``ready`` (with the ``sessionId`` to reattach by), ``output``, ``exit`` and ``error``.
    """
    await ws.accept()
    if not await _authenticated(ws):
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    link = _Link()
    incoming: asyncio.Future[str] | None = None
    try:
        while True:
            if incoming is None:
                incoming = asyncio.ensure_future(ws.receive_text())
            changed = asyncio.ensure_future(link.wait())
            try:
                await asyncio.wait({incoming, changed}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                changed.cancel()
            if incoming.done():
                raw = incoming.result()  # raises WebSocketDisconnect once the client has gone
                incoming = None
                for message in _on_message(raw, link):
                    await ws.send_json(message)
            for message in link.drain():
                await ws.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        if incoming is not None:
            incoming.cancel()
        link.detach()


async def shutdown_terminals() -> None:
    """Engine shutdown: end every terminal shell and wait until each one is gone."""
    tasks: list[asyncio.Task[None]] = []
    for session in list(_SESSIONS.values()):
        task = session.destroy()
        if task is not None:
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _SESSIONS.clear()
