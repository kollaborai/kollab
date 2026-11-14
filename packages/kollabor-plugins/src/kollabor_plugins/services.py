"""Background plugin process supervision primitives."""

import json
import logging
import os
import secrets
import selectors
import signal
import subprocess
import time
from pathlib import Path

import psutil

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import try_lock, unlock

from .packages import _ID, _SERVICE, _read_registry

logger = logging.getLogger(__name__)
_LOG_LIMIT = 10 * 1024 * 1024


def restart_decision(policy: str, exit_code: int, crashes: list[float], now: float) -> tuple[int | None, bool]:
    """Return the retry delay and whether the five-crash limit was reached.

    Crash timestamps and ``now`` share a monotonic time base. The caller records
    the current failure before calling this function.
    """
    if policy not in {"never", "on-failure", "always"}:
        raise ValueError("Invalid service restart policy")
    recent = [stamp for stamp in crashes if 0 <= now - stamp <= 600]
    if len(recent) >= 5:
        return None, True
    if policy == "never" or (policy == "on-failure" and exit_code == 0):
        return None, False
    return min(300, 2 ** max(1, len(recent))), False


def _write_state(path: Path, data: dict) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _process_start(pid: int) -> float:
    return float(psutil.Process(pid).create_time())


def _write_log(path: Path, data: bytes) -> None:
    """Append with a strict size cap and keep three rotated files."""
    while data:
        size = path.stat().st_size if path.exists() else 0
        if size >= _LOG_LIMIT:
            for index in range(3, 0, -1):
                destination = path.with_name(f"{path.name}.{index}")
                source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
                if source.exists():
                    os.replace(source, destination)
            size = 0
        chunk = data[: _LOG_LIMIT - size]
        with path.open("ab") as stream:
            stream.write(chunk)
        data = data[len(chunk) :]


