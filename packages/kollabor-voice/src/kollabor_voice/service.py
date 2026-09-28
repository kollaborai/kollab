"""One device process owns models, microphone, transcript and speaker.

The protocol is local-only and contains no provider credentials or hosted AI calls.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import logging
import os
import queue
import signal
import time
import uuid
from pathlib import Path

from .control import (
    LEASE_SECONDS,
    MAX_REQUEST,
    PROTOCOL,
    VoiceError,
    atomic_json,
    prepare_root,
    service_root,
    socket_path,
)
from .store import TranscriptLog, utc

log = logging.getLogger(__name__)


class VoiceService:
    def __init__(
        self,
        root: Path,
        *,
        load_assets=None,
        load_models=None,
        recorder_factory=None,
        segmenter_factory=None,
        player=None,
    ):
        self.root = root
        prepare_root(root)
        self.instance_id = str(uuid.uuid4())
        self.setup = {"state": "starting", "detail": "Preparing voice support"}
        self.models = None
        self.audio_warmup_seconds = None
        self.transcript = None
        self.output = None
        self.owner = None
        self.delegates = {}
        self.withdrawn_claims = {}
        self.worker_failures = set()
        self.last_activation = 0
        self.recorder = None
        self.segmenter = None
        self.last_seq = None
        self.error = None
        self.transcribing = False
        self.last_transcript = None
        self.segments = asyncio.Queue(maxsize=8)
        self.tasks = []
        self.setup_task = None
        self.server = None
        self.lifecycle = asyncio.Lock()
        self.load_assets = load_assets
        self.load_models = load_models
        self.recorder_factory = recorder_factory
        self.segmenter_factory = segmenter_factory
        self.player = player
        from .laya import LayaWorker

        self.classifier = LayaWorker(root)
        self.classifier_jobs = {}
        self.speech_review_jobs = {}
        self.speech_review_started = {}

    def progress(self, **state):
        self.setup = state
        atomic_json(
            self.root / "setup.json",
            {**state, "pid": os.getpid(), "updated_at": time.time()},
        )

    async def start(self):
        path = socket_path(self.root)
        # Caller must hold the singleton lock before removing a stale endpoint.
        if path.exists():
            path.unlink()
        self.server = await asyncio.start_unix_server(
            self.handle, path=str(path), limit=MAX_REQUEST
        )
        os.chmod(path, 0o600)
        self.tasks = [
            asyncio.create_task(self.watch(), name="voice-watch"),
            asyncio.create_task(self.transcribe(), name="voice-transcribe"),
        ]
        self.setup_task = asyncio.create_task(self.prepare(), name="voice-setup")

    async def prepare(self):
        try:
            from .assets import ensure_models
            from .audio import LocalModels, play_audio
            from .output import SpeechQueue

            loop = asyncio.get_running_loop()

            def update(**state):
                loop.call_soon_threadsafe(lambda: self.progress(**state))

            paths = await asyncio.to_thread(
                self.load_assets or ensure_models, self.root, update
            )
            self.progress(state="loading", detail="Preparing transcription and speech")
            if self.models is None:
                models = await asyncio.to_thread(self.load_models or LocalModels, paths)
                self.progress(
                    state="warming", detail="Warming transcription and speech"
                )
                started = time.monotonic()
                await asyncio.to_thread(models.warmup)
                self.audio_warmup_seconds = time.monotonic() - started
                self.models = models
            if self.transcript is None:
                self.transcript = TranscriptLog(self.root, self.instance_id)
            for task in list(self.tasks):
                if task.done() and task.get_name() in {
                    "voice-output",
                    "voice-transcribe",
                }:
                    if not task.cancelled():
                        task.exception()
                    self.tasks.remove(task)
                    if task.get_name() == "voice-output":
                        self.output = None
                    else:
                        self.tasks.append(
                            asyncio.create_task(
                                self.transcribe(), name="voice-transcribe"
                            )
                        )
            self.worker_failures.clear()
            if self.output is None:
                self.output = SpeechQueue(
                    self.root, self.models.synthesize, self.player or play_audio
                )
                self.tasks.append(
                    asyncio.create_task(self.output.run(), name="voice-output")
                )
            self.progress(state="ready", detail="Whisper base and speech ready")
            async with self.lifecycle:
                if (
                    self.owner
                    and time.monotonic() - self.owner["heartbeat"] < LEASE_SECONDS
                ):
                    await self.start_capture()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Voice setup failed")
            self.error = str(exc)
            self.progress(state="error", detail=f"{exc}. Run /voicemode retry")

    def authorize(self, token, epoch):
        if (
            not self.owner
            or time.monotonic() - self.owner["heartbeat"] >= LEASE_SECONDS
        ):
            raise VoiceError(
                "lease_expired", "Voice connection expired; run /voicemode retry"
            )
        if token != self.owner["token"] or epoch != self.owner["epoch"]:
            raise VoiceError(
                "not_owner",
                "Voice is active in another chat; run /voicemode on",
            )

    def status(self):
        state = self.setup.get("state", "starting")
        age = None
        if self.recorder and self.recorder._last_frame_ts is not None:
            age = time.monotonic() - self.recorder._last_frame_ts
        if state == "ready":
            if self.error:
                state = "error"
            elif not self.owner:
                state = "off"
            elif age is not None and age < 1.5:
                state = "listening"
            else:
                state = "starting"
        return {
            **self.setup,
            "state": state,
            "protocol": PROTOCOL,
            "pid": os.getpid(),
            "instance_id": self.instance_id,
            "owner": self.owner["client_id"] if self.owner else None,
            "owner_epoch": self.owner["epoch"] if self.owner else None,
            "device": getattr(self.recorder, "device_name", None),
            "frame_age": age,
            "level": getattr(self.recorder, "level", 0),
            "transcribing": self.transcribing,
            "speech_active": bool(self.segmenter and self.segmenter.active),
            "input_depth": self.segments.qsize(),
            "last_transcript": self.last_transcript,
            "transcript_path": str(self.transcript.path) if self.transcript else None,
            "output": (
                self.output.status() if self.output else {"state": "idle", "depth": 0}
            ),
            "error": self.error,
            "cursor": self.transcript.seq if self.transcript else 0,
            "stream_id": self.instance_id,
            "ready": self.setup.get("state") == "ready",
            "classifier": self.classifier.status(),
            "audio_warmup_seconds": self.audio_warmup_seconds,
        }

    async def dispatch(self, request):
        op = request.get("op")
        if op in {"hello", "status"}:
            return self.status()
        if op == "classifier_prepare":
            self.classifier.prepare()
            return self.classifier.status()
        if op == "withdraw":
            async with self.lifecycle:
                self.withdrawn_claims = {
                    key: at
                    for key, at in self.withdrawn_claims.items()
                    if time.monotonic() - at < 300
                }
                if len(self.withdrawn_claims) >= 1024:
                    raise VoiceError(
                        "busy", "Too many voice activations; wait before retrying"
                    )
                self.withdrawn_claims[request["claim_id"]] = time.monotonic()
                if (
                    self.owner
                    and request["client_id"] == self.owner["client_id"]
                    and request["claim_id"] == self.owner.get("claim_id")
                ):
                    await self.release_owner()
            return self.status()
        if op == "claim":
            async with self.lifecycle:
                if request.get("claim_id") in self.withdrawn_claims:
                    raise VoiceError("cancelled", "Voice activation was cancelled")
                activated = float(request["activated_at"])
                if activated < self.last_activation:
                    raise VoiceError(
                        "superseded", "A newer voice activation owns this device"
                    )
                if (
                    self.owner
                    and request["client_id"] == self.owner["client_id"]
                    and request.get("claim_id") == self.owner.get("claim_id")
                ):
                    self.owner["heartbeat"] = time.monotonic()
                    return self.claim_result()
                await self.release_owner()
                self.last_activation = activated
                self.error = None
                self.last_transcript = None
                self.owner = dict(
                    client_id=str(request["client_id"])[:200],
                    token=uuid.uuid4().hex,
                    epoch=uuid.uuid4().hex,
                    claim_id=request.get("claim_id"),
                    heartbeat=time.monotonic(),
                )
                if self.setup["state"] == "ready":
                    await self.start_capture()
                return self.claim_result()
        if op == "retry":
            if self.setup_task is None or self.setup_task.done():
                self.error = None
                self.progress(state="starting", detail="Retrying voice setup")
                self.setup_task = asyncio.create_task(
                    self.prepare(), name="voice-setup"
                )
            return self.status()
        delegated = self.delegates.get(request.get("token"))
        if delegated:
            if op not in {
                "voice_out",
                "receipts",
                "speech_review_submit",
                "speech_review_result",
            }:
                raise VoiceError(
                    "scope", "Delegated speech capability cannot control the microphone"
                )
            self.authorize(
                self.owner["token"] if self.owner else None, request.get("epoch")
            )
            if delegated["epoch"] != request.get("epoch"):
                raise VoiceError("not_owner", "Delegated speech capability expired")
            request["producer_id"] = delegated["producer_id"]
        else:
            self.authorize(request.get("token"), request.get("epoch"))
        if op == "delegate":
            if len(self.delegates) >= 128:
                raise VoiceError("queue_full", "Voice delegation limit reached")
            token = uuid.uuid4().hex
            self.delegates[token] = {
                "epoch": self.owner["epoch"],
                "producer_id": str(request["producer_id"])[:200],
            }
            return {"token": token, **self.delegates[token]}
        if op == "heartbeat":
            self.owner["heartbeat"] = time.monotonic()
            return self.status()
        if op == "classifier_submit":
            for key, task in list(self.classifier_jobs.items()):
                if task.done():
                    del self.classifier_jobs[key]
            if self.classifier_jobs:
                raise VoiceError(
                    "classifier_busy", "A classifier decision is already pending"
                )
            identity = uuid.uuid4().hex
            task = asyncio.create_task(
                self.classifier.decide(
                    request["records"],
                    str(request.get("context", "")),
                    request.get("transcript_context", []),
                    request.get("context_lines", 10),
                )
            )
            task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
            self.classifier_jobs[identity] = task
            return {"job_id": identity}
        if op == "classifier_result":
            task = self.classifier_jobs.get(request["job_id"])
            if task is None or task.cancelled():
                raise VoiceError(
                    "classifier_expired",
                    "Classifier result expired; speech remains pending",
                )
            return (
                {"done": True, "result": task.result()}
                if task.done()
                else {"done": False}
            )
        if op == "release":
            async with self.lifecycle:
                # The owner may have changed while waiting for capture teardown.
                self.authorize(request.get("token"), request.get("epoch"))
                await self.release_owner()
            return self.status()
        if op == "speech_review_submit":
            from .speech_review import format_decision

            for key, job in list(self.speech_review_jobs.items()):
                if (
                    job.done()
                    and time.monotonic() - self.speech_review_started[key] > 30
                ):
                    del self.speech_review_jobs[key]
                    del self.speech_review_started[key]
            if len(self.speech_review_jobs) >= 64:
                raise VoiceError("classifier_busy", "Speech review queue is full")
            text = str(request["text"])
            identity = uuid.uuid4().hex

            async def review():
                fixed = format_decision(text)
                return (
                    fixed.to_wire()
                    if fixed
                    else await self.classifier.review_speech(text)
                )

            task = asyncio.create_task(review())
            task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
            self.speech_review_jobs[identity] = task
            self.speech_review_started[identity] = time.monotonic()
            return {"job_id": identity}
        if op == "speech_review_result":
            task = self.speech_review_jobs.get(request["job_id"])
            if task is None or task.cancelled():
                raise VoiceError("classifier_expired", "Speech review expired")
            if not task.done():
                return {"done": False}
            del self.speech_review_jobs[request["job_id"]]
            del self.speech_review_started[request["job_id"]]
            return {"done": True, "result": task.result()}
        if op == "read":
            if not self.transcript:
                return {"records": [], "cursor": 0, "stream_id": self.instance_id}
            return self.transcript.read(
                request["stream_id"], int(request["after"]), self.owner["epoch"]
            )
        if op == "voice_out":
            from .speech_review import format_decision

            fixed = format_decision(str(request["text"]))
            if fixed and fixed.decision == "rewrite":
                return {"status": "rewrite", "review": fixed.to_wire()}
            if not self.output:
                raise VoiceError("not_ready", "Speech models are still loading")
            return self.output.admit(
                str(request["text"]),
                str(request["reply_id"]),
                self.owner["epoch"],
                str(request["producer_id"]),
            )
        if op == "cancel":
            if self.output:
                self.output.cancel(self.owner["epoch"], request.get("reply_id"))
            return {"cancel_requested": True}
        if op == "receipts":
            return {
                "receipts": (
                    [
                        item["receipt"]
                        for item in self.output.jobs.values()
                        if item["owner_epoch"] == self.owner["epoch"]
                        and (
                            not delegated
                            or item["producer_id"] == delegated["producer_id"]
                        )
                        and (
                            not request.get("reply_id")
                            or item["reply_id"] == request["reply_id"]
                        )
                    ]
                    if self.output
                    else []
                )
            }
        raise VoiceError("unknown_operation", "Unknown voice operation")

    def claim_result(self):
        return {
            "token": self.owner["token"],
            "epoch": self.owner["epoch"],
            "stream_id": self.instance_id,
            "cursor": self.transcript.seq if self.transcript else 0,
            "status": self.status(),
        }

    async def handle(self, reader, writer):
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), 3))
            if request.get("protocol") != PROTOCOL:
                raise VoiceError("incompatible", "Voice service protocol mismatch")
            result = await self.dispatch(request)
            response = {"protocol": PROTOCOL, "ok": True, "result": result}
        except Exception as exc:
            response = {
                "protocol": PROTOCOL,
                "ok": False,
                "code": getattr(exc, "code", "service_error"),
                "error": str(exc),
            }
        try:
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        except (ConnectionError, BrokenPipeError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def start_capture(self):
        if self.recorder or not self.owner or self.setup["state"] != "ready":
            return
        try:
            from .audio import SpeechSegmenter
            from .recorder import Recorder, RecorderConfig

            self.transcript.probe()
            self.segmenter = (self.segmenter_factory or SpeechSegmenter)()
            self.recorder = (
                self.recorder_factory()
                if self.recorder_factory
                else Recorder(RecorderConfig(queue_maxsize=100))
            )
            self.last_seq = None
            await asyncio.to_thread(self.recorder.start)
        except Exception as exc:
            self.error = str(exc)
            if self.recorder:
                await asyncio.to_thread(self.recorder.stop)
            self.recorder = None
            self.segmenter = None

    async def release_owner(self):
        for task in self.speech_review_jobs.values():
            task.cancel()
        await asyncio.gather(*self.speech_review_jobs.values(), return_exceptions=True)
        self.speech_review_jobs.clear()
        self.speech_review_started.clear()
        previous = self.owner
        await self.stop_capture()
        for task in self.classifier_jobs.values():
            task.cancel()
        await asyncio.gather(*self.classifier_jobs.values(), return_exceptions=True)
        self.classifier_jobs.clear()
        if previous and self.output:
            self.output.cancel(previous["epoch"], forget=True)
        self.owner = None
        self.delegates.clear()

    async def stop_capture(self):
        if self.recorder:
            recorder, self.recorder = self.recorder, None
            await asyncio.to_thread(recorder.stop)
            while not recorder.frames.empty():
                self.consume_frame(recorder.frames.get_nowait(), final=False)
        if self.segmenter:
            segment = self.segmenter.flush()
            if segment:
                self.submit(segment, final=False)
        self.segmenter = None

    def submit(self, segment, final=True):
        if not self.owner:
            return
        segment.final = segment.final and final
        try:
            self.segments.put_nowait((segment, self.owner["epoch"]))
        except asyncio.QueueFull:
            self.gap(
                "Transcription queue overflow", segment.started_at, segment.ended_at
            )

    def gap(self, reason, start=None, end=None):
        self.error = reason
        if self.transcript:
            now = time.time()
            self.transcript.append(
                {
                    "type": "gap",
                    "reason": reason,
                    "started_at": utc(start or now),
                    "ended_at": utc(end or now),
                    "final": False,
                    "owner_epoch": self.owner["epoch"] if self.owner else None,
                }
            )

    def consume_frame(self, frame, final=True):
        if not self.segmenter:
            return
        if self.last_seq is not None and frame.seq != self.last_seq + 1:
            partial = self.segmenter.flush()
            if partial:
                self.submit(partial, final=False)
            self.gap("Microphone frames were lost")
            from .audio import SpeechSegmenter

            self.segmenter = (self.segmenter_factory or SpeechSegmenter)()
        self.last_seq = frame.seq
        start = time.time() - time.monotonic() + frame.ts - len(frame.audio) / 16000
        for segment in self.segmenter.feed(frame.audio, start):
            self.submit(segment, final=final)

    async def watch(self):
        while True:
            try:
                async with self.lifecycle:
                    for task in self.tasks:
                        if (
                            task.done()
                            and not task.cancelled()
                            and task.exception()
                            and task.get_name() not in self.worker_failures
                        ):
                            self.worker_failures.add(task.get_name())
                            raise RuntimeError(
                                f"{task.get_name()} stopped: {task.exception()}. Run /voicemode retry"
                            )
                    if (
                        self.owner
                        and time.monotonic() - self.owner["heartbeat"] >= LEASE_SECONDS
                    ):
                        await self.release_owner()
                    recorder = self.recorder
                    if recorder:
                        # Drain errors even if the device produces no frames.
                        while not recorder.events.empty():
                            event = recorder.events.get_nowait()
                            if event["type"] in {"dead_stream", "error"}:
                                raise RuntimeError(
                                    event.get("detail", "Microphone stopped")
                                )
                            if event["type"] in {"gap", "warning"}:
                                self.gap(event.get("detail", "Microphone overflow"))
                        # Bounded batch leaves time for heartbeat and UI RPCs.
                        for _ in range(12):
                            try:
                                frame = recorder.frames.get_nowait()
                            except queue.Empty:
                                break
                            self.consume_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = str(exc)
                log.exception("Voice capture failed")
                with contextlib.suppress(Exception):
                    await self.stop_capture()
            await asyncio.sleep(0.02)

    async def transcribe(self):
        while True:
            segment, epoch = await self.segments.get()
            try:
                self.transcribing = True
                text, language = await asyncio.to_thread(
                    self.models.transcribe, segment.audio
                )
                if text:
                    overlap = (
                        self.output.overlap(segment.started_at, segment.ended_at)
                        if self.output
                        else []
                    )
                    final = segment.final and bool(
                        self.owner and self.owner["epoch"] == epoch and self.recorder
                    )
                    record = self.transcript.append(
                        {
                            "type": "transcript",
                            "started_at": utc(segment.started_at),
                            "ended_at": utc(segment.ended_at),
                            "text": text,
                            "language": language,
                            "final": final,
                            "owner_epoch": epoch,
                            "playback_overlap": overlap,
                        }
                    )
                    if self.owner and self.owner["epoch"] == epoch:
                        self.last_transcript = record
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = f"Transcription or transcript write failed: {exc}"
                log.exception("Voice transcription failed")
                async with self.lifecycle:
                    with contextlib.suppress(Exception):
                        await self.stop_capture()
            finally:
                self.transcribing = False
                self.segments.task_done()

    async def close(self):
        async with self.lifecycle:
            await self.release_owner()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        # Persist admitted trailing speech before ending workers.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.segments.join(), 15)
        for task in [self.setup_task, *self.tasks]:
            if task:
                task.cancel()
        await asyncio.gather(
            *(t for t in [self.setup_task, *self.tasks] if t), return_exceptions=True
        )
        await self.classifier.close()
        path = socket_path(self.root)
        if path.exists():
            path.unlink()


async def serve(root):
    service = VoiceService(root)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    await service.start()
    try:
        await stop.wait()
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=service_root())
    parser.add_argument("--lock-fd", type=int)
    args = parser.parse_args()
    prepare_root(args.root)
    logging.basicConfig(level=logging.INFO)
    lock = (
        os.fdopen(args.lock_fd, "a+")
        if args.lock_fd is not None
        else (args.root / "service.lock").open("a+")
    )
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    asyncio.run(serve(args.root))


if __name__ == "__main__":
    main()
