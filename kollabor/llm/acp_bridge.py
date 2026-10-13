"""Phase-one outbound ACP bridge using the pinned agent-client-protocol SDK."""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

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

    @classmethod
    def from_dict(cls, name: str, value: dict[str, Any]) -> "ACPProfile":
        command = value.get("command")
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

    def resolve(self) -> tuple[tuple[str, ...], dict[str, str], str]:
        executable = shutil.which(self.command[0])
        if not executable:
            raise ACPError(f"ACP adapter executable not found: {self.command[0]}")
        cwd = str(Path(self.cwd or self.workspace or os.getcwd()).expanduser().resolve())
        if not Path(cwd).is_dir():
            raise ACPError(f"ACP working directory does not exist: {cwd}")
        safe_env = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "TERM", "SHELL"}
        env = {key: value for key, value in os.environ.items() if key in safe_env or key in self.pass_env}
        env.update(self.env)
        return (executable, *self.command[1:]), env, cwd

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
    operation: str
    workspace: str
    profile: str
    options: tuple[dict[str, Any], ...]


class ACPPermissionBridge:
    """Fail-closed, first-answer-wins bridge for one generation."""

    def __init__(self, decide: Callable[[ACPPermissionRequest], Awaitable[str | None]] | None = None):
        self._decide = decide
        self._generation = 0
        self._session_id: str | None = None
        self._cancelled = False
        self._pending: set[asyncio.Task[str | None]] = set()

    def bind(self, session_id: str, generation: int) -> None:
        self._session_id = session_id
        self._generation = generation
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        for pending in tuple(self._pending):
            pending.cancel()

    async def request(self, request: ACPPermissionRequest) -> str | None:
        if self._cancelled or request.session_id != self._session_id or request.generation != self._generation:
            return None
        offered = {str(option.get("optionId")): option for option in request.options}
        if self._decide is None:
            return None
        pending = asyncio.create_task(self._decide(request))
        self._pending.add(pending)
        try:
            selected = await asyncio.wait_for(asyncio.shield(pending), timeout=300)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pending.cancel()
            return None
        finally:
            self._pending.discard(pending)
        if self._cancelled or request.session_id != self._session_id or request.generation != self._generation:
            return None
        if selected is None or selected not in offered:
            return None
        if offered[selected].get("kind") not in {"allow_once", "reject_once"}:
            return None
        return selected


@dataclass(frozen=True)
class ACPResult:
    generation: int
    session_id: str
    text: str
    stop_reason: str | None
    updates: tuple[dict[str, Any], ...]
    end_frame: dict[str, Any]
    turn_id: int = 0


class _Client(acp.Client):
    def __init__(self, runner: "ACPRunner"):
        self.runner = runner

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await self.runner._session_update(session_id, update)

    async def request_permission(self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any) -> Any:
        return await self.runner._request_permission(session_id, tool_call, options)


