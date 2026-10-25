"""LiveKit voice worker: one livekit-agents AgentSession per voice session.

Run by the Engine as `<voice runtime python> -m kollabor_voice.livekit_worker`
with KOLLAB_VOICE_WORKER_CONFIG (JSON: livekit_url, worker_token, room,
voice_session_id, generation, engine_url, engine_token).

The session mirrors ~/dev/livekit-demo: Silero VAD, the local v1-mini turn
detector, VAD interruptions. Speech-to-text and text-to-speech are nodes over
the shared voice service (Parakeet, VibeVoice); there is no second model
manager and no HTTP voice server. The "LLM" is the Kollab agent: every user
turn is one POST /voice/sessions/{id}/turns whose NDJSON sentences stream into
TTS as they arrive. Words spoken over the reply cancel the generation, which
closes that stream, which the Engine treats as a barge-in; a cough only pauses it.

Stdout carries one line, `WORKER_READY -`, once joined. SIGTERM stops it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
import urllib.request
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiohttp
import numpy as np
from livekit import local_inference, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    APIError,
    inference,
    llm,
    stt,
    tts,
    utils,
)
from livekit.agents.language import LanguageCode
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN
from livekit.agents.voice import room_io
from livekit.agents.voice.agent_activity import _SpeechHandleContextVar
from livekit.plugins import silero

from .speech_adapter import AdapterError, AudioFrame, RoomBinding, SpeechAdapter

log = logging.getLogger(__name__)

STT_RATE = 16000
STT_BATCH = 1600  # 100 ms, the KVA1 frame ceiling
PLAY_RATE = 24000
READINESS_POLL_S = 1.0
READY_TIMEOUT_S = 1200
PARENT_POLL_S = 1.0
GREETING = "Greet the user briefly and ask what they want to talk about."


@dataclass(frozen=True)
class WorkerConfig:
    livekit_url: str
    worker_token: str
    room: str
    voice_session_id: str
    generation: int
    engine_url: str
    engine_token: str

    @classmethod
    def from_env(cls, env=os.environ) -> "WorkerConfig":
        raw = json.loads(env["KOLLAB_VOICE_WORKER_CONFIG"])
        return cls(**{k: raw[k] for k in cls.__dataclass_fields__})


def resample(samples, source: int, target: int):
    if source == target or len(samples) == 0:
        return samples
    count = max(1, round(len(samples) * target / source))
    return np.interp(
        np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
    ).astype(np.float32)


async def rebatch(frames, size: int = STT_BATCH):
    """Group audio frames into 100 ms STT frames."""
    pending, sequence = np.empty(0, dtype=np.float32), 0
    async for frame in frames:
        pending = np.concatenate((pending, frame.samples))
        while len(pending) >= size:
            sequence += 1
            yield AudioFrame(sequence, STT_RATE, pending[:size])
            pending = pending[size:]
    if len(pending):
        yield AudioFrame(sequence + 1, STT_RATE, pending)


async def _single(frame):
    yield frame


# -- engine ---------------------------------------------------------------------


class EnginePoster:
    """Bearer HTTP posts to the Engine; failures are logged, never raised."""

    def __init__(self, config: WorkerConfig):
        self.base = config.engine_url.rstrip("/")
        self.session = config.voice_session_id
        self.headers = {
            "Authorization": f"Bearer {config.engine_token}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> bool:
        request = urllib.request.Request(
            f"{self.base}/voice/sessions/{self.session}/{path}",
            json.dumps(body).encode(),
            self.headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                return True
        except Exception as exc:  # status only: the URL and headers hold secrets
            log.warning("Engine post %s failed: %s", path, type(exc).__name__)
            return False

    async def post(self, path: str, body: dict) -> bool:
        return await asyncio.to_thread(self._post, path, body)


class TurnError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


class TurnClient:
    """POST /turns and yield the reply's spoken sentences as the Engine streams them."""

    def __init__(self, config: WorkerConfig):
        self.url = (
            f"{config.engine_url.rstrip('/')}/voice/sessions/"
            f"{config.voice_session_id}/turns"
        )
        self.headers = {"Authorization": f"Bearer {config.engine_token}"}
        self.http: aiohttp.ClientSession | None = None

    async def stream(self, body: dict) -> AsyncIterator[str]:
        if self.http is None:
            self.http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=5)
            )
        try:
            response = await self.http.post(self.url, json=body, headers=self.headers)
        except aiohttp.ClientError as exc:
            raise TurnError("engine_unreachable", type(exc).__name__, True) from exc
        try:
            if response.status != 200:
                raise TurnError(
                    "turn_rejected",
                    f"The Engine refused the turn ({response.status})",
                    response.status >= 500,
                )
            done = False
            async for raw in response.content:
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                except ValueError as exc:
                    raise TurnError("turn_invalid", "Unreadable turn stream") from exc
                if line.get("done"):
                    done = True
                    break
                if "error" in line:
                    raise TurnError("turn_failed", str(line["error"]))
                text = line.get("text")
                if isinstance(text, str) and text.strip():
                    yield text
            if not done:
                raise TurnError(
                    "turn_incomplete", "The Engine ended the turn early", True
                )
        finally:
            # Closing before `done` is the barge-in signal the Engine listens for.
            response.close()

    async def close(self) -> None:
        if self.http is not None:
            await self.http.close()
            self.http = None


