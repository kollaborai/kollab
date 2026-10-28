"""The kill switch on one machine: halt.json lock, listener, hand-off, sweep, waiters, CLI.

Spec: ``docs/specs/network-sync-and-control.md`` section 1 ("Halt, On Each
Machine That Takes It", "The Halt Listener", "Waiters", "Resume", "Commands").
The rules live in ``network_halt.py``; this module runs them on this machine.

``halt.json`` keeps, beside each network's halt book (``networks``):

- ``scopes``: ``{network id: "machine" | [folders]}`` for every network whose
  halt is in force here. Only the listener removes an entry, after it handed
  that network's link back; a folder is "marked lifted" when no scope holds it.
- ``stopped``: what the sweep stopped (``identity``, ``folder``, ``service``).

Locks: ``private/halt.lock`` is held by the one listener for its whole life
(its identity); ``private/halt.write.lock`` serializes every
read-modify-write of ``halt.json`` (two daemons merging at once).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import fcntl
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOCK = "halt.lock"
WRITE_LOCK = "halt.write.lock"
SOCK = "halt.sock"
HALT_JSON = "halt.json"
POLL_SECONDS = 5.0
LISTENER_IDLE_SECONDS = 2.0
HAND_BACK_FLUSH_SECONDS = 15.0
LISTENER_GRACE_SECONDS = 30.0  # a fresh listener waits this long for the hand-off that started it
LISTENER_FLAG = "--halt-listener"
WAITER_ENV = "KOLLAB_HALT_WAITER"
HALTED_LINE = "Network halted by {device}. Resume needs a person on two machines: Settings → Network."
UNREADABLE_LINE = (
    "halt.json is unreadable ({why}), so every folder is held as halted. "
    "Restore it, or clear it with: kollab --hub resume --local"
)
RESUME_PROPOSED = (
    "Resume proposed. A person on another machine confirms it: /connect resume there, "
    "or Settings → Network → Confirm Resume."
)

# Argv at process entry, before cli_main pops KOLLAB_SERVICE or the detached flag.
ENTRY: dict[str, Any] = {}


def capture_entry(argv: list[str] | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Keep the launch's argv and full environment; the first call wins."""
    if not ENTRY:
        ENTRY["argv"] = list(sys.argv if argv is None else argv)
        ENTRY["env"] = dict(os.environ if env is None else env)
    return ENTRY


# ------------------------------------------------------------- files


def kollab_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from kollabor_config.config_utils import get_config_directory

    return Path(get_config_directory())


def private_dir(root: Path | None = None) -> Path:
    d = kollab_root(root) / "private"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    if d.is_symlink():
        raise OSError("private dir is a link")
    return d


def _lock_fd(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)


@contextlib.contextmanager
def write_lock(root: Path | None = None) -> Iterator[None]:
    """``halt.write.lock`` around a read-modify-write of ``halt.json``."""
    fd = _lock_fd(private_dir(root) / WRITE_LOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_halt(root: Path | None = None) -> dict[str, Any]:
    from .network_halt import HaltFile

    return HaltFile(kollab_root(root)).read()


def read_halt_held(root: Path | None = None) -> dict[str, Any]:
    """``read_halt``, but a halt.json that exists and cannot be read holds every folder."""
    try:
        return read_halt(root)
    except (OSError, ValueError, AttributeError, TypeError) as error:
        logger.warning("halt check: halt.json unreadable (%s)", type(error).__name__)
        return {"scopes": {"unreadable": "machine"}, "networks": {}, "unreadable": type(error).__name__}


def clear_malformed_halt(root: Path | None = None) -> str:
    """F7/R6-3: ``resume --local`` on a halt.json that cannot be read.

    A transient OSError touches nothing. A book whose only defect is an odd scope keeps everything else (F10: an
    odd scope becomes ``"machine"``, so it still holds; dropping it would lift that network unconfirmed).
    Anything else unparseable is kept as ``halt.json.bad`` and an empty one started.
    Returns "" when there was nothing to fix, else a line for the person.
    """
    from .network_halt import HaltFile

    with write_lock(root):
        hf = HaltFile(kollab_root(root))
        try:
            hf.read()
            return ""
        except OSError:
            if not (hf.path.is_dir() and not hf.path.is_symlink()):
                return ""
            # F14: a directory named halt.json holds every folder and never reads; the typed resume --local
            # is the person's confirmation, so move it aside and start an empty book
            bad = hf.path.with_name(hf.path.name + ".bad")
            if bad.exists() or bad.is_symlink():
                bad = hf.path.with_name(f"{hf.path.name}.bad.{time.time_ns()}")
            os.replace(hf.path, bad)
            hf.write({"networks": {}, "stopped": []})
            return f"halt.json was a directory; kept as {bad.name} and started an empty one."
        except (ValueError, AttributeError, TypeError):
            pass
        try:
            data = json.loads(hf.path.read_bytes() or b"{}")
            scopes = data["scopes"]
            if not all(isinstance(data.get(k, {}), dict) for k in ("scopes", "networks", "folders")):
                raise ValueError
            fixed = {n: v if v == "machine" or (isinstance(v, list) and v and all(isinstance(r, str) for r in v))
                     else "machine" for n, v in scopes.items()}
            odd = sum(fixed[n] is not scopes[n] for n in scopes)
            data["scopes"] = fixed
            hf.write(data)
            hf.read()
            return f"halt.json had {odd} odd scope entries; each now holds the whole machine, kept the rest."
        except OSError:
            return ""
        except (ValueError, KeyError, AttributeError, TypeError):
            pass
        bad = hf.path.with_name(hf.path.name + ".bad")
        os.replace(hf.path, bad)
        os.chmod(bad, 0o600)
        hf.write({"networks": {}, "stopped": []})
    return f"halt.json could not be read; kept as {bad.name} and started an empty one."


# F10/NEW-1: the process that asked for a control act, set by the socket that carried it (halt.sock, the hub
# socket's connect_proxy); None means this process asked (a TUI's own /connect halt), 0 means unknown.
HALT_CALLER: contextvars.ContextVar[int | None] = contextvars.ContextVar("halt_caller", default=None)


def caller_refusal(pid: int | None) -> str:
    """The refusal line when ``pid`` is, or descends from, an agent or its tool shell, else "".

    Decided by the ppid chain, not by what the caller says: macOS hides the environment of system binaries
    (``nc``, ``zsh``), so an env check alone lets ``nc -U halt.sock`` from a tool shell through. An unknown
    caller, or one already gone, is refused (fail closed).
    """
    from .network_halt import GUARD_REFUSAL, TOOL_SHELL_VARS, caller_chain, read_process

    if not pid or pid < 0:
        return GUARD_REFUSAL
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # gone before its chain could be read (send and exit): refuse
        return GUARD_REFUSAL
    except PermissionError:
        pass
    rows = caller_chain(pid)
    if rows is None:  # F11/NEW-3: ps failed or a link is missing: the chain cannot be cleared
        return GUARD_REFUSAL
    chain = {p for p, _uid in rows}
    try:
        agent_pids = {int(a.get("pid") or 0) for a in _live_agents()}
    except Exception:  # presence unreadable: the chain cannot be cleared
        return GUARD_REFUSAL
    if (chain - {pid}) & agent_pids:
        return GUARD_REFUSAL
    for p, uid in rows:
        try:
            proc = read_process(p, with_cwd=False)
        except (OSError, ValueError):
            proc = None
        if proc is None:
            if uid == os.getuid():  # a process of this user whose environment cannot be read: fail closed
                return GUARD_REFUSAL
            continue  # another user's (login, sshd, sudo): an agent's tag cannot be cleared or set there
        if any(proc.env.get(v) for v in TOOL_SHELL_VARS):
            return GUARD_REFUSAL
    return ""


def peer_pid(sock: Any) -> int:
    """The pid on the other end of a unix socket (LOCAL_PEERPID / SO_PEERCRED), 0 when it cannot be read."""
    import socket
    import struct

    if sock is None or getattr(sock, "family", None) != socket.AF_UNIX:
        return 0
    try:
        if sys.platform == "darwin":
            return int(sock.getsockopt(0, 0x002))  # SOL_LOCAL, LOCAL_PEERPID
        return int(struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0])
    except (OSError, ValueError, struct.error):
        return 0


def tool_shell_refusal() -> str:
    """The refusal line when the caller of a control act is an agent or its tool shell, else ""."""
    from .network_halt import GUARD_REFUSAL

    caller = HALT_CALLER.get()
    if caller is None and os.environ.get("KOLLAB_TOOL_SHELL"):  # this process (the OS copy is its start env)
        return GUARD_REFUSAL
    return caller_refusal(os.getpid() if caller is None else caller)


