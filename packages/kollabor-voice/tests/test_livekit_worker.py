"""LiveKit worker: a real AgentSession over fake audio, a scripted VAD, a fake
voice service and a fake Engine. No network, no audio device, no livekit-server.
"""

import asyncio
import dataclasses
import itertools
import json
import os
import re
import signal
import socket
import time
from types import SimpleNamespace

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

ENGINE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

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

    def __init__(
        self, utterances=(), states=None, tts_chunks=100, tts_pace=0.03, tts_lead=0
    ):
        self.utterances, self.tts_chunks, self.tts_pace = (
            list(utterances),
            tts_chunks,
            tts_pace,
        )
        self.tts_lead = tts_lead  # silent 40 ms frames first, like VibeVoice
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
            for i in range(self.tts_lead + self.tts_chunks):
                if cancel.is_set():
                    return
                level = 0.0 if i < self.tts_lead else 0.05
                yield AudioFrame(i + 1, 24000, np.full(960, level, np.float32))  # 40 ms
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
        self.paused = self.resumed = 0

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

    def pause(self):
        self.paused += 1

    def resume(self):
        self.resumed += 1

    def clear_buffer(self):
        position, self.pushed = self.pushed, 0.0
        self.cleared += 1
        self.on_playback_finished(playback_position=position, interrupted=True)


class FakeTextOutput(io.TextOutput):
    """The room's transcript sink: with one, LiveKit keeps the text it forwarded."""

    def __init__(self):
        super().__init__(label="fake-transcript", next_in_chain=None)

    async def capture_text(self, text):
        pass

    def flush(self):
        pass


class FakeRoom:
    def __init__(self, transcript=False):
        self.transcript = transcript
        self.closed, self.participant = asyncio.Event(), asyncio.Event()
        self.connected = self.output = None
        self.is_closed = False
        self.order = []

    async def connect(self, url, token):
        self.connected = (url, token)

    async def attach(self, session, agent):
        session.input.audio = FakeAudioInput()
        self.output = session.output.audio = FakeAudioOutput()
        if self.transcript:
            session.output.transcription = FakeTextOutput()
        await session.start(agent=agent)

    async def wait_participant(self):
        await self.participant.wait()

    async def close(self):
        self.is_closed = True
        self.order.append("room")


class Rig:
    """A running Worker: real AgentSession, real TurnClient, everything else fake."""

    def __init__(
        self, utterances=(), scripts=(), tts_chunks=100, detector="vad", transcript=False
    ):
        self.engine, self.adapter = FakeEngine(scripts), FakeAdapter(
            utterances, tts_chunks=tts_chunks
        )
        self.vad, self.room, self.poster = (
            ScriptedVAD(),
            FakeRoom(transcript),
            Recorder(),
        )
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


# -- turn handling and models ---------------------------------------------------


def test_turn_handling_is_the_demos():
    handling = lw.turn_handling(inference.TurnDetector(version="v1-mini"))
    assert isinstance(handling["turn_detection"], inference.TurnDetector)
    # The demo sets only the mode: a talk-over pauses, words cancel, a cough resumes.
    assert handling["interruption"] == {"mode": "vad"}
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
    # the Engine's id rule (voice_types.VoiceSessionId) answers 422 to anything else
    assert ENGINE_ID.match(body["turn_id"]) and ENGINE_ID.match(body["transcript_id"])
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


def test_words_over_the_reply_cancel_it_and_become_the_next_turn():
    async def scenario():
        async with Rig(scripts=[[S1, "wait", S2, "done"], ["Okay.", "done"]]) as rig:
            await rig.say("is the build green")
            await until(
                lambda: rig.adapter.spoken == [S1] and rig.room.output.frames >= 3
            )
            await rig.say("stop that")  # speech with words: the interruption is real
            await until(
                rig.engine.disconnected.is_set
            )  # the Engine reads this as a barge-in and cancels the turn
            await until(lambda: len(rig.engine.requests) == 2)
            await until(lambda: rig.room.output.cleared >= 1)  # playback stopped
            rig.engine.gate.set()  # whatever the cancelled turn would still say
            await until(lambda: "Okay." in rig.adapter.spoken)
        return rig

    rig = run(scenario())
    assert rig.room.output.paused >= 1  # the voice paused first, like the demo
    assert rig.engine.requests[1]["display_text"] == "stop that"
    assert S2 not in rig.adapter.spoken  # nothing late from the cancelled turn
    # said over the agent: a yes in it never answers what the reply asked
    assert "talk_over" not in rig.engine.requests[0]
    assert rig.engine.requests[1]["talk_over"] is True


