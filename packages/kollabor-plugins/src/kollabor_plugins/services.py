"""Background plugin process supervision primitives."""

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import selectors
import signal
import subprocess
import time
from pathlib import Path

import psutil

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import try_lock, unlock

from .packages import _ID, _SERVICE, _read_registry, files_sha256

logger = logging.getLogger(__name__)
_LOG_LIMIT = 10 * 1024 * 1024


def redact_log(text: str, secret_values: list[str] | tuple[str, ...] = ()) -> str:
    """Redact declared values and common credential forms from log text."""
    for value in sorted((value for value in secret_values if value), key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    text = re.sub(r"(?:https?|ssh)://[^\s'\"<>]+", "[redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b(bearer|basic)\s+[^\s,;]+", r"\1 [redacted]", text)
    text = re.sub(r"(?i)\b(authorization|password|token|secret|credential)\s*[:=]\s*[^\s,;]+", r"\1: [redacted]", text)
    return re.sub(r"\b(?:sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]+", "[redacted]", text)


class _LogFilter:
    """Buffer lines across reads, suppressing output that cannot be safely bounded."""

    def __init__(self, secrets: list[str]):
        self.secrets = secrets
        self.pending = bytearray()
        self.discarding = False
        self.suppress = any("\n" in value or "\r" in value for value in secrets)
        self.notified = False

    def feed(self, data: bytes, *, final: bool = False) -> bytes:
        if self.suppress:
            if data and not self.notified:
                self.notified = True
                return b"[redacted multiline secret output]\n"
            return b""
        output = bytearray()
        for part in data.splitlines(keepends=True):
            ended = part.endswith((b"\n", b"\r"))
            if not self.discarding:
                self.pending.extend(part)
                if len(self.pending) > 1024 * 1024:
                    self.pending.clear()
                    self.discarding = True
                    output.extend(b"[redacted oversized log line]\n")
            if ended:
                if not self.discarding:
                    output.extend(redact_log(self.pending.decode("utf-8", "replace"), self.secrets).encode())
                self.pending.clear()
                self.discarding = False
        if final and self.pending and not self.discarding:
            output.extend(redact_log(self.pending.decode("utf-8", "replace"), self.secrets).encode())
            self.pending.clear()
        return bytes(output)


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


def _valid_plugin_id(plugin_id: str) -> bool:
    return isinstance(plugin_id, str) and bool(_ID.fullmatch(plugin_id.removesuffix("@dev")))


def _approved_service_root(home: Path, registry: dict, plugin_id: str) -> tuple[Path, dict]:
    if plugin_id.endswith("@dev"):
        folder = registry["dev"].get(plugin_id.removesuffix("@dev"))
        if not isinstance(folder, str):
            raise ValueError("Development plugin is not attached")
        root = Path(folder).resolve(strict=True)
        approval = home / "plugin-data" / plugin_id / "approval.json"
        if approval.is_symlink():
            raise ValueError("Invalid development approval")
        try:
            record = json.loads(approval.read_text(encoding="utf-8"))
            digest = hashlib.sha256((root / "kollab-plugin.json").read_bytes()).hexdigest()
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot read development approval") from exc
        if not isinstance(record, dict) or record.get("folder") != str(root) or record.get("manifest_sha256") != digest:
            raise ValueError("Development manifest changed since approval; attach again")
        return root, record
    record = registry["plugins"].get(plugin_id)
    if not isinstance(record, dict):
        raise ValueError("Plugin is not installed")
    root = home / "plugins" / plugin_id
    expected_hash = record.get("files_sha256")
    if root.is_symlink() or not isinstance(expected_hash, str) or files_sha256(root) != expected_hash:
        raise ValueError("Plugin files changed since approval")
    return root, record


def run_service(plugin_id: str, service_name: str, nonce: str) -> int:
    """Run and supervise one service while holding its lifetime lock."""
    if not _valid_plugin_id(plugin_id) or not _SERVICE.fullmatch(service_name):
        raise ValueError("Invalid plugin or service name")
    if not nonce or any(c not in "0123456789abcdef" for c in nonce):
        raise ValueError("Invalid service nonce")
    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    root, record = _approved_service_root(home, registry, plugin_id)
    if not plugin_id.endswith("@dev") and record.get("enabled") is not True:
        raise ValueError("Plugin is not enabled")
    try:
        manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
        service = manifest["services"][service_name]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Cannot load plugin service") from exc
    command = service.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise ValueError("Invalid service command")
    requested_secrets = service.get("secrets", [])
    granted = record.get("granted", [])
    if not isinstance(requested_secrets, list) or not isinstance(granted, list):
        raise ValueError("Invalid service secrets or grants")
    for name in requested_secrets:
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or name in {"PATH", "HOME", "LANG"}
            or name.startswith("KOLLAB_")
            or f"secret:{name}" not in granted
        ):
            raise ValueError("Invalid or ungranted service secret")
    secret_values = {}
    if requested_secrets:
        from kollabor_ai.profile_manager import _keyring_get

        try:
            for name in requested_secrets:
                value = _keyring_get(name)
                if not isinstance(value, str) or not value:
                    raise ValueError(f"Service secret {name} is not available")
                secret_values[name] = value
        except Exception:
            raise ValueError("Cannot read service secrets from the secure store") from None
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
    try:
        current_root, current = _approved_service_root(home, _read_registry(home / "plugins.json"), plugin_id)
        if current_root != root or current != record:
            raise ValueError("Plugin approval or files changed while starting service")
    except Exception:
        unlock(fd)
        os.close(fd)
        raise
    pid_path = run_dir / f"{service_name}.pid"
    initial_state_path = run_dir / f"{service_name}.state"
    try:
        if initial_state_path.is_symlink():
            raise ValueError("Service state may not be a symlink")
        if initial_state_path.exists():
            initial_state = json.loads(initial_state_path.read_text(encoding="utf-8"))
            if not isinstance(initial_state, dict):
                raise ValueError("Invalid service state")
            if initial_state.get("desired") == "stopped":
                unlock(fd)
                os.close(fd)
                return 0
    except Exception:
        unlock(fd)
        os.close(fd)
        raise
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
            env["KOLLAB_PLUGIN_ID"] = plugin_id
            env["KOLLAB_PLUGIN_ROOT"] = str(root)
            env["KOLLAB_PLUGIN_DATA"] = str(home / "plugin-data" / plugin_id)
            env.update(secret_values)
            try:
                child = subprocess.Popen(
                    command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
                )
            except OSError as exc:
                state["status"] = "error"
                state["error"] = f"Could not start {command[0]}: {exc.strerror or type(exc).__name__}"
                _write_state(state_path, state)
                return 1
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
            log_filter = _LogFilter(list(secret_values.values()))
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
                            filtered = log_filter.feed(output)
                            if filtered:
                                _write_log(log_path, filtered)
                        else:
                            filtered = log_filter.feed(b"", final=True)
                            if filtered:
                                _write_log(log_path, filtered)
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


