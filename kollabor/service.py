"""`kollab service`: keep this folder's agent running through crashes, logouts and reboots.

Linux gets a systemd system unit that runs as you and starts at boot; macOS a
LaunchAgent that starts at login. Either manager runs `kollab --detached` with
KOLLAB_SERVICE=1, which keeps the daemon in the foreground so the manager owns
it (kollabor.daemon.SERVICE_ENV). A bare `kollab` in the folder attaches to that
agent, and closing the window leaves it running.

`kollab relay serve --domain D --install` uses the systemd half for a directory.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from kollabor.daemon import SERVICE_ENV

SYSTEMD_DIR = Path("/etc/systemd/system")
ACTIONS = ("install", "uninstall", "status")
READY_SECONDS = 30


def service_name(root: Path) -> str:
    """kollab-<folder>-<hash>: readable, and two folders with one name never collide."""
    slug = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")[:32] or "root"
    return f"kollab-{slug}-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"


def service_env(extra: dict[str, str]) -> dict[str, str]:
    # A manager starts with a bare PATH; keep the one this shell finds tools and MCP servers on.
    return {SERVICE_ENV: "1", "PATH": os.environ.get("PATH", os.defpath), **extra}


def exec_argv() -> list[str]:
    # This interpreter, so a venv or `uv tool` install keeps working.
    return [sys.executable, "-m", "kollabor_cli_main", "--detached"]


def _user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return str(os.getuid())


def _quoted(text: str) -> str:
    """One systemd word: quotes, backslashes and %-specifiers escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def systemd_unit(root: Path, env: dict[str, str]) -> str:
    folder = str(root).replace("%", "%%")
    return "\n".join(
        [
            f"# kollab agent for {root}; `kollab service uninstall` there removes it.",
            "[Unit]",
            f"Description=kollab agent in {folder}",
            "Wants=network-online.target",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={_user()}",
            f"WorkingDirectory={folder}",
            *(f"Environment={_quoted(f'{key}={value}')}" for key, value in env.items()),
            "ExecStart=" + " ".join(_quoted(arg) for arg in exec_argv()),
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def launchd_plist(label: str, root: Path, env: dict[str, str], log: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": exec_argv(),
            "WorkingDirectory": str(root),
            "EnvironmentVariables": env,
            "RunAtLoad": True,  # at login
            "KeepAlive": True,  # and again after any exit
            "ThrottleInterval": 5,  # 5 s later, as systemd's RestartSec
            "StandardErrorPath": str(log),
        }
    )


def has_systemd() -> bool:
    """Booted with systemd: the check sd_booted() makes."""
    return bool(shutil.which("systemctl")) and Path("/run/systemd/system").is_dir()


def _run(
    cmd: list[str], *, root: bool = False, stdin: str | None = None, check: bool = True
):
    """Run a manager command; `root` adds sudo unless this is root already (sudo asks on the terminal)."""
    if root and os.geteuid() != 0:
        cmd = ["sudo", *cmd]
    result = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"kollab service: `{shlex.join(cmd)}` failed: {detail}")
    return result


def install_systemd(name: str, unit: str) -> None:
    """Write /etc/systemd/system/<name>.service, enable it at boot and (re)start it."""
    path = SYSTEMD_DIR / f"{name}.service"
    if os.geteuid():
        print(f"writing {path} needs root: sudo may ask for your password")
    _run(["tee", str(path)], root=True, stdin=unit)
    _run(["systemctl", "daemon-reload"], root=True)
    _run(["systemctl", "enable", name], root=True)
    _run(["systemctl", "restart", name], root=True)


def uninstall_systemd(name: str) -> bool:
    """Stop and remove the unit; False when it was not installed."""
    path = SYSTEMD_DIR / f"{name}.service"
    if not path.exists():
        return False
    _run(["systemctl", "disable", "--now", name], root=True, check=False)
    _run(["rm", "-f", str(path)], root=True)
    _run(["systemctl", "daemon-reload"], root=True)
    return True


def _systemd_state(name: str) -> tuple[bool, bool, str]:
    out = _run(
        ["systemctl", "show", name, "-p", "LoadState,ActiveState,MainPID"], check=False
    ).stdout
    state = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return (
        state.get("LoadState") == "loaded",
        state.get("ActiveState") == "active",
        state.get("MainPID", ""),
    )


def _plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _install_launchd(label: str, plist: bytes) -> None:
    path = _plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plist)
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    # A reinstall replaces the running job. bootout returns before it is gone,
    # and bootstrapping over it fails with "5: Input/output error".
    _run(["launchctl", "bootout", target], check=False)
    deadline = time.monotonic() + READY_SECONDS
    while not _run(["launchctl", "print", target], check=False).returncode:
        if time.monotonic() > deadline:
            raise SystemExit(f"kollab service: {label} did not stop; try again")
        time.sleep(0.5)
    _run(["launchctl", "enable", target])
    _run(["launchctl", "bootstrap", domain, str(path)])


def _uninstall_launchd(label: str) -> bool:
    path = _plist_path(label)
    if not path.exists():
        return False
    _run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], check=False)
    path.unlink()
    return True


def _launchd_state(label: str) -> tuple[bool, bool, str]:
    result = _run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], check=False)
    state = (
        dict(re.findall(r"^\t(state|pid) = (.+)$", result.stdout, re.M))
        if not result.returncode
        else {}
    )
    return (
        _plist_path(label).exists(),
        state.get("state") == "running",
        state.get("pid", ""),
    )