def test_words_after_the_reply_is_done_are_no_talk_over():
    async def scenario():
        async with Rig(scripts=[["Okay.", "done"], ["Sure.", "done"]]) as rig:
            await rig.say("is the build green")
            await until(lambda: rig.adapter.spoken == ["Okay."])
            await until(lambda: rig.worker.agent_state == "listening")
            await rig.say("yes do it")
            await until(lambda: len(rig.engine.requests) == 2)
        return rig

    rig = run(scenario())
    assert not any("talk_over" in body for body in rig.engine.requests)


def interrupted_posts(poster):
    return [(p, b) for p, b in poster.posts if p.endswith("/interrupted")]


def test_a_talk_over_posts_the_played_text_once_with_its_own_turn_id():
    async def scenario():
        async with Rig(
            scripts=[[S1, "wait", S2, "done"], ["Okay.", "done"]], transcript=True
        ) as rig:
            await rig.say("is the build green")
            await until(
                lambda: rig.adapter.spoken == [S1] and rig.room.output.frames >= 3
            )
            await rig.say("stop that")
            await until(lambda: interrupted_posts(rig.poster))
            await until(lambda: "Okay." in rig.adapter.spoken)
            await rig.worker.drain()
        return rig

    rig = run(scenario())
    first, second = rig.engine.requests
    [(path, body)] = interrupted_posts(rig.poster)  # once, not once per turn
    assert path == f"turns/{first['turn_id']}/interrupted"  # the cut reply's turn
    assert path != f"turns/{second['turn_id']}/interrupted"
    assert body["text"].startswith("Checking") and S2 not in body["text"]


def test_the_next_turn_goes_out_after_the_talked_over_replys_cut():
    """The cut is its own post, the next turn its own request: the turn waits for
    the cut, so the daemon has it before the next model call."""

    async def scenario():
        async with Rig(
            scripts=[[S1, "wait", S2, "done"], ["Okay.", "done"]], transcript=True
        ) as rig:
            order, post = [], rig.poster.post

            async def slow_post(path, body):
                if path.endswith("/interrupted"):
                    await asyncio.sleep(0.5)  # an Engine slow to take the cut
                    order.append("cut")
                return await post(path, body)

            class Requests(list):
                def append(self, request):
                    order.append(request["display_text"])
                    super().append(request)

            rig.poster.post, rig.engine.requests = slow_post, Requests()
            await rig.say("is the build green")
            await until(
                lambda: rig.adapter.spoken == [S1] and rig.room.output.frames >= 3
            )
            await rig.say("stop that")
            await until(lambda: "Okay." in rig.adapter.spoken)
        return order

    assert run(scenario()) == ["is the build green", "cut", "stop that"]


class Turns:
    """The Engine's /turns as the next turn sees it: the request is the order entry."""

    def __init__(self, order):
        self.order = order

    async def stream(self, body):
        self.order.append("turn")
        yield "Okay."


def next_turn(worker):
    chat_ctx = llm.ChatContext()
    chat_ctx.add_message(role="user", content="what about now")
    reply = lw.KollabAgent(worker).llm_node(chat_ctx, [], None)
    return asyncio.ensure_future(reply.__anext__())


def test_a_turn_waits_for_a_talked_over_reply_still_finishing_and_its_cut():
    """LiveKit marks a talked-over reply's generation done a few loop turns before
    its handle, and can start the next reply in between: that turn waits for the
    handle, whose done callback sends the cut, and then for the cut."""

    class Speech:  # talked over, playout stopped, the handle not done yet
        interrupted = True

        def __init__(self):
            self.finished, self.callbacks = asyncio.Event(), []
            self.chat_items = [
                llm.ChatMessage(role="assistant", content=["So the"], interrupted=True)
            ]

        def done(self):
            return self.finished.is_set()

        def add_done_callback(self, callback):
            self.callbacks.append(callback)

        async def wait_for_playout(self):
            await self.finished.wait()

        def finish(self):
            self.finished.set()
            for callback in self.callbacks:
                callback(self)

    async def scenario():
        order = []

        class Poster(Recorder):
            async def post(self, path, body):
                if path.endswith("/interrupted"):
                    await asyncio.sleep(0.2)  # an Engine slow to take the cut
                    order.append(f"cut {body['text']}")
                return await super().post(path, body)

        worker = lw.Worker(CONFIG, FakeRoom(), FakeAdapter(), Poster(), Turns(order))
        old = Speech()
        worker.keep_played_text(lw.Turn("old-turn", 0.0), old)
        sentence = next_turn(worker)
        await asyncio.sleep(0.3)
        assert order == []  # nothing goes out while the old reply is finishing
        old.finish()
        assert await sentence == "Okay."
        return order

    assert run(scenario()) == ["cut So the", "turn"]