def run_service(plugin_id: str, service_name: str, nonce: str) -> int:
    """Run and supervise one service while holding its lifetime lock."""
    if not _ID.fullmatch(plugin_id) or not _SERVICE.fullmatch(service_name):
        raise ValueError("Invalid plugin or service name")
    if not nonce or any(c not in "0123456789abcdef" for c in nonce):
        raise ValueError("Invalid service nonce")
    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    record = registry["plugins"].get(plugin_id)
    if not isinstance(record, dict) or record.get("enabled") is not True:
        raise ValueError("Plugin is not enabled")
    root = home / "plugins" / plugin_id
    try:
        manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
        service = manifest["services"][service_name]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Cannot load plugin service") from exc
    command = service.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise ValueError("Invalid service command")
    if service.get("secrets"):
        raise ValueError("Service secret injection is not available")
    path = record.get("enabled_path")
    if not isinstance(path, str) or not path:
        raise ValueError("Plugin has no captured PATH; enable it again")
    run_dir = home / "plugin-data" / plugin_id / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / f"{service_name}.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    if not try_lock(fd):
        os.close(fd)
        return 0
    pid_path = run_dir / f"{service_name}.pid"
    if _recorded_process_alive(pid_path):
        unlock(fd)
        os.close(fd)
        return 0
    state_path = run_dir / f"{service_name}.state"
    log_path = run_dir / f"{service_name}.log"
    wrapper = {"pid": os.getpid(), "start_time": _process_start(os.getpid()), "nonce": nonce}
    identity = {"wrapper": wrapper, "child": None}
    stopping = False
    child = None
    old_handler = signal.getsignal(signal.SIGTERM)

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    try:
        _write_state(pid_path, identity)
        state = {"desired": "running", "status": "starting", "exit_history": []}
        _write_state(state_path, state)
        crashes: list[float] = []
        while not stopping:
            env = {
                key: value
                for key, value in os.environ.items()
                if key in {"HOME", "USER", "LANG", "TERM", "TMPDIR", "SHELL"} or key.startswith("LC_")
            }
            env["PATH"] = path
            child = subprocess.Popen(
                command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
            )
            assert child.stdout is not None
            try:
                identity["child"] = {
                    "pid": child.pid,
                    "pgid": os.getpgid(child.pid),
                    "start_time": _process_start(child.pid),
                }
            except psutil.Error, ProcessLookupError:
                identity["child"] = {"pid": child.pid, "pgid": child.pid, "start_time": None}
            _write_state(pid_path, identity)
            state["status"] = "running"
            _write_state(state_path, state)
            deadline = None
            term_sent = False
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map() or child.poll() is None:
                    if stopping and not term_sent:
                        try:
                            os.killpg(child.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        term_sent = True
                        deadline = time.monotonic() + float(service.get("stop_timeout_s", 10))
                    if deadline is not None and time.monotonic() >= deadline:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        deadline = None
                    for key, _ in selector.select(0.1):
                        output = os.read(key.fd, 65536)
                        if output:
                            _write_log(log_path, output)
                        else:
                            selector.unregister(key.fileobj)
            if term_sent and deadline is not None:
                # The leader may exit before descendants that ignored SIGTERM.
                # Keep the wrapper alive until the group is gone or its grace expires.
                while time.monotonic() < deadline:
                    try:
                        os.killpg(child.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                else:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            code = child.wait()
            child.stdout.close()
            now = time.monotonic()
            state["exit_history"].append({"code": code, "time": time.time()})
            if code != 0 and not stopping:
                crashes.append(now)
            crashes = [stamp for stamp in crashes if now - stamp <= 600]
            delay, crash_stopped = restart_decision(service.get("restart", "on-failure"), code, crashes, now)
            if stopping or crash_stopped or delay is None:
                state["desired"] = "stopped" if stopping or crash_stopped else "running"
                state["status"] = "crash-stopped" if crash_stopped else ("stopped" if stopping else "exited")
                _write_state(state_path, state)
                return code if code >= 0 else 128 - code
            state["status"] = "backoff"
            _write_state(state_path, state)
            until = time.monotonic() + delay
            while not stopping and time.monotonic() < until:
                time.sleep(min(0.1, max(0, until - time.monotonic())))
        state["desired"] = "stopped"
        state["status"] = "stopped"
        _write_state(state_path, state)
        return 0
    finally:
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        signal.signal(signal.SIGTERM, old_handler)
        unlock(fd)
        os.close(fd)


def _identity_alive(identity: object, *, wrapper: bool = False) -> bool:
    if not isinstance(identity, dict):
        return False
    pid = identity.get("pid")
    started = identity.get("start_time")
    if type(pid) is not int or pid <= 0 or not isinstance(started, (int, float)):
        return False
    try:
        process = psutil.Process(pid)
        if not process.is_running() or abs(process.create_time() - started) > 0.02:
            return False
        if process.status() == psutil.STATUS_ZOMBIE:
            return False
        if wrapper:
            nonce = identity.get("nonce")
            return isinstance(nonce, str) and bool(nonce) and nonce in process.cmdline()
        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # Unverifiable identity is not permission to launch a second process.
        return True


def _recorded_process_alive(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        identity = json.loads(pid_path.read_text(encoding="utf-8"))
        if not isinstance(identity, dict):
            raise ValueError("Invalid pid record")
        return _identity_alive(identity.get("wrapper"), wrapper=True) or _identity_alive(identity.get("child"))
    except OSError, ValueError, TypeError:
        logger.warning("Cannot safely inspect service pid file %s", pid_path)
        return True


def supervisor_tick() -> None:
    """Start eligible installed services without scanning unrelated processes."""
    from kollabor.binary import kollab_argv

    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    for plugin_id, record in registry["plugins"].items():
        if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
            continue
        if not isinstance(record, dict) or record.get("enabled") is not True:
            continue
        root = home / "plugins" / plugin_id
        try:
            manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
            services = manifest.get("services", {})
            if not isinstance(services, dict):
                raise ValueError("Invalid services")
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Cannot load services for plugin %s: %s", plugin_id, exc)
            continue
        for service_name, service in services.items():
            if (
                not isinstance(service_name, str)
                or not _SERVICE.fullmatch(service_name)
                or not isinstance(service, dict)
            ):
                logger.warning("Invalid service declaration in plugin %s", plugin_id)
                continue
            run_dir = home / "plugin-data" / plugin_id / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(run_dir / f"{service_name}.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                if not try_lock(fd):
                    continue
                try:
                    state_path = run_dir / f"{service_name}.state"
                    if state_path.exists():
                        state = json.loads(state_path.read_text(encoding="utf-8"))
                        if (
                            not isinstance(state, dict)
                            or state.get("desired") != "running"
                            or state.get("status") in {"exited", "crash-stopped"}
                        ):
                            continue
                    elif service.get("autostart", False) is not True:
                        continue
                    if _recorded_process_alive(run_dir / f"{service_name}.pid"):
                        continue
                    nonce = secrets.token_hex(16)
                    subprocess.Popen(
                        [*kollab_argv(), "plugin", "run-service", plugin_id, service_name, nonce],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                except (OSError, ValueError, TypeError) as exc:
                    logger.warning("Could not start service %s/%s: %s", plugin_id, service_name, exc)
                finally:
                    unlock(fd)
            finally:
                os.close(fd)
