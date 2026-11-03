"""Engine-owned LiveKit server and speech-worker processes for voice rooms.

The server takes signaling and ICE over UDP on 127.0.0.1 on free ports, with a
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

from kollabor_config.proc_ctl import pid_alive

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
STALE_SERVICE_EXIT_S = (
    10.0  # an older Kollab's voice service gets this long after SIGTERM
)
# A closed or reloaded page sends no stop. Its room waits this long for the user
# to come back (a reload that starts voice again rejoins it), then ends.
USER_GONE_GRACE_S = 60.0
PRESENCE_POLL_S = 5.0
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
USER_PREFIX = (
    "user-"  # the browser's identity in a room (sign); the worker's is "worker-"
)
LIVEKIT_MISSING = (
    "livekit-server is not installed. Install it with brew install livekit."
)

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


def _free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class WorkerHandle:
    process: asyncio.subprocess.Process


PHONE_BRIDGE_READY_TIMEOUT_S = 10.0
# Downstream PCM buffer cap: 600 ms of 24 kHz PCM16LE mono, matching the
# broker's QUEUE_BYTES contract. Ticks normally drain it every interval; a
# stalled caller must not build seconds of lag, so overflow drops the
# OLDEST audio first and every dropped byte is counted (out_dropped_bytes).
PHONE_BRIDGE_OUT_CAP_BYTES = 28_800
# Graceful exit window for a bridge child that DID report ready. Kept
# under lane A's 5 s shutdown cap so close() always finishes inside it.
PHONE_BRIDGE_CLOSE_GRACE_S = 3.0


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate a spawned child's process group (SIGTERM, then SIGKILL).

    Module-level so both PhoneBridgeHandle.close and LiveKitRuntime share it;
    PhoneBridgeHandle has no _kill_group of its own (aquamarine's live run).
    """
    if process.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError, PermissionError:
            # The group is gone (or not signalable) but the child may still
            # be waiting to be reaped: returncode stays None until the event
            # loop reaps it, and close() must not hand back an unreaped
            # handle. Wait briefly instead of returning bare.
            try:
                await asyncio.wait_for(process.wait(), 3.0)
            except asyncio.TimeoutError:
                pass
            return
        try:
            await asyncio.wait_for(process.wait(), 3.0)
            return
        except asyncio.TimeoutError:
            continue


