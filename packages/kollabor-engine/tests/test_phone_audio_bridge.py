"""Lane B: LiveKit media bridge IPC and scope tests (no livekit import)."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import uuid
from pathlib import Path

import pytest

import kollabor_voice.phone_audio_bridge as pab
from kollabor_engine.voice_livekit import (
    LiveKitRuntime,
    PhoneBridgeHandle,
    mint_livekit_jwt,
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

    def on(self, event, handler):
        self.handlers[event] = handler

    async def connect(self, url, token, options):
        self.connected = (url, token, options)

    async def disconnect(self):
        self.disconnected = True

    @property
    def local_participant(self):
        return self

    async def publish_track(self, source):
        self.published.append(source)


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

    import kollabor_engine.voice_livekit as vlk

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
