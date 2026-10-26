"""Service side of KVA1: room audio in to the one STT owner, speech out of the one TTS owner.

The LiveKit worker never loads a model. It asks the lease owner for a one-time
capability (`audio_capability` op), opens `audio.sock`, and streams PCM frames.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import hmac
import logging
import os
import secrets
import socket
import struct
import sys
import time
from pathlib import Path

from . import kva

log = logging.getLogger(__name__)

CAPABILITY_TTL_S = 30
MAX_SPEAK_CHARS = 4096
STT_RATE = 16000
# A segment is at most 8 s, so 4 queued segments is the per-stream buffer; a
# fuller queue fails with backpressure instead of dropping speech.
MAX_QUEUED_SEGMENTS = 4


def audio_socket_path(root: Path) -> Path:
    path = root / "audio.sock"
    if len(os.fsencode(path)) >= 100:
        raise RuntimeError("Voice data path is too long for a local socket")
    return path


def peer_uid(sock) -> int | None:
    """Uid of the connecting process, or None where the OS cannot say."""
    try:
        if sys.platform == "darwin":  # LOCAL_PEERCRED -> struct xucred
            cred = sock.getsockopt(0, 1, struct.calcsize("II"))
            return struct.unpack("II", cred)[1]
        if hasattr(socket, "SO_PEERCRED"):
            return struct.unpack(
                "3i",
                sock.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                ),
            )[1]
    except OSError:
        pass
    return None


class AudioIPC:
    def __init__(self, service):
        self.service = service
        self.server = None
        self.capabilities = {}  # secret -> grant
        self.fence = {}  # voice_session_id -> newest generation allowed
        self.streams = set()

    async def start(self):
        path = audio_socket_path(self.service.root)
        if path.exists():
            path.unlink()
        self.server = await asyncio.start_unix_server(self.handle, path=str(path))
        os.chmod(path, 0o600)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self.streams):
            task.cancel()
        await asyncio.gather(*self.streams, return_exceptions=True)
        with contextlib.suppress(OSError, RuntimeError):
            audio_socket_path(self.service.root).unlink()

    # -- control RPC (called from VoiceService.dispatch after lease auth) ----

    def mint(self, request, epoch):
        session, generation = str(request["voice_session_id"]), int(
            request["generation"]
        )
        direction = request["direction"]
        if direction not in {"stt_input", "tts_output"}:
            raise kva.FrameError("invalid_audio", "Unknown audio direction")
        if generation < self.fence.get(session, 0):
            raise kva.FrameError("stale_generation", "Voice generation is stale")
        self.fence[session] = generation
        now = time.monotonic()
        self.capabilities = {
            k: v for k, v in self.capabilities.items() if v["expires"] > now
        }
        secret = secrets.token_hex(32)
        self.capabilities[secret] = {
            "owner_id": str(request["owner_id"]),
            "voice_session_id": session,
            "generation": generation,
            "epoch": epoch,
            "direction": direction,
            "expires": now + CAPABILITY_TTL_S,
        }
        return {"capability": secret, "expires_in": CAPABILITY_TTL_S}

    def stop(self, request):
        """Fence out `generation` and older for a session; open streams end."""
        session, generation = str(request["voice_session_id"]), int(
            request["generation"]
        )
        self.fence[session] = max(self.fence.get(session, 0), generation + 1)
        return {"fenced_below": self.fence[session]}

    # -- stream ---------------------------------------------------------------

    def grant(self, opened):
        """Validate an OPEN body and consume its one-time capability."""
        secret = str(opened.get("one_time_capability", ""))
        grant = None
        for key in list(self.capabilities):
            if hmac.compare_digest(key, secret):
                grant = self.capabilities.pop(key)
        if not grant or grant["expires"] < time.monotonic():
            raise kva.FrameError("unauthenticated", "Audio capability is invalid")
        for field in ("owner_id", "voice_session_id", "direction"):
            if opened.get(field) != grant[field]:
                raise kva.FrameError("target_mismatch", f"Audio {field} mismatch")
        if opened.get("generation") != grant["generation"]:
            raise kva.FrameError("stale_generation", "Audio generation mismatch")
        if str(opened.get("service_lease_epoch")) != str(grant["epoch"]):
            raise kva.FrameError("stale_generation", "Voice lease changed")
        return grant

    def live(self, grant):
        owner = self.service.owner
        return (
            owner is not None
            and owner["epoch"] == grant["epoch"]
            and self.fence.get(grant["voice_session_id"]) == grant["generation"]
        )

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.streams.add(task)
        seq = 0

        async def send(kind, **body):
            nonlocal seq
            writer.write(kva.control(kind, seq, **body))
            await writer.drain()

        try:
            uid = peer_uid(writer.get_extra_info("socket"))
            if uid is not None and uid != os.getuid():
                raise kva.FrameError("forbidden", "Audio peer is not this user")
            frame = await asyncio.wait_for(kva.read_frame(reader), 5)
            if not frame or frame[0] != kva.OPEN:
                raise kva.FrameError("invalid_audio", "First frame must be OPEN")
            opened = kva.parse_control(frame[3])
            grant = self.grant(opened)
            stream_id = str(opened.get("stream_id", ""))[:64]
            if grant["direction"] == "stt_input":
                await self.listen(reader, send, grant, stream_id)
            else:
                await self.speak(reader, writer, send, grant, stream_id)
        except asyncio.CancelledError:
            raise
        except kva.FrameError as exc:
            with contextlib.suppress(Exception):
                await send(kva.ERROR, code=exc.code, message=str(exc))
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("Audio stream failed")
            with contextlib.suppress(Exception):
                await send(
                    kva.ERROR, code="internal_error", message="Audio stream failed"
                )
        finally:
            self.streams.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def parakeet_ready(self):
        stt = self.service.stt
        status = stt.status() if stt else {}
        # No Whisper stand-in in a voice room: it must be Parakeet, ready.
        if status.get("engine") != "parakeet" or status.get("state") != "ready":
            raise kva.FrameError(
                "models_not_ready",
                status.get("detail") or "Parakeet is not ready",
            )

    async def listen(self, reader, send, grant, stream_id):
        from .audio import SpeechSegmenter

        service = self.service
        self.parakeet_ready()
        segmenter = (service.segmenter_factory or SpeechSegmenter)()
        queue = asyncio.Queue(maxsize=MAX_QUEUED_SEGMENTS)
        failure = []

        async def worker():
            while (item := await queue.get()) is not None:
                segment, enqueued = item
                try:
                    self.parakeet_ready()
                    started = time.monotonic()
                    text, language = await asyncio.to_thread(
                        service.stt.transcribe, segment.audio, ()
                    )
                    self.parakeet_ready()
                except kva.FrameError as exc:
                    failure.append(exc)
                    return
                except Exception:
                    log.exception("Room transcription failed")
                    failure.append(
                        kva.FrameError("provider_error", "Transcription failed")
                    )
                    return
                if text and text.strip():
                    await send(
                        kva.RESULT,
                        event="transcript",
                        text=text.strip(),
                        language=language,
                        final=bool(segment.final),
                        stt_ms=round((time.monotonic() - started) * 1000),
                        queued_ms=round((started - enqueued) * 1000),
                    )

        consumer = asyncio.create_task(worker())
        expected, was_active = 1, False
        try:
            await send(kva.RESULT, event="open", stream_id=stream_id)
            while True:
                if failure:
                    raise failure[0]
                if not self.live(grant):
                    raise kva.FrameError("stale_generation", "Voice room was replaced")
                frame = await kva.read_frame(reader)
                if frame is None or frame[0] in {kva.CANCEL}:
                    return
                kind, _, sequence, payload = frame
                if kind == kva.END:
                    break
                if kind != kva.PCM or sequence != expected:
                    raise kva.FrameError("invalid_audio", "Audio frames out of order")
                expected += 1
                audio = kva.pcm_from_bytes(payload)
                if len(audio) > STT_RATE * kva.MAX_FRAME_SECONDS:
                    raise kva.FrameError("invalid_audio", "Audio frame over 100 ms")
                now = time.time()
                for segment in segmenter.feed(audio, now - len(audio) / STT_RATE):
                    try:
                        queue.put_nowait((segment, time.monotonic()))
                    except asyncio.QueueFull:
                        raise kva.FrameError(
                            "backpressure", "Transcription is behind; retry"
                        ) from None
                if segmenter.active and not was_active:
                    await send(kva.RESULT, event="speech_start")
                was_active = bool(segmenter.active)
            tail = segmenter.flush()
            if tail:
                await queue.put((tail, time.monotonic()))
            await queue.put(None)
            await consumer
            if failure:
                raise failure[0]
            await send(kva.RESULT, event="closed")
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    async def speak(self, reader, writer, send, grant, stream_id):
        service = self.service
        speech = service.speech
        if speech is None or speech.status().get("state") != "ready":
            raise kva.FrameError(
                "models_not_ready",
                (speech.status().get("detail") if speech else "") or "Speech not ready",
            )
        await send(kva.RESULT, event="open", stream_id=stream_id)
        frame = await asyncio.wait_for(kva.read_frame(reader), 10)
        if not frame or frame[0] != kva.SPEAK:
            raise kva.FrameError("invalid_audio", "Expected SPEAK after OPEN")
        text = str(kva.parse_control(frame[3]).get("text", ""))[:MAX_SPEAK_CHARS]
        if not text.strip():
            raise kva.FrameError("invalid_audio", "Nothing to speak")
        cancelled = asyncio.Event()

        async def watch():  # CANCEL, END or a dropped socket all stop playback
            with contextlib.suppress(Exception):
                await kva.read_frame(reader)
            cancelled.set()

        watcher = asyncio.create_task(watch())
        chunks = speech.stream(text)
        # The generator holds the speech engine's RLock across its yields, so every
        # step and the close run on this one thread: an RLock refuses a release
        # from any thread but the one that took it.
        thread = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="voice-speak")
        loop = asyncio.get_running_loop()
        done = object()
        sequence, started = 0, time.monotonic()
        first_audio_ms = None
        try:
            while not cancelled.is_set():
                if not self.live(grant):
                    raise kva.FrameError("stale_generation", "Voice room was replaced")
                item = await loop.run_in_executor(thread, next, chunks, done)
                if item is done:
                    break
                samples, rate = item
                import numpy as np

                samples = np.asarray(samples, dtype=np.float32).reshape(-1)
                await send(
                    kva.RESULT,
                    event="format",
                    sample_rate_hz=int(rate),
                    channels=1,
                    encoding="pcm_f32le",
                )
                payloads = list(kva.split_pcm(samples, int(rate)))
                for index, payload in enumerate(payloads):
                    if cancelled.is_set() or not self.live(grant):
                        break
                    sequence += 1
                    writer.write(
                        kva.encode(
                            kva.PCM,
                            sequence,
                            payload,
                            kva.FLAG_FRAME_END if index == len(payloads) - 1 else 0,
                        )
                    )
                    await writer.drain()
                    if first_audio_ms is None:
                        first_audio_ms = round((time.monotonic() - started) * 1000)
            if not cancelled.is_set():
                await send(
                    kva.RESULT,
                    event="done",
                    first_audio_ms=first_audio_ms,
                    total_ms=round((time.monotonic() - started) * 1000),
                )
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            # Drain the sync generator off the loop so the VibeVoice worker resets.
            try:
                await loop.run_in_executor(thread, chunks.close)
            finally:
                thread.shutdown(wait=False)