class ACPRunner:
    """One SDK-owned ACP process/session with FIFO prompt queue."""

    def __init__(self, profile: ACPProfile, permission_bridge: ACPPermissionBridge | None = None):
        self.profile = profile
        self.permission_bridge = permission_bridge or ACPPermissionBridge()
        self.generation = 0
        self._next_turn_id = 0
        self._active_turn_id = 0
        self._active_future: asyncio.Future[ACPResult] | None = None
        self._restart_before_next = False
        self.session_id: str | None = None
        self._connection: Any = None
        self._process: Any = None
        self._client = _Client(self)
        self._stderr_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[tuple[int, int, str, asyncio.Future[ACPResult]]] = asyncio.Queue(maxsize=10)
        self._worker: asyncio.Task[None] | None = None
        self._updates: list[dict[str, Any]] = []
        self._active_generation = 0

    async def start(self) -> str:
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
        self._worker = asyncio.create_task(self._drain_queue())
        return self.session_id

    async def _drain_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while chunk := await self._process.stderr.readline():
            sys.stderr.buffer.write(b"[acp] " + chunk)
            sys.stderr.buffer.flush()

    async def prompt(self, text: str) -> ACPResult:
        if not self.session_id or self._connection is None:
            raise ACPError("ACP runner is not started")
        if not text.strip():
            raise ValueError("prompt must not be empty")
        generation = self.generation
        self._next_turn_id += 1
        turn_id = self._next_turn_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ACPResult] = loop.create_future()
        try:
            self._queue.put_nowait((generation, turn_id, text, future))
        except asyncio.QueueFull as exc:
            raise ACPError("ACP prompt queue is full") from exc
        return await future

    async def _drain_queue(self) -> None:
        while self.session_id is not None:
            generation, turn_id, text, future = await self._queue.get()
            if future.cancelled():
                continue
            if generation != self.generation:
                generation = self.generation
            self._active_future = future
            try:
                result = await self._run_prompt(generation, turn_id, text)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._active_future = None
            if self._restart_before_next and self._connection is not None:
                self._restart_before_next = False
                await self._restart_process()

    async def _restart_process(self) -> None:
        old_process = self._process
        if self._connection is not None:
            await self._connection.close()
        if old_process is not None and old_process.returncode is None:
            try:
                os.killpg(old_process.pid, signal.SIGTERM)
                await asyncio.wait_for(old_process.wait(), timeout=2)
            except (AttributeError, ProcessLookupError, asyncio.TimeoutError):
                if old_process.returncode is None:
                    try:
                        os.killpg(old_process.pid, signal.SIGKILL)
                    except (AttributeError, ProcessLookupError):
                        pass
                    await old_process.wait()
        if self._stderr_task:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
        self._connection = None
        self._process = None
        self._stderr_task = None
        await self.start()

    async def _run_prompt(self, generation: int, turn_id: int, text: str) -> ACPResult:
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
            answer = "".join(update["text"] for update in self._updates if update.get("kind") == "text")
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
                },
                turn_id=turn_id,
            )
        finally:
            self.permission_bridge.cancel()
            self._active_generation = 0
            self._active_turn_id = 0

    async def _session_update(self, session_id: str, update: Any) -> None:
        if session_id != self.session_id or self._active_generation != self.generation:
            return
        payload = update.model_dump() if hasattr(update, "model_dump") else {}
        kind = payload.get("sessionUpdate", payload.get("session_update", payload.get("type", "unknown")))
        if kind == "agent_message_chunk":
            content = payload.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else getattr(content, "text", "")
            self._updates.append({"kind": "text", "text": str(text)})
        else:
            self._updates.append({"kind": str(kind), "payload": payload})

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
        request = ACPPermissionRequest(
            request_id=str(getattr(tool_call, "toolCallId", "permission")),
            session_id=session_id,
            generation=self._active_generation,
            operation=str(getattr(tool_call, "title", "unknown")),
            workspace=self.profile.workspace or self.profile.cwd or os.getcwd(),
            profile=self.profile.name,
            options=tuple(option_data),
        )
        selected = await self.permission_bridge.request(request)
        if selected is None:
            return acp.RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return acp.RequestPermissionResponse(outcome=AllowedOutcome(optionId=selected, outcome="selected"))

    async def cancel(self) -> None:
        self.permission_bridge.cancel()
        self._restart_before_next = True
        self.generation += 1
        if self._active_future and not self._active_future.done():
            self._active_future.set_exception(ACPError("ACP turn cancelled"))
        if self.session_id and self._connection:
            await self._connection.cancel(session_id=self.session_id)

    async def close(self) -> None:
        self.permission_bridge.cancel()
        self.session_id = None
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        if self._connection is not None:
            await self._connection.close()
        if self._process is not None and self._process.returncode is None:
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
            except (AttributeError, ProcessLookupError):
                pass
        if self._stderr_task:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
            self._stderr_task = None
        if self._process is not None:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except (AttributeError, ProcessLookupError):
                    pass
                await self._process.wait()
        self._connection = None
        self._process = None
