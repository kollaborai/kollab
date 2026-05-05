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
    import signal

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
