"""LiveKit worker: a real AgentSession over fake audio, a scripted VAD, a fake
voice service and a fake Engine. No network, no audio device, no livekit-server.
"""

import asyncio
import dataclasses
import itertools
import json
import os
import signal
import socket
import time

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("livekit.agents")

from kollabor_voice import livekit_worker as lw  # noqa: E402
from kollabor_voice.speech_adapter import (  # noqa: E402
    AdapterError,
    AudioFrame,
    SpeechReadiness,
    SttEvent,
)
from livekit import rtc  # noqa: E402
from livekit.agents import inference, llm, vad  # noqa: E402
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS  # noqa: E402
from livekit.agents.voice import io  # noqa: E402

CONFIG = lw.WorkerConfig(
    "ws://x", "jwt", "room", "room-1", 1, "http://127.0.0.1:1", "tok"
)
S1, S2 = "Checking the build now.", "It is green."


def run(coro, timeout=20):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < end, "timed out waiting for the condition"
        await asyncio.sleep(0.01)


# -- fakes -----------------------------------------------------------------------


class FakeEngine:
    """The Engine's /turns route: chunked NDJSON, one script per request.

    A script step is a sentence, "wait" (hold until `gate`, or the client leaves),
    "done", or an int to answer with that HTTP status.
    """

    def __init__(self, scripts=()):
        self.scripts, self.requests = list(scripts), []
        self.gate, self.disconnected = asyncio.Event(), asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.url = "http://127.0.0.1:%d" % self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        size = next(
            int(h.split(b":")[1])
            for h in head.split(b"\r\n")
            if h.lower().startswith(b"content-length")
        )
        request = json.loads(await reader.readexactly(size))
        self.requests.append(request)
        gone = asyncio.create_task(self.watch(reader))
        script = self.scripts.pop(0) if self.scripts else ["done"]
        if isinstance(script[0], int):
            writer.write(b"HTTP/1.1 %d X\r\nContent-Length: 0\r\n\r\n" % script[0])
            return writer.close()
        writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
        try:
            for step in script:
                if step == "wait":
                    held = asyncio.create_task(self.gate.wait())
                    await asyncio.wait(
                        {held, gone}, return_when=asyncio.FIRST_COMPLETED
                    )
                    held.cancel()
                    if gone.done():
                        return
                    continue
                line = (
                    {"done": True, "turn_id": request["turn_id"]}
                    if step == "done"
                    else {"text": step}
                )
                data = json.dumps(line).encode() + b"\n"
                writer.write(b"%x\r\n%s\r\n" % (len(data), data))
                await writer.drain()
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            gone.cancel()
            writer.close()

    async def watch(self, reader):
        await reader.read()  # returns once the client closes its end
        self.disconnected.set()

    async def stop(self):
        self.server.close()


class FakeAdapter:
    """SpeechAdapter stand-in: scripted transcripts, paced synthetic audio."""

    def __init__(self, utterances=(), states=None, tts_chunks=100, tts_pace=0.03):
        self.utterances, self.tts_chunks, self.tts_pace = (
            list(utterances),
            tts_chunks,
            tts_pace,
        )
        self.states = states or itertools.repeat(SpeechReadiness("ready"))
        self.heard, self.spoken, self.finalized = [], [], []
        self.fenced = self.released = None
        self.order = []  # service and room teardown, in the order they happened

    async def readiness(self, owner_id=""):
        return next(self.states)

    async def acquire(self, sid):
        pass

    async def release(self):
        self.released = True
        self.order.append("release")

    async def fence(self, binding):
        self.fenced = binding.generation
        self.order.append("fence")

    async def cancel(self, binding, stream_id=None):
        pass

    async def transcribe(self, binding, frames):
        samples = 0
        async for frame in frames:
            samples += len(frame.samples)
        self.heard.append(samples)
        yield SttEvent("transcript", self.utterances.pop(0), "en", True, 42)

    async def synthesize(self, binding, text, cancel):
        self.spoken.append(text)
        try:
            for i in range(self.tts_chunks):
                if cancel.is_set():
                    return
                yield AudioFrame(i + 1, 24000, np.full(960, 0.05, np.float32))  # 40 ms
                await asyncio.sleep(self.tts_pace)
        finally:
            self.finalized.append(text)