def update_halt(fn: Callable[[dict[str, Any]], Any], root: Path | None = None) -> Any:
    """Read, change and write ``halt.json`` under the write lock."""
    from .network_halt import HaltFile

    with write_lock(root):
        hf = HaltFile(kollab_root(root))
        data = hf.read()
        out = fn(data)
        hf.write(data)
        return out


def active_scopes(data: dict[str, Any]) -> list:
    return list((data.get("scopes") or {}).values())


def folder_halted(folder: str, data: dict[str, Any]) -> bool:
    from .network_halt import in_scope

    scopes = active_scopes(data)
    return bool(scopes) and in_scope(folder, scopes)


def halted_line(data: dict[str, Any]) -> str:
    """The waiter/launch line, naming the issuer of the newest active halt."""
    if data.get("unreadable"):
        return UNREADABLE_LINE.format(why=data["unreadable"])
    newest: dict = {}
    for net in (data.get("scopes") or {}):
        book = (data.get("networks") or {}).get(net) or {}
        for order in (book.get("halts") or {}).values() if isinstance(book.get("halts"), dict) else []:
            if order.get("issued_at", 0) >= newest.get("issued_at", -1):
                newest = order
    device = newest.get("device") or (newest.get("machine") or "another machine")[:8]
    return HALTED_LINE.format(device=device)


# ----------------------------------------------------------- listener lock


def listener_running(root: Path | None = None) -> bool:
    """Whether a listener runs: the pid it wrote in ``halt.lock`` is a live listener.

    Never takes the lock, so a listener starting at the same moment cannot lose it.
    """
    try:
        pid = int((private_dir(root) / LOCK).read_text().strip() or 0)
    except (OSError, ValueError):
        return False
    if pid <= 0 or not _alive(pid):
        return False
    if pid == os.getpid():
        return True
    from .network_halt import read_process

    proc = read_process(pid, with_cwd=False)
    return proc is not None and LISTENER_FLAG in proc.argv


def start_listener(root: Path | None = None, *, spawn: Callable[[list[str]], Any] | None = None) -> bool:
    """Start a detached ``kollab --halt-listener`` unless one runs. True if started."""
    if listener_running(root):
        return False
    from kollabor.binary import kollab_argv

    argv = [*kollab_argv(), LISTENER_FLAG]
    if spawn is not None:
        spawn(argv)
        return True
    env = {k: v for k, v in os.environ.items() if k not in ("KOLLAB_TOOL_SHELL", "KOLLAB_AGENT_PID")}
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env,
    )
    return True


# ------------------------------------------------------------- halt.sock


def sock_path(root: Path | None = None) -> Path:
    return private_dir(root) / SOCK


async def sock_call(request: dict, root: Path | None = None, timeout: float = 30.0) -> dict:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(sock_path(root))), timeout)
    try:
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        reply = json.loads(line or b"{}")
        return reply if isinstance(reply, dict) else {}
    finally:
        writer.close()


async def wait_for_listener(root: Path | None = None, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError, asyncio.TimeoutError, ValueError):
            if (await sock_call({"op": "ping"}, root, timeout=2.0)).get("ok"):
                return True
        await asyncio.sleep(0.2)
    return False


async def hand_off(order: dict, root: Path | None = None, *, spawn=None) -> dict:
    """Pass a recorded halt to the listener over ``halt.sock``, starting one if needed."""
    start_listener(root, spawn=spawn)
    if not await wait_for_listener(root):
        raise OSError("the halt listener did not start")
    return await sock_call({"op": "halt", **order}, root, timeout=120.0)


# ----------------------------------------------------------- boot check

EXEMPT_FLAGS = frozenset({"--hub", "--version", "-V", "--help", "-h", "--attach", "--web-ui", LISTENER_FLAG})
EXEMPT_COMMANDS = frozenset({"service", "relay", "mcp"})
EXIT_FLAGS = frozenset({"-p", "--pipe"})


def _leading_flags(args: list[str]) -> list[str]:
    """Flags before the first prompt/positional argument (a value flag's value is skipped)."""
    from kollabor.daemon import _NEW_DAEMON_FLAGS, _VALUE_FLAGS

    value_flags = _VALUE_FLAGS | (_NEW_DAEMON_FLAGS - {"--daemon"})
    out, i = [], 0
    while i < len(args) and args[i].startswith("-") and args[i] not in ("-", "--"):
        out.append(args[i])
        if args[i] in value_flags:
            i += 1
        i += 1
    return out


def boot_decision(argv: list[str], env: dict[str, str], folder: str, data: dict[str, Any]) -> str:
    """What a launch does under a halt: ``run``, ``exit`` (75), ``waiter`` or ``interactive``.

    ``argv``/``env`` are the ones captured at entry; ``--detached`` decides in
    the forked child (its env carries ``KOLLAB_DETACHED_CHILD``) or a service.
    """
    from kollabor.daemon import DETACHED_CHILD_ENV, SERVICE_ENV

    args = argv[1:]
    if args[:1] and args[0] in EXEMPT_COMMANDS:
        return "run"
    if any(a in EXEMPT_FLAGS or a.split("=", 1)[0] in EXEMPT_FLAGS for a in _leading_flags(args)):
        return "run"
    detached = "--detached" in args or "-d" in args
    service = env.get(SERVICE_ENV) == "1"
    child = env.get(DETACHED_CHILD_ENV) == "1"
    if detached and not (child or service):
        return "run"  # the parent forks; the child checks
    if not folder_halted(folder, data):
        return "run"
    if args[:1] == ["acp"] or any(a in EXIT_FLAGS for a in args):
        return "exit"
    if detached or service or child:
        return "waiter"
    return "interactive"


def _on_a_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (OSError, ValueError, AttributeError):
        return False


def halted_after_exec_failure(argv: list[str], *, root: Path | None = None) -> bool:
    """A detached child whose exec failed re-checks the halt before it runs in-process."""
    from kollabor.daemon import DETACHED_CHILD_ENV

    if not os.path.lexists(kollab_root(root) / "private" / HALT_JSON):
        return False
    data = read_halt_held(root)
    return boot_decision(argv, {**os.environ, DETACHED_CHILD_ENV: "1"}, os.getcwd(), data) != "run"


def boot_check(argv: list[str] | None = None, env: dict[str, str] | None = None, *, root: Path | None = None) -> None:
    """Run at process entry: cheap when no halt is in force (one stat)."""
    entry = capture_entry(argv, env)
    path = kollab_root(root) / "private" / HALT_JSON
    if not os.path.lexists(path):
        return
    data = read_halt_held(root)
    folder = os.getcwd()
    decision = boot_decision(entry["argv"], entry["env"], folder, data)
    if decision == "run":
        return
    from .network_halt import EX_TEMPFAIL

    if decision == "interactive" and not _on_a_terminal():
        decision = "exit"  # `kollab "query"` piped or captured: nobody can confirm a resume

    if decision == "exit":
        print(halted_line(data), file=sys.stderr)
        raise SystemExit(EX_TEMPFAIL)
    start_listener(root)
    if decision == "waiter":
        run_waiter(entry["argv"], entry["env"], folder, root=root)
    else:
        raise SystemExit(run_interactive(folder, root=root))


# ---------------------------------------------------------------- waiter


def waiter_identity(argv: list[str]) -> str:
    for flag in ("--as", "--agent"):
        if flag in argv and argv.index(flag) + 1 < len(argv):
            return argv[argv.index(flag) + 1].rsplit("/", 1)[-1]
    return f"halted-{os.getpid()}"


def restart_argv(argv: list[str]) -> list[str]:
    from kollabor.binary import kollab_argv

    return [*kollab_argv(), *argv[1:]]


def run_waiter(
    argv: list[str],
    env: dict[str, str],
    folder: str,
    *,
    root: Path | None = None,
    execve: Callable[[str, list[str], dict[str, str]], Any] = os.execve,
    sleep: Callable[[float], Any] = time.sleep,
    max_polls: int | None = None,
) -> None:
    """Hold the launch until its folder is marked lifted, then exec it again."""
    os.environ[WAITER_ENV] = "1"
    register_waiter(root)
    presence, server = _waiter_presence(argv, folder, root)
    polls = 0
    try:
        while True:
            if not folder_halted(folder, read_halt_held(root)):
                break
            if not listener_running(root):
                start_listener(root)
            polls += 1
            if max_polls is not None and polls >= max_polls:
                return
            sleep(POLL_SECONDS)
    finally:
        unregister_waiter(root)
        if server is not None:
            server.stop()
        if presence is not None:
            presence.unlink(missing_ok=True)
    new_argv = restart_argv(argv)
    execve(new_argv[0], new_argv, {k: v for k, v in env.items() if k != WAITER_ENV})


def _waiters_dir(root: Path | None) -> Path:
    d = private_dir(root) / "waiters"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def register_waiter(root: Path | None = None) -> None:
    """A waiter's pid file: the sweep reads it, since a process cannot change the environment it was read with."""
    with contextlib.suppress(OSError):
        (_waiters_dir(root) / str(os.getpid())).write_text("")


