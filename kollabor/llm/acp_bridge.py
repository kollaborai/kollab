"""Outbound ACP process, session, turn and permission ownership."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar

import acp
import psutil
from acp.schema import AllowedOutcome, DeniedOutcome, EnvVariable, Implementation, McpServerStdio, TextContentBlock

from .acp_state import ACPBinding
from kollabor.version import __version__


class ACPError(RuntimeError):
    """A protocol, lifecycle, or adapter error."""

    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ACPProfile:
    name: str
    command: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    pass_env: tuple[str, ...] = ()
    workspace: str | None = None
    mcp_servers: tuple[dict[str, Any], ...] = ()

    BUILTIN_PROFILES: ClassVar[dict[str, dict[str, Any]]] = {
        "claude-code": {
            "command": ["claude-agent-acp"],
            "managed_dir": "claude-0.89.0",
            "install": "npm install --prefix ~/.local/share/kollab/acp-adapters/claude-0.89.0 --no-save @agentclientprotocol/claude-agent-acp@0.89.0",
        },
        "codex-cli": {
            "command": ["codex-acp"],
            "managed_dir": "codex-2.1.1",
            "install": "npm install --prefix ~/.local/share/kollab/acp-adapters/codex-2.1.1 --no-save @agentclientprotocol/codex-acp@2.1.1",
        },
    }
    _PROVIDER_KEYS: ClassVar[frozenset[str]] = frozenset({
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY",
    })
    LOGIN_GUIDANCE: ClassVar[dict[str, str]] = {
        "claude-code": "Run claude and /login on this device.",
        "codex-cli": "Run codex login on this device.",
    }

    @classmethod
    def from_dict(cls, name: str, value: dict[str, Any]) -> "ACPProfile":
        command = value.get("command")
        if command is None and name in cls.BUILTIN_PROFILES:
            command = cls.BUILTIN_PROFILES[name]["command"]
        if isinstance(command, str):
            command = (command,)
        if not isinstance(command, (list, tuple)) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ValueError("ACP profile command must be a non-empty list")
        return cls(
            name=name,
            command=tuple(command),
            cwd=value.get("cwd"),
            env={str(k): str(v) for k, v in (value.get("env") or {}).items()},
            pass_env=tuple(str(item) for item in (value.get("pass_env") or ())),
            workspace=value.get("workspace"),
            mcp_servers=tuple(value.get("mcp_servers") or ()),
        )

    @classmethod
    def builtin(cls, name: str, **overrides: Any) -> "ACPProfile":
        try:
            data = dict(cls.BUILTIN_PROFILES[name])
        except KeyError as exc:
            raise ValueError(f"unknown ACP profile: {name}") from exc
        data.pop("install", None)
        data.update(overrides)
        return cls.from_dict(name, data)

    @property
    def install_guidance(self) -> str:
        install = self.BUILTIN_PROFILES.get(self.name, {}).get("install")
        return f"Install the adapter with: {install}" if install else (
            f"Install the ACP adapter for profile '{self.name}' and retry."
        )

    def _cache_path(self) -> Path:
        root = Path(os.environ.get("KOLLAB_CONFIG_DIR", "~/.kollab")).expanduser()
        path = root / "acp" / "resolved.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _login_shell_path(self) -> str:
        shell = os.environ.get("SHELL") or "/bin/sh"
        try:
            result = subprocess.run(
                [shell, "-lc", f"command -v {shlex.quote(self.command[0])}"],
                check=False, capture_output=True, text=True, timeout=3,
            )
            return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    @staticmethod
    def _is_executable_file(path: str) -> bool:
        """Return whether path names a regular file executable by this process."""
        candidate = Path(path)
        return bool(path and candidate.is_file() and os.access(candidate, os.X_OK))

    def _managed_adapter_path(self) -> str:
        builtin = self.BUILTIN_PROFILES.get(self.name)
        if not builtin or self.command[0] != builtin["command"][0]:
            return ""
        return str(
            Path.home() / ".local" / "share" / "kollab" / "acp-adapters"
            / builtin["managed_dir"] / "node_modules" / ".bin" / self.command[0]
        )

    def resolve(self) -> tuple[tuple[str, ...], dict[str, str], str]:
        cache_path = self._cache_path()
        cache: dict[str, Any] = {}
        try:
            cache = json.loads(cache_path.read_text())
        except (OSError, ValueError):
            pass
        if not isinstance(cache, dict):
            cache = {}
        cached = cache.get(self.name)
        executable = str(cached.get("path", "")) if isinstance(cached, dict) else ""
        cached_command = cached.get("command") if isinstance(cached, dict) else None
        if (
            cached_command != self.command[0]
            or not executable
            or not self._is_executable_file(executable)
        ):
            executable = next(
                (
                    candidate
                    for candidate in (
                        self._login_shell_path(),
                        shutil.which(self.command[0]) or "",
                        self._managed_adapter_path(),
                    )
                    if self._is_executable_file(candidate)
                ),
                "",
            )
        if not executable:
            raise ACPError(f"ACP adapter executable not found: {self.command[0]}. {self.install_guidance}")
        executable = os.path.abspath(executable)
        cwd = str(Path(self.cwd or self.workspace or os.getcwd()).expanduser().resolve())
        if not Path(cwd).is_dir():
            raise ACPError(f"ACP working directory does not exist: {cwd}")
        resolved_dir = os.path.dirname(executable)
        node_path = ""
        try:
            first_line = Path(executable).read_text(errors="ignore").splitlines()[0]
            if first_line.startswith("#!") and "env node" in first_line:
                node_path = self._login_shell_node()
                node_file = Path(node_path)
                if not node_path or not node_file.is_file() or not os.access(node_file, os.X_OK):
                    raise ACPError(
                        f"Node executable for ACP adapter is unavailable: {node_path or 'node'}. "
                        f"{self.install_guidance}"
                    )
        except (OSError, IndexError):
            pass
        cache[self.name] = {
            "command": self.command[0],
            "path": executable,
            "directory": resolved_dir,
            "node": node_path,
        }
        try:
            cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")
        except OSError:
            pass
        path_prefix = [resolved_dir]
        if node_path:
            path_prefix.append(os.path.dirname(node_path))
        original_path = os.environ.get("PATH", "")
        safe_env = {
            key: value for key, value in os.environ.items()
            if key not in self._PROVIDER_KEYS or key in self.pass_env
        }
        safe_env.update({k: v for k, v in self.env.items() if k not in self._PROVIDER_KEYS})
        safe_env["PATH"] = os.pathsep.join(path_prefix + ([original_path] if original_path else []))
        if node_path:
            safe_env.setdefault("ACP_NODE", node_path)
        return (executable, *self.command[1:]), safe_env, cwd

    def _login_shell_node(self) -> str:
        shell = os.environ.get("SHELL") or "/bin/sh"
        try:
            result = subprocess.run(
                [shell, "-lc", "command -v node"],
                check=False, capture_output=True, text=True, timeout=3,
            )
            return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def sdk_mcp_servers(self) -> list[Any]:
        servers = []
        for server in self.mcp_servers:
            if server.get("type", "stdio") != "stdio":
                raise ACPError("phase-1 supports only stdio MCP servers")
            env = [EnvVariable(name=str(k), value=str(v)) for k, v in (server.get("env") or {}).items()]
            servers.append(McpServerStdio(
                name=str(server["name"]),
                command=str(server["command"]),
                args=[str(arg) for arg in server.get("args", [])],
                env=env,
            ))
        return servers


@dataclass(frozen=True)
class ACPPermissionRequest:
    request_id: str
    session_id: str
    generation: int
    operation: Any
    workspace: str
    profile: str
    options: tuple[dict[str, Any], ...]
    kind: str = ""
    display: dict[str, Any] | None = None


class ACPPermissionBridge:
    """Fail-closed, first-answer-wins bridge for one generation."""

    def __init__(self, decide: Callable[[ACPPermissionRequest], Awaitable[str | None]] | None = None):
        self._decide = decide
        self._generation = 0
        self._session_id: str | None = None
        self._cancelled = False
        self._pending: set[asyncio.Future[str | None]] = set()

    def bind(self, session_id: str, generation: int) -> None:
        self._session_id = session_id
        self._generation = generation
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        for pending in tuple(self._pending):
            if not pending.done():
                pending.set_result(None)

    async def request(self, request: ACPPermissionRequest) -> str | None:
        if self._cancelled or request.session_id != self._session_id or request.generation != self._generation:
            return None
        offered: dict[str, dict[str, Any]] = {}
        for option in request.options:
            option_id = option.get("optionId")
            if (
                not isinstance(option_id, str) or not option_id
                or option_id in offered
                or option.get("kind") not in {
                    "allow_once", "reject_once", "allow_always", "reject_always",
                }
            ):
                return None
            offered[option_id] = option
        if not offered or self._decide is None:
            return None
        decision = asyncio.get_running_loop().create_future()
        self._pending.add(decision)
        task = None
        try:
            task = asyncio.create_task(self._decide(request))
            done, _ = await asyncio.wait(
                (task, decision), timeout=300, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                reject_once = [
                    option_id for option_id, option in offered.items()
                    if option.get("kind") == "reject_once"
                ]
                # A timeout selects the first offered one-shot rejection.
                # Without one, the owning runner cancels the turn.
                selected = reject_once[0] if reject_once else None
            else:
                selected = decision.result() if decision in done else task.result()
        except (asyncio.CancelledError, Exception):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            return None
        finally:
            self._pending.discard(decision)
            if task is not None and not task.done():
                task.cancel()
        if self._cancelled or request.session_id != self._session_id or request.generation != self._generation:
            return None
        if not isinstance(selected, str) or selected not in offered:
            return None
        if offered[selected].get("kind") not in {
            "allow_once", "reject_once", "allow_always", "reject_always",
        }:
            return None
        return selected


@dataclass(frozen=True)
class ACPAdmission:
    request_id: str
    source: str
    thread_id: str | None
    generation: int
    turn_id: int
    text: str
    future: asyncio.Future["ACPResult"]
    origin: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ACPResult:
    generation: int
    session_id: str
    text: str
    stop_reason: str | None
    updates: tuple[dict[str, Any], ...]
    end_frame: dict[str, Any]
    turn_id: int = 0
    request_id: str = ""
    source: str = ""
    thread_id: str | None = None


class _Client(acp.Client):
    def __init__(self, runner: "ACPRunner"):
        self.runner = runner

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await self.runner._session_update(session_id, update)

    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any) -> Any:
        return await self.runner._request_permission(session_id, tool_call, options)


class ACPRunner:
    """One SDK-owned ACP process/session with FIFO prompt queue."""

    def __init__(
        self,
        profile: ACPProfile,
        permission_bridge: ACPPermissionBridge | None = None,
        update_callback: Callable[[dict[str, Any]], None] | None = None,
        lifecycle_callback: Callable[[str, "ACPRunner"], Awaitable[None]] | None = None,
    ):
        self.profile = profile
        self.permission_bridge = permission_bridge or ACPPermissionBridge()
        self.update_callback = update_callback
        self.lifecycle_callback = lifecycle_callback
        self.stderr_callback: Callable[[bytes], None] | None = None
        self.generation = 0
        self._next_turn_id = 0
        self._active_turn_id = 0
        self._active_future: asyncio.Future[ACPResult] | None = None
        self._restart_before_next = False
        self._active_done = asyncio.Event()
        self._active_done.set()
        self._queue_release = asyncio.Event()
        self._queue_release.set()
        self._active_rpc_task: asyncio.Task[ACPResult] | None = None
        self._force_cancel = False
        self._closing = False
        self._cancel_lock = asyncio.Lock()
        self._cancel_hold = False
        # The turn a cancel was accepted for: its reply prose stops here.
        self._cancelled_turn_id = 0
        self._cancel_target: tuple[str | None, int | None] | None = None
        self._cancel_active_task: asyncio.Task[Any] | None = None
        self._permission_cancel_tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self.cancel_timeout = 30.0
        self.session_id: str | None = None
        self._connection: Any = None
        self._process: Any = None
        self._client = _Client(self)
        self._stderr_task: asyncio.Task[None] | None = None
        self._process_tracker: asyncio.Task[None] | None = None
        self._owned_processes: dict[int, psutil.Process] = {}
        self._adapter_pid: int | None = None
        self._process_nonce: str | None = None
        self._process_started_at: float | None = None
        self._process_username: str | None = None
        self._teardown_lock = asyncio.Lock()
        self._queue: asyncio.Queue[ACPAdmission | None] = asyncio.Queue(maxsize=10)
        self._worker: asyncio.Task[None] | None = None
        self._updates: list[dict[str, Any]] = []
        self._active_generation = 0
        self.state = "stopped"
        self.capabilities: dict[str, Any] = {}
        self.initialize_metadata: dict[str, Any] = {}
        # ACP session configuration options advertised by the harness. Keep
        # the wire-shaped dictionaries so callers can render and validate
        # options without coupling UI code to the ACP schema classes.
        self.config_options: list[dict[str, Any]] = []
        self.resolved_command: tuple[str, ...] = ()
        self.workspace = ""
        self.resume_mode = "fresh"
        self._resume_binding: ACPBinding | None = None
        self._replaying = False
        self._preface = ""
        self._preface_pending = True
        self._tool_cards: dict[str, dict[str, Any]] = {}
        self.last_stderr = ""

    @property
    def is_processing(self) -> bool:
        """True while active RPC, queued work, or cancellation drain is held."""
        return bool(
            self._active_rpc_task is not None
            or self._active_future is not None
            or not self._queue.empty()
            or self._cancel_hold
            or not self._active_done.is_set()
        )

    @staticmethod
    def _wire_config_options(result: Any) -> list[dict[str, Any]]:
        """Return ACP config options as JSON-compatible dictionaries."""
        options = getattr(result, "config_options", None)
        if options is None and isinstance(result, dict):
            options = result.get("configOptions") or result.get("config_options")
        if not options:
            return []
        rows: list[dict[str, Any]] = []
        for option in options:
            if hasattr(option, "model_dump"):
                row = option.model_dump(by_alias=True, exclude_none=True)
            elif isinstance(option, dict):
                row = dict(option)
            else:
                continue
            rows.append(row)
        return rows

    def advertised_config_options(self) -> list[dict[str, Any]]:
        """Return a copy of the active session's ACP config options."""
        return [dict(option) for option in self.config_options]

    async def set_config_option(self, config_id: str, value: str | bool) -> list[dict[str, Any]]:
        """Set one advertised ACP option and return the refreshed option set."""
        if self._connection is None or not self.session_id:
            raise ACPError("ACP session is not active")
        option = next((item for item in self.config_options if item.get("id") == config_id), None)
        if option is None:
            raise ACPError(f"ACP config option is not advertised: {config_id}")
        if option.get("type") == "select":
            values: set[str] = set()
            for candidate in option.get("options") or []:
                if isinstance(candidate, dict):
                    if "value" in candidate:
                        values.add(str(candidate["value"]))
                    for nested in candidate.get("options") or []:
                        if isinstance(nested, dict) and "value" in nested:
                            values.add(str(nested["value"]))
            if str(value) not in values:
                raise ACPError(f"ACP config value is not advertised: {config_id}={value}")
        result = await self._connection.set_config_option(config_id, self.session_id, value)
        self.config_options = self._wire_config_options(result)
        return self.advertised_config_options()

    async def _start_process(self) -> str:
        if self._live_owned_processes():
            raise ACPError("previous ACP processes still require cleanup")
        self._owned_processes.clear()
        self.state = "starting"
        if self.lifecycle_callback is not None:
            await self.lifecycle_callback("starting", self)
        command, env, cwd = self.profile.resolve()
        self.resolved_command = tuple(command)
        self.workspace = cwd
        # An inherited run marker also identifies children that fork, detach
        # and lose their parent wholly between tree observations. It is not
        # a credential; avoid names that vendor env policies filter as one.
        self._process_nonce = secrets.token_hex(32)
        env["KOLLAB_ACP_PROCESS_NONCE"] = self._process_nonce
        self._process_username = psutil.Process(os.getpid()).username()
        # A rapidly exiting root may disappear before psutil can read it.
        # Allow one second of kernel birth-time rounding in that fallback.
        self._process_started_at = time.time() - 1
        self._process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._adapter_pid = self._process.pid
        with contextlib.suppress(psutil.Error):
            owner = psutil.Process(self._process.pid)
            birth = owner.create_time()
            if (
                owner.ppid() == os.getpid() and self._process.returncode is None
                and owner.is_running()
                and owner.environ().get("KOLLAB_ACP_PROCESS_NONCE") == self._process_nonce
                and owner.is_running()
            ):
                self._process_started_at = birth
                self._owned_processes[owner.pid] = owner
        self._refresh_process_tree()
        self._process_tracker = asyncio.create_task(self._track_process_tree())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._connection = acp.connect_to_agent(self._client, self._process.stdin, self._process.stdout)
        initialized = await asyncio.wait_for(
            self._connection.initialize(
                protocol_version=1, client_info=Implementation(name="kollab", version=__version__)
            ), timeout=30,
        )
        wire = initialized.model_dump(by_alias=True) if hasattr(initialized, "model_dump") else {}
        if int(wire.get("protocolVersion", 1)) != 1:
            raise ACPError("adapter did not negotiate ACP protocol version 1")
        self.capabilities = dict(wire.get("agentCapabilities") or {})
        self.initialize_metadata = dict(wire.get("_meta") or {})
        self.state = "opening"
        self.config_options = []
        binding = self._resume_binding
        self.resume_mode = "fresh"
        session_caps = self.capabilities.get("sessionCapabilities") or {}
        if binding is not None and binding.matches(cwd, self.profile.name, tuple(command)):
            self.session_id = binding.session_id
            self._replaying = True
            try:
                if session_caps.get("resume") is not None:
                    resumed = await asyncio.wait_for(self._connection.resume_session(
                        session_id=binding.session_id, cwd=cwd,
                        mcp_servers=self.profile.sdk_mcp_servers(),
                    ), timeout=60)
                    self.config_options = self._wire_config_options(resumed)
                    self.resume_mode = "resumed"
                elif self.capabilities.get("loadSession"):
                    loaded = await asyncio.wait_for(self._connection.load_session(
                        session_id=binding.session_id, cwd=cwd,
                        mcp_servers=self.profile.sdk_mcp_servers(),
                    ), timeout=60)
                    self.config_options = self._wire_config_options(loaded)
                    self.resume_mode = "loaded"
            except Exception:
                # A vanished session or changed login is recoverable with new
                # context. No prompt is replayed or retried.
                self.resume_mode = "fresh"
            finally:
                self._replaying = False
        if self.resume_mode == "fresh":
            result = await asyncio.wait_for(self._connection.new_session(
                cwd=cwd, mcp_servers=self.profile.sdk_mcp_servers(),
            ), timeout=60)
            self.session_id = str(result.sessionId)
            self.config_options = self._wire_config_options(result)
        self._resume_binding = None
        self._preface_pending = self.resume_mode == "fresh"
        self.generation = self.generation + 1 if self.generation else 1
        self.state = "ready"
        if self.lifecycle_callback is not None:
            await self.lifecycle_callback("ready", self)
        return self.session_id

    def _request_error(self, exc: acp.RequestError, updates: list[dict[str, Any]] | None = None) -> ACPError:
        """Keep a public provider refusal without opaque data or stale stderr."""
        auth_hints = (
            "authenticate", "authentication", "oauth", "session expired", "not logged in",
            "please log in", "please sign in", "invalid credentials", "unauthorized",
            "unauthenticated", "invalid api key", "api key is missing", "credentials missing",
            "requires login",
        )
        refusal_hints = (
            "weekly limit", "daily limit", "monthly limit", "rate limit", "usage limit",
            "quota exceeded", "quota exhausted", "quota limit", "insufficient quota",
            "too many requests", *auth_hints,
        )
        message = str(exc).strip() or "Harness request failed"
        streamed = self._select_answer(updates or []).strip()
        if (
            message.lower() in {"internal error", "authentication required", "request failed"}
            and any(hint in streamed.lower() for hint in refusal_hints)
        ):
            message = streamed
        message = re.sub(r"\bsk-[A-Za-z0-9_*-]{8,}", "[redacted]", message)
        message = re.sub(r"(?i)\b(bearer|(?:access_)?token[=:])\s*\S+", r"\1 [redacted]", message)
        if exc.code == -32000 and any(hint in message.lower() for hint in auth_hints):
            message += " " + self.profile.LOGIN_GUIDANCE.get(self.profile.name, "Log in using this harness's CLI.")
        return ACPError(f"{self.profile.name}: {message[:2000]}", code=exc.code)

    async def start(self, binding: ACPBinding | None = None) -> str:
        self._closing = False
        self._resume_binding = binding
        try:
            session_id = await self._start_process()
        except BaseException as exc:
            self.state = "failed"
            await self._terminate_process()
            if isinstance(exc, acp.RequestError):
                raise self._request_error(exc) from exc
            raise
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain_queue())
        return session_id

    async def clear(self) -> str:
        """Replace the session and its MCP process tree without replay."""
        await self.close()
        self._preface_pending = True
        return await self.start()

    async def _drain_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while chunk := await self._process.stderr.readline():
            self.last_stderr = chunk.decode(errors="replace").strip()[-1000:]
            if self.stderr_callback is not None:
                self.stderr_callback(chunk)
            else:
                sys.stderr.buffer.write(b"[acp] " + chunk)
                sys.stderr.buffer.flush()

    def _refresh_process_tree(self) -> None:
        """Retain birth-checked descendants, including detached/orphaned roots."""
        for owner in tuple(self._owned_processes.values()):
            with contextlib.suppress(psutil.Error):
                if not owner.is_running():
                    self._owned_processes.pop(owner.pid, None)
        for owner in tuple(self._owned_processes.values()):
            with contextlib.suppress(psutil.Error):
                if owner.ppid() in self._owned_processes:
                    continue
                for child in owner.children(recursive=True):
                    child.create_time()
                    self._owned_processes[child.pid] = child

    async def _track_process_tree(self) -> None:
        while self._owned_processes:
            self._refresh_process_tree()
            await asyncio.sleep(0.1)

    def _recover_tagged_processes(self) -> None:
        """Recover same-user newborns by this run's inherited marker only."""
        if self._process_nonce is None or self._process_started_at is None:
            return
        for owner in psutil.process_iter(("pid", "create_time", "username")):
            # psutil's macOS environ() raises SystemError, not psutil.Error,
            # for a process that exits while its environment is read.
            with contextlib.suppress(psutil.Error, SystemError):
                birth = owner.info.get("create_time")
                if (
                    owner.info.get("username") != self._process_username
                    or birth is None or birth < self._process_started_at
                ):
                    continue
                if self._owned_processes.get(owner.pid) == owner:
                    continue
                # Compare only the marker, without retaining/logging an
                # environment. Recheck birth identity across that read.
                if (
                    owner.is_running()
                    and owner.environ().get("KOLLAB_ACP_PROCESS_NONCE") == self._process_nonce
                    and owner.is_running()
                ):
                    owner.create_time()
                    self._owned_processes[owner.pid] = owner

    def _live_owned_processes(self) -> list[psutil.Process]:
        self._refresh_process_tree()
        # Do the process-wide marker lookup at lifecycle boundaries, rather
        # than reading unrelated environments on every 100ms tree sample.
        self._recover_tagged_processes()
        self._refresh_process_tree()
        alive: list[psutil.Process] = []
        for owner in tuple(self._owned_processes.values()):
            try:
                if not owner.is_running():
                    continue
                if owner.status() == psutil.STATUS_ZOMBIE:
                    # asyncio owns the adapter's waitpid. Reap a descendant
                    # only when it is still the recorded process identity.
                    if owner.pid != self._adapter_pid:
                        with contextlib.suppress(psutil.Error):
                            owner.wait(timeout=0)
                    continue
                alive.append(owner)
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                # An unreadable recorded process is not proof of exit.
                alive.append(owner)
        return alive

    def admit(
        self,
        text: str,
        *,
        request_id: str,
        source: str,
        thread_id: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> ACPAdmission:
        if not self.session_id or self._connection is None:
            raise ACPError("ACP runner is not started")
        if not text.strip():
            raise ValueError("prompt must not be empty")
        generation = self.generation
        self._next_turn_id += 1
        turn_id = self._next_turn_id
        future: asyncio.Future[ACPResult] = asyncio.get_running_loop().create_future()
        admission = ACPAdmission(request_id, source, thread_id, generation, turn_id, text, future, dict(origin or {}))
        try:
            self._queue.put_nowait(admission)
        except asyncio.QueueFull as exc:
            raise ACPError("ACP prompt queue is full") from exc
        return admission

    async def prompt(self, text: str) -> ACPResult:
        admission = self.admit(text, request_id=f"turn-{self._next_turn_id + 1}", source="rpc")
        return await admission.future

    async def _drain_queue(self) -> None:
        while True:
            await self._queue_release.wait()
            item = await self._queue.get()
            if item is None:
                if self._closing:
                    return
                continue
            admission = item
            generation = admission.generation
            future = admission.future
            if future.cancelled():
                continue
            if generation != self.generation:
                generation = self.generation
            self._active_future = future
            self._active_context = admission
            self._active_done.clear()
            self._queue_release.clear()
            self._active_rpc_task = asyncio.create_task(
                self._run_prompt(admission, generation)
            )
            try:
                result = await self._active_rpc_task
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if not self._force_cancel:
                    raise
                if not future.done():
                    future.set_exception(ACPError("ACP turn cancelled"))
            except Exception as exc:
                self.state = "failed"
                self._restart_before_next = True
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._active_rpc_task = None
                self._active_future = None
                self._active_done.set()
                # Advance the turn fence only after the RPC response and all
                # reader-delivered updates have drained.
                if self.generation == generation:
                    self.generation += 1
            try:
                if self._restart_before_next and not self._closing:
                    self._restart_before_next = False
                    await self._restart_process()
            except Exception as exc:
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is None:
                        continue
                    if not item.future.done():
                        item.future.set_exception(exc)
            finally:
                if not self._cancel_hold:
                    self._queue_release.set()

    async def _restart_process(self) -> None:
        """Replace the owned ACP process, even if SDK close is stuck."""
        if self._closing:
            return
        try:
            # _terminate_process bounds connection.close and always tears down
            # the process group/stderr task before a fresh session is started.
            await self._terminate_process()
            if self._closing:
                return
            await self._start_process()
            if self._closing:
                await self._terminate_process()
        except BaseException:
            # Never leave a half-restarted process or connection behind when
            # initialization/new_session fails (including cancellation).
            await self._terminate_process()
            raise

    async def _run_prompt(self, admission: ACPAdmission, generation: int) -> ACPResult:
        turn_id, text = admission.turn_id, admission.text
        self._active_generation = generation
        self._active_turn_id = turn_id
        self.state = "prompting"
        self._updates = []
        self._tool_cards = {}
        self.permission_bridge.bind(self.session_id or "", generation)
        if self._preface_pending and self._preface:
            text = self._preface + "\n\n" + text
            self._preface_pending = False
        if self.update_callback is not None:
            self.update_callback({"kind": "turn_start", "request_id": admission.request_id,
                                  "turn_id": turn_id, "source": admission.source,
                                  "thread_id": admission.thread_id, "generation": generation})
        try:
            response = await self._connection.prompt(
                session_id=self.session_id,
                prompt=[TextContentBlock(type="text", text=text)],
            )
            await asyncio.sleep(0)
            stop_reason = str(response.stopReason) if response else None
            answer = self._select_answer(self._updates)
            return ACPResult(
                generation=generation,
                session_id=self.session_id or "",
                text=answer,
                stop_reason=stop_reason,
                updates=tuple(self._updates),
                end_frame={
                    "type": "turn_end",
                    "generation": generation,
                    "turnId": turn_id,
                    "sessionId": self.session_id,
                    "stopReason": stop_reason,
                    "outcome": "success" if stop_reason == "end_turn" else "failed",
                },
                turn_id=turn_id,
                request_id=admission.request_id,
                source=admission.source,
                thread_id=admission.thread_id,
            )
        except Exception as exc:
            exit_code = getattr(self._process, "returncode", None)
            if isinstance(exc, acp.RequestError):
                raise self._request_error(exc, self._updates) from exc
            if exit_code is not None:
                line = f"{self.profile.name}: harness exited ({type(exc).__name__}, exit={exit_code}); it may have stopped mid-change"
            else:
                line = f"{self.profile.name}: harness request failed ({type(exc).__name__})"
            raise ACPError(line) from exc
        finally:
            if self.update_callback is not None:
                self.update_callback({"kind": "turn_finished", "request_id": admission.request_id,
                                      "turn_id": turn_id, "source": admission.source})
            self.permission_bridge.cancel()
            self._active_generation = 0
            self._active_turn_id = 0
            if self.state != "failed" and not self._closing:
                self.state = "ready"

    @property
    def binding(self) -> ACPBinding | None:
        if not self.session_id:
            return None
        return ACPBinding(self.session_id, self.workspace, self.profile.name,
                          self.resolved_command, dict(self.capabilities))

    @property
    def supports_steering(self) -> bool:
        return bool((self.initialize_metadata.get("steering") or {}).get("supported"))

    async def steer(self, text: str, *, origin: dict[str, Any], thread_id: str | None = None) -> bool:
        active = getattr(self, "_active_context", None)
        if not self._active_turn_id or not active or not self.supports_steering:
            return False
        human = bool(origin.get("operator"))
        same_requester = (
            bool(thread_id) and thread_id == active.thread_id
            and origin.get("sender") == active.origin.get("sender")
        )
        if not human and not same_requester:
            return False
        try:
            await asyncio.wait_for(self._connection.ext_method("session/steering", {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": text}],
            }), timeout=10)
            return True
        except Exception:
            return False

    def cancel_queued(self, request_id: str) -> bool:
        for admission in tuple(self._queue._queue):
            if admission is not None and admission.request_id == request_id:
                # This synchronous removal cannot race the queue consumer.
                # Release capacity before settling the future, preserving
                # every remaining item's order and the active owner's RPC.
                self._queue._queue.remove(admission)
                self._queue.task_done()
                self._queue._wakeup_next(self._queue._putters)
                if not admission.future.done():
                    admission.future.cancel()
                return True
        return False

    @staticmethod
    def _select_answer(updates: list[dict[str, Any]]) -> str:
        """Select the final assistant message while retaining every stream update."""
        text_updates = [item for item in updates if item.get("kind") == "text"]
        if not text_updates:
            return ""
        identified = [item for item in text_updates if item.get("message_id")]
        if identified:
            message_id = str(identified[-1]["message_id"])
            return "".join(
                str(item.get("text", ""))
                for item in text_updates
                if str(item.get("message_id", "")) == message_id
            )
        tool_indexes: list[int] = []
        active_tool_id: str | None = None
        for index, item in enumerate(updates):
            kind = str(item.get("kind", "")).lower()
            if kind not in {"tool_call", "tool_call_update", "tool_output", "tool_result"}:
                continue
            payload = item.get("payload") or {}
            tool_id = payload.get("toolCallId", payload.get("tool_call_id", payload.get("id")))
            tool_id = str(tool_id) if tool_id else None
            # Progress/output updates for the same tool do not start a new
            # assistant message boundary; a subsequent call does.
            if kind == "tool_call_update" and tool_id and tool_id == active_tool_id:
                continue
            if kind in {"tool_output", "tool_result"} and tool_id and tool_id == active_tool_id:
                continue
            tool_indexes.append(index)
            active_tool_id = tool_id
        if tool_indexes:
            boundaries = [-1, *tool_indexes, len(updates)]
            # Consecutive tools can leave empty text segments. Walk back to
            # the last assistant segment without merging separate messages.
            for index in range(len(boundaries) - 1, 0, -1):
                segment = updates[boundaries[index - 1] + 1:boundaries[index]]
                answer = "".join(
                    str(item.get("text", ""))
                    for item in segment
                    if item.get("kind") == "text"
                )
                if answer:
                    return answer
            return ""
        return "".join(str(item.get("text", "")) for item in text_updates)

    async def _session_update(self, session_id: str, update: Any) -> None:
        if self._replaying or session_id != self.session_id or self._active_generation != self.generation:
            return
        payload = update.model_dump(by_alias=True) if hasattr(update, "model_dump") else {}
        kind = payload.get("sessionUpdate", payload.get("session_update", payload.get("type", "unknown")))
        if (kind in {"agent_message_chunk", "agent_thought_chunk"}
                and self._active_turn_id and self._active_turn_id == self._cancelled_turn_id):
            # Prose flushed after the cancel was accepted was never shown
            # and is not part of the reply that stays. Tool and plan
            # updates still land: they are the state the turn stopped in.
            return
        if kind == "config_option_update":
            self.config_options = self._wire_config_options(payload)
        if kind == "agent_message_chunk":
            content = payload.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else getattr(content, "text", "")
            update_data = {"kind": "text", "text": str(text)}
            message_id = payload.get("messageId", payload.get("message_id"))
            if message_id:
                update_data["message_id"] = str(message_id)
        else:
            update_data = {"kind": str(kind), "payload": payload}
        if kind in {"tool_call", "tool_call_update"}:
            tool_id = str(payload.get("toolCallId") or "")
            merged = self._tool_cards.setdefault(tool_id, {})
            merged.update({key: value for key, value in payload.items() if value is not None})
            update_data["payload"] = dict(merged)
        update_data.update({
            "request_id": getattr(getattr(self, "_active_context", None), "request_id", ""),
            "turn_id": self._active_turn_id,
            "source": getattr(getattr(self, "_active_context", None), "source", ""),
            "thread_id": getattr(getattr(self, "_active_context", None), "thread_id", None),
        })
        self._updates.append(update_data)
        if self.update_callback is not None:
            self.update_callback(dict(update_data))

    def _merged_tool_payload(self, raw_tool: dict[str, Any], request_id: str) -> dict[str, Any]:
        """Merge id-only permission cards with a prior structured tool call."""
        merged: dict[str, Any] = {}
        for update in self._updates:
            payload = update.get("payload")
            if not isinstance(payload, dict):
                continue
            tool_id = payload.get("toolCallId", payload.get("tool_call_id", payload.get("id")))
            if tool_id is not None and str(tool_id) == request_id:
                # Permission cards can be id-only or contain explicit nulls;
                # never let a sparse update erase an earlier structured field.
                merged.update({key: value for key, value in payload.items() if value is not None})
        merged.update({key: value for key, value in raw_tool.items() if value is not None})
        return merged

    @staticmethod
    def _permission_operation(raw_tool: dict[str, Any], workspace: str) -> Any:
        """Derive a grant key from structured ACP tool-call fields only."""
        kind = str(raw_tool.get("kind") or "").lower()
        raw_input = raw_tool.get("rawInput", raw_tool.get("raw_input"))
        if not isinstance(raw_input, dict):
            raw_input = {}
        if kind == "execute":
            command = raw_input.get("command")
            if isinstance(command, str):
                if any(operator in command for operator in ("&&", "||", ";", "|")):
                    return None
                return {"kind": kind, "command": command}
            if isinstance(command, (list, tuple)) and all(isinstance(item, str) for item in command):
                return {"kind": kind, "command": list(command)}
            return None
        if kind not in {"edit", "delete", "move"}:
            return None
        locations = raw_tool.get("locations", raw_input.get("locations"))
        if not isinstance(locations, (list, tuple)) or not locations:
            return None
        workspace_root = os.path.realpath(workspace)
        canonical = []
        for location in locations:
            path = location.get("path") if isinstance(location, dict) else getattr(location, "path", None)
            if not isinstance(path, str) or not path:
                return None
            resolved = os.path.realpath(os.path.expanduser(path))
            try:
                if os.path.commonpath((workspace_root, resolved)) != workspace_root:
                    return None
            except ValueError:
                return None
            canonical.append(resolved)
        return {"kind": kind, "paths": canonical}


    @staticmethod
    def _permission_display(raw_tool: dict[str, Any]) -> dict[str, Any]:
        """Build non-authoritative card text from the merged ACP tool payload."""
        display: dict[str, Any] = {}
        title = raw_tool.get("title") or raw_tool.get("name") or raw_tool.get("toolName")
        if title:
            display["title"] = str(title)
        kind = raw_tool.get("kind")
        if kind:
            display["display_kind"] = str(kind)
        raw_input = raw_tool.get("rawInput", raw_tool.get("raw_input"))
        if not isinstance(raw_input, dict):
            raw_input = {}
        command = raw_input.get("command", raw_tool.get("command"))
        if isinstance(command, (list, tuple)):
            display["command"] = [str(part) for part in command]
        elif isinstance(command, str):
            display["command"] = command
        locations = raw_tool.get("locations", raw_input.get("locations"))
        if isinstance(locations, (list, tuple)):
            paths = []
            for location in locations:
                path = location.get("path") if isinstance(location, dict) else getattr(location, "path", None)
                if isinstance(path, str) and path:
                    paths.append(path)
            if paths:
                display["paths"] = paths
        return display

    async def _request_permission(self, session_id: str, tool_call: Any, options: list[Any]) -> Any:
        if session_id != self.session_id or not self._active_generation:
            return acp.RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        generation = self._active_generation
        turn_id = self._active_turn_id
        active = getattr(self, "_active_context", None)
        source = getattr(active, "source", "acp")
        thread_id = getattr(active, "thread_id", None)
        option_data = []
        for option in options:
            raw = option.model_dump() if hasattr(option, "model_dump") else {}
            option_data.append({
                **raw,
                "optionId": raw.get("optionId", raw.get("option_id")),
                "kind": raw.get("kind"),
            })
        if hasattr(tool_call, "model_dump"):
            try:
                raw_tool = tool_call.model_dump(by_alias=True)
            except TypeError:
                raw_tool = tool_call.model_dump()
        else:
            raw_tool = {}
        request_id = str(getattr(tool_call, "toolCallId", raw_tool.get("toolCallId", "permission")))
        raw_tool = self._merged_tool_payload(raw_tool, request_id)
        workspace = os.path.realpath(self.profile.workspace or self.profile.cwd or os.getcwd())
        operation = self._permission_operation(raw_tool, workspace)
        display = self._permission_display(raw_tool)
        request = ACPPermissionRequest(
            request_id=request_id,
            session_id=session_id,
            generation=generation,
            operation=operation,
            workspace=os.path.realpath(self.profile.workspace or self.profile.cwd or os.getcwd()),
            profile=self.profile.name,
            options=tuple(option_data),
            kind=str(raw_tool.get("kind") or "").lower(),
            display=display,
        )
        self.state = "awaiting"
        if self.update_callback is not None:
            self.update_callback({
                "kind": "permission_request",
                "request_id": request.request_id,
                "turn_id": turn_id,
                "source": source,
                "thread_id": thread_id,
                "payload": {
                    "operation": operation,
                    "workspace": request.workspace,
                    "profile": request.profile,
                    "options": option_data,
                    "waiting": True,
                    "kind": request.kind,
                    **display,
                },
            })
        try:
            selected = await self.permission_bridge.request(request)
        except (asyncio.CancelledError, Exception):
            selected = None
        if session_id != self.session_id or generation != self._active_generation or generation != self.generation:
            return acp.RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        if selected is None:
            self._schedule_permission_cancel(session_id, generation)
        elif not self._closing:
            self.state = "prompting"
        if self.update_callback is not None:
            self.update_callback({"kind": "permission_resolved", "request_id": request.request_id,
                                  "turn_id": turn_id, "source": source, "thread_id": thread_id,
                                  "payload": {"waiting": False, "tool_id": request.request_id}})
        if selected is None:
            return acp.RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return acp.RequestPermissionResponse(outcome=AllowedOutcome(optionId=selected, outcome="selected"))

    def _schedule_permission_cancel(self, session_id: str, generation: int) -> None:
        """Reserve this owner synchronously; never await cancel inside an SDK callback."""
        key = (session_id, generation)
        if self._closing or key in self._permission_cancel_tasks:
            return
        if not self.prepare_cancel(expected_session_id=session_id, expected_generation=generation):
            return
        task = asyncio.create_task(self.cancel(
            expected_session_id=session_id, expected_generation=generation,
        ))
        self._permission_cancel_tasks[key] = task

        def settled(completed: asyncio.Task[None]) -> None:
            if self._permission_cancel_tasks.get(key) is completed:
                self._permission_cancel_tasks.pop(key, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(settled)


    def _mark_cancelled(self) -> None:
        """Remember which turn a cancel was accepted for (see ``_session_update``)."""
        self._cancelled_turn_id = getattr(getattr(self, "_active_context", None), "turn_id", 0)

    def prepare_cancel(
        self,
        *,
        expected_session_id: str | None = None,
        expected_generation: int | None = None,
    ) -> bool:
        """Reserve the active turn's queue boundary before async cancellation."""
        if expected_session_id is not None and expected_session_id != self.session_id:
            return False
        if expected_generation is not None and expected_generation != self.generation:
            return False
        active = self._active_rpc_task
        if active is None or active.done():
            return False
        self._cancel_target = (expected_session_id, expected_generation)
        self._cancel_active_task = active
        self._cancel_hold = True
        self._mark_cancelled()
        self._queue_release.clear()
        return True

    async def cancel(
        self,
        *,
        expected_session_id: str | None = None,
        expected_generation: int | None = None,
    ) -> None:
        async with self._cancel_lock:
            target = self._cancel_target
            if target is None:
                if expected_session_id is not None and expected_session_id != self.session_id:
                    return
                if expected_generation is not None and expected_generation != self.generation:
                    return
            elif target != (expected_session_id, expected_generation):
                return
            self.permission_bridge.cancel()
            self.state = "cancelling"
            active = self._cancel_active_task or self._active_rpc_task
            if active is None and self._cancel_target is None:
                return
            # Hold the worker at the queue boundary before sending cancel so a
            # completion cannot release B between our snapshot and the RPC.
            self._cancel_hold = True
            if active is not None:
                self._mark_cancelled()
            self._queue_release.clear()
            if self._active_future and not self._active_future.done():
                self._active_future.set_exception(ACPError("ACP turn cancelled"))
            deadline = asyncio.get_running_loop().time() + self.cancel_timeout
            cancel_task = None
            try:
                if self.session_id and self._connection:
                    cancel_task = asyncio.create_task(self._connection.cancel(session_id=self.session_id))
                    remaining = max(0.01, deadline - asyncio.get_running_loop().time())
                    await asyncio.wait_for(asyncio.shield(cancel_task), timeout=remaining)
                remaining = max(0.01, deadline - asyncio.get_running_loop().time())
                if active is None:
                    cancel_result = None
                elif active.done():
                    cancel_result = active.result()
                else:
                    cancel_result = await asyncio.wait_for(asyncio.shield(active), timeout=remaining)
                if getattr(cancel_result, "stop_reason", None) != "cancelled":
                    self._restart_before_next = True
                    self.generation += 1
                # A protocol cancelled response and reader drain preserve the
                # process/session; the worker advances the turn fence.
            except asyncio.TimeoutError:
                self.generation += 1
                self._restart_before_next = True
                self._force_cancel = True
                try:
                    await self._terminate_process()
                    try:
                        await asyncio.wait_for(asyncio.shield(active), timeout=2)
                    except asyncio.TimeoutError:
                        active.cancel()
                        await asyncio.gather(active, return_exceptions=True)
                    except Exception:
                        pass
                finally:
                    self._force_cancel = False
            finally:
                if cancel_task is not None and not cancel_task.done():
                    cancel_task.cancel()
                    await asyncio.gather(cancel_task, return_exceptions=True)
                self._cancel_hold = False
                self._cancel_target = None
                self._cancel_active_task = None
                self._queue_release.set()

    async def _terminate_process(self) -> None:
        async with self._teardown_lock:
            connection, process, stderr_task = self._connection, self._process, self._stderr_task
            tracker = self._process_tracker
            self._connection = None
            self._process = None
            self._stderr_task = None
            self._process_tracker = None
            close_task = None
            session_id = self.session_id
            session_caps = self.capabilities.get("sessionCapabilities") or {}

            async def close_connection() -> None:
                try:
                    if session_id and session_caps.get("close") is not None:
                        with contextlib.suppress(Exception):
                            await connection.close_session(session_id=session_id)
                finally:
                    if process is not None and process.stdin is not None:
                        process.stdin.close()
                    await connection.close()

            try:
                if connection is not None:
                    close_task = asyncio.create_task(close_connection())
                    close_task.add_done_callback(
                        lambda task: None if task.cancelled() else task.exception()
                    )
                    # A dead root can leave no live tree to wait on. Still
                    # let SDK close run and reject its pending callbacks.
                    await asyncio.sleep(0)
                elif process is not None and process.stdin is not None:
                    process.stdin.close()
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 5
                # Keep discovering children during session/SDK close. An
                # observed child remains owned after setsid or parent death.
                while self._live_owned_processes() and loop.time() < deadline:
                    await asyncio.sleep(min(0.1, max(0, deadline - loop.time())))
                if close_task is not None and not close_task.done():
                    close_task.cancel()
                if process is not None and process.stdin is not None:
                    process.stdin.close()
                deadline = loop.time() + 5
                terminated: set[psutil.Process] = set()
                while alive := self._live_owned_processes():
                    for owner in reversed(alive):
                        if owner not in terminated:
                            with contextlib.suppress(psutil.Error):
                                owner.terminate()
                            terminated.add(owner)
                    if loop.time() >= deadline:
                        break
                    await asyncio.sleep(min(0.1, max(0, deadline - loop.time())))
            finally:
                # All exit paths, including cancellation of teardown, signal
                # only retained PID/birth identities. Never signal by PGID:
                # the dead adapter's PID may now name an unrelated group.
                for owner in reversed(self._live_owned_processes()):
                    with contextlib.suppress(psutil.Error):
                        owner.kill()
                if close_task is not None and not close_task.done():
                    close_task.cancel()
                if process is not None and process.stdin is not None:
                    process.stdin.close()
                if process is not None:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=1)
                deadline = asyncio.get_running_loop().time() + 1
                while alive := self._live_owned_processes():
                    for owner in reversed(alive):
                        with contextlib.suppress(psutil.Error):
                            owner.kill()
                    if asyncio.get_running_loop().time() >= deadline:
                        break
                    await asyncio.sleep(0.05)
                if tracker is not None:
                    tracker.cancel()
                    await asyncio.gather(tracker, return_exceptions=True)
                if stderr_task is not None:
                    stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
                if close_task is not None:
                    await asyncio.wait({close_task}, timeout=0.1)
                survivors = self._live_owned_processes()
                unreaped_adapter = process is not None and process.returncode is None
                if not survivors and not unreaped_adapter:
                    self._owned_processes.clear()
                    self._adapter_pid = None
                    self._process_nonce = None
                    self._process_started_at = None
                    self._process_username = None
                else:
                    detail = ", ".join(str(owner.pid) for owner in survivors)
                    if unreaped_adapter:
                        detail += f" (adapter {process.pid} not reaped)"
                    raise ACPError(
                        "ACP owned process cleanup incomplete: " + detail
                    )

    async def close(self) -> None:
        self._closing = True
        self.state = "closed"
        self.permission_bridge.cancel()
        current_task = asyncio.current_task()
        cancel_tasks = [
            task for task in self._permission_cancel_tasks.values()
            if task is not current_task
        ]
        for task in cancel_tasks:
            task.cancel()
        self._queue_release.set()

        def settle_queued() -> None:
            while True:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is not None and not item.future.done():
                    item.future.set_exception(ACPError("ACP runner closed"))

        settle_queued()
        worker = self._worker
        # Wake an idle consumer without relying on cancellation of a Queue.get
        # waiter, which can remain pending under SDK callbacks.
        if worker is not None and not worker.done():
            self._queue.put_nowait(None)
        if self._active_future is not None and not self._active_future.done():
            self._active_future.set_exception(ACPError("ACP runner closed"))
        try:
            if worker is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(worker), timeout=2)
                except asyncio.TimeoutError:
                    await self._terminate_process()
                    worker.cancel()
                    try:
                        await asyncio.wait_for(asyncio.shield(worker), timeout=1)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
        finally:
            try:
                await self._terminate_process()
            finally:
                if cancel_tasks:
                    await asyncio.gather(*cancel_tasks, return_exceptions=True)
                self._worker = None
                self.session_id = None
                # A cancelled consumer may never read its wake-up sentinel;
                # admissions accepted before process close must also settle.
                settle_queued()