class Recorder:
    def __init__(self):
        self.posts = []

    async def post(self, path, body):
        self.posts.append((path, body))
        return True


def events(poster, name):
    return [b for p, b in poster.posts if p == "events" and b["event"] == name]


class ScriptedVADStream(vad.VADStream):
    async def _main_task(self):
        async for _ in self._input_ch:
            pass


class ScriptedVAD(vad.VAD):
    """The test decides when speech starts and ends; the audio is ignored."""

    KINDS = {
        "start": vad.VADEventType.START_OF_SPEECH,
        "inference": vad.VADEventType.INFERENCE_DONE,
        "end": vad.VADEventType.END_OF_SPEECH,
    }

    def __init__(self):
        super().__init__(capabilities=vad.VADCapabilities(update_interval=0.032))
        self.streams = []

    def stream(self):
        self.streams.append(ScriptedVADStream(self))
        return self.streams[-1]

    def emit(self, kind, seconds=0.0):
        frames = [rtc.AudioFrame(bytes(3200), 16000, 1, 1600)] if kind == "end" else []
        event = vad.VADEvent(
            type=self.KINDS[kind], samples_index=0, timestamp=time.time(),
            speech_duration=seconds, silence_duration=0.0, frames=frames,
            probability=0.9, speaking=kind != "end",
        )  # fmt: skip
        for stream in self.streams:
            if not stream._event_ch.closed:
                stream._event_ch.send_nowait(event)


class FakeAudioInput(io.AudioInput):
    def __init__(self):
        super().__init__(label="fake-mic")

    async def __anext__(self):
        await asyncio.sleep(0.01)
        return rtc.AudioFrame(bytes(320), 16000, 1, 160)


class FakeAudioOutput(io.AudioOutput):
    """A speaker that can pause, like the room's, and counts what reached it."""

    def __init__(self):
        super().__init__(
            label="fake-speaker",
            capabilities=io.AudioOutputCapabilities(pause=True),
            sample_rate=24000,
        )
        self.frames, self.cleared, self.pushed = 0, 0, 0.0

    async def capture_frame(self, frame):
        await super().capture_frame(frame)
        if not self.pushed:
            self.on_playback_started(created_at=time.time())
        self.frames, self.pushed = self.frames + 1, self.pushed + frame.duration

    def flush(self):
        super().flush()
        position, self.pushed = self.pushed, 0.0
        asyncio.get_running_loop().call_later(
            0.01,
            lambda: self.on_playback_finished(
                playback_position=position, interrupted=False
            ),
        )

    def clear_buffer(self):
        position, self.pushed = self.pushed, 0.0
        self.cleared += 1
        self.on_playback_finished(playback_position=position, interrupted=True)


class FakeRoom:
    def __init__(self):
        self.closed, self.participant = asyncio.Event(), asyncio.Event()
        self.connected = self.output = None
        self.is_closed = False
        self.order = []

    async def connect(self, url, token):
        self.connected = (url, token)

    async def attach(self, session, agent):
        session.input.audio = FakeAudioInput()
        self.output = session.output.audio = FakeAudioOutput()
        await session.start(agent=agent)

    async def wait_participant(self):
        await self.participant.wait()

    async def close(self):
        self.is_closed = True
        self.order.append("room")


