"""Voice's own lines are kept on disk and replayed without synthesis."""

import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from kollabor_voice.classifiers import save_preferences
from kollabor_voice.output import SpeechQueue
from kollabor_voice.speech_cache import KEEP_S, SpeechCache

LINE = "Sorry, was that for me?"


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix="kv-", dir="/tmp") as directory:
        yield Path(directory)


class Speaker:
    """Fake StreamPlayer: records each written chunk instead of playing it."""

    def __init__(self):
        self.spoken = []

    def write(self, samples, rate, cancelled):
        self.spoken.append((np.asarray(samples, np.float32), rate))
        return not cancelled.is_set()

    def finish(self):
        pass

    def abort(self):
        pass


def engine(calls):
    """Streams two chunks per sentence, different on every call."""

    def stream(text):
        calls.append(text)
        yield np.full(8, len(calls) / 10, np.float32), 24000
        yield np.full(4, -0.5, np.float32), 24000

    return stream


async def say(root, stream, producer="voice-classifier"):
    """Speak LINE through a fresh queue, as a restarted service would."""
    speaker = Speaker()
    q = SpeechQueue(root, stream, speaker)
    q.admit(LINE, f"line-{time.monotonic_ns()}", "epoch", producer)
    worker = asyncio.create_task(q.run())
    await q.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    return q, speaker.spoken


def audio(spoken):
    assert {rate for _, rate in spoken} == {24000}
    return np.concatenate([samples for samples, _ in spoken])


def kept(root):
    return sorted((root / "cache/speech").glob("*.f32"))


@pytest.mark.asyncio
async def test_a_hit_plays_the_stored_audio_without_synthesis(root):
    calls = []
    _, first = await say(root, engine(calls))
    q, again = await say(root, engine(calls))
    assert calls == [LINE]
    assert np.array_equal(audio(again), audio(first))
    (job,) = q.jobs
    events = [json.loads(line) for line in q.receipt_path.read_text().splitlines()]
    assert [e["state"] for e in events if e["id"] == job] == [
        "queued",
        "synthesizing",
        "playing",
        "played",
    ]


@pytest.mark.asyncio
async def test_agent_replies_are_never_kept(root):
    calls = []
    await say(root, engine(calls), producer="agent")
    await say(root, engine(calls), producer="agent")
    assert calls == [LINE, LINE]
    assert not (root / "cache").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [{"kokoro_voice": "bf_emma"}, {"kokoro_speed": 1.2}, {"tts_engine": "vibevoice"}],
)
async def test_a_voice_change_misses(root, change):
    calls = []
    await say(root, engine(calls))
    save_preferences(change, root)
    _, spoken = await say(root, engine(calls))
    assert calls == [LINE, LINE]
    assert audio(spoken)[0] == np.float32(0.2)  # the new synthesis, not the old file
    assert len(kept(root)) == 2


DAMAGE = {
    "empty": lambda path: path.write_bytes(b""),
    "truncated": lambda path: path.write_bytes(path.read_bytes()[:-3]),
    "flipped": lambda path: path.write_bytes(
        (data := path.read_bytes())[:-1] + bytes([data[-1] ^ 1])
    ),
    "unreadable": lambda path: path.chmod(0),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", DAMAGE)
async def test_a_bad_file_is_a_miss_and_is_regenerated(root, damage):
    calls = []
    await say(root, engine(calls))
    (path,) = kept(root)
    DAMAGE[damage](path)
    _, spoken = await say(root, engine(calls))
    assert calls == [LINE, LINE]
    assert audio(spoken)[0] == np.float32(0.2)
    _, again = await say(root, engine(calls))
    assert calls == [LINE, LINE]  # the regenerated file replays
    assert np.array_equal(audio(again), audio(spoken))


@pytest.mark.asyncio
async def test_a_cancelled_sentence_is_never_kept(root):
    started, resume = threading.Event(), threading.Event()

    def stream(text):
        yield np.full(8, 0.1, np.float32), 24000
        started.set()
        resume.wait(2)
        yield np.full(4, -0.5, np.float32), 24000

    q = SpeechQueue(root, stream, Speaker())
    q.admit(LINE, "line", "epoch", "voice-classifier")
    worker = asyncio.create_task(q.run())
    deadline = time.monotonic() + 3
    while not started.is_set():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.01)
    q.cancel("epoch")
    resume.set()
    await q.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert not (root / "cache").exists()


@pytest.mark.asyncio
async def test_a_hit_keeps_the_file_three_more_days(root):
    calls = []
    await say(root, engine(calls))
    (path,) = kept(root)
    almost = time.time() - KEEP_S + 60
    os.utime(path, (almost, almost))
    await say(root, engine(calls))
    assert calls == [LINE]
    assert path.stat().st_mtime > time.time() - 60


def test_files_unplayed_for_three_days_are_pruned(root):
    folder = root / "cache/speech"
    folder.mkdir(parents=True)
    stale = time.time() - KEEP_S - 60

    def file(name, mtime):
        path = folder / name
        path.write_bytes(b"x")
        os.utime(path, (mtime, mtime))

    file("fresh.f32", time.time() - KEEP_S + 3600)
    file("old.f32", stale)
    file("crashed.tmp", stale)
    cache = SpeechCache(root)  # at startup
    assert [path.name for path in folder.iterdir()] == ["fresh.f32"]
    file("older.f32", stale)
    cache.store("new", [(np.ones(4, np.float32), 24000)])  # on write
    assert sorted(path.name for path in folder.iterdir()) == ["fresh.f32", "new.f32"]
