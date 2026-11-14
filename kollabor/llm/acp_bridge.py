"""Outbound ACP process, session, turn and permission ownership."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar

import acp
import psutil
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    EnvVariable,
    ImageContentBlock,
    Implementation,
    McpServerStdio,
    TextContentBlock,
)

from kollabor.version import __version__

from .acp_state import ACPBinding


class ACPError(RuntimeError):
    """A protocol, lifecycle, or adapter error.

    ``error_class`` carries the boundary classification (``authentication``,
    ``refusal``, ``queue_full``, ``images_unsupported``, ...) so downstream
    consumers never parse the rendered message text (#549, #720).
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        error_class: str | None = None,
        cause: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.error_class = error_class
        # Safe cause floor: the original exception's TYPE NAME only, never
        # its message (#720 — the same floor as StructuredErrorInfo.cause).
        self.cause = cause


class ACPSteeringUnconfirmed(ACPError):
    """A steer was sent but its receipt never arrived, so it may have landed."""


def _on_windows() -> bool:
    return sys.platform == "win32"


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
            "install": (
                "npm install --prefix ~/.local/share/kollab/acp-adapters/claude-0.89.0 --no-save "
                "@agentclientprotocol/claude-agent-acp@0.89.0"
            ),
        },
        "codex-cli": {
            "command": ["codex-acp"],
            "managed_dir": "codex-2.5.0",
            # Existing device-local installs may still only have this adapter.
            "previous_managed_dirs": ("codex-2.1.1",),
            "install": (
                "npm install --prefix ~/.local/share/kollab/acp-adapters/codex-2.5.0 --no-save "
                "@agentclientprotocol/codex-acp@2.5.0"
            ),
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

    def _cache_path(self, *, create_parent: bool = True) -> Path:
        root = Path(os.environ.get("KOLLAB_CONFIG_DIR", "~/.kollab")).expanduser()
        path = root / "acp" / "resolved.json"
        if create_parent:
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
        path = str(
            Path.home() / ".local" / "share" / "kollab" / "acp-adapters"
            / builtin["managed_dir"] / "node_modules" / ".bin" / self.command[0]
        )
        return path + ".cmd" if _on_windows() else path

    def _previous_managed_adapter_paths(self, *, for_binding: bool = False) -> tuple[str, ...]:
        builtin = self.BUILTIN_PROFILES.get(self.name)
        if not builtin or (not for_binding and self.command[0] != builtin["command"][0]):
            return ()
        root = Path.home() / ".local" / "share" / "kollab" / "acp-adapters"
        paths = (str(root / directory / "node_modules" / ".bin" / builtin["command"][0])
                 for directory in builtin.get("previous_managed_dirs", ()))
        # Both spellings can occur in a saved binding, even when the current
        # platform launches only one of them.
        return tuple(path for base in paths for path in (base, base + ".cmd"))

    def _retargets_previous_binding(
        self, binding: ACPBinding, cwd: str, command: tuple[str, ...]
    ) -> bool:
        """Identify an old managed Codex binding whose executable changed."""
        if (self.name != "codex-cli" or binding.profile != self.name
                or binding.workspace != os.path.realpath(cwd)
                or not binding.command or not command):
            return False
        def normalize(path: str) -> str:
            return os.path.normcase(os.path.abspath(path))
        return (
            normalize(binding.command[0]) in {
                normalize(path) for path in self._previous_managed_adapter_paths(for_binding=True)
            }
            and (normalize(command[0]) != normalize(binding.command[0])
                 or command[1:] != binding.command[1:])
        )

    def _matches_managed_upgrade(
        self, binding: ACPBinding, cwd: str, command: tuple[str, ...]
    ) -> bool:
        """Allow only identified Codex adapters to try the old session."""
        return (self._retargets_previous_binding(binding, cwd, command)
                and binding.command[1:] == command[1:]
                and _adapter_package_identity(command[0]) in {
                    ("@agentclientprotocol/codex-acp", "2.1.1"),
                    ("@agentclientprotocol/codex-acp", "2.5.0"),
                })

    def resolve(
        self, *, persist_cache: bool = True
    ) -> tuple[tuple[str, ...], dict[str, str], str]:
        """Resolve the executable and environment, optionally without writing local state."""
        cache_path = self._cache_path(create_parent=persist_cache)
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
        previous_managed = self._previous_managed_adapter_paths()
        preferred_managed = self._managed_adapter_path()
        if (_on_windows() and executable
                and os.path.normcase(os.path.abspath(executable)) in {
                    os.path.normcase(os.path.abspath(path)) for path in previous_managed
                }
                and not executable.lower().endswith(".cmd")
                and self._is_executable_file(executable + ".cmd")):
            executable += ".cmd"
        previous_candidates = tuple(
            path for path in previous_managed
            if path.lower().endswith(".cmd") == _on_windows()
        )
        # A cached path to the formerly managed pin is not an explicit custom
        # command. Adopt the new pin when present, but honor a shell/PATH
        # selection first. Paths cached outside our managed directory stay put.
        if (cached_command == self.command[0] and executable
                and os.path.normcase(os.path.abspath(executable)) in {
                    os.path.normcase(os.path.abspath(path)) for path in previous_managed
                } and self._is_executable_file(preferred_managed)):
            executable = next(
                (candidate for candidate in (self._login_shell_path(), shutil.which(self.command[0]) or "")
                 if self._is_executable_file(candidate)),
                preferred_managed,
            )
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
                        preferred_managed,
                        *previous_candidates,
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
        if persist_cache:
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
    images: tuple[dict[str, str], ...] = field(default_factory=tuple, repr=False)


def _turn_usage_payload(usage: Any) -> dict[str, int] | None:
    """Normalize a prompt-response Usage object into snake_case token totals.

    A response may include adapter-reported prompt usage. The adapter determines
    the coverage of its report, which is not necessarily all sidechain or
    subagent usage (#495).
    """
    if usage is None:
        return None
    payload: dict[str, int] = {}
    for name in (
        "total_tokens", "input_tokens", "output_tokens", "thought_tokens",
        "cached_read_tokens", "cached_write_tokens",
    ):
        value = getattr(usage, name, None)
        if value is not None:
            payload[name] = int(value)
    return payload or None


# SHA-256 of the complete npm cmd-shim 7 and 9 Node launcher templates after
# replacing their verified relative bin target with <npm-target>. Any changed
# wrapper, extra command, or unknown template remains an unverified adapter.
_NPM_NODE_SHIM_HASHES = {
    "": {
        "bebad2186d5fd4fb2875d002f3b4b78db2f5f9963f534c24fd20e49ed65e1ffc",
        "6a2ce219acc995b55e641a670a9ee75d90897e325659bfbb73dfdfb270353ad0",
    },
    ".cmd": {
        "ff5f8b1d8275cf4fa515d4a768aa4c0793611e77ac34b5756f26537f831ce645",
        "2b8447b91b7c62b3fd4786926e15e5c55bdaa9d3364925be946b11bcc4ed9cfb",
    },
}


def _npm_shim_identity(executable: str) -> tuple[str, str] | None:
    """Identify only an intact npm-generated Node shim for a known package."""
    try:
        shim = Path(executable).resolve(strict=True)
        suffix = ".cmd" if shim.suffix.lower() == ".cmd" else ""
        binary_name = shim.name[:-4] if suffix else shim.name
        packages = {
            "codex-acp": "@agentclientprotocol/codex-acp",
            "claude-agent-acp": "@agentclientprotocol/claude-agent-acp",
        }
        package_name = packages.get(binary_name.lower())
        if not package_name or shim.stat().st_size > 65536:
            return None
        # npm places project shims in node_modules/.bin and Windows global
        # shims beside node_modules itself.
        parent = shim.parent
        modules = parent.parent if parent.name.lower() == ".bin" else parent / "node_modules"
        if modules.name.lower() != "node_modules":
            return None
        package = modules / package_name
        manifest = package / "package.json"
        if manifest.stat().st_size > 65536:
            return None
        data = json.loads(manifest.read_text())
        if not isinstance(data, dict) or data.get("name") != package_name:
            return None
        binary = data.get("bin")
        target = binary.get(binary_name.lower()) if isinstance(binary, dict) else binary
        if not isinstance(target, str) or Path(target).is_absolute():
            return None
        actual = package / target
        if not actual.resolve(strict=True).is_relative_to(package.resolve(strict=True)):
            return None
        identity = _direct_adapter_package_identity(str(actual))
        if identity is None or identity != (package_name, data.get("version")):
            return None
        relative = os.path.relpath(actual, parent).replace("\\", "/")
        if suffix:
            relative = relative.replace("/", "\\")
        contents = shim.read_text()
        if relative not in contents:
            return None
        normalized = contents.replace(relative, "<npm-target>")
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        return identity if digest in _NPM_NODE_SHIM_HASHES[suffix] else None
    except (OSError, ValueError, RuntimeError):
        return None


def _direct_adapter_package_identity(executable: str) -> tuple[str, str] | None:
    """Read the resolved executable's own declared package identity, if present."""
    try:
        resolved = Path(executable).resolve(strict=True)
        directory = resolved.parent
        for _ in range(4):
            manifest = directory / "package.json"
            if manifest.is_file():
                if manifest.stat().st_size > 65536:
                    return None
                data = json.loads(manifest.read_text())
                if not isinstance(data, dict):
                    return None
                name, version = data.get("name"), data.get("version")
                binary = data.get("bin")
                official = {
                    "@agentclientprotocol/codex-acp": "codex-acp",
                    "@agentclientprotocol/claude-agent-acp": "claude-agent-acp",
                }
                binary_name = official.get(name) if isinstance(name, str) else None
                if binary_name is not None and (
                    directory.name.lower() != binary_name
                    or directory.parent.name.lower() != "@agentclientprotocol"
                    or directory.parent.parent.name.lower() != "node_modules"
                ):
                    # A random wrapper beside a self-declared manifest does not
                    # identify an installed official npm package.
                    return None
                target = binary.get(binary_name) if isinstance(binary, dict) else binary
                if (not isinstance(name, str) or not isinstance(version, str)
                        or not isinstance(target, str) or Path(target).is_absolute()
                        or not resolved.is_relative_to(directory.resolve(strict=True))
                        or (directory / target).resolve(strict=True) != resolved):
                    return None
                return name, version
            directory = directory.parent
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _adapter_package_identity(executable: str) -> tuple[str, str] | None:
    return _npm_shim_identity(executable) or _direct_adapter_package_identity(executable)


def _known_prompt_usage(executable: str) -> bool:
    """Trust only installed adapter versions whose prompt usage was checked.

    Read package metadata beside the resolved executable, without executing a
    command or guessing from a profile name. An unknown wrapper or version may
    report only its last request and is therefore not a verified turn total.
    """
    return _adapter_package_identity(executable) in {
        ("@agentclientprotocol/codex-acp", "2.5.0"),
        ("@agentclientprotocol/claude-agent-acp", "0.89.0"),
    }


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
    usage: dict[str, int] | None = None


class _Client(acp.Client):
    def __init__(self, runner: "ACPRunner"):
        self.runner = runner

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await self.runner._session_update(session_id, update)

    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any) -> Any:
        return await self.runner._request_permission(session_id, tool_call, options)