class Rig:
    """A running Worker: real AgentSession, real TurnClient, everything else fake."""

    def __init__(self, utterances=(), scripts=(), tts_chunks=100, detector="vad"):
        self.engine, self.adapter = FakeEngine(scripts), FakeAdapter(
            utterances, tts_chunks=tts_chunks
        )
        self.vad, self.room, self.poster = ScriptedVAD(), FakeRoom(), Recorder()
        self.detector = detector
        self.room.order = self.adapter.order

    async def __aenter__(self):
        await self.engine.start()
        config = dataclasses.replace(CONFIG, engine_url=self.engine.url)
        self.worker = lw.Worker(
            config,
            self.room,
            self.adapter,
            self.poster,
            models=lw.Models(self.vad, self.detector),
        )
        self.worker.endpointing = {"min_delay": 0.05, "max_delay": 0.5}
        self.worker.session_options = {
            "aec_warmup_duration": None
        }  # LiveKit ignores talk-over for 3 s
        self.task = asyncio.create_task(self.worker.run())
        await until(
            lambda: self.worker.session is not None and self.room.output is not None
        )
        return self

    async def __aexit__(self, *exc):
        self.worker.stopping.set()
        await asyncio.wait_for(self.task, 10)
        await self.engine.stop()

    async def say(self, text, seconds=0.8):
        self.adapter.utterances.append(text)
        self.vad.emit("start")
        self.vad.emit("inference", seconds)
        await asyncio.sleep(0.05)
        self.vad.emit("end", seconds)

    async def talk_over(self):
        self.vad.emit("start")
        self.vad.emit("inference", 0.7)  # longer than the 0.5 s interruption minimum


# -- turn handling and models ---------------------------------------------------


def test_turn_handling_is_the_demos_with_a_talk_over_that_really_cancels():
    handling = lw.turn_handling(inference.TurnDetector(version="v1-mini"))
    assert isinstance(handling["turn_detection"], inference.TurnDetector)
    assert handling["interruption"]["mode"] == "vad"
    # Without this a VAD hit only pauses the audio and the Kollab turn keeps running.
    assert handling["interruption"]["resume_false_interruption"] is False
    # Every reply is a real Kollab turn, never a speculative one.
    assert handling["preemptive_generation"] == {"enabled": False}


def test_models_prewarm_without_the_network(monkeypatch):
    attempts, connect = [], socket.socket.connect

    def guarded(self, address, *rest):
        if self.family != socket.AF_UNIX:
            attempts.append(address)
            raise OSError("no network in this test")
        return connect(self, address, *rest)

    def no_dns(*args, **kwargs):
        raise OSError("no dns in this test")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    models = lw.load_models()
    assert models.vad is not None
    assert isinstance(models.turn_detection, inference.TurnDetector)
    assert attempts == []


# -- the Engine turn stream -----------------------------------------------------


def test_turn_stream_yields_sentences_as_they_arrive_and_closes_when_cancelled():
    async def scenario():
        engine = FakeEngine([[S1, "wait", S2, "done"]])
        await engine.start()
        client = lw.TurnClient(dataclasses.replace(CONFIG, engine_url=engine.url))
        seen = []

        async def consume():
            async for sentence in client.stream(
                {"turn_id": "t1", "display_text": "hi"}
            ):
                seen.append(sentence)

        task = asyncio.create_task(consume())
        await until(lambda: seen == [S1])  # while the Engine still holds the rest
        assert not engine.gate.is_set() and not engine.disconnected.is_set()
        task.cancel()  # what LiveKit does on a talk-over
        await asyncio.gather(task, return_exceptions=True)
        await until(engine.disconnected.is_set)
        await client.close()
        await engine.stop()
        return engine

    engine = run(scenario())
    assert engine.requests[0]["display_text"] == "hi"


def test_turn_stream_reports_a_refused_or_cut_off_turn():
    async def outcome(script):
        engine = FakeEngine([script])
        await engine.start()
        client = lw.TurnClient(dataclasses.replace(CONFIG, engine_url=engine.url))
        try:
            return [s async for s in client.stream({"turn_id": "t1"})]
        except lw.TurnError as exc:
            return exc.code
        finally:
            await client.close()
            await engine.stop()

    assert run(outcome([409])) == "turn_rejected"
    assert run(outcome([S1])) == "turn_incomplete"  # closed before `done`
    assert run(outcome([S1, "done"])) == [S1]


# -- the session ----------------------------------------------------------------