def unregister_waiter(root: Path | None = None) -> None:
    with contextlib.suppress(OSError):
        (_waiters_dir(root) / str(os.getpid())).unlink(missing_ok=True)


def waiter_pids(root: Path | None = None) -> set[int]:
    try:
        return {int(f.name) for f in _waiters_dir(root).iterdir() if f.name.isdigit() and _alive(int(f.name))}
    except OSError:
        return set()


class _StatusSocket:
    """An attach socket that answers ``get_status`` with the halt and refuses turns."""

    def __init__(self, path: Path, identity: str, root: Path | None):
        import socket
        import threading

        self.path = path
        path.unlink(missing_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        os.chmod(path, 0o600)
        self.sock.listen(8)
        self.identity = identity
        self.root = root
        self.closed = False
        threading.Thread(target=self._serve, daemon=True, name="kollab-halt-waiter").start()

    def answer(self, request: dict) -> dict:
        line = halted_line(read_halt_held(self.root))
        if request.get("action") == "get_status":
            return {"type": "status", "identity": self.identity, "state": "halted", "halted": line}
        return {"type": "error", "error": line}

    def _serve(self) -> None:
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    raw = conn.makefile("rb").readline(65536)
                    request = json.loads(raw or b"{}")
                    reply = self.answer(request if isinstance(request, dict) else {})
                    conn.sendall(json.dumps(reply).encode() + b"\n")
                except (OSError, ValueError):
                    continue

    def stop(self) -> None:
        self.closed = True
        with contextlib.suppress(OSError):
            self.sock.close()
        self.path.unlink(missing_ok=True)


def _waiter_presence(argv: list[str], folder: str, root: Path | None) -> tuple[Path | None, _StatusSocket | None]:
    try:
        from .presence import get_presence_dir, get_socket_dir

        identity = waiter_identity(argv)
        sock = get_socket_dir() / f"halted-{os.getpid()}.sock"
        server = _StatusSocket(sock, identity, root)
        record = get_presence_dir() / f"halted-{os.getpid()}.json"
        record.write_text(
            json.dumps(
                {
                    "identity": identity,
                    "pid": os.getpid(),
                    "socket_path": str(sock),
                    "project": folder,
                    "state": "halted",
                    "started_at": time.time(),
                }
            )
        )
        return record, server
    except Exception as error:  # a waiter without presence still waits
        logger.warning("halt waiter: no presence (%s)", type(error).__name__)
        return None, None


# ------------------------------------------------------- interactive view


def run_interactive(folder: str, *, root: Path | None = None, read_key: Callable[[], str] | None = None) -> int:
    """The halted interactive launch: no model, the connect-halt view over ``halt.sock``.

    Drives the same ``HaltScreen`` the TUI's Connect screen opens with `h`
    (plugins/altview/connect_halt_altview.py), printed, since no app runs here.
    """
    from kollabor_tui.key_parser import KeyParser
    from kollabor_tui.terminal_state import get_global_terminal_state
    from plugins.altview.connect_halt_altview import HaltScreen

    from .halt_context import HaltContext

    print(halted_line(read_halt_held(root)))
    read_key = read_key or _read_key
    start_listener(root)
    screen = HaltScreen(HaltContext(folder, root))
    parser = KeyParser()

    async def step(name: str, char: str, frame_is_current: Callable[[], bool] = lambda: True) -> bool:
        await wait_for_listener(root, timeout=5.0)
        if (name == "Enter" or (screen.reason is None and char)) and not frame_is_current():
            return False
        try:
            done = await screen.key(name, char) if name or char else False
            await screen.load()
        except Exception as exc:  # noqa: BLE001 - operator-safe halt text
            screen.message = str(exc)
            done = False
        return done

    asyncio.run(step("", ""))
    with _waiter_input_mode(read_key is _read_key):
        return _waiter_loop(screen, read_key, step, parser, get_global_terminal_state)


def _waiter_loop(screen: Any, read_key: Callable[[], str], step: Any, parser: Any, terminal_state: Any) -> int:
    from kollabor_tui.key_parser import KeyParser
    from plugins.altview.connect_halt_altview import halt_frame_state, halt_input_allowed

    while True:
        terminal = terminal_state()
        terminal.update_size()
        width, height = terminal.get_size()
        lines, safe = _waiter_lines(screen, width, height)
        if sys.stdout.isatty():
            sys.stdout.write("\x1b[H\x1b[2J" + "\r\n".join(lines))
        else:
            print("\n".join(lines))
        sys.stdout.flush()
        frame = halt_frame_state(screen)
        key = read_key()
        if not key:
            return 0
        if key == "\n":
            key = "\r"
        event = None
        for char in key:
            event = parser.parse_char(char) or event
        if event is None:
            event = parser.check_for_standalone_escape()
        if event is None:
            parser = KeyParser()  # discard an incomplete sequence
            continue
        name, char = event.name, event.char or ""
        if name == "Ctrl+C":
            return 130
        if name == "Escape" and screen.reason is None:
            return 0
        terminal.update_size()
        current_size = terminal.get_size()
        if (not safe or current_size != (width, height)) and (name == "Enter" or (screen.reason is None and char)):
            continue
        if (name == "Enter" or (screen.reason is None and char)) and not halt_input_allowed(
            screen, max(1, width - 1), max(1, height), 1, name, char
        ):
            continue
        def frame_is_current() -> bool:
            terminal.update_size()
            return safe and terminal.get_size() == (width, height) and halt_frame_state(screen) == frame

        asyncio.run(step(name, char, frame_is_current))


def _waiter_lines(screen: Any, width: int, height: int) -> tuple[list[str], bool]:
    """Render a bounded viewport with the complete action target kept in view."""
    from plugins.altview.connect_halt_altview import _wrap, halt_viewport

    # Leave the last column unused so the terminal never auto-wraps a line.
    width = max(1, width - 1)
    height = max(1, height)
    if width < 8:
        return ["Resize"][:height], False
    body, safe = halt_viewport(screen, width, height, 1)
    if not safe:
        return _wrap("Resize terminal to use controls", width)[:height], False
    header = _wrap("Halt and Resume", width)
    if len(header) > 1:
        header = [" Halt"]
    return [*header, *body], True


@contextlib.contextmanager
def _waiter_input_mode(enabled: bool) -> Iterator[None]:
    import termios
    import tty

    if not enabled or not os.isatty(0):
        yield
        return
    old = termios.tcgetattr(0)
    try:
        tty.setraw(0, termios.TCSANOW)
        yield
    finally:
        termios.tcsetattr(0, termios.TCSADRAIN, old)


def _read_key() -> str:
    import codecs
    import select

    if not os.isatty(0):
        return ""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    first = os.read(0, 1)
    if not first:
        return ""
    try:
        result = decoder.decode(first)
    except UnicodeDecodeError:
        return "\ufffd"
    # Gather the rest of a UTF-8 character or terminal escape sequence.
    while (not result or result.startswith("\x1b")) and select.select([0], [], [], 0.3)[0]:
        part = os.read(0, 1)
        if not part:
            break
        try:
            result += decoder.decode(part)
        except UnicodeDecodeError:
            return "\ufffd"
        if result.startswith("\x1b") and len(result) > 1:
            from kollabor_tui.key_parser import KeyParser

            if result[1:] in KeyParser.ESCAPE_SEQUENCES:
                break
    return result or "\ufffd"


# ----------------------------------------------------------------- sweep


def process_table() -> dict[int, int]:
    """pid -> ppid for every process of this user (one ``ps``, or ``/proc`` where there is no ps)."""
    table: dict[int, int] = {}
    try:
        out = subprocess.run(
            ["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                table[int(parts[0])] = int(parts[1])
    except (OSError, subprocess.SubprocessError):
        pass
    if not table and Path("/proc").is_dir():
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    table[int(entry.name)] = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
                except (OSError, ValueError, IndexError):
                    continue
    table.pop(os.getpid(), None)
    return table


def process_tree() -> dict[int, tuple[int, int, int]]:
    """pid -> (ppid, pgid, sid) for every process of this user (F6: the sweep walks parents, not env tags).

    macOS prints a session pointer for ``sess``, not an id; ``_tree_members`` only trusts it on Linux.
    """
    tree: dict[int, tuple[int, int, int]] = {}
    try:
        out = subprocess.run(
            ["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,ppid=,pgid=,sess="],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
                sid = int(parts[3]) if parts[3].isdigit() else 0
                tree[int(parts[0])] = (int(parts[1]), int(parts[2]), sid)
    except (OSError, subprocess.SubprocessError):
        pass
    if not tree:  # no ps: parents only, from /proc
        tree = {pid: (ppid, 0, 0) for pid, ppid in process_table().items()}
    tree.pop(os.getpid(), None)
    return tree


def _etime_seconds(text: str) -> int | None:
    """``ps -o etime`` ([[dd-]hh:]mm:ss) as seconds; None when it is not one."""
    days, _, rest = text.rpartition("-")
    parts = rest.split(":")
    if not (days == "" or days.isdigit()) or not 2 <= len(parts) <= 3 or not all(x.isdigit() for x in parts):
        return None
    seconds = 0
    for x in parts:
        seconds = seconds * 60 + int(x)
    return seconds + int(days or 0) * 86400


def process_info() -> dict[int, tuple[float, str, str]]:
    """pid -> (start time, command, tty) for every process of this user (F11: pid reuse; F12: terminals).

    F12: the start is now minus the elapsed time (``etime``), an epoch second, so a wall clock that
    repeats an hour (the end of daylight saving time) cannot reorder starts the way ``lstart`` read as
    local time does. ``command`` is ps's name and can be forged; nothing trusts it for a decision.
    """
    info: dict[int, tuple[float, str, str]] = {}
    try:
        out = subprocess.run(
            ["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,tty=,etime=,comm="],
            capture_output=True, text=True, timeout=10, env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return info
    now = time.time()
    for line in out.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        elapsed = _etime_seconds(parts[2])
        if elapsed is None:
            continue
        info[int(parts[0])] = (float(int(now) - elapsed), parts[3] if len(parts) > 3 else "", parts[1])
    return info


def candidate_pids() -> list[int]:
    """Every same-user process: a tool child (a CLI, a dev server, a nohup job) rarely names kollab."""
    return sorted(process_table())


def descendants(pids: Iterable[int], all_procs: Iterable) -> set[int]:
    """Children (any depth) of ``pids`` among ``all_procs``: tagged CLIs agents started."""
    procs = list(all_procs)
    out = set(pids)
    grew = True
    while grew:
        grew = False
        for p in procs:
            if p.ppid in out and p.pid not in out:
                out.add(p.pid)
                grew = True
    return out


async def sweep(
    scopes: list,
    *,
    agents: list[dict],
    procs: list,
    stop: Callable[[dict], Awaitable[tuple[bool, str]]],
    protect: Iterable[int],
    install=None,
    waiters: Iterable[int] = (),
    tree: dict[int, tuple[int, int, int]] | None = None,
    info: dict[int, tuple[float, str]] | None = None,
) -> list[tuple[dict, bool, str]]:
    """Stop what a halt stops here (``select_sweep``), each with ``stop``.

    ``agents`` are presence records (waiters, state ``halted``, are skipped);
    ``procs`` are ``Proc`` rows already read; ``protect`` holds the listener,
    the issuing CLI and its ancestors.
    """
    from .network_halt import Install, _cli_args, in_scope, pid_ok, select_sweep, shell_rows

    install = install or Install.current()
    protect = set(protect) | {int(w) for w in waiters}
    # F12/NEW-4: presence is a file anything running as the user can write: a record with an odd pid,
    # project or tool_shells is skipped (its kollab is still found by its command line), never fatal
    agents = [a for a in agents if isinstance(a, dict)]
    protect |= {a["pid"] for a in agents if a.get("state") == "halted" and pid_ok(a.get("pid"))}
    # a presence pid is only stopped while it is still a kollab of this install (a pid is reused after a crash)
    kollab_pids = set()
    for p in procs:
        try:
            if _cli_args(p, install) is not None:
                kollab_pids.add(p.pid)
        except Exception:  # noqa: BLE001
            continue
    in_scope_agents = []
    for a in agents:
        try:
            pid = a.get("pid")
            if (a.get("state") != "halted" and pid_ok(pid) and pid not in protect and pid in kollab_pids
                    and in_scope(str(a.get("project") or "/nonexistent-folder"), scopes)):
                in_scope_agents.append(a)
        except Exception:  # noqa: BLE001
            logger.warning("halt sweep: skipped an unreadable presence record")
    chosen = select_sweep(
        procs,
        install=install,
        scopes=scopes,
        agent_pids=[a["pid"] for a in in_scope_agents],
        protect=protect,
        tree=tree,
        tool_shells=[row for a in in_scope_agents for row in shell_rows(a.get("tool_shells"), a["pid"])],
        info=info,
    )
    by_pid = {a["pid"]: a for a in in_scope_agents}
    records = [by_pid.get(p.pid) or {"identity": f"pid {p.pid}", "pid": p.pid, "project": p.cwd} for p in chosen]
    # F13/NEW-4c: one stop that raises is reported for that agent; the others still stop
    results = await asyncio.gather(*(stop(r) for r in records), return_exceptions=True)
    out = []
    for r, res in zip(records, results):
        if isinstance(res, BaseException):
            out.append((r, False, f"stop {r.get('identity', '?')} failed: {type(res).__name__}: {res}"[:200]))
        else:
            out.append((r, *res))
    return out


def group_kill(protect: Iterable[int]) -> Callable[[int, int], None]:
    """``os.kill`` that takes the whole process group when ``pid`` leads it, unless it holds a protected pid.

    F10/R6-1: a pid that does not lead its group (an agent in a login shell's or a launcher's group) is
    signalled alone; its group belongs to someone else.
    """
    protect = set(protect)

    def kill(pid: int, sig: int) -> None:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = 0
        mine = os.getpgid(0)
        guarded = set()
        for p in protect:
            try:
                guarded.add(os.getpgid(p))
            except OSError:  # it died since the check
                continue
        if pgid == pid and pgid > 1 and pgid != mine and pgid not in guarded:
            os.killpg(pgid, sig)
        else:
            os.kill(pid, sig)

    return kill


def process_identity(pid: int) -> tuple[float, str, str] | None:
    """F15b: (start time, exe, argv hash) of a live same-user ``pid``; None when it cannot be read."""
    from .network_halt import read_process

    try:
        proc = read_process(pid, with_cwd=False)
        row = process_info().get(pid)
        if proc is None or row is None:
            return None
        argv_hash = hashlib.sha256(json.dumps(proc.argv).encode()).hexdigest()[:16]
        return (row[0], proc.exe or row[1], argv_hash)
    except Exception:  # noqa: BLE001
        return None


def kollab_identity(pid: int, install=None) -> tuple[float, str, str] | None:
    """F15b: ``process_identity`` while ``pid`` is still a kollab of this install (a reused pid is None)."""
    from .network_halt import Install, _cli_args, read_process

    try:
        proc = read_process(pid, with_cwd=False)
        if proc is None or _cli_args(proc, install or Install.current()) is None:
            return None
        row = process_info().get(pid)
        if row is None:
            return None
        argv_hash = hashlib.sha256(json.dumps(proc.argv).encode()).hexdigest()[:16]
        return (row[0], proc.exe or row[1], argv_hash)
    except Exception:  # noqa: BLE001
        return None


def same_identity(now: tuple | None, before: tuple | None) -> bool:
    """F15b: one process still: start within ``REUSE_SLACK`` (ps reads drift a second), same exe, same argv."""
    from .network_halt import REUSE_SLACK

    if not isinstance(now, tuple) or not isinstance(before, tuple) or len(now) != 3 or len(before) != 3:
        return False
    return abs(now[0] - before[0]) <= REUSE_SLACK and now[1] == before[1] and now[2] == before[2]


async def fallback_stop(
    scopes: list, agents: list, procs: list, protect: Iterable[int], *, install=None, grace: float = 3.0,
    identify: Callable[[int], tuple[float, str, str] | None] | None = None,
) -> list[tuple[dict, bool, str]]:
    """F12/NEW-4: the sweep threw, so stop the in-scope agents by pid: SIGTERM, then SIGKILL after ``grace``.

    F13/NEW-4b: only pids the sweep itself would take: a kollab of this install among the processes
    already read, in scope by its own folder (or the presence record's, for the same pid). A presence pid
    that is not such a process is left alone (stale presence, a reused pid, a forged row). F15b: every
    signal is bound to the identity (start time, exe, argv hash) read at selection and re-verified
    immediately before SIGTERM and again before SIGKILL, so a pid that became another process in
    between is never signalled. Nothing the agents started is chased here (that is what failed).
    Never raises.
    """
    from .network_halt import Install, _cli_args, _never_swept, in_scope, pid_ok

    identify = identify or (lambda pid: kollab_identity(pid, install))
    out: list[tuple[dict, bool, str]] = []
    try:
        install = install or Install.current()
        protect = set(protect) | {os.getpid()}
        machine = "machine" in scopes
        by_pid: dict[int, dict] = {}
        for a in agents:
            try:
                if isinstance(a, dict) and pid_ok(a.get("pid")) and a.get("state") != "halted":
                    by_pid.setdefault(a["pid"], a)
            except Exception:  # noqa: BLE001
                continue
        targets: dict[int, dict] = {}
        for p in procs:
            try:
                args = _cli_args(p, install)
                if not pid_ok(p.pid) or p.pid in protect or args is None or _never_swept(args, p):
                    continue
                rec = by_pid.get(p.pid)
                # F14: scope by the process's own folder; presence's project is writable by anything of
                # this user, so it never pulls an out-of-scope kollab in. No folder: machine scope only.
                if machine or (p.cwd and in_scope(p.cwd, scopes)):
                    targets[p.pid] = rec or {"identity": f"pid {p.pid}", "pid": p.pid, "project": p.cwd}
            except Exception:  # noqa: BLE001
                continue
        starts = {pid: identify(pid) for pid in targets}
        for pid in list(targets):
            if not same_identity(identify(pid), starts[pid]):  # F15b: reuse between selection and the signal
                out.append((targets.pop(pid), False, f"pid {pid} not killed: identity changed"))
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and any(_running(p) for p in targets):
            await asyncio.sleep(0.1)
        for pid, rec in targets.items():
            name = rec.get("identity", pid)
            if not _running(pid):
                out.append((rec, True, f"stopped {name} (by pid)"))
            elif starts[pid] is not None and same_identity(identify(pid), starts[pid]):
                try:
                    os.kill(pid, signal.SIGKILL)
                    out.append((rec, True, f"stopped {name} (by pid, SIGKILL)"))
                except OSError:
                    out.append((rec, True, f"stopped {name} (by pid)"))
            else:
                out.append((rec, False, f"{name} still running and not verified as the same kollab: not killed"))
        await asyncio.sleep(0.05)
    except Exception as error:  # noqa: BLE001
        logger.exception("halt listener: fallback stop failed")
        out.append(({"identity": "fallback"}, False, f"fallback stop failed: {type(error).__name__}"))
    return out


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _running(pid: int) -> bool:
    """Alive and not a zombie (an exited process its parent has not reaped yet is stopped)."""
    if not _alive(pid):
        return False
    try:
        out = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    stat = out.stdout.strip()
    return not stat.startswith("Z") if stat else _alive(pid)


# -------------------------------------------------------------- listener


class HaltListener:
    """``kollab --halt-listener``: model-less, one per machine, never swept.

    Holds ``halt.lock``, serves ``halt.sock`` (hand-off and the halt panels),
    opens the links of networks in scope, hands them back on resume, and exits
    when no halt is left.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        stop: Callable[[dict], Awaitable[tuple[bool, str]]] | None = None,
        processes: Callable[[], list] | None = None,
        agents: Callable[[], list[dict]] | None = None,
        open_link: Callable[[str, Path], Awaitable[Any]] | None = None,
        idle: float = LISTENER_IDLE_SECONDS,
        grace: float = LISTENER_GRACE_SECONDS,
    ):
        self.root = root
        self._stop = stop
        self._processes = processes
        self._agents = agents
        self._open_link = open_link or open_relay_link
        self.idle = idle
        self._hold_until = time.monotonic() + grace
        self.links: dict[str, Any] = {}  # network id -> HaltLink
        self.folders: dict[str, set[str]] = {}  # network id -> folders seen here
        self.held: dict[str, Any] = {}  # F12: network id -> scope this listener holds, kept in memory too
        self.state_lock = asyncio.Lock()  # F15b: one halt/resume state change at a time (held + halt.json)
        self._lock_fd: int | None = None
        self._server: asyncio.AbstractServer | None = None
        self._done = asyncio.Event()

    # lock --------------------------------------------------------------

    def take_lock(self) -> bool:
        fd = _lock_fd(private_dir(self.root) / LOCK)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._lock_fd = fd
        os.ftruncate(fd, 0)
        os.pwrite(fd, str(os.getpid()).encode(), 0)
        return True

    def drop_lock(self) -> None:
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # run ---------------------------------------------------------------

    def _lock_is_ours(self) -> bool:
        try:
            on_disk = os.stat(private_dir(self.root) / LOCK, follow_symlinks=False)
            mine = os.fstat(self._lock_fd)
        except (OSError, TypeError):
            return False
        return (on_disk.st_dev, on_disk.st_ino) == (mine.st_dev, mine.st_ino)

    async def _keep_lock(self) -> None:
        """F13: ``private/`` (with halt.lock and halt.sock) deleted under a running listener: take a new lock
        at once so a second listener cannot start beside this one, and serve the socket again."""
        if self._lock_fd is None or self._lock_is_ours():
            return
        old, self._lock_fd = self._lock_fd, None
        if self.take_lock():
            logger.warning("halt listener: halt.lock was removed; taken again")
            with contextlib.suppress(OSError):
                os.close(old)
        else:
            self._lock_fd = old  # another listener started in the gap: both hold the halt until it lifts
            logger.warning("halt listener: halt.lock was removed and another listener took it")
            return
        if self._server is not None and not sock_path(self.root).exists():
            self._server.close()
            await self._serve()

    async def _serve(self) -> None:
        path = sock_path(self.root)
        path.unlink(missing_ok=True)
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._client, path=str(path))
        finally:
            os.umask(old)
        os.chmod(path, 0o600)

    async def run(self) -> int:
        if not self.take_lock():
            return 0  # another listener runs
        path = sock_path(self.root)
        await self._serve()
        try:
            await self._reopen_links()
            while not self._done.is_set():
                await self.tick()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._done.wait(), self.idle)
        finally:
            for net in list(self.links):
                await self.hand_back(net, mark=False)
            self._server.close()
            path.unlink(missing_ok=True)
            self.drop_lock()
        return 0

    async def tick(self) -> None:
        """Hand back networks whose halts are all lifted; exit when none is left."""
        try:
            await self._keep_lock()
        except Exception as error:  # noqa: BLE001
            logger.warning("halt listener: lock check failed (%s)", type(error).__name__)
        try:
            data = read_halt(self.root)
        except (OSError, ValueError, AttributeError, TypeError) as error:
            logger.warning("halt listener: halt.json unreadable (%s); nothing is lifted", type(error).__name__)
            return  # the halt holds; the file may come back
        data = await self._restore_scopes(data)
        for net in list((data.get("scopes") or {})):
            book = (data.get("networks") or {}).get(net)
            if book is None and net in self.held:
                continue  # F12: the record was lost (halt.json deleted): hold until a member sends it back
            if not _book_active(net, book):
                await self.hand_back(net)
        if not (read_halt_held(self.root).get("scopes") or {}) and time.monotonic() >= self._hold_until:
            self._done.set()

    async def _restore_scopes(self, data: dict[str, Any]) -> dict[str, Any]:
        """F12: a halt this listener holds stays in halt.json even when the file was deleted or rewritten.

        D7 found ``rm halt.json`` released a halted machine: the link's exchange wrote the halt back
        without its scope, the listener then saw nothing to hold and exited. A scope missing for a
        network this listener holds is written back from memory unless its book is known and no longer
        active (lifted, or ``resume --local``), so a missing file fails closed.
        """
        scopes = data.get("scopes") if isinstance(data.get("scopes"), dict) else {}
        books = data.get("networks") if isinstance(data.get("networks"), dict) else {}
        lost = {
            net: scope for net, scope in self.held.items()
            if net not in scopes and (books.get(net) is None or _book_active(net, books.get(net)))
        }
        if not lost:
            return data
        logger.warning("halt listener: halt.json lost the scope of %d held network(s); writing it back", len(lost))

        def restore(fresh: dict) -> None:
            fresh.setdefault("scopes", {}).update({n: v for n, v in lost.items() if n not in fresh["scopes"]})
            folders = fresh.setdefault("folders", {})
            for net in lost:
                folders[net] = sorted(set(folders.get(net) or []) | self.folders.get(net, set()))

        async with self.state_lock:  # F15b: cannot interleave with a hand-off or a lift
            update_halt(restore, self.root)
        try:
            return read_halt(self.root)
        except (OSError, ValueError, AttributeError, TypeError):
            return {**data, "scopes": {**scopes, **lost}}

    async def _reopen_links(self) -> None:
        """A listener started by a waiter after a reboot takes the links again."""
        data = read_halt_held(self.root)
        for net, scope in (data.get("scopes") or {}).items():
            self.held[net] = scope
            if isinstance(scope, list) and scope:
                self.folders.setdefault(net, set()).update(scope)
            # a machine-wide scope names no folder: reopen from the hand-off folders recorded with it
            self.folders.setdefault(net, set()).update(
                f for f in ((data.get("folders") or {}).get(net) or []) if isinstance(f, str)
            )
            await self.open_links(net)

    # socket ------------------------------------------------------------

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 30)
            request = json.loads(line or b"{}")
            request = request if isinstance(request, dict) else {}
            if request.get("op") == "act" and _peer_is_tool_shell(writer):
                from .network_halt import GUARD_REFUSAL

                reply = {"ok": False, "error": GUARD_REFUSAL}
            else:
                reply = await self.handle(request, peer=peer_pid(writer.get_extra_info("socket")))
        except Exception as error:  # never let one caller take the listener down
            reply = {"ok": False, "error": str(error) or type(error).__name__}
        writer.write(json.dumps(reply).encode() + b"\n")
        with contextlib.suppress(Exception):
            await writer.drain()
            writer.close()

    async def handle(self, request: dict, peer: int | None = None) -> dict:
        """``halt.sock``: the hand-off and the two halt panels, nothing else."""
        op = request.get("op")
        if op == "ping":
            return {"ok": True}
        if op == "halt":
            return await self.take_halt(request, peer)
        if op in ("describe", "act", "members", "status"):
            link = self._link_for(request.get("folder"), request.get("network"))
            if link is None:
                return {"ok": False, "error": "no halted network holds that folder"}
            if op == "describe":
                return {"ok": True, "snapshot": await link.hub_halt_snapshot()}
            if op == "members":
                return {"ok": True, "members": await link.hub_halt_members()}
            if op == "status":
                return {"ok": True, "acked": link.acked(), "online": sorted(link.online()),
                        "members": await link.hub_halt_members()}
            result = await link.hub_halt_act(str(request.get("action")), request.get("payload") or {})
            await self.tick()
            return {"ok": True, **result}
        return {"ok": False, "error": "unknown halt.sock call"}

    def _link_for(self, folder: Any, network: Any) -> Any:
        from .network_halt import in_scope

        if isinstance(network, str) and network in self.links:
            return self.links[network]
        if isinstance(folder, str):
            data = read_halt_held(self.root)
            for net, scope in (data.get("scopes") or {}).items():
                if net in self.links and in_scope(folder, [scope]):
                    return self.links[net]
        return next(iter(self.links.values()), None) if len(self.links) == 1 else None

    # halt --------------------------------------------------------------

    async def take_halt(self, order: dict, peer: int | None = None) -> dict:
        """Step 2 to 4: record scope, sweep, open links.

        F15b: the scope comes from this listener's own state -- the membership-bound roster
        (``halt_scope``, read through the folder's control), what it already holds and the folders
        it has seen for the network -- never from the request, and a hand-off can only widen the
        hold. The protected pids are the listener, its waiters and the peer's parents, read from
        the socket; a claim in the request body protects nothing. The sender is not screened by
        ancestry (the relay agent legitimately hands off): what it may set is bounded by the
        recorded, signature-verified halt in this network's book and the derivations above.
        """
        net = order.get("network")
        if not isinstance(net, str):
            return {"ok": False, "error": "invalid hand-off"}
        # F13/NEW-4c: from here on every step is guarded on its own: the halt is always recorded (on disk,
        # or in memory for the next tick to write), everything that can be stopped is, and the reply
        # carries every error. Nothing below raises.
        from .network_halt import pid_ok, read_process

        errors: list[str] = []

        def guarded(what: str, fn: Callable[[], Any], default: Any) -> Any:
            try:
                return fn()
            except Exception as error:  # noqa: BLE001
                logger.exception("halt listener: %s failed", what)
                errors.append(f"{what}: {type(error).__name__}: {error}"[:200])
                return default

        # a sweep needs a halt recorded in this network's book (halt_act or a merged peer order)
        async with self.state_lock:  # F15b: one hand-off at a time; a concurrent resume cannot interleave
            data = read_halt_held(self.root)
            book = (data.get("networks") or {}).get(net)
            active = _book_active_hashes(net, book)
            if not active or (order.get("hash") and order["hash"] not in active):
                return {"ok": False, "error": "no recorded halt for that network"}
            folder = order.get("folder")
            if isinstance(folder, str):  # membership-checked: a folder joins only a network it belongs to
                guarded("presence", lambda: self._remember_folder(folder), None)
            agents = guarded("presence", lambda: list(self._agents() if self._agents else _live_agents()), [])
            for a in agents:
                if isinstance(a, dict) and a.get("project"):
                    guarded("presence", lambda a=a: self._remember_folder(a["project"]), None)
            scope = self._trusted_scope(net, data)
            self.held[net] = scope

            def record(data: dict) -> None:
                data.setdefault("scopes", {})[net] = scope
                seen = set(data.setdefault("folders", {}).get(net, []))
                data["folders"][net] = sorted(seen | self.folders.get(net, set()))

            guarded("record", lambda: update_halt(record, self.root), None)
        waiters = guarded("waiters", lambda: set(waiter_pids(self.root)), set())
        protect = guarded("protect", lambda: self._protect_set(peer, waiters), {os.getpid(), *waiters})
        stop = self._stop or guarded("stop", lambda: _default_stop(protect, self.root), None)
        scopes = guarded("scopes", lambda: active_scopes(read_halt_held(self.root)), []) or [scope]
        if scope not in scopes:
            scopes = [*scopes, scope]  # the halt being taken holds even if the file could not be written
        procs: list = []
        sweep_error = ""
        try:
            # F14: read the process table before anything that can fail, so the fallback judges it all
            procs = self._processes() if self._processes else _read_candidates()
            if stop is None:
                raise RuntimeError("no way to stop an agent")
            # injected process lists (tests) get no live tree: a real ps would name real processes
            tree = None if self._processes else process_tree()
            info = None if self._processes else process_info()
            results = await sweep(scopes, agents=agents, procs=procs, stop=stop, protect=protect,
                                  waiters=waiters, tree=tree, info=info)
        except Exception as error:  # noqa: BLE001 - F12/NEW-4: the agents still stop
            sweep_error = f"{type(error).__name__}: {error}"[:200]
            errors.append(f"sweep: {sweep_error}")
            logger.exception("halt listener: sweep failed; stopping agents by pid")
            if not procs and not self._processes:  # the read itself failed: judge the presence pids alone
                procs = guarded("processes", lambda: [
                    p for p in (read_process(a["pid"]) for a in agents if isinstance(a, dict) and pid_ok(a.get("pid")))
                    if p is not None], [])
            results = await fallback_stop(scopes, agents, procs, protect)  # never raises
        errors += [m for _, ok, m in results if not ok]
        stopped = [
            {"identity": r.get("identity", "?"), "folder": r.get("project", ""), "service": bool(r.get("service"))}
            for r, ok, _ in results if ok
        ]

        def note(data: dict) -> None:
            known = {(s.get("identity"), s.get("folder")) for s in data.get("stopped", [])}
            data.setdefault("stopped", []).extend(s for s in stopped if (s["identity"], s["folder"]) not in known)

        async with self.state_lock:  # F15b: every halt.json write of the listener goes under the lock
            guarded("record stopped", lambda: update_halt(note, self.root), None)
        try:
            await self.open_links(net)
        except Exception as error:  # noqa: BLE001
            logger.exception("halt listener: opening links failed")
            errors.append(f"links: {type(error).__name__}: {error}"[:200])
        reply = {"ok": True, "stopped": [m for _, ok, m in results if ok], "count": len(stopped)}
        if sweep_error:
            reply["sweep_error"] = sweep_error
        if errors:
            reply["errors"] = errors
        return reply

    def _remember_folder(self, folder: str) -> None:
        for net in self._networks_of(folder):
            self.folders.setdefault(net, set()).add(folder)

    def _trusted_scope(self, net: str, data: dict) -> "str | list[str]":
        """F15b: what to hold for ``net``: this listener's own derivation unioned with what it already
        holds and the folders it has seen for that network. Never a request claim, never narrower
        than what is already held."""
        candidates: list[Any] = []
        held = (data.get("scopes") or {}).get(net)
        if held == "machine" or (isinstance(held, list) and held):
            candidates.append(held)
        try:
            derived = self._roster_scope(net, data)
        except Exception as error:  # noqa: BLE001
            logger.warning("halt listener: scope derivation failed (%s)", type(error).__name__)
            derived = None
        if derived == "machine" or (isinstance(derived, list) and derived):
            candidates.append(derived)
        if any(c == "machine" for c in candidates):
            return "machine"  # union with a machine-wide hold is the machine
        folders = {f for c in candidates if isinstance(c, list) for f in c if isinstance(f, str)}
        folders |= {f for f in self.folders.get(net, ()) if isinstance(f, str)}
        folders |= {f for f in ((data.get("folders") or {}).get(net) or []) if isinstance(f, str)}
        return sorted(folders) if folders else "machine"  # F7: nothing else derivable holds the machine

    def _roster_scope(self, net: str, data: dict) -> "str | list[str] | None":
        """The scope the issuing CLI computed (``halt_scope``), read here from the folder's own network
        state: the machine when this machine counts on that network, else its folders of the network."""
        from .network_halt import halt_scope

        hints = sorted(self.folders.get(net, ()))
        hints += [f for f in ((data.get("folders") or {}).get(net) or []) if f not in hints]
        for folder in hints:
            if not isinstance(folder, str):
                continue
            control = _folder_control(folder)
            if control is None or control.network_id() != net:
                continue
            return halt_scope(control.view(), control.machine_hex, [folder])
        return None

    def _protect_set(self, peer: int | None, waiters: Iterable[int]) -> set[int]:
        """F15b: the listener, its waiters and the caller's chain, read from the socket peer pid.
        The peer itself is spared only when the F10 guard clears it (a person's CLI): an agent,
        a tool shell or an unknown caller is not shielded from the sweep by its own request.
        A claim in the request body protects nothing."""
        from .network_halt import ancestors

        protect = {os.getpid(), *waiters}
        if isinstance(peer, int) and peer > 0:
            chain = ancestors(peer)
            # F17/#363: caller_refusal judges ancestors and env only, so a peer that IS itself a
            # live agent — a daemon handing off a listener-learned halt — used to slip through and
            # shield the machine's own agent from the sweep. A presence pid is an agent: never spared.
            try:
                agent_pids = {int(a.get("pid") or 0) for a in (self._agents() if self._agents else _live_agents())}
            except Exception:  # presence unreadable: fail closed like caller_refusal, spare nothing
                agent_pids = {peer}
            if caller_refusal(peer) or peer in agent_pids:
                chain.discard(peer)  # its parents (the person's shell, tmux) still survive group kills
            protect |= chain
        return protect

    def _networks_of(self, folder: str) -> list[str]:
        try:
            from .membership_runtime import Memberships

            return [_network_of(folder, m.state_dir) for m in Memberships(Path(folder)).list()]
        except Exception as error:  # noqa: BLE001
            # #364: this silent [] left folders[net] empty on every machine and jammed all
            # resume forms; a folder that resolves to nothing is worth a log line.
            logger.warning("halt listener: folder %s resolved to no network (%s)", folder, type(error).__name__)
            return []

    async def open_links(self, net: str) -> None:
        """Take the relay-owner lock of the network's first in-scope folder (swept already)."""
        from .network_halt import in_scope

        if net in self.links:
            return
        scopes = active_scopes(read_halt(self.root))
        folders = sorted(f for f in self.folders.get(net, ()) if in_scope(f, scopes))
        for folder in folders:
            state_dir = _state_dir_for(folder, net)
            if state_dir is None:
                continue
            link = await self._open_link(folder, state_dir)
            if link is not None:
                self.links[net] = link
                return

    async def hand_back(self, net: str, *, mark: bool = True) -> None:
        """R3-F9: close the link and release the owner lock, then mark the folders lifted."""
        link = self.links.pop(net, None)
        if link is not None:
            flush = getattr(link, "flush", None) if mark else None
            if flush is not None:  # tell the members online the halt is lifted before leaving the relay
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(flush(), HAND_BACK_FLUSH_SECONDS)
                book = (read_halt(self.root).get("networks") or {}).get(net)
                if _book_active(net, book):
                    # `resume --local`: a member still holds the halt and the flush brought it
                    # back; stay halted with the link instead of lifting the scope (found in Docker)
                    self.links[net] = link
                    return
            await link.close()  # releases the relay-owner lock
        if mark:
            async with self.state_lock:  # F15b: cannot interleave with a hand-off taking the halt again
                self.held.pop(net, None)

                def lift(data: dict) -> None:
                    (data.get("scopes") or {}).pop(net, None)

                update_halt(lift, self.root)


def _peer_is_tool_shell(writer: Any) -> bool:
    """Whether the caller on a halt.sock connection is an agent, its tool shell, or unknown (F10/NEW-1)."""
    return bool(caller_refusal(peer_pid(writer.get_extra_info("socket"))))


def _book_active_hashes(net: str, book: Any) -> set[str]:
    if not isinstance(book, dict):
        return set()
    from .network_halt import HaltBook

    try:
        return set(HaltBook(net, book).active())
    except Exception:
        return set()


def _book_active(net: str, book: Any) -> bool:
    if not isinstance(book, dict):
        return False
    from .network_halt import HaltBook

    return bool(HaltBook(net, book).active())


def _live_agents() -> list[dict]:
    from .presence import live_agents_on_machine

    return [data for _hub, data in live_agents_on_machine()]


def _read_candidates(install=None) -> list:
    """The processes a sweep can choose from: kollab of this install, and anything tagged by an agent.

    Reads argv and environment of each (cheap), then the folder of the ones kept.
    """
    from .network_halt import Install, _cli_args, read_process

    install = install or Install.current()
    out = []
    for pid, ppid in process_table().items():
        p = read_process(pid, with_cwd=False)
        if p is None or not (p.env.get("KOLLAB_AGENT_PID") or _cli_args(p, install) is not None):
            continue
        full = read_process(pid)
        if full is not None:
            full.ppid = full.ppid or ppid
            out.append(full)
    return out


def _default_stop(protect: set[int], root: Path | None):
    from kollabor.cli import stop_agent_record

    kill = group_kill(protect)

    async def stop(agent: dict) -> tuple[bool, str]:
        return await stop_agent_record(agent, "network halt", kill=kill)

    return stop


def _network_of(folder: str, state_dir: Path) -> str:
    from .relay_commands import RelayCommands

    return RelayCommands(Path(folder), state_dir=state_dir).client.network_id()


def _state_dir_for(folder: str, net: str) -> Path | None:
    from .membership_runtime import Memberships

    for m in Memberships(Path(folder)).list():
        try:
            if _network_of(folder, m.state_dir) == net:
                return m.state_dir
        except Exception:
            continue
    # a machine that never registered a membership keeps the folder's default state dir
    from .relay_state import default_workspace_state_dir

    try:
        state_dir = default_workspace_state_dir(Path(folder))
        return state_dir if _network_of(folder, state_dir) == net else None
    except Exception:
        return None


class HaltLink:
    """One network's relay link held by the listener: allowlist first, halt book only."""

    def __init__(self, folder: Path, state_dir: Path, owner, commands, transport):
        from .network_control import HaltSync

        self.folder, self.state_dir, self.owner = folder, state_dir, owner
        self.commands, self.transport = commands, transport
        self.client = commands.client
        self.sync = HaltSync(client=self.client, transport=transport, online=self.online)
        self.resume_task: asyncio.Task | None = None

    def online(self) -> dict[str, str]:
        """Approved peers on the relay now (``RelayAgentBridge._config_online_peers``)."""
        try:
            if self.client.status().get("state") != "online":
                return {}
            approved = set(self.client.state.approvals)
            return {p["key"]: p["session"] for p in self.client.peers() if p["key"] in approved}
        except Exception:
            return {}

    async def start(self) -> None:
        self.client.set_request_handler(self.receive)
        self.resume_task = asyncio.create_task(self.commands.resume(), name="kollab-halt-link")
        self.sync.start()

    async def receive(self, peer: str, method: str, payload: Any) -> Any:
        from .network_halt import listener_allows
        from .relay_state import RelayError

        stranger = peer in self.client.state.links
        member = peer in self.client.members()
        if not listener_allows(stranger=stranger, member=member, method=method, inner="network_halt"):
            raise RelayError("this machine is halted")
        if method == "secure_identity":
            return self.transport.identity_response(peer, payload)

        async def inner(peer: str, inner_method: str, inner_payload: Any) -> Any:
            if not listener_allows(stranger=stranger, member=member, method=method, inner=inner_method):
                raise RelayError("this machine is halted")
            if inner_method == "network_halt":
                return await self.sync.receive(peer, inner_payload)
            from .network_members import MembershipSync

            return await MembershipSync(client=self.client, transport=self.transport, online=self.online).receive(
                peer, inner_payload
            )

        return await self.transport.handle_packet(peer, payload, inner)

    async def flush(self) -> None:
        """One more exchange with every member online (the halt book changed: a resume)."""
        self.sync._delivered.clear()
        self.sync._retry_at.clear()
        await self.sync.tick()

    def acked(self) -> list[str]:
        return sorted(self.sync._delivered)

    async def hub_halt_snapshot(self) -> dict:
        return self.client.control.halt_snapshot(set(self.online()))

    async def hub_halt_members(self) -> list[dict]:
        return self.client.control.halt_members()

    async def hub_halt_act(self, action: str, payload: dict) -> dict:
        from .network_halt import HaltError, require_confirmed
        from .relay_state import RelayError

        try:
            require_confirmed(action, payload)
            result = self.client.control.halt_act(action, payload)
        except HaltError as error:
            raise RelayError(str(error)) from None
        self.sync._delivered.clear()
        self.sync._retry_at.clear()
        return result

    async def close(self) -> None:
        await self.sync.close()
        task, self.resume_task = self.resume_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with contextlib.suppress(Exception):
            self.client.set_request_handler(None)
            await self.commands.close()
        self.owner.release()


def _global_config():
    """The user's config, for the relay's CA and private-origin settings (None if unreadable)."""
    try:
        from kollabor_config import ConfigService
        from kollabor_config.config_utils import get_config_directory

        return ConfigService(get_config_directory() / "config.json", None, fast_mode=True)
    except Exception:
        logger.warning("halt listener: config unreadable, relay settings default")
        return None


async def open_relay_link(folder: str, state_dir: Path) -> HaltLink | None:
    """Assumption 1: a membership's link with no plugins and no model."""
    from .relay_commands import RelayCommands
    from .relay_owner import WorkspaceRelayOwner
    from .secure_conversation import SecureConversationTransport

    owner = WorkspaceRelayOwner(Path(folder), state_dir)
    if not owner.acquire(str(sock_path()), "halt-listener"):
        return None  # its owner still runs: the sweep did not stop it
    try:
        commands = RelayCommands(Path(folder), config=_global_config(), state_dir=state_dir, agent_bridge=None)
        transport = SecureConversationTransport(commands.client, commands.client._store.key.encode())
        link = HaltLink(Path(folder), state_dir, owner, commands, transport)
        await link.start()
        return link
    except Exception:
        owner.release()
        raise


def listener_main() -> int:
    """``kollab --halt-listener``."""
    logging.basicConfig(level=logging.WARNING)
    for sig in (signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)
    return asyncio.run(HaltListener().run())


# ------------------------------------------------------------------- CLI


def parse_flags(rest: list[str], flags: dict[str, bool]) -> dict[str, Any]:
    """``--reason x``, ``--revoke d``, ``--confirm h``, ``--local``; unknown is an error."""
    out: dict[str, Any] = {}
    i = 0
    while i < len(rest):
        name = rest[i]
        if name not in flags:
            raise ValueError(f"unknown option {name}")
        if flags[name]:
            if i + 1 >= len(rest):
                raise ValueError(f"{name} needs a value")
            out[name.lstrip("-")] = rest[i + 1]
            i += 2
        else:
            out[name.lstrip("-")] = True
            i += 1
    return out


def member_lines(
    members: list[dict], acked: Iterable[str], own_folder: str, stopped: int, online: Iterable[str] = ()
) -> list[str]:
    """One line per member; one that has not answered is never printed as done."""
    acked, online = set(acked), set(online)
    lines = []
    for m in members:
        name = m.get("name") or m.get("folder", "")[:8]
        if m.get("folder") == own_folder:
            lines.append(f"{name}: halted, {stopped} agents stopped")
        elif m.get("folder") in acked:
            lines.append(f"{name}: halted")
        elif m.get("folder") in online:
            lines.append(f"{name}: halted, ack pending")
        else:
            lines.append(f"{name}: offline, halts on reconnect")
    return lines


async def cli_control(subcmd: str, rest: list[str], *, folder: str | None = None, root: Path | None = None) -> int:
    """``kollab --hub halt|resume``. Returns the exit code; prints the spec's lines."""
    from .network_halt import ancestors, network_control_refusal

    refusal = network_control_refusal() or tool_shell_refusal()  # F10: env, tty, then the ppid chain
    if refusal:
        print(refusal)
        return 1
    folder = folder or os.getcwd()
    try:
        if subcmd == "halt":
            opts = parse_flags(rest, {"--reason": True, "--revoke": True, "--here-only": False})
        else:
            opts = parse_flags(rest, {"--confirm": True, "--local": False})
    except ValueError as error:
        print(f"{error}\nusage: kollab --hub halt [--reason <text>] [--revoke <device> [--here-only]]\n"
              "       kollab --hub resume [--confirm <8-hex> | --local]")
        return 2
    control = _folder_control(folder)
    if control is None:
        print("This folder is not on a network.")
        return 1
    if subcmd == "halt":
        revoke = [k for k in [_device_key(control, opts.get("revoke"))] if k]
        if opts.get("revoke") and not revoke:
            print(f"no member device named {opts['revoke']}")
            return 1
        try:
            act = await _via_owner(
                folder, control, "halt", root,
                {"reason": opts.get("reason", ""), "revoke": revoke, "revoke_here_only": bool(opts.get("here-only")),
                 "confirmed": True},  # the typed command is the person's confirmation
            )
        except Exception as error:
            print(str(error))
            return 1
        scope = _scope_for(control, folder)
        try:
            reply = await hand_off(
                {"network": control.network_id(), "scope": scope, "folder": folder,
                 "hash": act.get("hash"), "protect": sorted(ancestors(os.getpid()))},
                root,
            )
        except (OSError, asyncio.TimeoutError) as error:
            print(f"Halt {str(act.get('hash', ''))[:8]} recorded, but the halt listener did not answer: "
                  f"{str(error) or type(error).__name__}")
            return 1
        print(f"Halt {str(act.get('hash', ''))[:8]} recorded.")
        status = await _await_acks(folder, root)
        for line in member_lines(status.get("members") or control.halt_members(), status.get("acked") or [],
                                 control.client.public_key, int(reply.get("count") or 0),
                                 status.get("online") or []):
            print(line)
        return 0
    if opts.get("local"):
        payload, action = {}, "resume_local"
    elif opts.get("confirm"):
        payload, action = {"hash": opts["confirm"]}, "confirm_resume"
    else:
        payload, action = {}, "propose_resume"
    payload["confirmed"] = True  # the typed command is the person's confirmation
    try:
        try:
            result = await _act(folder, control, action, payload, root)
        except Exception:
            if action != "resume_local":
                raise
            note = clear_malformed_halt(root)  # R6-3: only after the act failed, only a malformed file
            if not note:
                raise
            print(note)
            result = await _act(folder, control, action, payload, root)
    except Exception as error:
        print(str(error))
        return 1
    if action == "resume_local":
        print(result.get("line"))
    elif action == "confirm_resume":
        print(f"Resume confirmed: lifts {len(result.get('lifted') or [])} halts")
    else:
        snap = control.halt_snapshot()
        for h in snap.get("active") or []:
            print(f"  {h['hash'][:8]} {h.get('reason') or ''}")
        print(RESUME_PROPOSED)
    return 0


async def _act(folder: str, control, action: str, payload: dict, root: Path | None) -> dict:
    """Through the listener while it holds the network, else on this folder's own record."""
    if listener_running(root):
        reply = await sock_call({"op": "act", "folder": folder, "action": action, "payload": payload}, root)
        if not reply.get("ok"):
            if action == "resume_local" and not reply.get("error"):
                # F12 (D7): the listener lifted the halt and exited before it answered; say what happened
                if not folder_halted(folder, read_halt_held(root)):
                    from .network_halt import LOCAL_RESUME_LINE

                    return {"ok": True, "line": LOCAL_RESUME_LINE}
                raise RuntimeError("The halt listener closed without an answer; this folder is still halted.")
            raise RuntimeError(reply.get("error") or "the halt listener refused")
        return reply
    return await _via_owner(folder, control, action, root, payload)


async def _via_owner(folder: str, control, action: str, root: Path | None, payload: dict) -> dict:
    """Through the process that runs this folder's network (it holds the live copy of the control file), else direct."""
    from .halt_context import HaltContext, NoOwner

    try:
        return await HaltContext(folder, root)._owner("halt_act", {"action": action, "payload": payload})
    except (NoOwner, OSError, asyncio.TimeoutError):
        return control.halt_act(action, payload)


async def _await_acks(folder: str, root: Path | None, seconds: float = 10.0, online_seconds: float = 40.0) -> dict:
    """Wait ``seconds`` for acks; keep waiting up to ``online_seconds`` while a member that is online has not acked."""
    start = time.monotonic()
    status: dict = {}
    while True:
        with contextlib.suppress(OSError, ValueError, asyncio.TimeoutError):
            status = await sock_call({"op": "status", "folder": folder}, root, timeout=5.0)
        members = status.get("members") or []
        acked = set(status.get("acked") or [])
        online = set(status.get("online") or [])
        pending = [m for m in members if m.get("folder") in online and m.get("folder") not in acked]
        elapsed = time.monotonic() - start
        if members and len(acked) >= len(members) - 1:
            break
        if elapsed >= (online_seconds if pending else seconds):
            break
        await asyncio.sleep(1.0)
    return status


def _folder_control(folder: str):
    from .relay_commands import RelayCommands

    try:
        return RelayCommands(Path(folder)).client.control
    except Exception:
        return None


def _device_key(control, name: Any) -> str:
    if not isinstance(name, str) or not name:
        return ""
    for m in control.halt_members():
        if name in (m.get("name"), m.get("folder")):
            return m["folder"]
    return ""


def _scope_for(control, folder: str):
    from .network_halt import halt_scope

    return halt_scope(control.view(), control.machine_hex, [folder])