# -- livekit-agents pieces -----------------------------------------------------------


@dataclass(frozen=True)
class Models:
    vad: object
    turn_detection: object


def load_models() -> Models:
    """Load everything the session needs before the first word.

    Silero ships in its wheel and the v1-mini turn detector runs in
    livekit-local-inference, so this only warms them: nothing downloads or
    loads at the first call.
    """
    vad = silero.VAD.load()
    local_inference.init_eot()
    local_inference.init_vad()
    return Models(vad, inference.TurnDetector(version="v1-mini"))


def turn_handling(turn_detection, endpointing: dict | None = None) -> dict:
    handling = {
        "turn_detection": turn_detection,
        # The demo's talk-over (LiveKit's defaults): half a second of speech
        # pauses the voice. Words in it commit a user turn, which interrupts the
        # reply and so cancels the Kollab turn; no words within 2 s (a cough)
        # and the voice picks up where it left off.
        "interruption": {"mode": "vad"},
        # Every reply is a real Kollab turn: never start one speculatively.
        "preemptive_generation": {"enabled": False},
    }
    if endpointing:
        handling["endpointing"] = endpointing
    return handling


class _KollabLLM(llm.LLM):
    """AgentSession wants an LLM object; KollabAgent.llm_node answers every turn."""

    def chat(self, **_):
        raise RuntimeError("Kollab answers each turn through KollabAgent.llm_node")


class SpeechSTT(stt.STT):
    """Non-streaming STT: AgentSession's VAD cuts the utterance, Parakeet reads it."""

    def __init__(self, worker: "Worker"):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self.worker = worker

    async def _recognize_impl(
        self, buffer, *, language=NOT_GIVEN, conn_options
    ) -> stt.SpeechEvent:
        frame = utils.merge_frames(buffer)
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        if frame.num_channels > 1:
            pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1)
        text, lang = await self.worker.transcribe(
            resample(pcm, frame.sample_rate, STT_RATE)
        )
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(language=LanguageCode(lang or "en"), text=text)
            ],
        )


# VibeVoice opens every clip with 0.5-1 s of silence, and a clip is one
# sentence, so replies paused about a second between sentences. A breath of
# silence stays at each end of a clip.
SILENCE = 0.01  # |sample| at or below this is silence, about -40 dBFS
LEAD_KEEP = round(0.1 * PLAY_RATE)
TAIL_KEEP = round(0.2 * PLAY_RATE)


class _EdgeTrim:
    """Cuts a clip's leading and trailing silence down to a breath."""

    def __init__(self):
        self.started = False
        self.quiet = np.zeros(0, np.float32)  # silence since the last sound

    def push(self, audio):
        loud = np.flatnonzero(np.abs(audio) > SILENCE)
        if not loud.size:
            self.quiet = np.concatenate([self.quiet, audio])
            if not self.started:
                self.quiet = self.quiet[-LEAD_KEEP:]
            return audio[:0]
        before = np.concatenate([self.quiet, audio[: loud[0]]])
        if not self.started:
            before = before[-LEAD_KEEP:]
        self.started, self.quiet = True, audio[loud[-1] + 1 :]
        return np.concatenate([before, audio[loud[0] : loud[-1] + 1]])

    def end(self):
        # a silent clip still sends its breath, so the stream is never empty
        return self.quiet[:TAIL_KEEP] if self.started else self.quiet


