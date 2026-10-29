"""Engine-owned LiveKit server and speech-worker processes for voice rooms.

The server takes signaling on 127.0.0.1 and ICE over TCP on free ports, with a
per-run key and secret that live only in this process and the server's
environment. One worker process joins each room and calls back into the engine
(/turns, /events); the engine never pushes to it. Stop is SIGTERM to the
worker's process group; a worker that exits by itself ends its session
(EngineRuntimeHost waits on wait_exited), and so does a room its user left
(wait_abandoned asks the server who is in it).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
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
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .voice_runtime_host import RuntimeHostError
from .voice_types import RuntimeStatus, StopReason, VoiceSessionBinding

logger = logging.getLogger(__name__)

WORKER_READY_PREFIX = "WORKER_READY"  # the worker prints "WORKER_READY -" once joined
WORKER_ERROR_PREFIX = "WORKER_ERROR"  # or "WORKER_ERROR <reason>" when it cannot start
WORKER_SHORTAGE_PREFIX = "WORKER_SHORTAGE"  # "WORKER_SHORTAGE <reason>": out of memory
# The worker waits up to 1200 s for its models (a first-run download), then joins.
WORKER_READY_TIMEOUT_S = 1260.0
SERVER_READY_TIMEOUT_S = 15.0
WORKER_TOKEN_TTL_S = 3600
STALE_SERVICE_EXIT_S = 10.0  # an older Kollab's voice service gets this long after SIGTERM
# A closed or reloaded page sends no stop. Its room waits this long for the user
# to come back (a reload that starts voice again rejoins it), then ends.
USER_GONE_GRACE_S = 60.0
PRESENCE_POLL_S = 5.0
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
USER_PREFIX = "user-"  # the browser's identity in a room (sign); the worker's is "worker-"
LIVEKIT_MISSING = "livekit-server is not installed. Install it with brew install livekit."

# Runs the server and a watchdog in one process group: if the engine dies
# without cleanup (crash, SIGKILL) the watchdog sees its pid vanish and kills
# the server, so no livekit-server is left running. SIGKILL: on SIGTERM the
# server drains, waiting for rooms that only the dead engine could end.
_SUPERVISE = (
    'ppid="$1"; shift; "$@" & srv=$!; '
    '( while kill -0 "$ppid" 2>/dev/null && kill -0 "$srv" 2>/dev/null; do sleep 1; done; '
    'kill -9 "$srv" 2>/dev/null ) & wait "$srv"'
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
        log_dir: Path | None = None,
    ) -> None:
        self._engine_url = engine_url
        self._log_dir = log_dir
        self._engine_token = engine_token
        self._server_binary = server_binary
        self._worker_argv = list(worker_argv) if worker_argv else None
        self._worker_env = worker_env or {}
        self.port = _free_port()
        # ICE over TCP. livekit-server's default, 7881, is one port for every server
        # on the machine, so a second one (another Engine, LiveKit's own demo) could
        # not start.
        self.rtc_tcp_port = _free_port()
        while self.rtc_tcp_port == self.port:
            self.rtc_tcp_port = _free_port()
        self.livekit_url = f"ws://127.0.0.1:{self.port}"
        self._api_key = "kv" + secrets.token_hex(8)
        self._api_secret = secrets.token_urlsafe(32)
        self._server: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._workers: dict[tuple[str, int], WorkerHandle] = {}

    def _log(self, name: str):
        """Where a child's output goes: a log under log_dir, or nowhere without one.

        A file past 5 MB starts over, so a crash reason stays and the log stays small.
        """
        if self._log_dir is None:
            return contextlib.nullcontext(asyncio.subprocess.DEVNULL)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_dir / name
        small = path.exists() and path.stat().st_size < 5_000_000
        return open(path, "ab" if small else "wb")

    # --- RuntimeBackend -------------------------------------------------

    def _binary(self) -> str | None:
        return self._server_binary or shutil.which("livekit-server")

    async def prepare(self) -> RuntimeStatus:
        if self._binary() is None:
            return RuntimeStatus(
                state="error",
                component="livekit_server",
                detail=LIVEKIT_MISSING,
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
                raise RuntimeHostError("runtime_unavailable", 503, LIVEKIT_MISSING)
            env = dict(os.environ)
            env["LIVEKIT_KEYS"] = f"{self._api_key}: {self._api_secret}"
            with self._log("livekit-server.log") as log:
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
                    "--rtc.tcp_port",
                    str(self.rtc_tcp_port),
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
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

    async def ensure_runtime(self, progress: Callable[[str], None]) -> None:
        """Start the shared voice service the worker speaks through.

        The first start installs its runtime, as /voice on does in the terminal;
        progress gets each setup step. A service another Kollab left running runs
        that Kollab's code, so an idle one is replaced. An injected worker or
        KOLLAB_VOICE_PYTHON means voice is run by hand.
        """
        if self._binary() is None:
            # Say so before minutes of setup, not after.
            raise RuntimeHostError("runtime_unavailable", 503, LIVEKIT_MISSING)
        if self._worker_argv or os.environ.get("KOLLAB_VOICE_PYTHON"):
            return
        from kollabor_voice.bootstrap import prepared_runtime_python
        from kollabor_voice.client import VoiceClient

        client = VoiceClient()
        await self._retire_stale_service(
            client, runtime_ready=prepared_runtime_python() is not None
        )
        try:
            await client.ensure(lambda state: progress(state.get("detail") or "Starting voice"))
        except RuntimeError as exc:  # VoiceError, or a voice root too long for its socket
            raise RuntimeHostError("voice_setup_failed", 503, str(exc), retryable=True) from exc
        except OSError as exc:
            raise RuntimeHostError(
                "voice_setup_failed", 503, f"Voice setup failed: {exc}", retryable=True
            ) from exc

    @staticmethod
    async def _retire_stale_service(client: Any, *, runtime_ready: bool) -> None:
        """Stop a voice service that runs other code than this Kollab's, unless a chat is using it.

        The service keeps the code of the Kollab that started it until it exits.
        Without this Kollab's runtime prepared it must go before setup can install
        that runtime, so a chat using it keeps voice off here. With the runtime
        prepared, a chat using it keeps it: its worker and ours speak through it
        as it is, and it is replaced at a start that finds it idle.
        """
        from kollabor_voice.control import install_identity

        try:
            status = await client.call("status")
        except (RuntimeError, OSError, ValueError):
            return  # none answering (VoiceError is a RuntimeError): ensure() starts this version's
        if runtime_ready and (status.get("install") or {}).get("code") == install_identity()["code"]:
            return
        if status.get("owner"):
            if runtime_ready:
                return
            raise RuntimeHostError(
                "voice_service_busy",
                409,
                "Voice is on in another chat, on a voice service from an older Kollab. "
                "Turn voice off there, then try again.",
                retryable=True,
            )
        pid = status.get("pid")
        if not isinstance(pid, int):
            return
        logger.info(
            "Replacing idle voice service pid %s: runtime %s, code from %s",
            pid,
            "prepared" if runtime_ready else "not prepared",
            (status.get("install") or {}).get("path", "an install that does not say"),
        )
        for sig, wait_s in ((signal.SIGTERM, STALE_SERVICE_EXIT_S), (signal.SIGKILL, 2.0)):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return  # gone, and its lock with it: a new service can start
                await asyncio.sleep(0.1)

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
                    "Voice support is not installed. Turn voice on again to install it.",
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
        with self._log("livekit-worker.log") as log:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
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
            if text.startswith(WORKER_ERROR_PREFIX):
                raise RuntimeHostError(
                    "runtime_unavailable",
                    503,
                    text[len(WORKER_ERROR_PREFIX) :].strip() or "Voice worker exited before joining.",
                    retryable=True,
                )
            if text.startswith(WORKER_SHORTAGE_PREFIX):
                raise RuntimeHostError(
                    "resource_shortage",
                    503,
                    text[len(WORKER_SHORTAGE_PREFIX) :].strip()
                    or "A voice model ran out of memory.",
                    retryable=True,
                )

    @staticmethod
    async def _drain(process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while await process.stdout.readline():
            pass

    async def wait_exited(self, handle: object) -> None:
        """Return once the worker has exited, stopped by the engine or by itself."""
        assert isinstance(handle, WorkerHandle)
        await handle.process.wait()  # at exit, not at pipe close (gh-119710)

    async def wait_abandoned(self, binding: VoiceSessionBinding, handle: object) -> bool:
        """True once the worker's room has had no user for USER_GONE_GRACE_S.

        False when the worker exits first. Counted from the worker joining, so a
        browser that never joins ends it too. A server that cannot answer counts
        as the user still there: only a room seen empty ends.
        """
        assert isinstance(handle, WorkerHandle)
        room = room_name_for(binding)
        seen = time.monotonic()
        while handle.process.returncode is None:
            await asyncio.sleep(PRESENCE_POLL_S)
            if await self._user_in_room(room) is not False:
                seen = time.monotonic()
            elif time.monotonic() - seen >= USER_GONE_GRACE_S:
                return handle.process.returncode is None
        return False

    async def _user_in_room(self, room: str) -> bool | None:
        """Whether a browser is in the room, from the server's RoomService; None if it cannot say."""
        token = mint_livekit_jwt(
            self._api_key,
            self._api_secret,
            identity="engine",
            video={"room": room, "roomAdmin": True},
            expires_at_s=int(time.time()) + 60,
        )
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/twirp/livekit.RoomService/ListParticipants",
            json.dumps({"room": room}).encode(),
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )

        def ask() -> bool | None:
            try:
                # No proxy: HTTP_PROXY or the system proxy would answer for 127.0.0.1.
                with _NO_PROXY.open(request, timeout=3) as response:
                    participants = json.loads(response.read()).get("participants") or []
            except urllib.error.HTTPError as exc:
                return False if exc.code == 404 else None  # no such room: nobody in it
            except (OSError, ValueError):
                return None
            return any(str(p.get("identity", "")).startswith(USER_PREFIX) for p in participants)

        return await asyncio.to_thread(ask)

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
            identity=f"{USER_PREFIX}{binding.owner_id}-{binding.voice_session_id[:8]}",
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
            # SIGKILL at once: on SIGTERM the server drains, waiting for rooms
            # that only this engine could end (see _SUPERVISE).
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self._server.pid, signal.SIGKILL)
            await self._server.wait()
            self._server = None
