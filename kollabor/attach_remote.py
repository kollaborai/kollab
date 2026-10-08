"""Attach to an agent on another computer over SSH.

``kollab --attach lapis@devbox`` asks devbox's kollab for the live socket of
lapis (``kollab --hub where lapis``), forwards that socket to a private local
socket with OpenSSH unix-socket forwarding (``ssh -L``), and hands the local
path to the normal attach. SSH is the transport and the auth: whoever can ssh
to the box may attach to its agents. No new protocol and no relay.
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_S = 15.0
FORWARD_TIMEOUT_S = 10.0
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SSH_OPTS = {
    "BatchMode": "yes",  # a password prompt would hang the attach
    "ConnectTimeout": "5",
    "ControlMaster": "no",
    "ControlPath": "none",  # this ssh owns its connection: killing it drops the forward
}


class RemoteAttachError(Exception):
    """A plain, user-facing reason the remote attach cannot start."""


def parse_attach_target(value: str) -> tuple[str, Optional[str]]:
    """Split ``identity[@host]``. The first "@" splits; the rest is the ssh destination."""
    identity, sep, host = value.partition("@")
    if not sep:
        return value, None
    if not _IDENTITY_RE.match(identity):
        raise RemoteAttachError(f"invalid agent name '{identity}'")
    if not host or host.startswith("-") or not host.isprintable() or " " in host:
        raise RemoteAttachError(f"invalid ssh destination '{host}'")
    return identity, host


def _ssh_options(**extra: str) -> list[str]:
    opts = {**_SSH_OPTS, **extra}
    return [arg for key, value in opts.items() for arg in ("-o", f"{key}={value}")]


def _last_line(text: Optional[str]) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _exit_on_sigterm(signum, frame) -> None:
    raise SystemExit(128 + signum)


def _tail(path: str) -> str:
    try:
        with open(path, errors="replace") as fh:
            return _last_line(fh.read())
    except OSError:
        return ""


def find_remote_socket(
    identity: str, host: str, timeout: float = DISCOVERY_TIMEOUT_S
) -> str:
    """Ask ``host``'s kollab for the live socket path of ``identity``."""
    argv = ["ssh", *_ssh_options(), host, f"kollab --hub where {identity}"]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError:
        raise RemoteAttachError("ssh is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise RemoteAttachError(
            f"timed out after {timeout:g}s reaching {host} over ssh"
        ) from None
    except OSError as e:
        raise RemoteAttachError(f"cannot run ssh: {e.strerror or e}") from None
    logger.debug("hub where %s on %s: exit %s", identity, host, done.returncode)
    if done.returncode == 255:
        raise RemoteAttachError(
            f"cannot reach {host} over ssh: {_last_line(done.stderr) or 'ssh failed'}"
        )
    if done.returncode == 127:
        raise RemoteAttachError(f"kollab is not on the PATH of ssh sessions on {host}")
    sock = _last_line(done.stdout)
    if done.returncode == 0 and sock.startswith("/"):
        return sock
    reason = _last_line(done.stderr)
    if reason:
        raise RemoteAttachError(f"{host}: {reason}")
    raise RemoteAttachError(
        f"{host}'s kollab gave no socket for '{identity}' (too old for --hub where?)"
    )


class RemoteAttach:
    """One ssh process forwarding a remote agent socket to a private local socket."""

    def __init__(
        self, identity: str, host: str, timeout: float = FORWARD_TIMEOUT_S
    ) -> None:
        self.identity = identity
        self.host = host
        self.timeout = timeout
        self.tmp_dir: Optional[str] = None
        self.proc: Optional[subprocess.Popen] = None
        self._owner = os.getpid()

    def open(self) -> str:
        """Start the forward and return the local socket path once it exists."""
        remote_sock = find_remote_socket(self.identity, self.host)
        # mkdtemp creates the dir mode 0700: only this user reaches the socket.
        self.tmp_dir = tempfile.mkdtemp(prefix="kollab-attach-")
        atexit.register(self.close)
        # `kill <pid>` would skip atexit and leave the forward running; make
        # the default SIGTERM a normal exit (a handler set later still wins).
        try:
            if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
                signal.signal(signal.SIGTERM, _exit_on_sigterm)
        except ValueError:  # not the main thread
            pass
        local_sock = os.path.join(self.tmp_dir, "agent.sock")
        log_path = os.path.join(self.tmp_dir, "ssh.log")
        argv = [
            "ssh",
            "-N",
            *_ssh_options(
                ExitOnForwardFailure="yes",
                StreamLocalBindUnlink="yes",
                ServerAliveInterval="15",
                ServerAliveCountMax="3",
            ),
            "-L",
            f"{local_sock}:{remote_sock}",
            self.host,
        ]
        logger.debug("ssh forward: %s", argv)
        try:
            with open(log_path, "w") as log:
                self.proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                )
        except OSError as e:
            self.close()
            raise RemoteAttachError(f"cannot run ssh: {e.strerror or e}") from None
        deadline = time.monotonic() + self.timeout
        while not os.path.exists(local_sock):
            if self.proc.poll() is not None:
                reason = _tail(log_path) or f"ssh exited {self.proc.returncode}"
                self.close()
                raise RemoteAttachError(f"ssh forward to {self.host} failed: {reason}")
            if time.monotonic() >= deadline:
                self.close()
                raise RemoteAttachError(
                    f"timed out after {self.timeout:g}s waiting for the forward to {self.host}"
                )
            time.sleep(0.05)
        return local_sock

    def close(self) -> None:
        """Stop the forward and remove its private dir. Safe to call twice."""
        atexit.unregister(self.close)
        if (
            os.getpid() != self._owner
        ):  # a forked child exiting must not kill our forward
            return
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self.tmp_dir:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
            self.tmp_dir = None


def open_attach_target(value: str) -> tuple[str, Optional[str]]:
    """Resolve ``--attach`` input to ``(identity, local_socket)``.

    A plain identity returns ``(identity, None)`` and attaches exactly as
    before. ``identity@host`` opens the ssh forward and returns its local socket.
    """
    identity, host = parse_attach_target(value)
    if host is None:
        return identity, None
    return identity, RemoteAttach(identity, host).open()