def test_sentences_stream_into_tts_before_the_reply_finishes():
    async def scenario():
        async with Rig(
            ["is the build green"], [[S1, "wait", S2, "done"]], tts_chunks=15
        ) as rig:
            await rig.say("is the build green")
            await until(
                lambda: rig.adapter.spoken == [S1]
            )  # the Engine still holds sentence two
            assert not rig.engine.gate.is_set()
            rig.engine.gate.set()
            await until(lambda: rig.adapter.spoken == [S1, S2])
            await until(lambda: events(rig.poster, "voice.metrics") and any(
                e["payload"]["stage"] == "total_turn" for e in events(rig.poster, "voice.metrics")))  # fmt: skip
        return rig

    rig = run(scenario())
    [body] = rig.engine.requests
    assert body["display_text"] == "is the build green" and body["speaker"] == "user"
    assert (
        body["generation"] == 1
        and body["voice_session_id"] == "room-1"
        and "kind" not in body
    )
    assert (
        len(rig.adapter.heard) == 1 and rig.adapter.heard[0] > 0
    )  # one utterance, one STT stream
    stages = {e["payload"]["stage"] for e in events(rig.poster, "voice.metrics")}
    assert stages == {"stt", "llm_first_token", "first_audio", "total_turn"}
    stt = next(
        e for e in events(rig.poster, "voice.metrics") if e["payload"]["stage"] == "stt"
    )
    assert stt["payload"]["ms"] == 42 and stt["turn_id"] == body["turn_id"]
    states = [e["payload"]["state"] for e in events(rig.poster, "voice.state")]
    assert {"hearing", "thinking", "speaking"} <= set(states)


def test_talking_over_the_reply_cancels_tts_and_closes_the_turn_stream():
    async def scenario():
        async with Rig(["is the build green"], [[S1, "wait", S2, "done"]]) as rig:
            await rig.say("is the build green")
            await until(
                lambda: rig.adapter.spoken == [S1] and rig.room.output.frames >= 3
            )
            await rig.talk_over()
            await until(
                rig.engine.disconnected.is_set
            )  # the Engine reads this as a barge-in
            await until(
                lambda: rig.adapter.finalized == [S1]
            )  # TTS cancelled, service drains
            await until(lambda: rig.room.output.cleared >= 1)  # playback stopped
            rig.engine.gate.set()  # whatever the cancelled turn would still say
            await asyncio.sleep(0.3)
        return rig

    rig = run(scenario())
    assert rig.adapter.spoken == [S1]  # nothing late speaks
    assert len(rig.engine.requests) == 1


def test_greeting_on_join():
    hello = "Hey Marco, what do you want to talk about?"

    async def scenario():
        async with Rig(scripts=[[hello, "done"]], tts_chunks=10) as rig:
            await asyncio.sleep(0.1)
            assert not rig.engine.requests  # nobody has joined yet
            rig.room.participant.set()
            await until(lambda: rig.adapter.spoken == [hello])
        return rig

    rig = run(scenario())
    [body] = rig.engine.requests
    assert (
        body["kind"] == "greeting"
        and body["speaker"] == "user"
        and body["language"] is None
    )
    assert body["display_text"] == lw.GREETING
    stages = {e["payload"]["stage"] for e in events(rig.poster, "voice.metrics")}
    assert "stt" not in stages and {"first_audio", "llm_first_token"} <= stages


def test_the_production_turn_detector_runs_a_session_and_a_greeting():
    hello = "Hey Marco."

    async def scenario():
        detector = inference.TurnDetector(version="v1-mini")
        async with Rig(
            scripts=[[hello, "done"]], tts_chunks=5, detector=detector
        ) as rig:
            rig.room.participant.set()
            await until(lambda: rig.adapter.spoken == [hello])
        return rig

    rig = run(scenario())
    assert rig.engine.requests[0]["kind"] == "greeting" and not events(
        rig.poster, "voice.error"
    )