def _pcm16(audio) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class SpeechTTS(tts.TTS):
    """One ChunkedStream per sentence over the shared VibeVoice owner."""

    def __init__(self, worker: "Worker"):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=PLAY_RATE,
            num_channels=1,
        )
        self.worker = worker

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return _SpeechAudio(tts=self, input_text=text, conn_options=conn_options)


class _SpeechAudio(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        worker = self._tts.worker
        output_emitter.initialize(
            request_id=uuid.uuid4().hex,
            sample_rate=PLAY_RATE,
            num_channels=1,
            mime_type="audio/pcm",
            frame_size_ms=40,
        )
        cancel = asyncio.Event()
        frames = worker.adapter.synthesize(worker.binding, self.input_text, cancel)
        trim = _EdgeTrim()
        try:
            async with contextlib.aclosing(frames):
                async for frame in frames:
                    audio = resample(frame.samples, frame.sample_rate_hz, PLAY_RATE)
                    if (audio := trim.push(audio)).size:
                        output_emitter.push(_pcm16(audio))
            if (tail := trim.end()).size:
                output_emitter.push(_pcm16(tail))
            output_emitter.flush()
        except AdapterError as exc:
            # TTS failure changes state only; the display transcript stays.
            worker.error(exc.code, str(exc), exc.retryable)
            raise APIError(str(exc), retryable=False) from exc
        finally:
            cancel.set()  # the service drains its VibeVoice generator on cancel


@dataclass
class Turn:
    id: str
    started: float
    heard_text: bool = False
    heard_audio: bool = False
    body: dict | None = None

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000


def _turn_text(chat_ctx) -> tuple[str, bool]:
    """The user's words, or the greeting instruction generate_reply appended."""
    for item in reversed(chat_ctx.items):
        if item.type != "message":
            continue
        text = (item.text_content or "").strip()
        if item.role == "user":
            return text, False
        if item.role in {"system", "developer"} and text == GREETING:
            return text, True
        return "", False
    return "", False


class KollabAgent(Agent):
    """The Kollab agent speaking through LiveKit: no model, no tools, no history here."""

    def __init__(self, worker: "Worker"):
        super().__init__(instructions="")
        self.worker, self.turn = worker, None

    async def llm_node(self, chat_ctx, tools, model_settings):
        worker = self.worker
        text, greeting = _turn_text(chat_ctx)
        if not text or worker.stopping.is_set():
            return
        turn = self.turn = worker.begin_turn(text, greeting)
        await worker.cuts_sent()  # a reply this turn talked over is cut first
        worker.keep_played_text(turn, _SpeechHandleContextVar.get(None))
        try:
            async with contextlib.aclosing(worker.turns.stream(turn.body)) as sentences:
                async for sentence in sentences:
                    if turn.heard_text:
                        sentence = " " + sentence  # LiveKit joins played text as sent
                    else:
                        turn.heard_text = True
                        worker.metric("llm_first_token", turn.elapsed_ms(), turn.id)
                    yield sentence
        except TurnError as exc:
            worker.error(exc.code, str(exc), exc.retryable, turn.id)

    async def tts_node(self, text, model_settings):
        # Each sentence is one utterance. The default node re-splits the text and
        # holds a sentence back until the next one starts, which delays first audio.
        # (One clip per several sentences is worse: VibeVoice pauses 0.7-1.3 s
        # between the sentences of a clip.)
        worker, turn = self.worker, None
        async for sentence in text:
            turn = self.turn  # llm_node set it before it produced this sentence
            async with self.session.tts.synthesize(sentence.strip()) as stream:
                async for event in stream:
                    if turn and not turn.heard_audio:
                        turn.heard_audio = True
                        worker.metric("first_audio", turn.elapsed_ms(), turn.id)
                    yield event.frame
        if turn and turn.heard_audio:
            worker.metric("total_turn", turn.elapsed_ms(), turn.id)


# -- room ---------------------------------------------------------------------------


class LiveKitRoom:
    """The only code that touches `rtc.Room`; RoomIO does the audio."""

    def __init__(self):
        self.room = rtc.Room()
        self.closed = asyncio.Event()

    async def connect(self, url: str, token: str) -> None:
        @self.room.on("disconnected")
        def disconnected(*_):
            self.closed.set()

        await self.room.connect(url, token)

    async def attach(self, session: AgentSession, agent: Agent) -> None:
        await session.start(
            agent=agent,
            room=self.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    sample_rate=STT_RATE, num_channels=1
                ),
                audio_output=room_io.AudioOutputOptions(
                    sample_rate=PLAY_RATE, num_channels=1
                ),
                text_input=False,
            ),
        )

    async def wait_participant(self) -> None:
        arrived = asyncio.Event()
        self.room.on("participant_connected", lambda *_: arrived.set())
        if not self.room.remote_participants:
            await arrived.wait()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.room.disconnect()


