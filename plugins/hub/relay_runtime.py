"""Supervise relay workers and an optional private, managed Valkey sidecar.

Run with ``kollab relay run --config /private/relay.json``. This module owns
only its child processes and a container bearing its exact ownership labels.
It never deletes containers, volumes, or backend data.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiohttp
from aiohttp import web

from .relay_state import canonical_origin, strict_json

DEFAULT_IMAGE = "valkey/valkey:8.1.10-alpine"
OWNER_LABEL = "io.kollab.relay.owner"
INSTANCE_LABEL = "io.kollab.relay.instance"
CONFIG_LABEL = "io.kollab.relay.config"
OWNER_VALUE = "kollab-relay-runtime-v1"
BACKEND_ENV = "KOLLAB_RELAY_BACKEND_URL"
FIRST_READY_GRACE_SECONDS = 45
LIMIT_DEFAULTS = {
    "max_connections_per_node": 512,
    "max_connections_per_room": 16,
    "max_connections_per_source": 16,
}


class RuntimeConfigError(ValueError):
    """An operator-actionable error containing no backend credentials."""


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise RuntimeConfigError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _private_directory(path: Path, *, create: bool = False) -> Path:
    # Resolve system aliases such as macOS /var first, then examine the actual
    # ownership/write boundary. Never chmod an existing unrelated parent.
    if path.is_symlink():
        raise RuntimeConfigError("private runtime directory must not be a symbolic link")
    path = path.expanduser().resolve()
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeConfigError("config and state directories must be owned by this user with mode 0700")
    for parent in path.parents:
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeConfigError("runtime parent is not a directory")
        if info.st_uid not in (0, os.getuid()):
            raise RuntimeConfigError("runtime parent directory is owned by another user")
        # A root-owned sticky temporary directory cannot rename another user's
        # private child; ordinary shared writable ancestors are rejected.
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise RuntimeConfigError("runtime parent directory permits unsafe shared writes")
    return path


def _read_private(path: Path, *, limit: int = 65536) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeConfigError("config and credential files must be owned by this user with mode 0600")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise RuntimeConfigError("private runtime file exceeds its size limit")
        return raw


def _write_private(path: Path, value: str, *, uid: int | None = None, gid: int | None = None):
    descriptor, temporary = tempfile.mkstemp(prefix=".relay-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            if uid is not None:
                os.fchown(stream.fileno(), uid, gid if gid is not None else -1)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            not isinstance(value, str)
            or len(value) > 4096
            or any(ord(c) < 33 for c in value)
            or parsed.scheme not in {"redis", "rediss"}
            or not parsed.hostname
            or parsed.fragment
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError("backend URL must be a redis:// or rediss:// URL") from exc
    return value


@dataclass(frozen=True)
class RuntimeConfig:
    origin: str
    bind_host: str
    base_port: int
    workers: int
    health_port: int
    node_prefix: str
    state_dir: Path
    trusted_proxies: tuple[str, ...]
    backend: dict
    limits: dict[str, int]

    @classmethod
    def load(cls, path: Path):
        if path.is_symlink():
            raise RuntimeConfigError("runtime configuration must not be a symbolic link")
        path = path.expanduser().absolute()
        parent = _private_directory(path.parent)
        payload = strict_json(_read_private(parent / path.name))
        fields = {
            "origin",
            "bind_host",
            "base_port",
            "workers",
            "health_port",
            "node_prefix",
            "state_dir",
            "trusted_proxies",
            "backend",
            "limits",
        }
        if set(payload) - fields or not {"origin", "node_prefix", "backend"} <= set(payload):
            raise RuntimeConfigError(
                "runtime config requires origin, node_prefix and backend; unknown fields are rejected"
            )
        origin = canonical_origin(payload["origin"])
        prefix = payload["node_prefix"]
        if not isinstance(prefix, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", prefix):
            raise RuntimeConfigError("node_prefix must be 1..40 lowercase letters, digits or hyphens")
        bind = payload.get("bind_host", "127.0.0.1")
        try:
            if not isinstance(bind, str):
                raise ValueError()
            ipaddress.ip_address(bind)
        except (ValueError, TypeError) as exc:
            raise RuntimeConfigError("bind_host must be a literal IP address") from exc
        workers = _integer(payload.get("workers", 2), "workers", 1, 64)
        port = _integer(payload.get("base_port", 9078), "base_port", 1024, 65535 - workers)
        health_port = _integer(payload.get("health_port", port + workers), "health_port", 1024, 65535)
        if port <= health_port < port + workers:
            raise RuntimeConfigError("health_port overlaps a worker port")
        state_value = payload.get("state_dir", str(parent / "runtime"))
        if not isinstance(state_value, str) or not Path(state_value).expanduser().is_absolute():
            raise RuntimeConfigError("state_dir must be an absolute path")
        state_dir = _private_directory(Path(state_value), create=True)
        proxies = payload.get("trusted_proxies", [])
        if not isinstance(proxies, list) or len(proxies) > 32:
            raise RuntimeConfigError("trusted_proxies must be a bounded list of exact IP addresses")
        try:
            if any(not isinstance(value, str) for value in proxies):
                raise ValueError()
            proxies = tuple(str(ipaddress.ip_address(value)) for value in proxies)
        except (ValueError, TypeError) as exc:
            raise RuntimeConfigError("trusted_proxies requires exact IP addresses") from exc
        limits = payload.get("limits", {})
        if not isinstance(limits, dict) or set(limits) - set(LIMIT_DEFAULTS):
            raise RuntimeConfigError("unknown connection quota")
        limits = {
            name: _integer(limits.get(name, default), name, 1, 256 if name == "max_connections_per_room" else 100000)
            for name, default in LIMIT_DEFAULTS.items()
        }
        backend = payload["backend"]
        if (
            not isinstance(backend, dict)
            or not isinstance(backend.get("mode"), str)
            or backend["mode"] not in {"external", "managed"}
        ):
            raise RuntimeConfigError("backend mode must be managed or external")
        if backend["mode"] == "external":
            if set(backend) - {"mode", "url_env", "url_file", "cluster"}:
                raise RuntimeConfigError("unknown external backend field; inline backend URLs are not accepted")
            if ("url_env" in backend) == ("url_file" in backend) or type(backend.get("cluster", False)) is not bool:
                raise RuntimeConfigError(
                    "external backend requires exactly one url_env or url_file and a boolean cluster flag"
                )
            if "url_env" in backend and (
                not isinstance(backend["url_env"], str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", backend["url_env"])
            ):
                raise RuntimeConfigError("url_env must name an environment variable")
            if "url_file" in backend and (
                not isinstance(backend["url_file"], str) or not Path(backend["url_file"]).is_absolute()
            ):
                raise RuntimeConfigError("url_file must be an absolute private credential path")
        else:
            if set(backend) - {"mode", "port", "memory_mb", "image"}:
                raise RuntimeConfigError("unknown managed backend field")
            backend = {
                "mode": "managed",
                "port": _integer(backend.get("port", 16379), "backend port", 1024, 65535),
                "memory_mb": _integer(backend.get("memory_mb", 256), "backend memory_mb", 64, 65536),
                "image": backend.get("image", DEFAULT_IMAGE),
            }
            if not isinstance(backend["image"], str) or not re.fullmatch(
                r"valkey/valkey:(?:\d+\.\d+\.\d+)(?:-alpine(?:\d+\.\d+)?)?(?:@sha256:[0-9a-f]{64})?", backend["image"]
            ):
                raise RuntimeConfigError(
                    "managed image must pin an official valkey/valkey patch version (optionally a digest)"
                )
            if backend["port"] in {*range(port, port + workers), health_port}:
                raise RuntimeConfigError("managed backend port overlaps a runtime listener")
        return cls(origin, bind, port, workers, health_port, prefix, state_dir, proxies, backend, limits)

    def node_id(self, index: int) -> str:
        return hashlib.sha256(f"{self.origin}\n{self.node_prefix}\n{index}".encode()).hexdigest()[:32]


class ManagedValkey:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.instance = hashlib.sha256(str(config.state_dir).encode()).hexdigest()[:24]
        self.name = f"kollab-relay-{config.node_prefix}-{self.instance[:12]}"
        self.uid = os.getuid() or 65532
        self.gid = os.getgid() if os.getuid() else 65532
        self.path = config.state_dir / "valkey.conf"
        self.password_path = config.state_dir / "valkey-password"
        self.fingerprint = ""
        self.owned = False

    async def _docker(self, *args, required=True, timeout=30):
        binary = shutil.which("docker")
        if not binary:
            raise RuntimeConfigError(
                "managed backend requires the Docker CLI; install Docker or configure an external backend"
            )
        process = await asyncio.create_subprocess_exec(
            binary, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if required and process.returncode:
            raise RuntimeConfigError(
                f"Docker {args[0]} failed; check daemon access, image availability and owned container configuration"
            )
        return process.returncode, output, error

    async def _inspect(self):
        code, output, error = await self._docker("container", "inspect", self.name, required=False)
        if code:
            if b"No such" in error or b"no such" in error:
                return None
            raise RuntimeConfigError("unable to inspect managed container; Docker daemon may be unavailable")
        try:
            value = json.loads(output)
            if not isinstance(value, list) or len(value) != 1:
                raise ValueError()
            return value[0]
        except (ValueError, TypeError) as exc:
            raise RuntimeConfigError("invalid Docker inspect response") from exc

    def _validate_inspect(self, item):
        config, host = item.get("Config", {}), item.get("HostConfig", {})
        labels = config.get("Labels") or {}
        expected_labels = {OWNER_LABEL: OWNER_VALUE, INSTANCE_LABEL: self.instance, CONFIG_LABEL: self.fingerprint}
        mounts = item.get("Mounts", [])
        bind_mounts = [mount for mount in mounts if mount.get("Type") == "bind"]
        memory = self.config.backend["memory_mb"] * 1024 * 1024
        safe = (
            item.get("Name") == "/" + self.name
            and all(labels.get(key) == value for key, value in expected_labels.items())
            and config.get("Image") == self.config.backend["image"]
            and config.get("User") == f"{self.uid}:{self.gid}"
            and config.get("Entrypoint") == ["valkey-server"]
            and config.get("Cmd") == ["/etc/kollab/valkey.conf"]
            and not host.get("Privileged")
            and host.get("ReadonlyRootfs")
            and host.get("CapDrop") == ["ALL"]
            and not host.get("CapAdd")
            and "no-new-privileges:true" in (host.get("SecurityOpt") or [])
            and host.get("Memory") == memory
            and host.get("MemorySwap") == memory
            and host.get("PidsLimit") == 64
            and host.get("NetworkMode") == "bridge"
            and host.get("RestartPolicy", {}).get("Name") == "unless-stopped"
            and host.get("PortBindings")
            == {"6379/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.config.backend["port"])}]}
            and len(bind_mounts) == 1
            and bind_mounts[0].get("Source") == str(self.path)
            and bind_mounts[0].get("Destination") == "/etc/kollab/valkey.conf"
            and bind_mounts[0].get("RW") is False
            and all(
                mount.get("Type") == "bind" or (mount.get("Type") == "tmpfs" and mount.get("Destination") == "/data")
                for mount in mounts
            )
            and host.get("Tmpfs") == {"/data": self._tmpfs_options()}
        )
        if not safe:
            raise RuntimeConfigError("existing container ownership or configuration mismatch; refusing to modify it")

    def _tmpfs_options(self):
        return f"rw,noexec,nosuid,nodev,size=16777216,uid={self.uid},gid={self.gid},mode=700"

    async def start(self) -> str:
        await self._docker("version", "--format", "{{.Server.Version}}", timeout=15)
        existing = await self._inspect()
        # Fail before touching config/credentials if this name belongs to
        # anything outside this exact runtime instance.
        if existing:
            labels = existing.get("Config", {}).get("Labels") or {}
            if labels.get(OWNER_LABEL) != OWNER_VALUE or labels.get(INSTANCE_LABEL) != self.instance:
                raise RuntimeConfigError("managed container name is already owned by another service")
        if self.password_path.exists() or self.password_path.is_symlink():
            password = _read_private(self.password_path, limit=128).strip()
            if not re.fullmatch(r"[0-9a-f]{64}", password):
                raise RuntimeConfigError("invalid managed backend credential file")
        elif existing:
            raise RuntimeConfigError("managed container exists but its credential file is missing")
        else:
            password = secrets.token_hex(32)
            _write_private(self.password_path, password + "\n")
        content = "\n".join(
            (
                "bind 0.0.0.0",
                "port 6379",
                "protected-mode yes",
                f"requirepass {password}",
                'save ""',
                "appendonly no",
                "dir /data",
                'logfile ""',
                "loglevel warning",
                f"maxmemory {self.config.backend['memory_mb'] // 2}mb",
                "maxmemory-policy noeviction",
                "daemonize no",
                "",
            )
        )
        identity = {
            "backend": self.config.backend,
            "uid": self.uid,
            "gid": self.gid,
            "config_sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        self.fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if existing:
            self._validate_inspect(existing)
        if self.path.exists() or self.path.is_symlink():
            # Root may assign the container's non-root UID to this file, so
            # validate bytes via a no-follow fd rather than owner equality.
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor) as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self.uid
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or stream.read(4097) != content
                ):
                    raise RuntimeConfigError("managed Valkey configuration changed; refusing to restart container")
        elif existing:
            raise RuntimeConfigError("managed container configuration file is missing")
        else:
            _write_private(self.path, content, uid=self.uid, gid=self.gid)
        if not existing:
            args = [
                "create",
                "--name",
                self.name,
                "--label",
                f"{OWNER_LABEL}={OWNER_VALUE}",
                "--label",
                f"{INSTANCE_LABEL}={self.instance}",
                "--label",
                f"{CONFIG_LABEL}={self.fingerprint}",
                "--user",
                f"{self.uid}:{self.gid}",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--read-only",
                "--pids-limit",
                "64",
                "--memory",
                f"{self.config.backend['memory_mb']}m",
                "--memory-swap",
                f"{self.config.backend['memory_mb']}m",
                "--cpus",
                "1",
                "--network",
                "bridge",
                "--publish",
                f"127.0.0.1:{self.config.backend['port']}:6379",
                "--restart",
                "unless-stopped",
                "--tmpfs",
                "/data:" + self._tmpfs_options(),
                "--mount",
                f"type=bind,src={self.path},dst=/etc/kollab/valkey.conf,readonly",
                "--log-driver",
                "local",
                "--log-opt",
                "max-size=10m",
                "--log-opt",
                "max-file=2",
                "--entrypoint",
                "valkey-server",
                self.config.backend["image"],
                "/etc/kollab/valkey.conf",
            ]
            await self._docker(*args, timeout=180)
            self._validate_inspect(await self._inspect())
        self.owned = True
        if not existing or not existing.get("State", {}).get("Running"):
            await self._docker("start", self.name)
        return f"redis://:{password}@127.0.0.1:{self.config.backend['port']}/0"

    async def stop(self):
        if self.owned:
            item = await self._inspect()
            if item:
                self._validate_inspect(item)
                await self._docker("stop", "--time", "10", self.name, timeout=15)


@dataclass
class Worker:
    index: int
    process: asyncio.subprocess.Process | None = None
    log_task: asyncio.Task | None = None
    ready: bool = False
    failures: int = 0
    retry_at: float = 0
    started_at: float = 0
    unhealthy: int = 0


class RelayRuntime:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.stop_event = asyncio.Event()
        self.workers = [Worker(index) for index in range(1, config.workers + 1)]
        self.sidecar = ManagedValkey(config) if config.backend["mode"] == "managed" else None
        self.backend = None
        self.backend_ready = False
        self.backend_url = ""
        self.health_runner = None
        self.started = int(time.time())
        self.checked_at = 0
        self._last_probe = 0.0
        self._lock_fd = None

    def snapshot(self):
        healthy = sum(worker.ready for worker in self.workers)
        fresh = bool(self._last_probe and time.monotonic() - self._last_probe <= 15)
        ready = self.backend_ready and healthy > 0 and fresh
        from .relay_service import PROTOCOL, PROTOCOLS

        return {
            "status": "ok" if ready else "unavailable",
            "protocol": PROTOCOL,
            "protocols": list(PROTOCOLS),
            "origin": self.config.origin,
            "backend_ready": self.backend_ready,
            "ready_workers": healthy,
            "workers": len(self.workers),
            "degraded": healthy != len(self.workers),
            "started_at": self.started,
            "worker_restarts": sum(worker.failures for worker in self.workers),
            "checked_at": self.checked_at,
            "readiness_stale": not fresh,
        }

    def _log(self, event, **fields):
        print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)

    async def _health(self, _request):
        value = self.snapshot()
        return web.json_response(
            value, status=200 if value["status"] == "ok" else 503, headers={"Cache-Control": "no-store"}
        )

    async def _open_backend(self):
        from .relay_backend import validate_backend_url

        try:
            import redis.asyncio as redis
            from redis.asyncio.cluster import RedisCluster
        except ImportError as exc:
            raise RuntimeConfigError("relay runtime requires the redis Python dependency") from exc
        if self.sidecar:
            self.backend_url = await self.sidecar.start()
        elif "url_env" in self.config.backend:
            self.backend_url = _validate_url(os.environ.get(self.config.backend["url_env"], ""))
        else:
            path = Path(self.config.backend["url_file"])
            _private_directory(path.parent)
            self.backend_url = _validate_url(_read_private(path, limit=4096).strip())
        try:
            validate_backend_url(self.backend_url, cluster=self.config.backend.get("cluster", False))
        except ValueError as exc:
            raise RuntimeConfigError(
                "backend URL rejected by relay security policy; check TLS, endpoint scope and cluster database"
            ) from exc
        factory = RedisCluster if self.config.backend.get("cluster", False) else redis.Redis
        try:
            self.backend = factory.from_url(
                self.backend_url, socket_connect_timeout=3, socket_timeout=3, decode_responses=True
            )
        except Exception as exc:
            raise RuntimeConfigError("backend URL or cluster configuration is invalid") from exc
        deadline = time.monotonic() + 30
        while not self.stop_event.is_set():
            if await self._backend_ping():
                return
            if time.monotonic() >= deadline:
                raise RuntimeConfigError(
                    "backend did not become ready within 30 seconds; check credentials, TLS, topology and connectivity"
                )
            try:
                await asyncio.wait_for(self.stop_event.wait(), 1)
            except TimeoutError:
                pass

    async def _backend_ping(self):
        try:
            async with asyncio.timeout(5):
                self.backend_ready = bool(await self.backend.ping())
        except Exception:
            self.backend_ready = False
        return self.backend_ready

    async def _worker_logs(self, reader, index):
        pending = bytearray()
        discard = False
        bucket, available = time.monotonic(), 20
        parsed = urlsplit(self.backend_url)
        secrets_to_hide = [self.backend_url, parsed.password or "", unquote(parsed.password or "")]
        while chunk := await reader.read(4096):
            for part in chunk.splitlines(keepends=True):
                if not discard:
                    pending.extend(part)
                    if len(pending) > 8192:
                        pending.clear()
                        discard = True
                if part.endswith(b"\n"):
                    if time.monotonic() - bucket >= 1:
                        bucket, available = time.monotonic(), 20
                    if not discard and available:
                        text = pending.decode(errors="replace").strip()
                        for secret in secrets_to_hide:
                            if secret:
                                text = text.replace(secret, "[redacted]")
                        text = re.sub(r"rediss?://\S+", "[redacted-backend-url]", text)
                        self._log("worker_log", worker=f"{self.config.node_prefix}-{index}", message=text[:2000])
                        available -= 1
                    pending.clear()
                    discard = False

    async def _spawn(self, worker):
        args = [
            sys.executable,
            "-m",
            "plugins.hub.relay_service",
            "--origin",
            self.config.origin,
            "--bind",
            self.config.bind_host,
            "--port",
            str(self.config.base_port + worker.index - 1),
            "--node-id",
            self.config.node_id(worker.index),
            "--backend-url-env",
            BACKEND_ENV,
        ]
        if self.config.backend.get("cluster", False):
            args.append("--backend-cluster")
        for proxy in self.config.trusted_proxies:
            args += ["--trusted-proxy", proxy]
        for key, value in self.config.limits.items():
            args += ["--" + key.replace("_", "-"), str(value)]
        environment = dict(os.environ)
        environment[BACKEND_ENV] = self.backend_url
        environment["PYTHONUNBUFFERED"] = "1"
        # Workers stop themselves if this supervisor dies without a clean stop.
        environment["KOLLAB_RELAY_SUPERVISOR_PID"] = str(os.getpid())
        worker.process = await asyncio.create_subprocess_exec(
            *args,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        worker.started_at = time.monotonic()
        worker.ready, worker.unhealthy = False, 0
        worker.log_task = asyncio.create_task(self._worker_logs(worker.process.stdout, worker.index))
        self._log(
            "worker_started",
            worker=f"{self.config.node_prefix}-{worker.index}",
            port=self.config.base_port + worker.index - 1,
            pid=worker.process.pid,
        )

    @staticmethod
    def _extend_first_ready_deadline(deadline: float, worker) -> float:
        """A previous owner's lease can outlive the startup window; wait it out."""
        return max(deadline, worker.retry_at + FIRST_READY_GRACE_SECONDS)

    async def _worker_owner_lease_expired(self, worker) -> bool:
        """Allow a replacement only after Redis no longer fences the old owner."""
        now = time.monotonic()
        if not self.backend_ready or self.backend is None:
            worker.retry_at = now + 3
            return False

        from .relay_backend import relay_owner_key

        try:
            ttl_ms = int(
                await self.backend.pttl(
                    relay_owner_key(self.config.node_id(worker.index))
                )
            )
        except Exception as exc:
            worker.retry_at = now + 3
            self._log(
                "worker_owner_lease_check_failed",
                worker=f"{self.config.node_prefix}-{worker.index}",
                error=type(exc).__name__,
                retry_seconds=3,
            )
            return False

        if ttl_ms == -2:
            return True
        if ttl_ms == -1:
            delay = 30.0
            event = "worker_owner_lease_persistent"
        elif ttl_ms >= 0:
            delay = max(0.1, (ttl_ms + 100) / 1000)
            event = "worker_waiting_for_owner_lease"
        else:
            worker.retry_at = now + 3
            self._log(
                "worker_owner_lease_check_failed",
                worker=f"{self.config.node_prefix}-{worker.index}",
                error="InvalidPTTL",
                retry_seconds=3,
            )
            return False

        worker.retry_at = now + delay
        self._log(
            event,
            worker=f"{self.config.node_prefix}-{worker.index}",
            retry_seconds=round(delay, 3),
        )
        return False

    async def _stop_worker(self, worker):
        worker.ready = False
        worker.unhealthy = 0
        process = worker.process
        if process and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 12)
            except TimeoutError:
                # Only this supervisor's known child, never another process.
                process.kill()
                await process.wait()
        if worker.log_task:
            try:
                await asyncio.wait_for(worker.log_task, 3)
            except TimeoutError:
                pass
            worker.log_task = None

    async def _worker_health(self, session, worker):
        if not worker.process or worker.process.returncode is not None:
            worker.ready = False
            return
        host = self.config.bind_host
        if host == "0.0.0.0":
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        if ":" in host:
            host = f"[{host}]"
        url = f"http://{host}:{self.config.base_port + worker.index - 1}/relay/v1/health"
        try:
            async with session.get(url, allow_redirects=False) as response:
                raw = await response.content.read(8193)
                value = strict_json(raw, limit=8192)
                worker.ready = (
                    response.status == 200
                    and value.get("status") == "ok"
                    and value.get("origin") == self.config.origin
                    and value.get("node_id") == self.config.node_id(worker.index)
                )
        except (aiohttp.ClientError, TimeoutError, ValueError, OSError):
            worker.ready = False
        worker.unhealthy = 0 if worker.ready or not self.backend_ready else worker.unhealthy + 1

    async def run(self):
        lock_path = self.config.state_dir / "runtime.lock"
        self._lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(self._lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise RuntimeConfigError("runtime lock must be a private regular file owned by this user")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise RuntimeConfigError("this runtime state directory is already supervised") from exc
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop_event.set)
        try:
            app = web.Application()
            app.router.add_get("/relay/v1/health", self._health)
            self.health_runner = web.AppRunner(app, access_log=None)
            await self.health_runner.setup()
            await web.TCPSite(self.health_runner, self.config.bind_host, self.config.health_port).start()
            opening = asyncio.create_task(self._open_backend())
            stopping = asyncio.create_task(self.stop_event.wait())
            try:
                done, _ = await asyncio.wait({opening, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if stopping in done and not opening.done():
                    opening.cancel()
                    await asyncio.gather(opening, return_exceptions=True)
                    return
                await opening
            finally:
                stopping.cancel()
                await asyncio.gather(stopping, return_exceptions=True)
            if self.stop_event.is_set():
                return
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                first_ready_deadline = time.monotonic() + FIRST_READY_GRACE_SECONDS
                had_ready = False
                while not self.stop_event.is_set():
                    await self._backend_ping()
                    for worker in self.workers:
                        if worker.process and worker.process.returncode is not None:
                            if worker.log_task:
                                await worker.log_task
                                worker.log_task = None
                            code = worker.process.returncode
                            worker.process = None
                            worker.ready = False
                            worker.failures += 1
                            worker.retry_at = time.monotonic() + min(2 ** min(worker.failures, 5), 30)
                            self._log("worker_exited", worker=f"{self.config.node_prefix}-{worker.index}", code=code)
                        if (
                            worker.process is None
                            and time.monotonic() >= worker.retry_at
                        ):
                            if await self._worker_owner_lease_expired(worker):
                                await self._spawn(worker)
                            elif not had_ready:
                                first_ready_deadline = self._extend_first_ready_deadline(
                                    first_ready_deadline, worker
                                )
                    await asyncio.gather(*(self._worker_health(session, worker) for worker in self.workers))
                    self._last_probe = time.monotonic()
                    self.checked_at = int(time.time())
                    for worker in self.workers:
                        if (
                            worker.process is not None
                            and worker.process.returncode is None
                            and worker.unhealthy >= 3
                            and time.monotonic() - worker.started_at >= 15
                        ):
                            self._log("worker_unhealthy", worker=f"{self.config.node_prefix}-{worker.index}")
                            await self._stop_worker(worker)
                    snapshot = self.snapshot()
                    _write_private(self.config.state_dir / "status.json", json.dumps(snapshot, sort_keys=True) + "\n")
                    if snapshot["status"] == "ok":
                        if not had_ready:
                            self._log("runtime_ready", **snapshot)
                        had_ready = True
                    elif not had_ready and time.monotonic() > first_ready_deadline:
                        raise RuntimeConfigError(
                            "no relay worker became ready; inspect redacted worker logs and configuration"
                        )
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), 3)
                    except TimeoutError:
                        pass
        finally:
            self.stop_event.set()
            try:
                results = await asyncio.gather(
                    *(self._stop_worker(worker) for worker in self.workers), return_exceptions=True
                )
                for result in results:
                    if isinstance(result, Exception):
                        self._log("worker_shutdown_error", error=type(result).__name__)
                for label, resource in (("backend", self.backend), ("sidecar", self.sidecar)):
                    if resource:
                        try:
                            async with asyncio.timeout(20):
                                await (resource.aclose() if label == "backend" else resource.stop())
                        except Exception as exc:
                            self._log("dependency_shutdown_error", dependency=label, error=type(exc).__name__)
                self.backend_ready = False
                _write_private(
                    self.config.state_dir / "status.json", json.dumps(self.snapshot(), sort_keys=True) + "\n"
                )
            finally:
                if self.health_runner:
                    await self.health_runner.cleanup()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.remove_signal_handler(sig)
                if self._lock_fd is not None:
                    os.close(self._lock_fd)
                    self._lock_fd = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervise Kollab relay workers and a shared backend.")
    parser.add_argument("--config", type=Path, required=True, help="private JSON configuration file (0600; parent0700)")
    args = parser.parse_args(argv)
    try:
        config = RuntimeConfig.load(args.config)
        asyncio.run(RelayRuntime(config).run())
    except (RuntimeConfigError, ValueError, OSError) as exc:
        # Config errors are crafted safe; OS errors expose only their category,
        # avoiding backend URLs or credential contents in chained exceptions.
        message = str(exc) if isinstance(exc, (RuntimeConfigError, ValueError)) else type(exc).__name__
        print(f"relay runtime: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
