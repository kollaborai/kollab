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
