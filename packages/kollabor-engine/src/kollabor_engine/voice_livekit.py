"""Engine-owned LiveKit server and speech-worker processes for voice rooms.

The server binds 127.0.0.1 on a free port with a per-run key and secret that
live only in this process and the server's environment. One worker process
joins each room and calls back into the engine (/turns, /events); the engine
never pushes to it. Stop is SIGTERM to the worker's process group.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import signal
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .voice_runtime_host import RuntimeHostError
from .voice_types import RuntimeStatus, StopReason, VoiceSessionBinding

logger = logging.getLogger(__name__)

WORKER_READY_PREFIX = "WORKER_READY"  # the worker prints "WORKER_READY -" once joined
WORKER_READY_TIMEOUT_S = 300.0
SERVER_READY_TIMEOUT_S = 15.0
WORKER_TOKEN_TTL_S = 3600

# Runs the server and a watchdog in one process group: if the engine dies
# without cleanup (crash, SIGKILL) the watchdog sees its pid vanish and kills
# the server, so no livekit-server is left running.
_SUPERVISE = (
    'ppid="$1"; shift; "$@" & srv=$!; '
    '( while kill -0 "$ppid" 2>/dev/null && kill -0 "$srv" 2>/dev/null; do sleep 1; done; '
    'kill "$srv" 2>/dev/null ) & wait "$srv"'
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_livekit_jwt(
    api_key: str,
    api_secret: str,
    *,
    identity: str,
    video: dict[str, Any],
    expires_at_s: int,
    now_s: int | None = None,
) -> str:
    """HS256 LiveKit access token (iss=key, sub=identity, video grants)."""
    now = int(time.time()) if now_s is None else now_s
    header = _b64(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    claims = {
        "iss": api_key,
        "sub": identity,
        "nbf": now,
        "exp": expires_at_s,
        "video": video,
    }
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(
        api_secret.encode(), f"{header}.{body}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{body}.{_b64(signature)}"


def room_name_for(binding: VoiceSessionBinding) -> str:
    # Same shape EngineRuntimeHost.issue_join_ticket uses for the browser.
    return f"kollab-{binding.voice_session_id}-g{binding.generation}"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class WorkerHandle:
    process: asyncio.subprocess.Process


class LiveKitRuntime:
    """RuntimeBackend + JoinTicketSigner over a local livekit-server."""

    def __init__(
        self,
        *,
        engine_url: Callable[[], str],
        engine_token: Callable[[], str],
        server_binary: str | None = None,
        worker_argv: Sequence[str] | None = None,
        worker_env: dict[str, str] | None = None,
    ) -> None:
        self._engine_url = engine_url
        self._engine_token = engine_token
        self._server_binary = server_binary
        self._worker_argv = list(worker_argv) if worker_argv else None
        self._worker_env = worker_env or {}
        self.port = _free_port()
        self.livekit_url = f"ws://127.0.0.1:{self.port}"
        self._api_key = "kv" + secrets.token_hex(8)
        self._api_secret = secrets.token_urlsafe(32)
        self._server: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._workers: dict[tuple[str, int], WorkerHandle] = {}

    # --- RuntimeBackend -------------------------------------------------

    def _binary(self) -> str | None:
        return self._server_binary or shutil.which("livekit-server")

    async def prepare(self) -> RuntimeStatus:
        if self._binary() is None:
            return RuntimeStatus(
                state="error",
                component="livekit_server",
                detail="livekit-server is not installed. Install it with brew install livekit.",
                retryable=False,
            )
        running = self._server is not None and self._server.returncode is None
        return RuntimeStatus(
            state="ready",
            component="livekit_server",
            detail="running" if running else "starts with the first voice session",
        )

    async def get_status(self) -> RuntimeStatus:
        return await self.prepare()

    async def _ensure_server(self) -> None:
        async with self._lock:
            if self._server is not None and self._server.returncode is None:
                return
            binary = self._binary()
            if binary is None:
                raise RuntimeHostError(
                    "runtime_unavailable", 503, "livekit-server is not installed."
                )
            env = dict(os.environ)
            env["LIVEKIT_KEYS"] = f"{self._api_key}: {self._api_secret}"
            self._server = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                _SUPERVISE,
                "sh",
                str(os.getpid()),
                binary,
                "--bind",
                "127.0.0.1",
                "--port",
                str(self.port),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.monotonic() + SERVER_READY_TIMEOUT_S
            while time.monotonic() < deadline:
                if self._server.returncode is not None:
                    raise RuntimeHostError(
                        "runtime_unavailable", 503, "livekit-server exited at startup."
                    )
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                    writer.close()
                    return
                except OSError:
                    await asyncio.sleep(0.1)
            await self._kill_group(self._server)
            self._server = None
            raise RuntimeHostError(
                "runtime_unavailable",
                503,
                "livekit-server did not become ready.",
                retryable=True,
            )

    def preflight(self) -> None:
        self._worker_command()

    def _worker_command(self) -> tuple[list[str], dict[str, str]]:
        if self._worker_argv:
            return list(self._worker_argv), {}
        # The voice service's own prepared runtime has livekit and numpy; the
        # engine's interpreter has neither. KOLLAB_VOICE_PYTHON overrides.
        python = os.environ.get("KOLLAB_VOICE_PYTHON")
        if not python:
            from kollabor_voice.bootstrap import prepared_runtime_python

            prepared = prepared_runtime_python()
            if prepared is None:
                raise RuntimeHostError(
                    "voice_runtime_not_ready",
                    503,
                    "Voice support is not set up yet. Turn on voice in Settings "
                    "and wait for model setup to finish, then try again.",
                    retryable=True,
                )
            python = str(prepared)
        import kollabor_voice

        src = str(Path(kollabor_voice.__file__).resolve().parent.parent)
        return [python, "-m", "kollabor_voice.livekit_worker"], {"PYTHONPATH": src}

    async def start_session(self, binding: VoiceSessionBinding) -> WorkerHandle:
        cmd, cmd_env = self._worker_command()  # fail before starting any server
        await self._ensure_server()
        room = room_name_for(binding)
        config = {
            "livekit_url": self.livekit_url,
            "worker_token": mint_livekit_jwt(
                self._api_key,
                self._api_secret,
                identity=f"worker-{binding.voice_session_id}",
                video={
                    "room": room,
                    "roomJoin": True,
                    "canPublish": True,
                    "canSubscribe": True,
                },
                expires_at_s=int(time.time()) + WORKER_TOKEN_TTL_S,
            ),
            "room": room,
            "voice_session_id": binding.voice_session_id,
            "generation": binding.generation,
            "engine_url": self._engine_url(),
            "engine_token": self._engine_token(),
        }
        env = dict(os.environ)
        env.update(cmd_env)
        env.update(self._worker_env)
        env["KOLLAB_VOICE_WORKER_CONFIG"] = json.dumps(config)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(self._read_ready(process), WORKER_READY_TIMEOUT_S)
        except BaseException:
            await self._kill_group(process)
            raise
        handle = WorkerHandle(process)
        self._workers[(binding.voice_session_id, binding.generation)] = handle
        asyncio.create_task(self._drain(process))
        return handle

    @staticmethod
    async def _read_ready(process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                raise RuntimeHostError(
                    "runtime_unavailable",
                    503,
                    "Voice worker exited before joining.",
                    retryable=True,
                )
            text = line.decode(errors="replace").strip()
            if text.startswith(WORKER_READY_PREFIX):
                return

    @staticmethod
    async def _drain(process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while await process.stdout.readline():
            pass

    async def stop_session(
        self, binding: VoiceSessionBinding, handle: object, reason: StopReason
    ) -> None:
        assert isinstance(handle, WorkerHandle)
        self._workers.pop((binding.voice_session_id, binding.generation), None)
        # SIGTERM first (the worker leaves its room), SIGKILL after the grace.
        await self._kill_group(handle.process)

    # --- JoinTicketSigner -----------------------------------------------

    async def sign(
        self, binding: VoiceSessionBinding, *, room_name: str, expires_at_utc_ms: int
    ) -> str:
        return mint_livekit_jwt(
            self._api_key,
            self._api_secret,
            identity=f"user-{binding.owner_id}-{binding.voice_session_id[:8]}",
            video={
                "room": room_name,
                "roomJoin": True,
                "canPublish": True,
                "canPublishSources": ["microphone"],
                "canPublishData": False,
                "canSubscribe": True,
            },
            expires_at_s=expires_at_utc_ms // 1000,
        )

    # --- shutdown -------------------------------------------------------

    @staticmethod
    async def _kill_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError, PermissionError:
                return
            try:
                await asyncio.wait_for(process.wait(), 3.0)
                return
            except asyncio.TimeoutError:
                continue

    async def shutdown(self) -> None:
        for handle in list(self._workers.values()):
            await self._kill_group(handle.process)
        self._workers.clear()
        if self._server is not None:
            await self._kill_group(self._server)
            self._server = None
