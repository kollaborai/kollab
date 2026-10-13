"""Phase-one outbound ACP bridge using the pinned agent-client-protocol SDK."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar

import acp
from acp.schema import AllowedOutcome, DeniedOutcome, EnvVariable, McpServerStdio, TextContentBlock


class ACPError(RuntimeError):
    """A protocol, lifecycle, or adapter error."""


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
            "install": "npm install -g @agentclientprotocol/claude-agent-acp@0.89.0",
        },
        "codex-cli": {
            "command": ["codex-acp"],
            "install": "npm install -g @agentclientprotocol/codex-acp@2.1.1",
        },
        "gemini-cli": {
            "command": ["gemini", "--acp"],
            "install": "npm install -g @google/gemini-cli@0.63.0",
        },
    }
    _PROVIDER_KEYS: ClassVar[frozenset[str]] = frozenset({
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY",
    })

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
        executable_path = Path(executable)
        if (
            cached_command != self.command[0]
            or not executable
            or not executable_path.is_file()
            or not os.access(executable_path, os.X_OK)
        ):
            executable = self._login_shell_path() or shutil.which(self.command[0]) or ""
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
        offered = {str(option.get("optionId")): option for option in request.options}
        if self._decide is None or not offered:
            return None
        decision = asyncio.get_running_loop().create_future()
        self._pending.add(decision)
        task = asyncio.create_task(self._decide(request))
        try:
            done, _ = await asyncio.wait(
                (task, decision), timeout=300, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return None
            selected = next(iter(done)).result()
        except (asyncio.CancelledError, asyncio.TimeoutError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return None
        finally:
            self._pending.discard(decision)
            if not task.done():
                task.cancel()
        if self._cancelled or request.session_id != self._session_id or request.generation != self._generation:
            return None
        if selected is None or selected not in offered:
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
    ):
        self.profile = profile
        self.permission_bridge = permission_bridge or ACPPermissionBridge()
        self.update_callback = update_callback
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
        self.cancel_timeout = 30.0
        self.session_id: str | None = None
        self._connection: Any = None
        self._process: Any = None
        self._client = _Client(self)
        self._stderr_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[ACPAdmission | None] = asyncio.Queue(maxsize=10)
        self._worker: asyncio.Task[None] | None = None
        self._updates: list[dict[str, Any]] = []
        self._active_generation = 0

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

    async def _start_process(self) -> str:
        command, env, cwd = self.profile.resolve()
        self._process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._connection = acp.connect_to_agent(self._client, self._process.stdin, self._process.stdout)
        await self._connection.initialize(protocol_version=1)
        result = await self._connection.new_session(
            cwd=cwd,
            mcp_servers=self.profile.sdk_mcp_servers(),
        )
        self.session_id = str(result.sessionId)
        self.generation = self.generation + 1 if self.generation else 1
        return self.session_id

    async def start(self) -> str:
        self._closing = False
        session_id = await self._start_process()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain_queue())
        return session_id

    async def _drain_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while chunk := await self._process.stderr.readline():
            sys.stderr.buffer.write(b"[acp] " + chunk)
            sys.stderr.buffer.flush()

    def admit(
        self,
        text: str,
        *,
        request_id: str,
        source: str,
        thread_id: str | None = None,
    ) -> ACPAdmission:
        if not self.session_id or self._connection is None:
            raise ACPError("ACP runner is not started")
        if not text.strip():
            raise ValueError("prompt must not be empty")
        generation = self.generation
        self._next_turn_id += 1
        turn_id = self._next_turn_id
        future: asyncio.Future[ACPResult] = asyncio.get_running_loop().create_future()
        admission = ACPAdmission(request_id, source, thread_id, generation, turn_id, text, future)
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
        self._updates = []
        self.permission_bridge.bind(self.session_id or "", generation)
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
        finally:
            self.permission_bridge.cancel()
            self._active_generation = 0
            self._active_turn_id = 0

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
            last_tool = tool_indexes[-1]
            tail = [item for item in updates[last_tool + 1:] if item.get("kind") == "text"]
            if tail:
                return "".join(str(item.get("text", "")) for item in tail)
            previous_tool = tool_indexes[-2] if len(tool_indexes) > 1 else -1
            segment = [item for item in updates[previous_tool + 1:last_tool] if item.get("kind") == "text"]
            return "".join(str(item.get("text", "")) for item in segment)
        return "".join(str(item.get("text", "")) for item in text_updates)

    async def _session_update(self, session_id: str, update: Any) -> None:
        if session_id != self.session_id or self._active_generation != self.generation:
            return
        payload = update.model_dump() if hasattr(update, "model_dump") else {}
        kind = payload.get("sessionUpdate", payload.get("session_update", payload.get("type", "unknown")))
        if kind == "agent_message_chunk":
            content = payload.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else getattr(content, "text", "")
            update_data = {"kind": "text", "text": str(text)}
            message_id = payload.get("messageId", payload.get("message_id"))
            if message_id:
                update_data["message_id"] = str(message_id)
        else:
            update_data = {"kind": str(kind), "payload": payload}
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

    async def _request_permission(self, session_id: str, tool_call: Any, options: list[Any]) -> Any:
        if session_id != self.session_id or not self._active_generation:
            return acp.RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
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
        request = ACPPermissionRequest(
            request_id=request_id,
            session_id=session_id,
            generation=self._active_generation,
            operation=operation,
            workspace=os.path.realpath(self.profile.workspace or self.profile.cwd or os.getcwd()),
            profile=self.profile.name,
            options=tuple(option_data),
            kind=str(raw_tool.get("kind") or "").lower(),
        )
        if self.update_callback is not None:
            self.update_callback({
                "kind": "permission_request",
                "request_id": request.request_id,
                "turn_id": self._active_turn_id,
                "source": getattr(getattr(self, "_active_context", None), "source", "acp"),
                "thread_id": getattr(getattr(self, "_active_context", None), "thread_id", None),
                "payload": {
                    "operation": operation,
                    "workspace": request.workspace,
                    "profile": request.profile,
                    "options": option_data,
                    "waiting": True,
                    "kind": request.kind,
                },
            })
        selected = await self.permission_bridge.request(request)
        if selected is None:
            return acp.RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return acp.RequestPermissionResponse(outcome=AllowedOutcome(optionId=selected, outcome="selected"))

    async def cancel(self) -> None:
        async with self._cancel_lock:
            self.permission_bridge.cancel()
            active = self._active_rpc_task
            if active is None or active.done():
                return
            # Hold the worker at the queue boundary before sending cancel so a
            # completion cannot release B between our snapshot and the RPC.
            self._cancel_hold = True
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
                self._queue_release.set()

    async def _terminate_process(self) -> None:
        connection, process, stderr_task = self._connection, self._process, self._stderr_task
        self._connection = None
        self._process = None
        self._stderr_task = None
        if connection is not None:
            try:
                await asyncio.wait_for(connection.close(), timeout=2)
            except (Exception, asyncio.CancelledError):
                pass
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=2)
            except (AttributeError, PermissionError, ProcessLookupError, asyncio.TimeoutError):
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (AttributeError, PermissionError, ProcessLookupError):
                        pass
                    await process.wait()
        if stderr_task is not None:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    async def close(self) -> None:
        self._closing = True
        self.permission_bridge.cancel()
        self.session_id = None
        self._queue_release.set()
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None and not item.future.done():
                item.future.set_exception(ACPError("ACP runner closed"))
        # Wake an idle consumer without relying on cancellation of a Queue.get
        # waiter, which can remain pending under SDK callbacks.
        self._queue.put_nowait(None)
        if self._active_future is not None and not self._active_future.done():
            self._active_future.set_exception(ACPError("ACP runner closed"))
        worker = self._worker
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
            await self._terminate_process()
            self._worker = None
