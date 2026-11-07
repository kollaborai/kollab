"""Lane B: LiveKit media bridge IPC and scope tests (no livekit import)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import kollabor_voice.phone_audio_bridge as pab
import pytest
from kollabor_engine.voice_livekit import (
    LiveKitRuntime,
    PhoneBridgeHandle,
    _kill_process_group,
    room_name_for,
)

FAKE_CHILD = (
    "import json, os, sys\n"
    "line = sys.stdin.readline()\n"
    "sys.stdout.write('{\"type\": \"ready\"}\\n')\n"
    "sys.stdout.flush()\n"
    "capture = os.environ.get('KOLLAB_TEST_CAPTURE')\n"
    "if capture:\n"
    "    open(capture, 'w').write(line)\n"
    "sys.stdin.readline()\n"
)


class FakeReader:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeWriter:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        pass


class FakeRoom:
    def __init__(self):
        self.handlers = {}
        self.connected = None
        self.disconnected = False
        self.published = []
        self.publish_options = []

    def on(self, event, handler):
        self.handlers[event] = handler

    async def connect(self, url, token, options):
        self.connected = (url, token, options)

    async def disconnect(self):
        self.disconnected = True

    @property
    def local_participant(self):
        return self

    async def publish_track(self, source, options=None):
        self.published.append(source)
        self.publish_options.append(options)


class FakeSource:
    def __init__(self):
        self.frames = []

    def capture_frame(self, frame):
        self.frames.append(bytes(frame.data))


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return type("Event", (), {"frame": type("F", (), {"data": self._chunks.pop(0)})()})()


def fake_seams(monkeypatch, stream_chunks=()):
    room = FakeRoom()
    source = FakeSource()
    streams = []
    monkeypatch.setattr(
        pab, "_new_frame", lambda data, rate, samples: type("F", (), {"data": data})()
    )
    monkeypatch.setattr(pab, "_new_room", lambda: room)
    monkeypatch.setattr(pab, "_room_options", lambda: object())
    monkeypatch.setattr(pab, "_mic_publish_options", lambda: SimpleNamespace(source="microphone"), raising=False)
    monkeypatch.setattr(pab, "_audio_source", lambda rate, channels=1: source)
    monkeypatch.setattr(
        pab, "_local_audio_track", lambda name, source: ("track", source)
    )
    monkeypatch.setattr(
        pab,
        "_track_stream",
        lambda track, sample_rate, capacity: streams.append(
            (track, sample_rate, capacity)
        )
        or FakeStream(list(stream_chunks)),
    )
    return room, source, streams


def start_message():
    return {
        "type": "start",
        "url": "ws://127.0.0.1:7880",
        "token": "fake-token",
        "rate_in": 16000,
        "rate_out": 24000,
    }


@pytest.mark.asyncio
async def test_bridge_publishes_mic_frames(monkeypatch):
    room, source, _streams = fake_seams(monkeypatch)
    mic = bytes(6400)
    lines = [json.dumps(start_message()).encode()]
    lines.append(json.dumps({"type": "mic", "n": 1, "pcm": base64.b64encode(mic).decode()}).encode())
    lines.append(json.dumps({"type": "close"}).encode())
    writer = FakeWriter()
    code = await pab.run_bridge(FakeReader(lines), writer)
    assert code == 0
    emitted = "".join(writer.lines)
    assert '"type": "ready"' in emitted.replace('": "', '":"') or '"ready"' in emitted
    assert room.connected is not None
    assert len(room.published) == 1
    assert getattr(room.publish_options[0], "source", None) == "microphone"
    # 6400B of 16k PCM16 == 10 x 20ms frames of 640B each.
    assert len(source.frames) == 10
    assert all(len(f) == 640 for f in source.frames)


@pytest.mark.asyncio
async def test_bridge_only_subscribes_worker(monkeypatch):
    room, _source, streams = fake_seams(monkeypatch)
    lines = [json.dumps(start_message()).encode()]
    lines.append(json.dumps({"type": "close"}).encode())
    writer = FakeWriter()
    task = asyncio.ensure_future(pab.run_bridge(FakeReader(lines), writer))
    await asyncio.sleep(0.05)
    handler = room.handlers["track_subscribed"]
    handler(object(), object(), type("P", (), {"identity": "user-1"})())
    assert streams == []
    worker_track = object()
    handler(worker_track, object(), type("P", (), {"identity": "worker-xyz"})())
    await asyncio.sleep(0.05)
    assert streams and streams[0][0] is worker_track
    assert streams[0][1] == 24000
    task.cancel()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_bridge_output_and_overflow(monkeypatch):
    fake_seams(monkeypatch)
    queue = pab._Outbound(3)
    for i in range(5):
        queue.put(bytes([i]) * 8)
    assert queue.dropped == 2
    chunks = queue.drain()
    assert len(chunks) == 3
    assert chunks[0] == bytes([2]) * 8
    # Engine-side chunk cap: audio lines carry at most 9600 byte payloads.
    big = b"\x01\x02" * 6000
    assert len(big) > pab.MAX_AUDIO_CHUNK_BYTES
    session_chunks = [big[i : i + pab.MAX_AUDIO_CHUNK_BYTES] for i in range(0, len(big), pab.MAX_AUDIO_CHUNK_BYTES)]
    assert all(len(c) % 2 == 0 and len(c) <= pab.MAX_AUDIO_CHUNK_BYTES for c in session_chunks)


@pytest.mark.asyncio
async def test_bridge_eof_and_crash_cleanup(monkeypatch):
    room, _source, _streams = fake_seams(monkeypatch)
    writer = FakeWriter()
    lines = [json.dumps(start_message()).encode()]
    code = await pab.run_bridge(FakeReader(lines), writer)  # EOF after start
    assert code == 0
    assert room.disconnected is True
    # Engine handle against a crashing child: start raises, close still cleans up.
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "raise SystemExit(3)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    handle = PhoneBridgeHandle(process)
    with pytest.raises(Exception):
        await handle.start("{}\n")
    await handle.close()
    assert process.returncode is not None

@pytest.mark.asyncio
async def test_pump_replacement_does_not_rereport_drops(monkeypatch):
    """Drops reported by an earlier pump are not re-reported after the pump
    is replaced (track re-subscription): the drop baseline lives on the
    session, not on the pump worker."""
    session = pab._BridgeSession(FakeReader([]), FakeWriter())
    session.out.dropped_bytes = 4800  # one 20 ms frame at 24 kHz

    class _Frame:
        data = b"\x00\x00" * 10

    class _Event:
        frame = _Frame()

    class _Stream:
        def __init__(self, events):
            self._events = list(events)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._events:
                raise StopAsyncIteration
            return self._events.pop(0)

    track = object()
    monkeypatch.setattr(pab, "_track_stream", lambda t, sr, cap: _Stream([_Event()]))

    def reported():
        return [
            json.loads(line)["bytes"]
            for line in session._writer.lines
            if json.loads(line).get("type") == "audio_dropped"
        ]

    pump1 = asyncio.create_task(session._pump_worker(track))
    await pump1
    assert reported() == [4800]

    # Pump replacement: total unchanged, second worker must stay silent.
    session.out.dropped_bytes = 4800
    pump2 = asyncio.create_task(session._pump_worker(track))
    await pump2
    assert reported() == [4800]


@pytest.mark.asyncio
async def test_kill_process_group_reaps_already_exited_child(monkeypatch):
    """When the kill path finds the group already gone the helper must still
    reap the child: under load close() can reach the kill before the
    transport reaps an exited child, and returncode stays None until the
    event loop reaps it."""
    # The child is alive (so the entry returncode check passes), it is NOT
    # a process-group leader (like a plain asyncio spawn without
    # start_new_session), and the group kill is forced to fail with
    # ProcessLookupError - the deterministic version of the load race where
    # close() reaches the kill path before the transport reaps an exited
    # child. The helper must still reap it instead of returning bare.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(0.2); raise SystemExit(3)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:

        def _no_such_group(pid, sig):
            raise ProcessLookupError(f"no process group: {pid}")

        monkeypatch.setattr(os, "killpg", _no_such_group)
        await _kill_process_group(process)
        assert process.returncode == 3
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), 5.0)


@pytest.mark.asyncio
async def test_bridge_token_scoped_to_room(tmp_path, monkeypatch):
    capture = tmp_path / "start.json"
    runtime = LiveKitRuntime(
        engine_url=lambda: "http://127.0.0.1:0",
        engine_token=lambda: "engine-token",
        worker_argv=[sys.executable, "-c", FAKE_CHILD],
        worker_env={"KOLLAB_TEST_CAPTURE": str(capture)},
    )
    binding = type(
        "Binding", (), {"voice_session_id": str(uuid.uuid4()), "generation": 2}
    )()
    handle = await runtime.start_phone_bridge(binding)
    try:
        payload = json.loads(capture.read_text())
        assert payload["type"] == "start"
        assert payload["url"] == runtime.livekit_url
        assert payload["rate_in"] == 16000 and payload["rate_out"] == 24000
        header, body, _sig = payload["token"].split(".")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert claims["video"]["room"] == room_name_for(binding)
        assert claims["video"].get("canPublishSources") == ["microphone"]
        assert claims["sub"].startswith("phone-")
        assert claims["exp"] > claims["nbf"]
        # The token reached the child on stdin (captured line), not via argv:
        # the fake child prints nothing else and start_phone_bridge wrote it
        # through the stdin pipe only.
    finally:
        await handle.close()
    assert handle.process.returncode is not None


def test_bridge_never_opens_sound_device():
    banned = (
        "sounddevice", "pyaudio", "soundfile", "pygame",
        "miniaudio", "simpleaudio", "ossaudiodev", "winsound",
    )
    for path in (
        Path(pab.__file__),
        Path(__import__("kollabor_engine.voice_livekit", fromlist=["x"]).__file__),
    ):
        text = path.read_text()
        for name in banned:
            assert name not in text, f"{path.name} references {name}"
    for name in banned:
        assert name not in sys.modules, f"{name} imported by bridge tests"


@pytest.mark.asyncio
async def test_bridge_malformed_start_emits_error_line(monkeypatch):
    """ruby P2: the bad-start error must reach stdout (was missing await)."""
    fake_seams(monkeypatch)
    lines = [
        json.dumps({"type": "start"}).encode(),  # no url/token
        json.dumps({"type": "close"}).encode(),
    ]
    writer = FakeWriter()
    code = await pab.run_bridge(FakeReader(lines), writer)
    assert code == 1
    emitted = "".join(writer.lines)
    assert "bridge_bad_start" in emitted


@pytest.mark.asyncio
async def test_bridge_second_worker_track_cancels_first_pump(monkeypatch):
    """ruby P3: a re-publish window must not orphan the first pump."""
    room, _source, streams = fake_seams(
        monkeypatch, stream_chunks=[b"\x00" * 16] * 50
    )
    session = pab._BridgeSession(
        FakeReader([json.dumps(start_message()).encode()]), FakeWriter()
    )
    assert await session._start(start_message()) is False
    handler = room.handlers["track_subscribed"]
    worker = type("P", (), {"identity": "worker-xyz"})()

    # Fire both back-to-back with no intervening yield: that is exactly the
    # re-publish window. The first pump is scheduled but must never run.
    handler(object(), object(), worker)
    first = session._pump
    handler(object(), object(), worker)
    second = session._pump
    await asyncio.sleep(0.05)

    assert first is not None and second is not first
    assert first.cancelled()  # without the fix it runs on, feeding both pumps
    assert session._pump is second
    # The cancelled pump never even opened its stream: exactly one survives.
    assert len(streams) == 1

    await session._teardown()
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await second


@pytest.mark.asyncio
async def test_handle_output_buffer_is_bounded_drop_oldest(monkeypatch):
    """ruby P2: a stalled caller must not grow engine memory unbounded."""
    import kollabor_engine.voice_livekit as vlk

    monkeypatch.setattr(vlk, "PHONE_BRIDGE_OUT_CAP_BYTES", 1024)
    payloads = [bytes([index]) * 512 for index in range(5)]  # 2560 > 1024
    lines = [
        json.dumps(
            {"type": "audio", "pcm": base64.b64encode(payload).decode()}
        ).encode()
        for payload in payloads
    ]
    handle = PhoneBridgeHandle(
        __import__("types").SimpleNamespace(stdout=FakeReader(lines))
    )
    await handle._read_loop()

    assert len(handle._out) == 1024
    assert handle._out_dropped == 2560 - 1024
    assert bytes(handle._out).endswith(payloads[-1])  # newest kept
    drained = handle.take_output()
    assert drained == bytes(handle._out) or drained.endswith(payloads[-1])
    assert handle._out == bytearray()  # take_output clears


@pytest.mark.asyncio
async def test_handle_counts_midcall_bridge_errors():
    """ruby P3: mid-call error lines must not vanish into silence."""
    lines = [
        json.dumps({"type": "error", "code": "bridge_audio"}).encode(),
        json.dumps(
            {"type": "audio", "pcm": base64.b64encode(b"\x01\x02").decode()}
        ).encode(),
        b"not json at all",
        json.dumps({"type": "error", "code": "bridge_pcm"}).encode(),
    ]
    handle = PhoneBridgeHandle(
        __import__("types").SimpleNamespace(stdout=FakeReader(lines))
    )
    await handle._read_loop()

    # 2 parsed bridge errors; the malformed line is protocol noise, not one
    assert handle.bridge_errors == 2
    assert handle.bridge_last_error == "bridge_pcm"
    assert handle._out == bytearray(base64.b64decode(base64.b64encode(b"\x01\x02")))
@pytest.mark.asyncio
async def test_handle_close_kill_path_no_attributeerror(monkeypatch):
    """aquamarine's live bug (#440): a child that ignores stdin made close()
    hit the exit timeout and call self._kill_group, which only exists on
    LiveKitRuntime -> AttributeError mid-cleanup. The timeout path must
    terminate the group without raising."""
    import kollabor_engine.voice_livekit as vlk

    monkeypatch.setattr(vlk, "PHONE_BRIDGE_CLOSE_GRACE_S", 0.1)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    handle = PhoneBridgeHandle(process)
    await handle.close()  # must not raise AttributeError
    assert process.returncode is not None  # kill path actually reaped it

@pytest.mark.asyncio
async def test_never_ready_close_kills_child_at_once():
    """aquamarine's shutdown-cap constraint (#440): a child that never
    reported ready must die IMMEDIATELY on close(), not after the graceful
    window. No timeout patching here: if close() still waited the full
    grace (or the old 5 s), elapsed would blow past the 1 s bound."""
    import time as _time


    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    handle = PhoneBridgeHandle(process)  # start() never called: _ready False
    t0 = _time.monotonic()
    await handle.close()
    elapsed = _time.monotonic() - t0
    assert elapsed < 1.0, f"never-ready close took {elapsed:.2f}s"
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_ready_close_uses_short_grace_then_kills(monkeypatch):
    """koordinator's order (#440): a ready-but-unresponsive child gets the
    short graceful window (constant under lane A's 5 s cap), then the
    group kill. Patching the constant to 0.1 and seeing close() return
    quickly proves the constant is the knob (a hardcoded 5 s would hang)."""
    import time as _time

    import kollabor_engine.voice_livekit as vlk

    monkeypatch.setattr(vlk, "PHONE_BRIDGE_CLOSE_GRACE_S", 0.1)
    child = (
        "import json,sys,time;"
        "print(json.dumps({'type':'ready'}),flush=True);"
        "[None for _ in iter(lambda: sys.stdin.readline(), '')];"
        "time.sleep(30)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", child,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    handle = PhoneBridgeHandle(process)
    await handle.start('{"type":"open"}')  # sees ready: _ready True
    assert handle._ready is True
    t0 = _time.monotonic()
    await handle.close()  # child ignores the close line -> grace -> kill
    elapsed = _time.monotonic() - t0
    assert elapsed < 2.0, f"ready close took {elapsed:.2f}s"
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_handle_output_cap_is_contract_28800():
    """jasper burst repro (review_bridge_overflow.py): the handle's received-
    audio buffer must cap at the broker's 28,800-byte contract (600 ms), not
    1 MiB of lag, and every dropped byte must be publicly countable."""
    import kollabor_engine.voice_livekit as vlk

    assert vlk.PHONE_BRIDGE_OUT_CAP_BYTES == 28_800
    payloads = [bytes([1, 2]) * 4800 for _ in range(5)]  # 5 * 9600 = 48000
    lines = [
        json.dumps(
            {"type": "audio", "pcm": base64.b64encode(payload).decode()}
        ).encode()
        for payload in payloads
    ]
    handle = PhoneBridgeHandle(
        __import__("types").SimpleNamespace(stdout=FakeReader(lines))
    )
    await handle._read_loop()

    assert len(handle._out) == 28_800  # never more than the contract
    assert handle.out_dropped_bytes == 48_000 - 28_800  # oldest bytes dropped
    assert handle.out_dropped == handle.out_dropped_bytes  # alias, same bytes
    assert bytes(handle._out).endswith(payloads[-1])  # newest kept
    assert handle.bridge_errors == 0  # cap drops are accounting, not faults


@pytest.mark.asyncio
async def test_handle_counts_child_queue_drops_over_ipc():
    """jasper child-queue repro (review_child_queue_overflow.py): drops the
    child makes in its own outbound queue must arrive over IPC and be added
    to the handle's total -- and must NOT be counted as bridge errors."""
    lines = [
        json.dumps({"type": "audio_dropped", "bytes": 9600}).encode(),
        json.dumps(
            {"type": "audio", "pcm": base64.b64encode(b"\x01\x02").decode()}
        ).encode(),
        json.dumps({"type": "audio_dropped", "bytes": 4800}).encode(),
        json.dumps({"type": "audio_dropped", "bytes": "bogus"}).encode(),
    ]
    handle = PhoneBridgeHandle(
        __import__("types").SimpleNamespace(stdout=FakeReader(lines))
    )
    await handle._read_loop()

    assert handle.out_dropped_bytes == 14_400  # 9600 + 4800, bad value skipped
    assert handle.bridge_errors == 0  # audio accounting is not a fault


@pytest.mark.asyncio
async def test_outbound_dropped_bytes_reach_ipc_from_pump(monkeypatch):
    """The child's pump must report its _Outbound drops over IPC as
    audio_dropped deltas, so the engine total covers every loss point."""
    room, _source, _streams = fake_seams(
        monkeypatch, stream_chunks=[bytes([1, 2]) * 4800] * 4  # 4 into a 3-queue
    )
    writer = FakeWriter()
    session = pab._BridgeSession(
        FakeReader([json.dumps(start_message()).encode()]), writer
    )
    assert await session._start(start_message()) is False

    handler = room.handlers["track_subscribed"]
    handler(object(), object(), type("P", (), {"identity": "worker-xyz"})())
    assert session._pump is not None
    await session._pump

    # Unit half: the counter itself counts bytes of dropped-oldest items.
    queue = pab._Outbound(3)
    for _ in range(4):
        queue.put(bytes([1, 2]) * 4800)
    assert queue.dropped == 1
    assert queue.dropped_bytes == 9600

    # IPC half: the pump reported one audio_dropped delta for the dropped
    # frame and left exactly the 3 kept frames queued (audio lines themselves
    # are emitted by run_bridge's flusher, covered by the protocol tests).
    messages = [
        json.loads(line) for line in "".join(writer.lines).splitlines() if line.strip()
    ]
    drops = [m for m in messages if m.get("type") == "audio_dropped"]
    assert len(drops) == 1
    assert drops[0]["bytes"] == 9600
    assert len(session.out.drain()) == 3

    await session._teardown()


@pytest.mark.asyncio
async def test_bad_pcm_error_reaches_the_engine():
    """A bad-PCM frame reports the error to the engine: the bridge_pcm _emit
    call is async, so it must be awaited or the error never leaves the bridge."""
    session = pab._BridgeSession(FakeReader([]), FakeWriter())

    handled = await session._mic({"pcm": "!!!not base64!!!"})

    assert handled is False
    errors = [
        json.loads(line)
        for line in session._writer.lines
        if json.loads(line).get("type") == "error"
    ]
    assert {"type": "error", "code": "bridge_pcm"} in errors


@pytest.mark.asyncio
async def test_voice_control_opens_only_the_current_phone_generation():
    from types import SimpleNamespace

    from kollabor_engine.phone_audio import PhoneCallError
    from kollabor_engine.voice_control import VoiceControl

    binding = SimpleNamespace(generation=3)
    calls = []

    class Host:
        async def get_binding(self, owner, session):
            assert owner == "engine" and session == "voice-id"
            return binding

        async def run_if_current(self, actual, operation):
            assert actual is binding
            calls.append("fenced")
            return await operation()

    async def start(actual):
        assert actual is binding
        calls.append("started")
        return "bridge"

    control = VoiceControl.__new__(VoiceControl)
    control.host = Host()
    control.runtime = SimpleNamespace(start_phone_bridge=start)
    with pytest.raises(PhoneCallError, match="call_stale"):
        await control.open_phone_bridge("voice-id", 2)
    assert calls == []
    assert await control.open_phone_bridge("voice-id", 3) == "bridge"
    assert calls == ["fenced", "started"]
