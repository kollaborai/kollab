"""Phase-one outbound ACP bridge primitives.

The bridge intentionally owns only a local stdio ACP child.  It does not parse
Kollab tags from harness text or participate in the model/tool loop.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


class ACPError(RuntimeError):
    """A protocol, lifecycle, or adapter error."""


@dataclass(frozen=True)
class ACPProfile:
    """Portable profile plus machine-local command resolution."""

    name: str
    command: tuple[str, ...]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    pass_env: tuple[str, ...] = ()
    workspace: str | None = None

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
        )

    def resolve(self) -> tuple[tuple[str, ...], dict[str, str], str]:
        """Resolve command on this device without persisting absolute paths."""
        executable = shutil.which(self.command[0])
        if not executable:
            raise ACPError(f"ACP adapter executable not found: {self.command[0]}")
        cwd = str(Path(self.cwd or self.workspace or os.getcwd()).expanduser().resolve())
        if not Path(cwd).is_dir():
            raise ACPError(f"ACP working directory does not exist: {cwd}")
        safe_env = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "TERM", "SHELL"}
        inherited = {
            key: value
            for key, value in os.environ.items()
            if key in safe_env or key in self.pass_env
        }
        inherited.update(self.env)
        return (executable, *self.command[1:]), inherited, cwd


@dataclass(frozen=True)
class ACPPermissionRequest:
    request_id: str
    operation: str
    workspace: str
    profile: str
    options: tuple[dict[str, Any], ...]


class ACPPermissionBridge:
    """First-answer-wins bridge for one ACP prompt generation."""

    def __init__(self, decide: Callable[[ACPPermissionRequest], Awaitable[str | None]] | None = None):
        self._decide = decide
        self._futures: dict[str, asyncio.Future[str | None]] = {}

    async def request(self, request: ACPPermissionRequest) -> str | None:
        if not request.options:
            return None
        if self._decide is not None:
            return await self._decide(request)
        return next(
            (
                option["optionId"]
                for option in request.options
                if option.get("kind") in {"allow_once", "reject_once"}
            ),
            None,
        )

    def scope(self, request: ACPPermissionRequest) -> tuple[str, str, str, str]:
        return (request.workspace, request.profile, request.operation, request.request_id)


@dataclass(frozen=True)
class ACPResult:
    generation: int
    session_id: str
    text: str
    stop_reason: str | None
    updates: tuple[dict[str, Any], ...]
    end_frame: dict[str, Any]


class ACPTransport:
    """Newline-delimited JSON transport over an ACP child process."""

    def __init__(self, profile: ACPProfile, on_notification: Callable[[dict[str, Any]], Awaitable[Any]] | None = None):
        self.profile = profile
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self.on_notification = on_notification
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        command, env, cwd = self.profile.resolve()
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        while chunk := await self.process.stderr.readline():
            sys.stderr.buffer.write(b"[acp] " + chunk)
            sys.stderr.buffer.flush()

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise ACPError("ACP transport is not started")
        request_id = self._next_id
        self._next_id += 1
        async with self._write_lock:
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            self.process.stdin.write((json.dumps(payload) + "\n").encode())
            await self.process.stdin.drain()
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                raise ACPError("ACP adapter closed stdout")
            message = json.loads(raw)
            if message.get("id") == request_id:
                if "error" in message:
                    raise ACPError(str(message["error"]))
                return message.get("result")
            await self._notification(message)

    async def _notification(self, message: dict[str, Any]) -> None:
        if self.on_notification is None:
            return None
        result = await self.on_notification(message)
        if "id" in message and self.process and self.process.stdin:
            payload = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            self.process.stdin.write((json.dumps(payload) + "\n").encode())
            await self.process.stdin.drain()

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise ACPError("ACP transport is not started")
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode())
        await self.process.stdin.drain()

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=2)
            except (ProcessLookupError, asyncio.TimeoutError):
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
        if self._stderr_task:
            self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)
            self._stderr_task = None


class ACPRunner:
    """One-owner ACP session with generation fencing and FIFO prompt queue."""

    def __init__(self, profile: ACPProfile, permission_bridge: ACPPermissionBridge | None = None):
        self.profile = profile
        self.transport = ACPTransport(profile)
        self.transport.on_notification = self._handle_notification
        self.permission_bridge = permission_bridge or ACPPermissionBridge()
        self.generation = 0
        self.session_id: str | None = None
        self._active = False
        self._queue: asyncio.Queue[tuple[int, str, asyncio.Future[ACPResult]]] = asyncio.Queue(maxsize=10)
        self._worker: asyncio.Task[None] | None = None
        self._updates: list[dict[str, Any]] = []

    async def start(self) -> str:
        await self.transport.start()
        await self.transport.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        cwd = self.profile.cwd or self.profile.workspace or os.getcwd()
        result = await self.transport.request("session/new", {"cwd": cwd})
        self.session_id = str((result or {}).get("sessionId"))
        if not self.session_id or self.session_id == "None":
            raise ACPError("ACP adapter returned no sessionId")
        self._worker = asyncio.create_task(self._drain_queue())
        return self.session_id

    async def prompt(self, text: str) -> ACPResult:
        if not self.session_id:
            raise ACPError("ACP runner is not started")
        if not text.strip():
            raise ValueError("prompt must not be empty")
        self.generation += 1
        generation = self.generation
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ACPResult] = loop.create_future()
        try:
            self._queue.put_nowait((generation, text, future))
        except asyncio.QueueFull as exc:
            raise ACPError("ACP prompt queue is full") from exc
        return await future

    async def _drain_queue(self) -> None:
        while self.session_id is not None:
            generation, text, future = await self._queue.get()
            if future.cancelled():
                continue
            try:
                result = await self._run_prompt(generation, text)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)

    async def _run_prompt(self, generation: int, text: str) -> ACPResult:
        self._active = True
        self._updates = []
        try:
            params = {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": text}],
            }
            result = await self.transport.request("session/prompt", params)
            stop_reason = (result or {}).get("stopReason") if isinstance(result, dict) else None
            text_parts = [str(update.get("text", "")) for update in self._updates if update.get("kind") == "text"]
            answer = "".join(text_parts)
            return ACPResult(
                generation=generation,
                session_id=self.session_id or "",
                text=answer,
                stop_reason=stop_reason,
                updates=tuple(self._updates),
                end_frame={
                    "type": "turn_end",
                    "generation": generation,
                    "sessionId": self.session_id,
                    "stopReason": stop_reason,
                },
            )
        finally:
            self._active = False

    async def _handle_notification(self, message: dict[str, Any]) -> Any:
        method = message.get("method")
        params = message.get("params") or {}
        if method in {"session/update", "session/notification"}:
            update = params.get("update", params)
            kind = str(update.get("sessionUpdate", update.get("type", "text")))
            if kind in {"agent_message_chunk", "text", "assistant_message"}:
                content = update.get("content", update.get("text", ""))
                if isinstance(content, dict):
                    content = content.get("text", "")
                self._updates.append({"kind": "text", "text": str(content)})
            else:
                self._updates.append({"kind": kind, "payload": update})
            return None
        if method in {"session/request_permission", "session/requestPermission"}:
            request = ACPPermissionRequest(
                request_id=str(params.get("requestId", params.get("toolCallId", secrets.token_hex(8)))),
                operation=str(params.get("toolCall", params.get("operation", "unknown"))),
                workspace=self.profile.workspace or self.profile.cwd or os.getcwd(),
                profile=self.profile.name,
                options=tuple(params.get("options") or ()),
            )
            option_id = await self.permission_bridge.request(request)
            return {"optionId": option_id} if option_id else {"outcome": "cancelled"}
        return None

    async def cancel(self) -> None:
        if self.session_id:
            await self.transport.notify("session/cancel", {"sessionId": self.session_id})

    async def close(self) -> None:
        self.session_id = None
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        await self.transport.close()