# -- worker -------------------------------------------------------------------------


class Worker:
    def __init__(
        self,
        config: WorkerConfig,
        room,
        adapter: SpeechAdapter,
        poster,
        turns: TurnClient | None = None,
        models: Models | None = None,
    ):
        self.config, self.room, self.adapter, self.poster = (
            config,
            room,
            adapter,
            poster,
        )
        self.turns = turns or TurnClient(config)
        self.models = models
        self.generation = config.generation
        self.binding = RoomBinding(
            config.voice_session_id, config.voice_session_id, config.generation
        )
        self.stopping = asyncio.Event()
        # Session defaults, like the demo. Tests shorten them (and the 3 s AEC warm-up
        # in which LiveKit ignores a talk-over after the agent starts speaking).
        self.endpointing: dict | None = None
        self.session_options: dict = {}
        self.session = self.agent = self.greeting_task = self.pump = None
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.cuts: set[asyncio.Task] = set()  # talk-over cuts on their way (cuts_sent)
        self.last_reply = None  # the newest reply's SpeechHandle (cuts_sent)
        self.stt_ms = self.language = None  # last utterance, joins the next turn
        self.user_speaking, self.agent_state, self.shown = False, "idle", None
        self.spoke_over = False  # the user started over the agent: the next turn is a talk-over

    # -- events: one ordered queue to the Engine ------------------------------------

    def event(self, event: str, payload: dict, turn_id: str | None = None) -> None:
        self.send(
            "events",
            {
                "event": event,
                "generation": self.generation,
                "turn_id": turn_id,
                "payload": payload,
            },
        )

    def send(self, path: str, body: dict) -> None:
        self.outbox.put_nowait((path, body))
        if self.pump is None:
            self.pump = asyncio.create_task(self._pump(), name="voice-events")

    async def _pump(self) -> None:
        while True:
            path, body = await self.outbox.get()
            try:
                await self.poster.post(path, body)
            finally:
                self.outbox.task_done()

    def cut(self, turn_id: str, text: str) -> None:
        """Post a talk-over's cut on its own: a turn waits for cuts, never for events."""
        task = asyncio.create_task(
            self.poster.post(f"turns/{turn_id}/interrupted", {"text": text})
        )
        self.cuts.add(task)
        task.add_done_callback(self.cuts.discard)

    async def cuts_sent(self, timeout: float = 5.0) -> None:
        """Wait, bounded, until the Engine has the cut of every reply talked over.

        LiveKit can start the next reply a few loop turns before a talked-over
        reply's handle is done (it marks the generation done first and the
        scheduler moves on), and that handle's done callback sends the cut, so wait
        for both. The Engine answers a cut once the daemon has it queued.
        """
        reply = self.last_reply
        if reply is not None and reply.interrupted and not reply.done():
            playout = asyncio.ensure_future(reply.wait_for_playout())
            await asyncio.wait({playout}, timeout=timeout)
        if self.cuts:
            await asyncio.wait(set(self.cuts), timeout=timeout)

    async def drain(self, timeout: float = 5.0) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.outbox.join(), timeout)
        await self.cuts_sent(timeout)

    def state(self, state: str, detail: str = "", **extra) -> None:
        self.event("voice.state", {"state": state, "detail": detail, **extra})

    def metric(self, stage: str, ms, turn_id: str | None = None) -> None:
        if ms is not None:
            self.event("voice.metrics", {"stage": stage, "ms": round(ms)}, turn_id)

    def error(self, code, message, retryable=False, turn_id=None) -> None:
        self.event(
            "voice.error",
            {"code": code, "message": message, "retryable": retryable},
            turn_id,
        )

    # -- startup --------------------------------------------------------------------

    async def wait_ready(self) -> None:
        """Mirror the service's one readiness snapshot until models are ready."""
        last = None
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline and not self.stopping.is_set():
            ready = await self.adapter.readiness(self.binding.owner_id)
            if ready.state == "ready":
                return
            if ready.state == "error":  # includes "Parakeet is not selected"
                self.error("models_not_ready", ready.detail, ready.retryable)
                raise AdapterError("models_not_ready", ready.detail, ready.retryable)
            key = (ready.state, ready.bytes_done)
            if key != last:
                last = key
                if ready.state == "downloading":
                    self.state(
                        "downloading",
                        ready.detail,
                        model=ready.model,
                        bytes_done=ready.bytes_done,
                        bytes_total=ready.bytes_total,
                    )
                else:
                    self.state("starting", ready.detail)
            await asyncio.sleep(READINESS_POLL_S)
        raise AdapterError(
            "models_not_ready", "Voice models did not become ready", True
        )

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT)
        for sig in signals:
            loop.add_signal_handler(sig, self.stopping.set)  # SIGTERM is the stop path
        orphaned = asyncio.create_task(self._stop_when_orphaned(os.getppid()))
        try:
            await self._run()
        finally:
            orphaned.cancel()
            for sig in signals:
                loop.remove_signal_handler(sig)

    async def _stop_when_orphaned(self, engine_pid: int) -> None:
        # An Engine that dies without stopping us (crash, SIGKILL) never sends the
        # SIGTERM: we are handed to launchd or init instead, and stop the same way.
        while os.getppid() == engine_pid:
            await asyncio.sleep(PARENT_POLL_S)
        self.stopping.set()

    async def _run(self) -> None:
        cfg = self.config
        try:
            self.state("starting", "Preparing voice models")
            await self.wait_ready()
            self.state("starting", "Loading turn detection")
            self.models = self.models or await asyncio.to_thread(load_models)
            await self.adapter.acquire(cfg.voice_session_id)
            await self.room.connect(cfg.livekit_url, cfg.worker_token)
            self.agent = KollabAgent(self)
            self.session = self.build_session()
            await self.room.attach(self.session, self.agent)
            print("WORKER_READY -", flush=True)
            self.state("listening")
            self.greeting_task = asyncio.create_task(
                self.greet(), name="voice-greeting"
            )
            waits = [
                asyncio.create_task(self.stopping.wait()),
                asyncio.create_task(self.room.closed.wait()),
            ]
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in waits:
                    task.cancel()
        except AdapterError:
            raise  # already reported as voice.error
        except Exception as exc:
            self.error("worker_failed", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            await self.shutdown()

    def build_session(self) -> AgentSession:
        session = AgentSession(
            vad=self.models.vad,
            stt=SpeechSTT(self),
            llm=_KollabLLM(),
            tts=SpeechTTS(self),
            turn_handling=turn_handling(self.models.turn_detection, self.endpointing),
            tts_text_transforms=[],  # the Engine already speech-formats each sentence
            **self.session_options,
        )
        session.on("user_state_changed", self._user_state)
        session.on("agent_state_changed", self._agent_state)
        session.on("close", lambda *_: self.stopping.set())
        return session

    async def greet(self) -> None:
        try:
            await self.room.wait_participant()
            if not self.stopping.is_set():
                await self.session.generate_reply(instructions=GREETING)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Greeting failed: %s", type(exc).__name__)
            self.error("greeting_failed", f"{type(exc).__name__}: {exc}", True)

    # -- session state -> Engine ----------------------------------------------------

    def _user_state(self, event) -> None:
        self.user_speaking = event.new_state == "speaking"
        if self.user_speaking and self.agent_state in ("thinking", "speaking"):
            # Also in the AEC warm-up, when LiveKit lets the reply play on: a yes
            # in it never answers what the reply asks (it was not heard whole).
            self.spoke_over = True
        self._show()

    def _agent_state(self, event) -> None:
        self.agent_state = event.new_state
        self._show()

    def _show(self) -> None:
        if self.user_speaking:
            now = "hearing"
        else:
            now = {"thinking": "thinking", "speaking": "speaking"}.get(
                self.agent_state, "listening"
            )
        if now != self.shown:
            self.shown = now
            self.state(now)

    # -- nodes call these -----------------------------------------------------------

    async def transcribe(self, samples) -> tuple[str, str | None]:
        """One utterance to the single Parakeet owner."""
        if self.stopping.is_set():
            return "", None
        pieces, language, stt_ms = [], None, 0
        frames = rebatch(_single(AudioFrame(1, STT_RATE, samples)))
        try:
            async for event in self.adapter.transcribe(self.binding, frames):
                if event.kind == "transcript" and event.text:
                    pieces.append(event.text)
                    language = event.language or language
                    stt_ms += event.stt_ms or 0
        except AdapterError as exc:
            if not self.stopping.is_set():
                self.error(exc.code, str(exc), exc.retryable)
            return "", None
        self.stt_ms, self.language = stt_ms or None, language
        return " ".join(pieces), language

    def keep_played_text(self, turn: Turn, speech) -> None:
        """A talk-over leaves the reply cut short: tell the Engine the part that was played.

        `speech` is the reply's own SpeechHandle (the context var is set where its
        task is created). Its done callback can come after the next turn began
        (`cuts_sent` holds that turn until the cut is out), so the turn id is bound
        here, never read off `self.turn`.
        """
        if speech is None:
            return
        self.last_reply = speech

        def done(handle) -> None:
            if not handle.interrupted or self.stopping.is_set():  # closing is no talk-over
                return
            for item in reversed(handle.chat_items):
                if item.type == "message" and item.role == "assistant":
                    if item.interrupted:
                        self.cut(turn.id, (item.text_content or "").strip())
                    return  # not interrupted: it was all played
            # LiveKit adds no message when nothing was played: nothing was heard.
            self.cut(turn.id, "")

        speech.add_done_callback(done)

    def begin_turn(self, text: str, greeting: bool) -> Turn:
        turn = Turn(str(uuid.uuid4()), time.monotonic())  # the Engine takes canonical UUIDs only
        if not greeting:
            self.metric("stt", self.stt_ms, turn.id)
            self.stt_ms = None
        turn.body = {
            "transcript_id": str(uuid.uuid4()),
            "voice_session_id": self.config.voice_session_id,
            "turn_id": turn.id,
            "generation": self.generation,
            "speaker": "user",
            "revision": 1,
            "final": True,
            "display_text": text,
            "spoken_text": None,
            "language": None if greeting else self.language,
            "observed_at_utc_ms": int(time.time() * 1000),
        }
        if greeting:
            turn.body["kind"] = "greeting"
        elif self.spoke_over:
            turn.body["talk_over"] = True
        self.spoke_over = False
        return turn

    async def shutdown(self) -> None:
        self.stopping.set()
        if self.greeting_task:
            self.greeting_task.cancel()
            await asyncio.gather(self.greeting_task, return_exceptions=True)
        # The service first: fence so no older stream of this room listens or speaks
        # past this point, then give the lease back. The Engine sends SIGKILL 3 s after
        # SIGTERM and LiveKit's own teardown can take 2 s, so it goes last and bounded.
        await self.adapter.fence(
            RoomBinding(
                self.binding.owner_id,
                self.binding.voice_session_id,
                self.generation + 1,
            )
        )
        await self.adapter.cancel(self.binding)
        await self.adapter.release()
        if self.session:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.session.aclose(), 2)
        await self.turns.close()
        await self.room.close()
        await self.drain(1.0)
        if self.pump:
            self.pump.cancel()


async def amain() -> None:
    logging.basicConfig(level=logging.INFO)
    config = WorkerConfig.from_env()
    worker = Worker(config, LiveKitRoom(), SpeechAdapter(), EnginePoster(config))
    await worker.run()


def main() -> None:
    try:
        asyncio.run(amain())
    except AdapterError:
        raise SystemExit(2)  # already reported to the Engine as voice.error


if __name__ == "__main__":
    main()