def test_a_turn_never_waits_for_the_events_ahead_of_it():
    """States and metrics ride their own queue: an Engine slow on events (here, the
    stt metric begin_turn sends just before the turn) holds no turn up."""

    async def scenario():
        order, gate = [], asyncio.Event()

        class Poster(Recorder):
            async def post(self, path, body):
                if path == "events":
                    await gate.wait()  # the Engine has not answered the events yet
                return await super().post(path, body)

        worker = lw.Worker(CONFIG, FakeRoom(), FakeAdapter(), Poster(), Turns(order))
        worker.state("listening")
        worker.stt_ms = 120
        sentence = await asyncio.wait_for(next_turn(worker), 1)
        gate.set()
        await worker.drain()
        return sentence, order, [b["payload"].get("stage") for _, b in worker.poster.posts]

    # the state and the stt metric were queued ahead of the turn, and posted after it
    assert run(scenario()) == ("Okay.", ["turn"], [None, "stt", "llm_first_token"])


def test_the_played_text_keeps_the_space_between_sentences():
    async def scenario():
        async with Rig(
            scripts=[[S1, S2, "wait", "done"], ["Okay.", "done"]],
            tts_chunks=10,
            transcript=True,
        ) as rig:
            await rig.say("is the build green")
            await until(  # the second sentence is playing
                lambda: rig.adapter.spoken == [S1, S2] and rig.room.output.frames >= 13
            )
            await rig.say("stop that")
            await until(lambda: interrupted_posts(rig.poster))
            await rig.worker.drain()
        return rig

    rig = run(scenario())
    [(_, body)] = interrupted_posts(rig.poster)
    assert body["text"].startswith(f"{S1} It"), body["text"]  # not "now.It"


def test_a_reply_that_was_never_played_posts_that_nothing_was_heard():
    async def scenario():
        async with Rig(
            scripts=[["wait", S1, "done"], ["Okay.", "done"]], transcript=True
        ) as rig:
            await rig.say("is the build green")
            await until(lambda: len(rig.engine.requests) == 1)  # thinking, no audio
            await rig.say("stop that")
            await until(lambda: "Okay." in rig.adapter.spoken)
            await rig.worker.drain()
        return rig

    rig = run(scenario())
    first = rig.engine.requests[0]
    assert rig.adapter.spoken == ["Okay."]
    # the Engine takes the reply nobody heard away
    assert interrupted_posts(rig.poster) == [(f"turns/{first['turn_id']}/interrupted", {"text": ""})]


def test_the_played_text_is_bound_to_the_turn_that_registered_it():
    class Speech:
        def __init__(self, text, interrupted=True):
            self.interrupted = interrupted
            self.chat_items = [
                llm.ChatMessage(role="user", content=["hi"]),
                llm.ChatMessage(role="assistant", content=[text], interrupted=interrupted),
            ]

        def add_done_callback(self, callback):
            self.callback = callback

    async def scenario():
        worker = lw.Worker(CONFIG, FakeRoom(), FakeAdapter(), Recorder())
        old, new = lw.Turn("old-turn", 0.0), lw.Turn("new-turn", 0.0)
        cut, blank, whole = Speech("So the build"), Speech("  "), Speech("All done.", False)
        for turn, speech in ((old, cut), (new, blank), (new, whole)):
            worker.keep_played_text(turn, speech)
        worker.keep_played_text(new, None)  # no handle to follow: nothing to register
        # the new turn's callbacks fire first; the old reply's message came late
        for speech in (blank, whole, cut):
            speech.callback(speech)
        await worker.drain()
        return worker

    worker = run(scenario())
    assert worker.poster.posts == [  # whole: played to the end, nothing to cut
        ("turns/new-turn/interrupted", {"text": ""}),  # blank: nothing was heard
        ("turns/old-turn/interrupted", {"text": "So the build"}),
    ]


def test_a_talk_over_marks_its_own_turn_only():
    def state(new_state):
        return SimpleNamespace(new_state=new_state)

    async def scenario():
        worker = lw.Worker(CONFIG, FakeRoom(), FakeAdapter(), Recorder())
        worker._agent_state(state("speaking"))
        worker._user_state(state("speaking"))  # over the reply, even one LiveKit lets play on
        turns = [worker.begin_turn("yeah", False)]
        worker._agent_state(state("listening"))
        worker._user_state(state("speaking"))
        turns.append(worker.begin_turn("yes", False))
        worker._agent_state(state("thinking"))
        worker._user_state(state("speaking"))
        turns.append(worker.begin_turn(lw.GREETING, True))
        turns.append(worker.begin_turn("yes", False))  # a greeting resets it too
        await worker.drain()
        return turns

    over, after, greeting, later = run(scenario())
    assert over.body["talk_over"] is True
    assert not any("talk_over" in turn.body for turn in (after, greeting, later))