def _verified_wrapper(identity: object) -> bool:
    """Return true only with positive pid, start-time, and nonce verification."""
    if not isinstance(identity, dict):
        return False
    pid, started, nonce = identity.get("pid"), identity.get("start_time"), identity.get("nonce")
    if (
        type(pid) is not int
        or pid <= 0
        or not isinstance(started, (int, float))
        or not isinstance(nonce, str)
        or not nonce
    ):
        return False
    try:
        process = psutil.Process(pid)
        return (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
            and abs(process.create_time() - started) <= 0.02
            and nonce in process.cmdline()
        )
    except psutil.NoSuchProcess, psutil.AccessDenied:
        return False


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
            expected_hash = record.get("files_sha256")
            if not isinstance(expected_hash, str) or files_sha256(root) != expected_hash:
                raise ValueError("Plugin files changed since approval")
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
                            or state.get("status") in {"exited", "crash-stopped", "error"}
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


def control_service(plugin_id: str, service_name: str, action: str) -> None:
    """Set one installed service's desired state and signal only its verified wrapper."""
    if not _valid_plugin_id(plugin_id) or not _SERVICE.fullmatch(service_name):
        raise ValueError("Invalid plugin or service name")
    if action not in {"start", "stop"}:
        raise ValueError("Invalid service action")
    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    root, record = _approved_service_root(home, registry, plugin_id)
    if action == "start" and not plugin_id.endswith("@dev") and record.get("enabled") is not True:
        raise ValueError("Enable the plugin before starting its service")
    try:
        manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
        if service_name not in manifest["services"]:
            raise ValueError("Unknown plugin service")
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError("Cannot load plugin service") from exc
    data = home / "plugin-data" / plugin_id
    run_dir = data / "run"
    if any(path.is_symlink() for path in (home / "plugin-data", data, run_dir)):
        raise ValueError("Plugin data path may not be a symlink")
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / f"{service_name}.state"
    if state_path.is_symlink():
        raise ValueError("Service state may not be a symlink")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if not isinstance(state, dict):
        raise ValueError("Invalid service state")
    pid_path = run_dir / f"{service_name}.pid"
    if action == "start":
        if _recorded_process_alive(pid_path):
            raise ValueError("Service is already running or has an unverifiable process")
        state.update({"desired": "running", "status": "starting"})
        _write_state(state_path, state)
        supervisor_tick()
        return
    identity = {}
    if not pid_path.exists() and not pid_path.is_symlink():
        fd = os.open(run_dir / f"{service_name}.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not try_lock(fd):
                raise ValueError("Service is starting; try stopping it again")
            try:
                if pid_path.exists() or pid_path.is_symlink():
                    raise ValueError("Service started concurrently; try stopping it again")
                state.update({"desired": "stopped", "status": "stopped"})
                _write_state(state_path, state)
                return
            finally:
                unlock(fd)
        finally:
            os.close(fd)
    if pid_path.exists() or pid_path.is_symlink():
        if pid_path.is_symlink():
            raise ValueError("Service pid file may not be a symlink")
        identity = json.loads(pid_path.read_text(encoding="utf-8"))
        if not isinstance(identity, dict):
            raise ValueError("Invalid service identity")
    wrapper = identity.get("wrapper")
    child = identity.get("child")
    verified = _verified_wrapper(wrapper)
    if not verified and (_identity_alive(wrapper, wrapper=True) or _identity_alive(child)):
        raise ValueError("Service is alive without a verified wrapper")
    state.update(
        {
            "desired": "stopped",
            "status": "stopping" if verified else "stopped",
        }
    )
    _write_state(state_path, state)
    if verified:
        try:
            os.kill(wrapper["pid"], signal.SIGTERM)
        except ProcessLookupError:
            state["status"] = "stopped"
            _write_state(state_path, state)


def stop_services(plugin_id: str) -> None:
    """Signal only verified wrappers belonging to the named plugin."""
    if not _ID.fullmatch(plugin_id):
        raise ValueError("Invalid plugin id")
    run_dir = Path.home() / ".kollab" / "plugin-data" / plugin_id / "run"
    for pid_path in run_dir.glob("*.pid"):
        try:
            identity = json.loads(pid_path.read_text(encoding="utf-8"))
            wrapper = identity.get("wrapper")
            if _verified_wrapper(wrapper):
                os.kill(wrapper["pid"], signal.SIGTERM)
            elif _identity_alive(wrapper, wrapper=True):
                logger.warning("Refusing to signal unverifiable wrapper for %s", pid_path.name)
        except ProcessLookupError:
            continue
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            logger.warning("Could not stop service %s: %s", pid_path.name, exc)


async def supervise_services() -> None:
    """Sweep on startup and every 30 seconds until the application shuts down."""
    while True:
        try:
            await asyncio.to_thread(supervisor_tick)
        except Exception:
            logger.exception("Plugin service supervisor sweep failed")
        await asyncio.sleep(30)