def test_ready_line_then_sigterm_stops_and_fences(capsys):
    async def scenario():
        async with Rig() as rig:
            assert capsys.readouterr().out == "WORKER_READY -\n"
            assert rig.room.connected == ("ws://x", "jwt")
            assert (
                signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
            )  # run() owns SIGTERM
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(rig.task, 10)
            return rig, rig.worker.agent

    rig, agent = run(scenario())
    assert rig.adapter.fenced == CONFIG.generation + 1 and rig.adapter.released
    assert rig.room.is_closed and rig.worker.stopping.is_set()
    # the service lease goes back before LiveKit's slow teardown (SIGKILL follows SIGTERM by 3 s)
    assert rig.adapter.order == ["fence", "release", "room"]
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # restored

    async def after_stop():
        text, _ = await rig.worker.transcribe(np.zeros(1600, np.float32))
        ctx = llm.ChatContext.empty()
        ctx.add_message(role="user", content="late words")
        return text, [s async for s in agent.llm_node(ctx, [], None)]

    assert run(after_stop()) == ("", [])  # the old generation neither listens nor asks
    assert rig.adapter.heard == [] and len(rig.engine.requests) == 0


def test_the_stt_node_hands_one_utterance_to_the_single_parakeet_owner():
    async def scenario():
        rig = Rig(["hello there"])
        worker = lw.Worker(CONFIG, rig.room, rig.adapter, rig.poster, models=None)
        half_second = rtc.AudioFrame(
            bytes(48000), 48000, 1, 24000
        )  # 48 kHz in, Parakeet wants 16 kHz
        speech = lw.SpeechSTT(worker)
        return (
            await speech._recognize_impl(
                [half_second], conn_options=DEFAULT_API_CONNECT_OPTIONS
            ),
            rig,
        )

    event, rig = run(scenario())
    assert event.alternatives[0].text == "hello there"
    assert rig.adapter.heard == [8000]


# -- startup --------------------------------------------------------------------


def test_a_room_never_starts_while_parakeet_is_not_ready(capsys):
    rig = Rig()
    rig.adapter.states = iter(
        [SpeechReadiness("error", "Parakeet is not selected", retryable=True)]
    )
    worker = lw.Worker(CONFIG, rig.room, rig.adapter, rig.poster)
    with pytest.raises(AdapterError) as err:
        run(worker.run())
    assert err.value.code == "models_not_ready"
    assert rig.room.connected is None and "WORKER_READY" not in capsys.readouterr().out
    [error] = events(rig.poster, "voice.error")
    assert (
        error["payload"]["code"] == "models_not_ready" and error["payload"]["retryable"]
    )


def test_download_progress_is_posted_while_models_load(monkeypatch):
    rig = Rig()
    rig.adapter.states = iter(
        [
            SpeechReadiness("downloading", "parakeet / m", "parakeet", 1, 10),
            SpeechReadiness("downloading", "parakeet / m", "parakeet", 6, 10),
            SpeechReadiness("ready"),
        ]
    )
    monkeypatch.setattr(lw, "READINESS_POLL_S", 0)
    worker = lw.Worker(CONFIG, rig.room, rig.adapter, rig.poster)

    async def scenario():
        await worker.wait_ready()
        await worker.drain()

    run(scenario())
    progress = [e["payload"] for e in events(rig.poster, "voice.state")]
    assert [
        (p["state"], p["model"], p["bytes_done"], p["bytes_total"]) for p in progress
    ] == [
        ("downloading", "parakeet", 1, 10),
        ("downloading", "parakeet", 6, 10),
    ]


# -- helpers --------------------------------------------------------------------


def test_rebatch_and_resample():
    async def frames():
        for _ in range(5):
            yield AudioFrame(1, 16000, np.zeros(700, dtype=np.float32))

    async def collect():
        return [len(f.samples) async for f in lw.rebatch(frames())]

    assert run(collect()) == [1600, 1600, 300]
    assert len(lw.resample(np.zeros(16000, np.float32), 16000, 24000)) == 24000


def test_config_loads_from_env_and_the_poster_never_raises():
    raw = {k: getattr(CONFIG, k) for k in lw.WorkerConfig.__dataclass_fields__}
    assert (
        lw.WorkerConfig.from_env({"KOLLAB_VOICE_WORKER_CONFIG": json.dumps(raw)})
        == CONFIG
    )
    unreachable = lw.WorkerConfig("ws://x", "j", "r", "s", 1, "http://127.0.0.1:9", "t")
    assert run(lw.EnginePoster(unreachable).post("events", {})) is False