def test_a_cough_over_the_reply_pauses_it_and_it_resumes():
    async def scenario():
        async with Rig(scripts=[[S1, S2, "done"]], tts_chunks=150) as rig:
            await rig.say("is the build green")
            await until(
                lambda: rig.adapter.spoken[:1] == [S1] and rig.room.output.frames >= 3
            )
            await rig.say("", seconds=0.7)  # speech, no words: a cough
            await until(lambda: rig.room.output.paused >= 1)
            await until(lambda: rig.room.output.resumed >= 1)  # 2 s without words
            await until(lambda: rig.adapter.spoken == [S1, S2], timeout=15)  # it goes on
        return rig

    rig = run(scenario())
    assert not rig.engine.disconnected.is_set()  # the Kollab turn ran to its end
    assert len(rig.engine.requests) == 1  # the cough was no turn
    assert rig.adapter.spoken == [S1, S2]


def test_greeting_on_join():
    hello = "Hey there, what do you want to talk about?"

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
    hello = "Hey there."

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


def test_a_worker_whose_engine_dies_stops_and_fences(monkeypatch):
    parent = [os.getppid()]
    monkeypatch.setattr(lw.os, "getppid", lambda: parent[0])
    monkeypatch.setattr(lw, "PARENT_POLL_S", 0.01)

    async def scenario():
        async with Rig() as rig:
            parent[0] = 1  # a SIGKILLed Engine sends no SIGTERM: launchd adopts the worker
            await asyncio.wait_for(rig.task, 10)
            return rig

    rig = run(scenario())
    assert rig.worker.stopping.is_set() and rig.room.is_closed
    assert rig.adapter.fenced == CONFIG.generation + 1 and rig.adapter.released


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


def test_edge_trim_keeps_a_breath_of_a_clips_silence():
    rate = lw.PLAY_RATE
    quiet, word = np.zeros(rate // 2, np.float32), np.full(rate // 5, 0.3, np.float32)
    pause = quiet[: rate * 3 // 10]
    trim = lw._EdgeTrim()
    # 1.5 s of lead-in over three chunks, a word, a pause, a word, 0.6 s of tail
    chunks = [quiet, quiet, np.concatenate([quiet, word])]
    chunks += [np.concatenate([pause, word, quiet]), quiet[: rate // 10]]
    out = np.concatenate([trim.push(chunk) for chunk in chunks] + [trim.end()])
    loud = np.flatnonzero(out)
    assert loud[0] == lw.LEAD_KEEP
    assert out.size - 1 - loud[-1] == lw.TAIL_KEEP
    assert out.size == lw.LEAD_KEEP + 2 * word.size + pause.size + lw.TAIL_KEEP

    silent = lw._EdgeTrim()
    assert silent.push(quiet).size == 0 and silent.end().size == lw.LEAD_KEEP


def test_a_clip_plays_its_voice_after_a_breath_not_its_lead_in():
    # 0.8 s of silence, then 0.4 s of voice: what plays is 0.1 s, then the voice
    adapter = FakeAdapter(tts_chunks=10, tts_pace=0, tts_lead=20)
    worker = SimpleNamespace(adapter=adapter, binding=None, error=lambda *_: None)

    async def play():
        async with lw.SpeechTTS(worker).synthesize(S1) as stream:
            return [event.frame async for event in stream]

    frames = run(play())
    pcm = np.concatenate([np.frombuffer(f.data, np.int16) for f in frames])
    assert np.flatnonzero(pcm)[0] == lw.LEAD_KEEP
    assert np.count_nonzero(pcm) == 10 * 960
    assert pcm.size < lw.LEAD_KEEP + 11 * 960  # LiveKit may pad the last frame


def test_config_loads_from_env_and_the_poster_never_raises():
    raw = {k: getattr(CONFIG, k) for k in lw.WorkerConfig.__dataclass_fields__}
    assert (
        lw.WorkerConfig.from_env({"KOLLAB_VOICE_WORKER_CONFIG": json.dumps(raw)})
        == CONFIG
    )
    unreachable = lw.WorkerConfig("ws://x", "j", "r", "s", 1, "http://127.0.0.1:9", "t")
    assert run(lw.EnginePoster(unreachable).post("events", {})) is False