class ACPRunner:
    """One SDK-owned ACP process/session with FIFO prompt queue."""

    @property
    def config_options(self) -> list[dict[str, Any]]:
        return self._config_options

    @config_options.setter
    def config_options(self, options: list[dict[str, Any]]) -> None:
        self._config_options = options
        profile = getattr(self, "profile", None)
        if (
            options and profile is not None
            and getattr(self, "remember_options", True)
        ):  # normal sessions remember options; discovery stays in-memory
            from kollabor_ai import acp_options

            acp_options.remember(profile.name, options)

    def __init__(
        self,
        profile: ACPProfile,
        permission_bridge: ACPPermissionBridge | None = None,
        update_callback: Callable[[dict[str, Any]], None] | None = None,
        lifecycle_callback: Callable[[str, "ACPRunner"], Awaitable[None]] | None = None,
        *,
        remember_options: bool = True,
        operation_timeout: float | None = None,
        startup_timeout: float | None = None,
        persist_profile_resolution: bool = True,
    ):
        self.profile = profile
        self.permission_bridge = permission_bridge or ACPPermissionBridge()
        self.update_callback = update_callback
        self.lifecycle_callback = lifecycle_callback
        self.remember_options = remember_options
        self.operation_timeout = operation_timeout
        self.startup_timeout = startup_timeout
        self.persist_profile_resolution = persist_profile_resolution
        self.stderr_callback: Callable[[bytes], None] | None = None
        self.generation = 0
        self._next_turn_id = 0
        self._active_turn_id = 0
        self._active_future: asyncio.Future[ACPResult] | None = None
        self._restart_before_next = False
        self._restarting = False
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
        self._process_group_id: int | None = None
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
        self._prompt_usage_supported = False
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
        operation_timeout = getattr(self, "operation_timeout", None)
        if operation_timeout is None:
            result = await self._connection.set_config_option(
                config_id, self.session_id, value
            )
        else:
            result = await asyncio.wait_for(
                self._connection.set_config_option(config_id, self.session_id, value),
                timeout=operation_timeout,
            )
        self.config_options = self._wire_config_options(result)
        return self.advertised_config_options()

    async def _start_process(self) -> str:
        if self._live_owned_processes() or self._owned_process_group_exists():
            raise ACPError("previous ACP process group still requires cleanup")
        self._owned_processes.clear()
        self.state = "starting"
        if self.lifecycle_callback is not None:
            await self.lifecycle_callback("starting", self)
        command, env, cwd = self.profile.resolve(
            persist_cache=getattr(self, "persist_profile_resolution", True)
        )
        self.resolved_command = tuple(command)
        self._prompt_usage_supported = _known_prompt_usage(command[0])
        self.workspace = cwd
        binding = self._resume_binding
        managed_upgrade = bool(binding and self.profile._matches_managed_upgrade(binding, cwd, tuple(command)))
        if (binding is not None and not managed_upgrade
                and self.profile._retargets_previous_binding(binding, cwd, tuple(command))):
            # Do not execute an unidentified replacement merely to discover
            # whether an existing managed session could be resumed.
            raise ACPError(
                "The selected Codex adapter could not be identified for this saved session. "
                "The existing binding was kept; select the previous adapter or start a new session."
            )
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
        if os.name == "posix":
            # start_new_session makes the adapter the leader of a private
            # process group. Descendants inherit this recorded cleanup scope.
            self._process_group_id = self._process.pid
        with contextlib.suppress(psutil.Error):
            owner = psutil.Process(self._process.pid)
            owner.create_time()
            if (
                owner.ppid() == os.getpid() and self._process.returncode is None
                and owner.is_running()
            ):
                self._owned_processes[owner.pid] = owner
        self._refresh_process_tree()
        self._process_tracker = asyncio.create_task(self._track_process_tree())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._connection = acp.connect_to_agent(self._client, self._process.stdin, self._process.stdout)
        initialized = await asyncio.wait_for(
            self._connection.initialize(
                protocol_version=1, client_info=Implementation(name="kollab", version=__version__)
            ), timeout=(
                getattr(self, "startup_timeout", None)
                or getattr(self, "operation_timeout", None)
                or 30
            ),
        )
        wire = initialized.model_dump(by_alias=True) if hasattr(initialized, "model_dump") else {}
        if int(wire.get("protocolVersion", 1)) != 1:
            raise ACPError("adapter did not negotiate ACP protocol version 1")
        self.capabilities = dict(wire.get("agentCapabilities") or {})
        self.initialize_metadata = dict(wire.get("_meta") or {})
        self.state = "opening"
        self.config_options = []
        self.resume_mode = "fresh"
        session_caps = self.capabilities.get("sessionCapabilities") or {}
        if binding is not None and (binding.matches(cwd, self.profile.name, tuple(command)) or managed_upgrade):
            self.session_id = binding.session_id
            self._replaying = True
            try:
                if session_caps.get("resume") is not None:
                    resumed = await asyncio.wait_for(self._connection.resume_session(
                        session_id=binding.session_id, cwd=cwd,
                        mcp_servers=self.profile.sdk_mcp_servers(),
                    ), timeout=(
                        getattr(self, "startup_timeout", None)
                        or getattr(self, "operation_timeout", None)
                        or 60
                    ))
                    self.config_options = self._wire_config_options(resumed)
                    self.resume_mode = "resumed"
                elif self.capabilities.get("loadSession"):
                    loaded = await asyncio.wait_for(self._connection.load_session(
                        session_id=binding.session_id, cwd=cwd,
                        mcp_servers=self.profile.sdk_mcp_servers(),
                    ), timeout=(
                        getattr(self, "startup_timeout", None)
                        or getattr(self, "operation_timeout", None)
                        or 60
                    ))
                    self.config_options = self._wire_config_options(loaded)
                    self.resume_mode = "loaded"
                elif managed_upgrade:
                    raise ACPError("the updated Codex adapter does not offer session resume or load")
            except BaseException as exc:
                if managed_upgrade:
                    # Do not replace the saved binding with a new session on an
                    # upgrade failure, including during orchestrator cleanup.
                    self.session_id = None
                    if not isinstance(exc, Exception):
                        raise
                    raise ACPError(
                        "The updated Codex adapter could not resume the saved session. "
                        "The existing binding was kept; select the previous adapter or start a new session."
                    ) from exc
                if not isinstance(exc, Exception):
                    raise
                # A vanished session or changed login is recoverable with new
                # context. No prompt is replayed or retried.
                self.resume_mode = "fresh"
            finally:
                self._replaying = False
        if self.resume_mode == "fresh":
            result = await asyncio.wait_for(self._connection.new_session(
                cwd=cwd, mcp_servers=self.profile.sdk_mcp_servers(),
            ), timeout=(
                getattr(self, "startup_timeout", None)
                or getattr(self, "operation_timeout", None)
                or 60
            ))
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
        # Boundary classification, computed once from the raw harness payload:
        # auth verdicts read the pre-render text here, then ride downstream as
        # a structured field. The rendered message (provider-name prefix,
        # appended login guidance) is display-only (#549, #720).
        is_auth = exc.code == -32000 and any(hint in message.lower() for hint in auth_hints)
        if is_auth:
            message += " " + self.profile.LOGIN_GUIDANCE.get(self.profile.name, "Log in using this harness's CLI.")
        error_class = (
            "authentication" if is_auth
            else "refusal" if any(hint in message.lower() for hint in refusal_hints)
            else "request_error"
        )
        return ACPError(
            f"{self.profile.name}: {message[:2000]}",
            code=exc.code,
            error_class=error_class,
            cause=type(exc).__name__,
        )

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
        """Refresh only process identities already recorded by this runner."""
        for owner in tuple(self._owned_processes.values()):
            with contextlib.suppress(psutil.Error):
                if not owner.is_running():
                    self._owned_processes.pop(owner.pid, None)

    async def _track_process_tree(self) -> None:
        while self._owned_processes:
            self._refresh_process_tree()
            if self._process is not None and self._process.returncode is not None:
                # The adapter exited without a close request. Its descendants
                # still belong to the recorded private process group.
                self._signal_owned_process_group(signal.SIGTERM)
                await asyncio.sleep(0.1)
                self._signal_owned_process_group(getattr(signal, "SIGKILL", signal.SIGTERM))
                return
            await asyncio.sleep(0.1)

    def _live_owned_processes(self) -> list[psutil.Process]:
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

    def _signal_owned_process_group(self, signum: int) -> bool:
        """Signal only the POSIX group created with this adapter process."""
        group_id = self._process_group_id
        if group_id is None or not hasattr(os, "killpg"):
            return False
        try:
            os.killpg(group_id, signum)
        except ProcessLookupError:
            return False
        except PermissionError as exc:
            raise ACPError(
                f"cannot signal recorded ACP process group {group_id}"
            ) from exc
        return True

    def _owned_process_group_exists(self) -> bool:
        """Check the recorded group without enumerating its members."""
        group_id = self._process_group_id
        if group_id is None or not hasattr(os, "killpg"):
            return False
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def admit(
        self,
        text: str,
        *,
        request_id: str,
        source: str,
        thread_id: str | None = None,
        origin: dict[str, Any] | None = None,
        images: tuple[dict[str, str], ...] = (),
    ) -> ACPAdmission:
        # While the harness restarts (after a failed or cancelled prompt) its
        # connection is briefly gone; a request then waits in the queue like one
        # admitted before the failure, and the worker settles it.
        restarting = (self._restarting or self._restart_before_next) and not self._closing
        if not restarting and (not self.session_id or self._connection is None):
            raise ACPError("ACP runner is not started", error_class="not_started")
        if not text.strip():
            raise ValueError("prompt must not be empty")
        if images and not self.supports_image_input:
            raise ACPError(
                "This harness can't take images.", error_class="images_unsupported"
            )
        generation = self.generation
        # A rejected queue admission is not a turn and must not create a hole
        # in the orchestrator's compact completed-turn frontier.
        turn_id = self._next_turn_id + 1
        future: asyncio.Future[ACPResult] = asyncio.get_running_loop().create_future()
        admission = ACPAdmission(
            request_id, source, thread_id, generation, turn_id, text, future, dict(origin or {}), images
        )
        try:
            self._queue.put_nowait(admission)
        except asyncio.QueueFull as exc:
            raise ACPError(
                "ACP prompt queue is full", error_class="queue_full",
                cause=type(exc).__name__,
            ) from exc
        self._next_turn_id = turn_id
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
        self._restarting = True
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
        finally:
            self._restarting = False

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
            if admission.images and not self.supports_image_input:
                raise ACPError(
                    "This harness can't take images.", error_class="images_unsupported"
                )
            response = await self._connection.prompt(
                session_id=self.session_id,
                prompt=[
                    TextContentBlock(type="text", text=text),
                    *(ImageContentBlock(type="image", data=image["data"], mimeType=image["mime_type"])
                      for image in admission.images),
                ],
            )
            await asyncio.sleep(0)
            stop_reason = str(response.stopReason) if response else None
            turn_usage = (_turn_usage_payload(getattr(response, "usage", None))
                          if self._prompt_usage_supported else None)
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
                    "acp_turn_usage": turn_usage,
                },
                turn_id=turn_id,
                request_id=admission.request_id,
                source=admission.source,
                thread_id=admission.thread_id,
                usage=turn_usage,
            )
        except Exception as exc:
            if isinstance(exc, ACPError):
                raise
            exit_code = getattr(self._process, "returncode", None)
            if isinstance(exc, acp.RequestError):
                raise self._request_error(exc, self._updates) from exc
            if exit_code is not None:
                line = (
                    f"{self.profile.name}: harness exited ({type(exc).__name__}, exit={exit_code}); "
                    "it may have stopped mid-change"
                )
            else:
                line = f"{self.profile.name}: harness request failed ({type(exc).__name__})"
            raise ACPError(line, cause=type(exc).__name__) from exc
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

    @property
    def busy(self) -> bool:
        """Whether a prompt runs or waits in the queue."""
        active = self._active_future
        return (active is not None and not active.done()) or not self._queue.empty()

    @property
    def supports_image_input(self) -> bool:
        prompt_capabilities = self.capabilities.get("promptCapabilities")
        return isinstance(prompt_capabilities, dict) and prompt_capabilities.get("image") is True

    async def steer(
        self, text: str, *, origin: dict[str, Any], thread_id: str | None = None,
        images: tuple[dict[str, str], ...] = (),
    ) -> bool:
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
        if images and not self.supports_image_input:
            raise ACPError(
                "This harness can't take images.", error_class="images_unsupported"
            )
        connection = self._connection
        if connection is None:
            return False
        try:
            await asyncio.wait_for(connection.ext_method("session/steering", {
                "sessionId": self.session_id,
                "prompt": [
                    {"type": "text", "text": text},
                    *({"type": "image", "data": image["data"], "mimeType": image["mime_type"]}
                      for image in images),
                ],
            }), timeout=10)
            return True
        except acp.RequestError:
            return False
        except Exception as exc:
            # A timeout or dropped connection after the send is not a refusal:
            # the harness may already hold this text, so it must not be queued.
            raise ACPSteeringUnconfirmed(
                f"{self.profile.name}: steering receipt not confirmed ({type(exc).__name__})"
            ) from exc

    def cancel_queued(self, request_id: str, admission: ACPAdmission | None = None) -> bool:
        """Cancel one queued admission: that exact admission if given, else the first with this id."""
        for queued in tuple(self._queue._queue):
            matched = (queued is admission) if admission is not None else (
                queued is not None and queued.request_id == request_id)
            if matched:
                # This synchronous removal cannot race the queue consumer.
                # Release capacity before settling the future, preserving
                # every remaining item's order and the active owner's RPC.
                self._queue._queue.remove(queued)
                self._queue.task_done()
                self._queue._wakeup_next(self._queue._putters)
                if not queued.future.done():
                    queued.future.cancel()
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
                # Descendants share the private group created for the adapter.
                # Signal that recorded scope so cleanup still works after the
                # adapter exits and its child handles are no longer available.
                self._signal_owned_process_group(signal.SIGTERM)
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
                # All exit paths finish by stopping any surviving members of
                # the recorded group, including children no longer discoverable
                # through a live parent tree.
                self._signal_owned_process_group(getattr(signal, "SIGKILL", signal.SIGTERM))
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
                self._signal_owned_process_group(getattr(signal, "SIGKILL", signal.SIGTERM))
                group_deadline = asyncio.get_running_loop().time() + 2
                while self._owned_process_group_exists() and asyncio.get_running_loop().time() < group_deadline:
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
                group_survives = self._owned_process_group_exists()
                if not survivors and not unreaped_adapter and not group_survives:
                    self._owned_processes.clear()
                    self._adapter_pid = None
                    self._process_group_id = None
                else:
                    detail = ", ".join(str(owner.pid) for owner in survivors)
                    if unreaped_adapter:
                        detail += f" (adapter {process.pid} not reaped)"
                    if group_survives:
                        detail += f" (process group {self._process_group_id} still has members)"
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