def _agents(root: Path) -> list[tuple[str, int, bool]]:
    """(identity, pid, service) for each agent answering on its hub socket in this folder."""
    from plugins.hub.messenger import AgentMessenger
    from plugins.hub.presence import get_presence_dir
    from plugins.hub.project_scope import is_project_scoped

    found = []
    for record in get_presence_dir().glob("*.json"):
        try:
            data = json.loads(record.read_text())
            pid = int(data["pid"])
            os.kill(pid, 0)
            if not is_project_scoped() and Path(data["project"]).resolve() != root:
                continue
            status = asyncio.run(
                AgentMessenger.request_status(str(data["socket_path"]), timeout=1.0)
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
        # The presence record names it; a starting agent's status may not yet.
        if status:
            identity = str(data.get("identity") or record.stem)
            found.append((identity, pid, status.get("service") is True))
    return found


class _Manager:
    """The platform's service manager, named for this folder."""

    def __init__(self, root: Path):
        self.root = root
        self.name = service_name(root)
        if sys.platform == "darwin":
            self.kind, self.label = "launchd", f"ai.kollabor.{self.name}"
        elif has_systemd():
            self.kind, self.label = "systemd", self.name
        else:
            raise SystemExit(
                "kollab service: needs systemd (Linux) or launchd (macOS); "
                "elsewhere, start `kollab --detached` in this folder from your init system"
            )

    @property
    def log(self) -> Path:
        from kollabor_config.config_utils import get_project_data_dir

        return get_project_data_dir(self.root) / "logs" / "service.log"

    def render(self, env: dict[str, str]) -> str:
        if self.kind == "systemd":
            return systemd_unit(self.root, env)
        return launchd_plist(self.label, self.root, env, self.log).decode()

    def install(self, text: str) -> None:
        if self.kind == "systemd":
            install_systemd(self.name, text)
        else:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            _install_launchd(self.label, text.encode())

    def uninstall(self) -> bool:
        return (
            uninstall_systemd(self.name)
            if self.kind == "systemd"
            else _uninstall_launchd(self.label)
        )

    def state(self) -> tuple[bool, bool, str]:
        """(installed, running, pid)."""
        return (
            _systemd_state(self.name)
            if self.kind == "systemd"
            else _launchd_state(self.label)
        )

    @property
    def logs(self) -> str:
        return f"journalctl -u {self.name}" if self.kind == "systemd" else str(self.log)


def install(manager: _Manager, env: dict[str, str], *, show: bool) -> int:
    text = manager.render(env)
    if show:
        sys.stdout.write(text)
        return 0
    others = [
        (identity, pid)
        for identity, pid, service in _agents(manager.root)
        if not service
    ]
    if others:
        names = ", ".join(f"{identity} (pid {pid})" for identity, pid in others)
        target = others[0][0] if len(others) == 1 else "all"
        raise SystemExit(
            f"kollab service: {names} already running in {manager.root}.\n"
            "Stop it so the service is this folder's only agent, then install again:\n"
            f"  kollab --hub stop {target}"
        )
    manager.install(text)
    start = "boot" if manager.kind == "systemd" else "login"
    print(f"installed {manager.name}: starts at {start}, restarts 5 s after it stops")
    deadline = time.monotonic() + READY_SECONDS
    while not _agent(manager) and time.monotonic() < deadline:
        time.sleep(1)
    return status(manager)


def uninstall(manager: _Manager) -> int:
    if manager.uninstall():
        print(f"removed {manager.name}: stopped, and it no longer starts on its own")
    else:
        print(f"no kollab service for {manager.root}")
    return 0


def _agent(manager: _Manager) -> str | None:
    """The service's agent, once the process the manager runs answers on its hub socket."""
    _, running, pid = manager.state()
    found = _agents(manager.root) if running else []
    return next(
        (name for name, at, service in found if service and str(at) == pid), None
    )


def status(manager: _Manager) -> int:
    installed, running, pid = manager.state()
    if not installed:
        print(f"no kollab service for {manager.root}")
        print("  install: kollab service install")
        return 1
    agent = _agent(manager)
    print(
        f"{manager.name} ({manager.kind}): "
        + (f"running, pid {pid}" if running else "not running")
    )
    print(f"  folder  {manager.root}")
    if agent:
        print(f"  agent   {agent}; `kollab` in this folder attaches to it")
    else:
        print("  agent   not answering yet")
    print(f"  logs    {manager.logs}")
    return 0 if agent else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kollab service",
        description="Keep this folder's agent running: it starts at boot (systemd) or login (launchd) "
        "and restarts after a crash. `kollab` in the folder attaches to it.",
    )
    actions = parser.add_subparsers(
        dest="action", required=True, metavar="{install,uninstall,status}"
    )
    install_parser = actions.add_parser(
        "install", help="install and start it (again: restart on the current code)"
    )
    install_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="extra environment for the agent, e.g. KOLLAB_NO_KEYRING=1; never a secret: unit files are world-readable",
    )
    install_parser.add_argument(
        "--print",
        action="store_true",
        help="print the systemd unit or launchd plist and exit",
    )
    actions.add_parser("uninstall", help="stop it and remove it")
    actions.add_parser(
        "status", help="installed, running, and answering on its hub socket?"
    )
    args = parser.parse_args(argv)

    from plugins.hub.project_scope import resolve_project_root

    manager = _Manager(resolve_project_root())
    try:
        if args.action == "install":
            extra = {}
            for item in args.env:
                key, sep, value = item.partition("=")
                if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                    parser.error(f"--env takes KEY=VALUE, got {item!r}")
                extra[key] = value
            return install(manager, service_env(extra), show=args.print)
        if args.action == "uninstall":
            return uninstall(manager)
        return status(manager)
    except OSError as exc:
        raise SystemExit(f"kollab service: {exc}") from None
