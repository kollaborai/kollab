"""Daemon-first process management for Kollab.

Every kollab session auto-forks into a daemon + client pair:
  - Daemon: headless process running LLM, plugins, hub socket
  - Client: TUI attach client connected via hub socket

The daemon is identical to --detached mode. The client is identical
to --attach mode. Ctrl+Z detaches the client; daemon keeps running.

Ready signaling uses an os.pipe(): daemon writes its hub socket path
to the pipe once the hub plugin initializes, parent reads it and
connects as an attach client.
"""

import logging
import os
import signal
import sys
import time

logger = logging.getLogger(__name__)

# Env var the daemon sets so the hub plugin knows to signal readiness
DAEMON_READY_FD_ENV = "KOLLAB_DAEMON_READY_FD"
# The attach client runs as `--attach <identity>`; this keeps the command the
# user launched so /upgrade can relaunch it with a fresh daemon.
LAUNCH_ARGS_ENV = "KOLLAB_LAUNCH_ARGS"


def fork_daemon(argv: list[str]) -> tuple[int, str]:
    """Fork a daemon process and wait for it to signal readiness.

    Returns:
        (daemon_pid, socket_path) on success.

    Raises:
        RuntimeError: If daemon fails to start within timeout.
    """
    # Create pipe for ready signaling (daemon writes, parent reads)
    read_fd, write_fd = os.pipe()

    pid = os.fork()

    if pid > 0:
        # --- PARENT (becomes client) ---
        os.close(write_fd)

        # Wait for daemon to write socket path (timeout 15s)
        socket_path = _wait_for_ready(read_fd, pid, timeout=15.0)
        os.close(read_fd)

        if not socket_path:
            os.kill(pid, signal.SIGKILL)
            raise RuntimeError(f"daemon hung (killed pid {pid})")

        logger.info(f"daemon ready: pid={pid} socket={socket_path}")
        return pid, socket_path

    else:
        # --- CHILD (becomes daemon) ---
        os.close(read_fd)

        # New session, detach from controlling terminal
        os.setsid()

        # Pass the write fd to the child so hub plugin can signal
        os.environ[DAEMON_READY_FD_ENV] = str(write_fd)

        # Redirect stdio AFTER setting up the fd env var
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)  # stdin
        os.dup2(devnull, 1)  # stdout
        os.dup2(devnull, 2)  # stderr
        os.close(devnull)

        # Run the full app headless (same as --detached)
        # This function never returns in the child
        _run_daemon(argv, write_fd)
        sys.exit(0)


# A launch that names an agent, a project or a first message asks for a new
# daemon; any other flag leaves the workspace's live one in charge.
_NEW_DAEMON_FLAGS = {"--agent", "-a", "--as", "--daemon", "--project"}
_VALUE_FLAGS = {
    "--llm",
    "--model",
    "--effort",
    "--context",
    "--system-prompt",
    "--skill",
    "-s",
    "--timeout",
}


def _is_bare_launch(argv: list[str]) -> bool:
    args = iter(argv)
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in _NEW_DAEMON_FLAGS:
            return False
        if not arg.startswith("-"):
            return False  # query text: the live daemon would never see it
        if flag in _VALUE_FLAGS and "=" not in arg:
            next(args, None)
    return True


def find_workspace_daemon(argv: list[str]) -> tuple[int, str] | None:
    """The live daemon already serving this workspace, as (pid, socket_path).

    A bare relaunch attaches to it. Forking another one doubles the agent: the
    new daemon takes a second designation while the first keeps running with no
    window, and whatever is sent to the first is never seen.
    """
    if not _is_bare_launch(argv):
        return None

    import json

    from plugins.hub.presence import PresenceManager, get_presence_dir
    from plugins.hub.project_scope import is_project_scoped

    try:
        records = list(get_presence_dir().glob("*.json"))
    except OSError:
        return None
    live = []
    for record in records:
        try:
            data = json.loads(record.read_text())
            pid, sock = int(data["pid"]), str(data["socket_path"])
            started = float(data.get("started_at") or 0)
            # A daemon is its own session leader (fork_daemon and --detached
            # both setsid); an interactive window never is. Raises for a dead pid.
            if os.getsid(pid) != pid or not PresenceManager._socket_responds(sock):
                continue
            if not is_project_scoped() and data.get("project") != os.getcwd():
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        live.append((not data.get("is_coordinator"), started, pid, sock))
    if not live:
        return None
    _, _, pid, sock = min(live)
    return pid, sock


def stop_daemon(pid: int, grace_seconds: float = 5.0) -> None:
    """SIGTERM an owned daemon and reap it, escalating to SIGKILL after the grace.

    Reaping matters before an exec: an unreaped child stays a zombie, and a
    daemon still shutting down can hold the hub socket the new one needs.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return  # not our child, or already reaped
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def signal_daemon_ready(socket_path: str) -> None:
    """Signal the parent process that the daemon is ready.

    Called by the hub plugin once the socket server is listening.
    Writes the socket path to the pipe fd, then closes it.
    """
    fd_str = os.environ.get(DAEMON_READY_FD_ENV)
    if not fd_str:
        return  # Not in daemon-fork mode

    try:
        fd = int(fd_str)
        msg = (socket_path + "\n").encode()
        os.write(fd, msg)
        os.close(fd)
        logger.info(f"signaled daemon ready: {socket_path}")
    except (OSError, ValueError) as e:
        logger.error(f"failed to signal daemon ready: {e}")
    finally:
        # Clear the env var so it's not inherited by subprocesses
        os.environ.pop(DAEMON_READY_FD_ENV, None)


def _wait_for_ready(read_fd: int, daemon_pid: int, timeout: float) -> str | None:
    """Wait for daemon to write socket path to pipe.

    Returns socket_path or None on timeout/failure.
    """
    import select

    deadline = time.monotonic() + timeout
    buf = b""

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        # Check if daemon is still alive while we poll for readiness.
        # WNOHANG lets the parent keep waiting on the pipe without blocking,
        # and also reaps the child immediately if it already crashed.
        try:
            wpid, status = os.waitpid(daemon_pid, os.WNOHANG)
            if wpid != 0:
                # Daemon exited before signaling ready
                return None
        except ChildProcessError:
            # Already reaped or not our child
            return None

        # Poll the pipe for data
        ready, _, _ = select.select([read_fd], [], [], min(remaining, 0.5))
        if ready:
            try:
                chunk = os.read(read_fd, 4096)
            except OSError:
                return None

            if not chunk:
                # Pipe closed without data = daemon failed
                return None

            buf += chunk
            if b"\n" in buf:
                line = buf.split(b"\n")[0].decode().strip()
                return line if line else None

    return None


def _run_daemon(argv: list[str], write_fd: int) -> None:
    """Run the full kollabor app headless as a daemon.

    This is the child process after fork. Runs async_main() which
    starts TerminalLLMChat with render loop + input handler pointing
    to /dev/null. The hub plugin signals readiness via write_fd.
    """
    import asyncio

    # Rebuild argv for the child so it re-enters the normal CLI path in
    # detached mode instead of recursively trying to fork another daemon.
    # --detached also disables pipe-mode auto-detection now that stdin is
    # redirected to /dev/null.
    clean_argv = [a for a in argv if a not in ("--daemon",)]
    if "--detached" not in clean_argv:
        clean_argv.insert(1, "--detached")
    sys.argv = clean_argv

    try:
        from kollabor.cli import async_main

        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"daemon crashed: {e}")
    finally:
        # Close the write fd in case we crashed before signaling
        try:
            os.close(write_fd)
        except OSError:
            pass