class PhoneBridgeHandle:
    """Engine-side handle for one bridge child (satisfies PhoneBridge)."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._out = bytearray()
        self._out_dropped = 0
        self.bridge_errors = 0
        self.bridge_last_error: str | None = None
        self._reader_task: asyncio.Task[None] | None = None
        # True only once start() saw the child's ready handshake; close()
        # uses it to pick graceful-exit vs immediate kill.
        self._ready = False

    async def start(self, start_line: str) -> None:
        """Write the start line and wait for the child's ready handshake."""
        stdin = self.process.stdin
        stdout = self.process.stdout
        if stdin is None or stdout is None:
            raise RuntimeHostError(
                "voice_runtime_not_ready",
                500,
                "Phone audio bridge pipes are missing.",
                retryable=False,
            )
        stdin.write((start_line + "\n").encode())
        await stdin.drain()
        line = await asyncio.wait_for(stdout.readline(), PHONE_BRIDGE_READY_TIMEOUT_S)
        try:
            greeting = json.loads(line.decode() or "{}")
        except json.JSONDecodeError, UnicodeDecodeError:
            greeting = {}
        if greeting.get("type") != "ready":
            code = str(greeting.get("code") or "bridge_not_ready")
            raise RuntimeHostError(
                "voice_runtime_not_ready",
                503,
                f"Phone audio bridge failed to start: {code}",
                retryable=True,
            )
        self._ready = True
        self._reader_task = asyncio.ensure_future(self._read_loop())

    async def _read_loop(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            return
        while True:
            line = await stdout.readline()
            if not line:
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError, UnicodeDecodeError:
                continue
            if message.get("type") == "audio":
                try:
                    self._out.extend(base64.b64decode(str(message.get("pcm", ""))))
                except ValueError, TypeError:
                    continue
                if len(self._out) > PHONE_BRIDGE_OUT_CAP_BYTES:
                    excess = len(self._out) - PHONE_BRIDGE_OUT_CAP_BYTES
                    del self._out[:excess]
                    self._out_dropped += excess
            elif message.get("type") == "audio_dropped":
                # The child's own outbound-queue drops, reported over IPC so
                # the phone's dropped count covers every loss point. Never a
                # bridge error: it is an audio accounting line, not a fault.
                try:
                    self._out_dropped += max(0, int(message.get("bytes", 0)))
                except TypeError, ValueError:
                    continue
            else:
                # A mid-call bridge error must not vanish: the engine would
                # keep ticking into silence with no failure signal.
                self.bridge_errors += 1
                self.bridge_last_error = str(message.get("code") or message.get("type"))

    async def send_mic(self, n: int, pcm: bytes) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.is_closing():
            raise RuntimeError("phone bridge stdin closed")
        line = json.dumps(
            {"type": "mic", "n": int(n), "pcm": base64.b64encode(pcm).decode()},
            separators=(",", ":"),
        )
        stdin.write((line + "\n").encode())
        await stdin.drain()

    def take_output(self) -> bytes:
        data = bytes(self._out)
        self._out.clear()
        return data

    @property
    def out_dropped_bytes(self) -> int:
        """Total downstream audio dropped, in bytes: this handle's buffer-cap
        drops plus the child's own outbound-queue drops (reported over IPC).
        The broker converts to samples (PCM16: bytes // 2) for the phone."""
        return self._out_dropped

    @property
    def out_dropped(self) -> int:
        """Alias of out_dropped_bytes (bytes, not samples)."""
        return self._out_dropped

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        stdin = self.process.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.write(b'{"type":"close"}\n')
            try:
                await stdin.drain()
            except ConnectionResetError, BrokenPipeError:
                pass
        if self._ready:
            # Reported ready: give it a short graceful window (always inside
            # lane A's shutdown cap), then kill the group if it overstays.
            try:
                await asyncio.wait_for(self.process.wait(), PHONE_BRIDGE_CLOSE_GRACE_S)
            except asyncio.TimeoutError:
                await _kill_process_group(self.process)
        else:
            # Never reported ready (bad greeting, start timeout, pipes
            # missing): no graceful window — end the child at once so the
            # caller's except path cannot outlive any shutdown cap.
            await _kill_process_group(self.process)
        stdin = self.process.stdin
        if stdin is not None:
            stdin.close()
        transport = getattr(self.process, "transport", None)
        if transport is not None:
            transport.close()


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
        # LiveKit's TCP mux ignores --bind and listens on every interface. All
        # media clients here run on this host, including the PhoneLink bridge.
        # A filtered UDP mux keeps the actual media socket on loopback as well.
        self.rtc_udp_port = _free_port(socket.SOCK_DGRAM)
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
            rtc_config = json.dumps(
                {
                    "rtc": {
                        "tcp_port": 0,
                        "allow_tcp_fallback": False,
                        "udp_port": str(self.rtc_udp_port),
                        "node_ip": "127.0.0.1",
                        "use_external_ip": False,
                        "enable_loopback_candidate": True,
                        "ips": {"includes": ["127.0.0.1/32"]},
                    }
                }
            )
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
                    "--config-body",
                    rtc_config,
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
            await client.ensure(
                lambda state: progress(state.get("detail") or "Starting voice")
            )
        except (
            RuntimeError
        ) as exc:  # VoiceError, or a voice root too long for its socket
            raise RuntimeHostError(
                "voice_setup_failed", 503, str(exc), retryable=True
            ) from exc
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
        except RuntimeError, OSError, ValueError:
            return  # none answering (VoiceError is a RuntimeError): ensure() starts this version's
        if (
            runtime_ready
            and (status.get("install") or {}).get("code") == install_identity()["code"]
        ):
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
        for sig, wait_s in (
            (signal.SIGTERM, STALE_SERVICE_EXIT_S),
            (signal.SIGKILL, 2.0),
        ):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                if not pid_alive(
                    pid
                ):  # os.kill(pid, 0) signals the process on Windows (#460)
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
        from kollabor_voice.module_runtime import module_command

        # Never put the Engine's site-packages on the prepared interpreter's
        # import path. Load Kollab's pure-Python package from its exact directory
        # while leaving stdlib and third-party imports to the prepared runtime.
        return module_command(python, "kollabor_voice.livekit_worker"), {
            "PYTHONPATH": ""
        }

    def _child_env(self, cmd_env: dict[str, str]) -> dict[str, str]:
        env = dict(os.environ)
        env.update(cmd_env)
        env.update(self._worker_env)
        # Child imports must use its own interpreter's site-packages, never the
        # Engine's paths inherited from its launcher or a caller override.
        from kollabor_voice.module_runtime import isolated_env

        return isolated_env(env)

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
        env = self._child_env(cmd_env)
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

    async def start_phone_bridge(
        self, binding: VoiceSessionBinding
    ) -> PhoneBridgeHandle:
        """Spawn the LiveKit media bridge child for one phone call.

        The child authenticates with a token scoped to this binding's room
        and generation; the token travels on stdin, never in argv.
        """
        cmd, cmd_env = await self._bridge_command()
        room = room_name_for(binding)
        token = mint_livekit_jwt(
            self._api_key,
            self._api_secret,
            identity=f"phone-{binding.voice_session_id}",
            video={
                "room": room,
                "roomJoin": True,
                "canPublish": True,
                "canPublishSources": ["microphone"],
                "canSubscribe": True,
            },
            expires_at_s=int(time.time()) + WORKER_TOKEN_TTL_S,
        )
        start = json.dumps(
            {
                "type": "start",
                "url": self.livekit_url,
                "token": token,
                "rate_in": 16000,
                "rate_out": 24000,
            },
            separators=(",", ":"),
        )
        env = self._child_env(cmd_env)
        with self._log("phone-audio-bridge.log") as log:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                start_new_session=True,
            )
        handle = PhoneBridgeHandle(process)
        try:
            await handle.start(start)
        except BaseException:
            await handle.close()
            raise
        return handle

    async def _bridge_command(self) -> tuple[list[str], dict[str, str]]:
        # Same prepared interpreter and package loader as the worker; the bridge
        # is a different module in the same voice package.
        cmd, env = self._worker_command()
        if cmd[-1].endswith("kollabor_voice.livekit_worker"):
            cmd = list(cmd)
            cmd[-1] = "kollabor_voice.phone_audio_bridge"
        return cmd, env

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
                    text[len(WORKER_ERROR_PREFIX) :].strip()
                    or "Voice worker exited before joining.",
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

    async def wait_abandoned(
        self, binding: VoiceSessionBinding, handle: object
    ) -> bool:
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
            except OSError, ValueError:
                return None
            return any(
                str(p.get("identity", "")).startswith(USER_PREFIX) for p in participants
            )

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
        await _kill_process_group(process)

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
