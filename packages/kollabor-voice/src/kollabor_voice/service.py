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
from .observer import is_playback_echo, strip_playback
from .store import TranscriptLog, utc

log = logging.getLogger(__name__)

# A recorder start or stop that takes longer than this is stuck in CoreAudio.
RECORDER_TIMEOUT_S = 5


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
        self.speech = None
        self.speech_prepare_task = None
        self._speech_preferences_revision = None
        self.stt = None
        self.stt_prepare_task = None
        self._stt_preferences_revision = None
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
        self.shutdown = asyncio.Event()
        self.stuck = False
        self.load_assets = load_assets
        self.load_models = load_models
        self.recorder_factory = recorder_factory
        self.segmenter_factory = segmenter_factory
        self.player = player
        from .laya import LayaWorker
        from .qwen import QwenWorker

        # Local classifier workers for the on-demand classifier ops. Voice never
        # prepares one: no process starts until a classifier op names it, and
        # status() leaves them out. One is loaded at a time: the last prepared.
        self.classifiers = {w.name: w for w in (LayaWorker(root), QwenWorker(root))}
        self.classifier = self.classifiers["laya"]
        self.classifier_jobs = {}
        self.speech_review_jobs = {}
        self.speech_review_started = {}
        from .echo import PlaybackReference
        from .speaker import SpeakerGate

        # Filters in front of transcription; both off until preferences say so.
        self.gate = SpeakerGate(root)
        self.gate_task = None
        self.reference = PlaybackReference()
        self.echo_cancellation = False
        self.echo = None  # EchoCanceller while capturing with it on
        self.echo_error = None
        self._capture_preferences_revision = "unread"
        from .audio_ipc import AudioIPC

        self.audio_ipc = AudioIPC(self)  # LiveKit worker PCM, same model owner

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
        await self.audio_ipc.start()
        self.tasks = [
            asyncio.create_task(self.watch(), name="voice-watch"),
            asyncio.create_task(self.transcribe(), name="voice-transcribe"),
        ]
        self.setup_task = asyncio.create_task(self.prepare(), name="voice-setup")

    async def prepare(self):
        try:
            from .assets import ensure_models
            from .audio import LocalModels, StreamPlayer
            from .output import SpeechQueue
            from .speech import SpeechEngine
            from .transcription import TranscriptionEngine

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
            if self.speech is None:
                self.speech = SpeechEngine(self.root, self.models.synthesize)
            await asyncio.to_thread(
                self.speech.configure, self.speech.status()["state"] == "error"
            )
            if self.stt is None:
                self.stt = TranscriptionEngine(
                    self.root, lambda audio, names: self.models.transcribe(audio, names)
                )
            if self.stt_prepare_task is None or self.stt_prepare_task.done():
                # Whisper transcribes while a slower engine prepares.
                self.stt_prepare_task = asyncio.create_task(
                    self.prepare_transcription(self.stt.status()["state"] == "error"),
                    name="stt-prepare",
                )
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
                player = self.player or StreamPlayer()
                player.reference = self.reference  # what echo cancellation removes
                self.output = SpeechQueue(self.root, self.speech.stream, player)
                self.tasks.append(
                    asyncio.create_task(self.output.run(), name="voice-output")
                )
            self.progress(state="ready", detail="Transcription and speech ready")
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
            self.progress(state="error", detail=str(exc))

    def authorize(self, token, epoch):
        if (
            not self.owner
            or time.monotonic() - self.owner["heartbeat"] >= LEASE_SECONDS
        ):
            raise VoiceError(
                "lease_expired", "Voice connection expired"
            )
        if token != self.owner["token"] or epoch != self.owner["epoch"]:
            raise VoiceError(
                "not_owner",
                "Voice is active in another chat; run /voice on",
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
            "ready": self.setup.get("state") == "ready"
            and (self.speech is None or self.speech.status()["state"] == "ready"),
            "speech": (
                self.speech.status()
                if self.speech
                else {"state": "off", "engine": None}
            ),
            "transcription": (
                self.stt.status() if self.stt else {"state": "off", "engine": None}
            ),
            "speaker": self.gate.status(),
            "echo_cancellation": {
                "enabled": self.echo_cancellation,
                "active": self.echo is not None,
                "detail": self.echo_error or "",
            },
            "audio_warmup_seconds": self.audio_warmup_seconds,
        }

    async def dispatch(self, request):
        op = request.get("op")
        if op in {"hello", "status"}:
            return self.status()
        if op == "classifier_prepare":
            await self.select_classifier(request.get("model", "laya"))
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
                    capture=request.get("capture", True) is not False,
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
        if op == "speaker_forget":
            # Deleting a voiceprint needs no microphone lease.
            return {"forgotten": self.gate.forget(), "speaker": self.gate.status()}
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
        if op == "hint":
            # The owner's agent names prime transcription for its lease.
            self.owner["names"] = [str(n)[:40] for n in request.get("names", [])][:8]
            return {"names": self.owner["names"]}
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
        if op == "speaker_enroll":
            # The owner's next 30 seconds of speech; never sent while enrolling.
            self.gate.enroll()
            self.prepare_gate()
            return self.gate.status()
        if op == "audio_capability":
            return self.audio_ipc.mint(request, self.owner["epoch"])
        if op == "audio_stop":
            return self.audio_ipc.stop(request)
        if op == "classifier_submit":
            for key, task in list(self.classifier_jobs.items()):
                if task.done():
                    del self.classifier_jobs[key]
            if self.classifier_jobs:
                raise VoiceError(
                    "classifier_busy", "A classifier decision is already pending"
                )
            if request.get("model", self.classifier.name) != self.classifier.name:
                raise VoiceError(
                    "classifier_not_ready",
                    "Voice classifier changed; speech remains pending",
                )
            identity = uuid.uuid4().hex
            if request.get("task") == "answer":
                # The reply to "was that for me?"; only the Qwen model reads it.
                if not hasattr(self.classifier, "answer"):
                    raise VoiceError(
                        "unsupported",
                        f"{self.classifier.label} does not answer questions",
                    )
                job = self.classifier.answer(
                    str(request["held"]), str(request["question"]), str(request["text"])
                )
            else:
                job = self.classifier.decide(
                    request["records"],
                    str(request.get("context", "")),
                    request.get("transcript_context", []),
                    request.get("context_lines", 10),
                )
            task = asyncio.create_task(job)
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

    async def select_classifier(self, name):
        """Load only the selected local classifier; switching closes the other."""
        if name not in self.classifiers:
            raise VoiceError("unknown_classifier", "Choose laya or qwen")
        previous, self.classifier = self.classifier, self.classifiers[name]
        if previous is not self.classifier:
            await previous.close()

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
        if not self.owner.get("capture", True):
            return  # a room lease: audio arrives over audio.sock, not this mic
        if self.speech and self.speech.status()["state"] != "ready":
            return
        try:
            from .audio import SpeechSegmenter
            from .recorder import Recorder, RecorderConfig

            self.transcript.probe()
            self.segmenter = (self.segmenter_factory or SpeechSegmenter)()
            self.echo = self.new_echo() if self.echo_cancellation else None
            self.recorder = (
                self.recorder_factory()
                if self.recorder_factory
                else Recorder(RecorderConfig(queue_maxsize=100))
            )
            self.last_seq = None
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.recorder.start), timeout=RECORDER_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                self.capture_stuck("recorder.start")
                raise VoiceError(
                    "capture_start_timeout",
                    "Microphone did not open; run /voice again to restart voice",
                ) from None
        except Exception as exc:
            log.exception("Voice capture failed to start")
            self.error = str(exc)
            if self.recorder and not self.stuck:
                with contextlib.suppress(Exception):
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
        self.gate.cancel()  # an unfinished enrollment belongs to that owner
        self.owner = None
        self.delegates.clear()

    async def stop_capture(self):
        if self.recorder:
            recorder, self.recorder = self.recorder, None
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(recorder.stop), timeout=RECORDER_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                self.capture_stuck("recorder.stop")
            finally:
                while not recorder.frames.empty():
                    self.consume_frame(recorder.frames.get_nowait(), final=False)
        if self.segmenter:
            segment = self.segmenter.flush()
            if segment:
                self.submit(segment, final=False)
        self.segmenter = None
        self.echo = None

    def new_echo(self):
        """An echo canceller for this capture, or None when it cannot load."""
        try:
            from .echo import EchoCanceller

            echo, self.echo_error = EchoCanceller(self.reference), None
            return echo
        except Exception as exc:  # e.g. a runtime prepared before pywebrtc-audio
            self.echo_error = f"Echo cancellation unavailable: {exc}"
            log.warning(self.echo_error)
            return None

    def apply_capture_preferences(self):
        from .speech_settings import load_speech_preferences

        preferences = load_speech_preferences(self.root)
        self.gate.configure(preferences)
        self.prepare_gate()
        self.echo_cancellation = preferences["echo_cancellation"]
        if self.segmenter and self.echo_cancellation != (self.echo is not None):
            self.echo = self.new_echo() if self.echo_cancellation else None

    def prepare_gate(self):
        """Load the speaker model in the background once the gate needs it."""
        if not self.gate.needed or self.gate.model is not None:
            return
        if self.gate_task is None or self.gate_task.done():
            self.gate_task = asyncio.create_task(
                asyncio.to_thread(self.gate.prepare), name="speaker-prepare"
            )
            self.gate_task.add_done_callback(
                lambda task: task.cancelled()
                or not task.exception()
                or log.warning("Speaker gate not ready: %s", task.exception())
            )

    def capture_stuck(self, call):
        # The stuck thread holds CoreAudio's device mutex, so no stream in this
        # process opens again. Restart: serve() exits and the next /voice
        # launches a fresh service.
        log.error(
            "%s did not return within %ss; restarting the voice service",
            call,
            RECORDER_TIMEOUT_S,
        )
        self.stuck = True
        self.shutdown.set()

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
        audio, captured = frame.audio, frame.ts - len(frame.audio) / 16000
        if self.echo is not None:
            audio = self.echo.process(audio, captured)  # voice's own speech removed
        start = time.time() - time.monotonic() + captured
        for segment in self.segmenter.feed(audio, start):
            self.submit(segment, final=final)

    async def watch(self):
        while True:
            try:
                if self.speech and (
                    self.speech_prepare_task is None or self.speech_prepare_task.done()
                ):
                    from .speech_settings import load_speech_preferences, speech_key

                    revision = self._preferences_revision()
                    changed = revision != self._speech_preferences_revision
                    self._speech_preferences_revision = revision
                    if (
                        changed
                        and speech_key(load_speech_preferences(self.root)) != self.speech.key
                    ):
                        self.speech_prepare_task = asyncio.create_task(
                            self.prepare_speech(), name="speech-prepare"
                        )
                if self.stt and (
                    self.stt_prepare_task is None or self.stt_prepare_task.done()
                ):
                    revision = self._preferences_revision()
                    retry = self.stt.retry_due()
                    if revision != self._stt_preferences_revision or retry:
                        self._stt_preferences_revision = revision
                        self.stt_prepare_task = asyncio.create_task(
                            self.prepare_transcription(retry), name="stt-prepare"
                        )
                revision = self._preferences_revision()
                if revision != self._capture_preferences_revision:
                    self._capture_preferences_revision = revision
                    self.apply_capture_preferences()
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
                                f"{task.get_name()} stopped: {task.exception()}"
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

    def _preferences_revision(self):
        try:
            st = (self.root / "preferences.json").stat()
            return st.st_mtime_ns, st.st_ino
        except OSError:
            return None

    async def judge_speaker(self, audio, overlap):
        """Speaker fields for a transcript record. A failing gate passes speech."""
        if not self.gate.needed:
            return {}
        try:
            return await asyncio.to_thread(self.gate.judge, audio, overlap)
        except Exception as exc:
            log.exception("Speaker gate failed")
            self.gate.state, self.gate.detail = "error", f"Speaker gate failed: {exc}"
            return {}

    async def prepare_transcription(self, force=False):
        try:
            await asyncio.to_thread(self.stt.configure, force)
        except Exception:
            log.exception("Transcription engine preparation failed")

    async def prepare_speech(self):
        try:
            await asyncio.to_thread(self.speech.configure)
            async with self.lifecycle:
                if (
                    self.owner
                    and time.monotonic() - self.owner["heartbeat"] < LEASE_SECONDS
                ):
                    await self.start_capture()
        except Exception:
            log.exception("Speech engine preparation failed")

    async def transcribe(self):
        while True:
            segment, epoch = await self.segments.get()
            try:
                self.transcribing = True
                text, language = await asyncio.to_thread(
                    self.stt.transcribe,
                    segment.audio,
                    (self.owner or {}).get("names", ()),
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
                    # Who spoke: the enrolled user, another speaker, unjudged.
                    fields = await self.judge_speaker(segment.audio, bool(overlap))
                    if self.echo is not None:
                        fields["echo_cancellation"] = True
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
                            **fields,
                        }
                    )
                    # Heard is what the user said: the mic picking up voice's
                    # own playback or another speaker is not shown, and talking
                    # over voice shows only the person's words.
                    own = "" if is_playback_echo(record) else strip_playback(record)
                    if fields.get("speaker") == "other_speaker":
                        own = ""
                    if own and self.owner and self.owner["epoch"] == epoch:
                        self.last_transcript = {**record, "text": own}
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
        await self.audio_ipc.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        # Persist admitted trailing speech before ending workers.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.segments.join(), 15)
        tasks = [
            self.setup_task,
            self.speech_prepare_task,
            self.stt_prepare_task,
            self.gate_task,
            *self.tasks,
        ]
        for task in tasks:
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in tasks if t), return_exceptions=True)
        if self.speech:
            await asyncio.to_thread(self.speech.close)
        if self.stt:
            await asyncio.to_thread(self.stt.close)
        for worker in self.classifiers.values():
            await worker.close()
        path = socket_path(self.root)
        if path.exists():
            path.unlink()


async def serve(root):
    service = VoiceService(root)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, service.shutdown.set)
    await service.start()
    try:
        await service.shutdown.wait()
    finally:
        await service.close()
    if service.stuck:
        # Interpreter exit joins every executor thread, the stuck one too.
        os._exit(1)


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
