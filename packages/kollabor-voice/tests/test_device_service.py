"""Device boundary checks; doubles here do not prove real speech or playback."""

import asyncio
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
from kollabor_voice.audio import AudioSegment, SpeechSegmenter
from kollabor_voice.client import VoiceClient
from kollabor_voice.control import VoiceError, manifest
from kollabor_voice.observer import AdmissionStore, parse_decision
from kollabor_voice.output import SpeechQueue, sentences
from kollabor_voice.service import VoiceService
from kollabor_voice.store import TranscriptLog


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix="kv-", dir="/tmp") as directory:
        yield Path(directory)


class Recorder:
    active = 0
    peak = 0
    starts = 0

    def __init__(self):
        self.frames, self.events = queue.Queue(), queue.Queue()
        self._last_frame_ts = None
        self.level = 0.02
        self.device_name = "test device"
        self.running = False

    def start(self):
        self.running = True
        type(self).active += 1
        type(self).starts += 1
        type(self).peak = max(type(self).peak, type(self).active)

    def stop(self):
        if self.running:
            type(self).active -= 1
            self.running = False


class Models:
    def warmup(self):
        pass

    def transcribe(self, audio):
        return "Kollab, check the tests.", "en"

    def synthesize(self, text):
        return np.zeros(16, np.float32), 24000


async def until(predicate, seconds=3):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def service(root):
    Recorder.active = Recorder.peak = Recorder.starts = 0
    server = VoiceService(
        root,
        load_assets=lambda *args: {},
        load_models=lambda _: Models(),
        recorder_factory=Recorder,
        segmenter_factory=lambda: SpeechSegmenter(lambda _: True),
        player=lambda *args: True,
    )
    await server.start()
    await until(lambda: server.setup["state"] == "ready")
    try:
        yield server
    finally:
        await server.close()


async def claim(client, name="one"):
    return await client.call(
        "claim", client_id=name, claim_id=name, activated_at=time.time()
    )


def auth(lease):
    return {"token": lease["token"], "epoch": lease["epoch"]}


def test_client_has_no_heavy_startup_imports():
    source = Path(__file__).resolve().parents[1] / "src"
    script = (
        f"import sys;sys.path.insert(0,{str(source)!r});import kollabor_voice.client;"
        "assert not set(['numpy','faster_whisper','sounddevice','kokoro_onnx']) & set(sys.modules)"
    )
    subprocess.run([sys.executable, "-S", "-c", script], check=True)


def test_manifest_contains_complete_hashes():
    for bundle in manifest()["bundles"].values():
        assert bundle["license"] and bundle["revision"]
        for item in bundle["files"]:
            assert len(item["sha256"]) == 64
            int(item["sha256"], 16)
            assert item["size"] > 0


def test_broken_runtime_marker_repairs_dependencies_before_launch(root, monkeypatch):
    import hashlib

    from kollabor_voice import bootstrap

    pins = manifest()["runtime"]
    runtime = (
        root / "runtimes" / hashlib.sha256(json.dumps(pins).encode()).hexdigest()[:16]
    )
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python").touch()
    (runtime / "ready.json").write_text(json.dumps({"requirements": pins}))
    calls, launches = [], []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(sys, "argv", ["voice-bootstrap", "--root", str(root)])
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/mock/uv")
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap.os, "execv", lambda *args: launches.append(args))
    bootstrap.main()
    assert "--reinstall" in calls[1]
    assert len(calls) == 3 and len(launches) == 1
    assert json.loads((root / "setup.json").read_text())["state"] == "starting"


@pytest.mark.asyncio
async def test_fifty_clients_share_service_and_transfer_without_overlapping_capture(
    service, root
):
    clients = [VoiceClient(root) for _ in range(50)]
    results = await asyncio.gather(*(c.call("status") for c in clients))
    assert len({r["instance_id"] for r in results}) == 1
    assert Recorder.starts == 0  # Merely opening an agent never opens the mic.
    first = await claim(clients[0])
    second = await claim(clients[1], "two")
    assert Recorder.peak == 1
    assert Recorder.starts == 2
    with pytest.raises(VoiceError, match="another chat"):
        await clients[0].call(
            "voice_out", **auth(first), text="old", reply_id="old", producer_id="one"
        )
    await clients[1].call("release", **auth(second))
    assert Recorder.active == 0
    assert (root / "service.sock").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_expired_owner_stops_without_promoting_another_client(service, root):
    client = VoiceClient(root)
    lease = await claim(client)
    service.owner["heartbeat"] -= 4
    await until(lambda: service.owner is None)
    assert Recorder.active == 0
    assert (await client.call("status"))["state"] == "off"
    with pytest.raises(VoiceError):
        await client.call("heartbeat", **auth(lease))


@pytest.mark.asyncio
async def test_persist_before_read_and_partial_after_off(service, root):
    client = VoiceClient(root)
    lease = await claim(client)
    now = time.time()
    service.submit(AudioSegment(np.ones(16000, np.float32), now - 1, now))
    await service.segments.join()
    response = await client.call(
        "read", **auth(lease), stream_id=lease["stream_id"], after=0
    )
    record = response["records"][0]
    assert record["text"] == "Kollab, check the tests."
    assert json.loads(service.transcript.path.read_text().splitlines()[0]) == record
    assert record["final"]
    assert (
        abs(
            (
                __import__("datetime").datetime.fromisoformat(record["ended_at"])
                - __import__("datetime").datetime.fromisoformat(record["started_at"])
            ).total_seconds()
            - 1
        )
        < 0.001
    )
    service.segmenter.feed(np.ones(16000, np.float32), now)
    await client.call("release", **auth(lease))
    await service.segments.join()
    assert not json.loads(service.transcript.path.read_text().splitlines()[-1])["final"]


@pytest.mark.asyncio
async def test_dead_mic_error_without_any_frames(service, root):
    client = VoiceClient(root)
    await claim(client)
    assert (await client.call("status"))["state"] == "starting"
    service.recorder.events.put({"type": "dead_stream", "detail": "permission denied"})
    await until(lambda: service.recorder is None)
    assert (await client.call("status"))["state"] == "error"
    assert Recorder.active == 0


@pytest.mark.asyncio
async def test_disk_failure_is_visible_and_closes_microphone(
    service, root, monkeypatch
):
    await claim(VoiceClient(root))

    def fail(_):
        raise OSError("disk full")

    monkeypatch.setattr(service.transcript, "append", fail)
    service.submit(
        AudioSegment(np.ones(16000, np.float32), time.time() - 1, time.time())
    )
    await service.segments.join()
    assert service.status()["state"] == "error"
    assert "disk full" in service.status()["error"]
    assert Recorder.active == 0


@pytest.mark.asyncio
async def test_finishing_setup_after_release_never_opens_mic(root):
    gate = threading.Event()
    Recorder.starts = 0

    def assets(*args):
        gate.wait(2)
        return {}

    service = VoiceService(
        root,
        load_assets=assets,
        load_models=lambda _: Models(),
        recorder_factory=Recorder,
        player=lambda *args: True,
    )
    await service.start()
    client = VoiceClient(root)
    lease = await claim(client)
    await client.call("release", **auth(lease))
    gate.set()
    await until(lambda: service.setup["state"] == "ready")
    assert Recorder.starts == 0
    await service.close()


def test_continuous_speech_cap_and_no_duplicate_preroll():
    segmenter = SpeechSegmenter(lambda _: True)
    samples = np.arange(16000 * 17, dtype=np.float32)
    results = segmenter.feed(samples, 1000)
    assert [len(r.audio) for r in results] == [128000, 128000]
    results.append(segmenter.flush())
    assert np.array_equal(np.concatenate([r.audio for r in results]), samples)
    assert results[0].started_at == 1000 and results[1].started_at == 1008
    assert results[-1].ended_at == 1017


def test_transcript_cursor_torn_tail_and_rotation(root, monkeypatch):
    log = TranscriptLog(root, capacity=2)
    log.path.write_text('{"torn":')
    r = log.append({"text": "one", "owner_epoch": "e", "final": True})
    assert json.loads(log.path.read_text())["event_id"] == r["event_id"]
    assert list(log.directory.glob("*.torn-*"))
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 86400)
    log.append({"text": "two", "owner_epoch": "e", "final": True})
    assert len(list(log.directory.glob("*.jsonl"))) == 2
    assert len(log.read(log.stream_id, 0, "e")["records"]) == 2
    log.append({"text": "three", "owner_epoch": "e", "final": True})
    with pytest.raises(VoiceError, match="fell behind"):
        log.read(log.stream_id, 0, "e")


def test_admission_crash_and_duplicate_never_blindly_replay(root):
    store = AdmissionStore(root / "admit.db")
    assert store.reserve("id", "test") is None
    store.close()
    store = AdmissionStore(root / "admit.db")
    assert store.reserve("id", "test")["status"] == "delivery_unknown"
    store.finish("id", "queued")
    assert store.reserve("id", "test")["status"] == "queued"
    with pytest.raises(VoiceError):
        store.reserve("id", "changed")
    store.close()


def test_decision_cannot_invent_events_or_execute_invalid_output():
    records = [{"event_id": "a", "text": "turn the page"}]
    assert parse_decision('{"decision":"ignore","event_ids":["a"]}', records) == []
    assert (
        parse_decision('{"decision":"respond","event_ids":["a"]}', records) == records
    )
    for raw in (
        "not json",
        '{"decision":"respond","event_ids":["missing"]}',
        '{"decision":"respond","event_ids":[]}',
    ):
        with pytest.raises(VoiceError):
            parse_decision(raw, records)


@pytest.mark.asyncio
async def test_retry_clears_failed_setup_before_reporting_progress(root):
    attempts = 0

    def assets(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("offline")
        return {}

    server = VoiceService(root, load_assets=assets, load_models=lambda _: Models())
    await server.start()
    try:
        await until(lambda: server.setup["state"] == "error")
        result = await VoiceClient(root).call("retry")
        assert result["state"] != "error" and not result["error"]
        await until(lambda: server.setup["state"] == "ready")
        assert server.recorder is None and attempts == 2
    finally:
        await server.close()


def test_receipt_crash_tail_is_preserved_and_interrupted_speech_not_replayed(root):
    q = SpeechQueue(root, lambda _: None, lambda *_: True)
    q.admit("First sentence.", "r", "epoch", "p")
    with q.receipt_path.open("ab") as f:
        f.write(b'{"torn":')
    recovered = SpeechQueue(root, lambda _: None, lambda *_: True)
    receipts = [
        json.loads(line) for line in recovered.receipt_path.read_text().splitlines()
    ]
    assert receipts[-1]["state"] == "cancelled"
    assert receipts[-1]["reason"] == "service_restarted"
    assert list(root.glob("speech.jsonl.torn-*"))
    assert recovered.queue.empty()


def test_sentence_split_preserves_abbreviations_decimals_and_final_fragment():
    assert sentences("Dr. Jones has 3.5 tests. Another sentence! Final fragment") == [
        "Dr. Jones has 3.5 tests.",
        "Another sentence!",
        "Final fragment",
    ]


@pytest.mark.asyncio
async def test_output_all_sentences_and_idempotent_reply(root):
    spoken = []
    q = SpeechQueue(
        root,
        lambda text: (text, 1),
        lambda text, rate, event: spoken.append(text) or True,
    )
    result = q.admit(
        "First sentence. Second sentence. Final", "reply", "epoch", "producer"
    )
    assert len(result["ids"]) == 3
    assert q.admit(
        "First sentence. Second sentence. Final", "reply", "epoch", "producer"
    )["duplicate"]
    worker = asyncio.create_task(q.run())
    await q.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert spoken == ["First sentence.", "Second sentence.", "Final"]
    assert all(item["receipt"]["state"] == "played" for item in q.jobs.values())


@pytest.mark.asyncio
async def test_cancel_during_synthesis_fences_late_result_and_keeps_later_reply(root):
    started, finish = threading.Event(), threading.Event()
    played = []

    def synth(text):
        if text == "Cancel me.":
            started.set()
            finish.wait(2)
        return text, 1

    q = SpeechQueue(root, synth, lambda text, *_: played.append(text) or True)
    q.admit("Cancel me.", "old", "epoch", "one")
    q.admit("Keep me.", "new", "epoch", "two")
    worker = asyncio.create_task(q.run())
    await until(started.is_set)
    q.cancel("epoch", "old")
    finish.set()
    await q.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert played == ["Keep me."]
    events = [json.loads(line) for line in q.receipt_path.read_text().splitlines()]
    assert not any(e["reply_id"] == "old" and e["state"] == "played" for e in events)
    assert any(e["reply_id"] == "old" and e["state"] == "cancelled" for e in events)


@pytest.mark.asyncio
async def test_cancel_playback_and_queue_overload(root):
    started = threading.Event()

    def play(samples, rate, cancelled):
        started.set()
        cancelled.wait(2)
        return not cancelled.is_set()

    q = SpeechQueue(root, lambda _: ([], 1), play, capacity=1)
    q.admit("First.", "one", "epoch", "one")
    with pytest.raises(VoiceError, match="full"):
        q.admit("Second.", "two", "epoch", "two")
    worker = asyncio.create_task(q.run())
    await until(started.is_set)
    q.cancel("epoch")
    await q.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert next(iter(q.jobs.values()))["receipt"]["state"] == "cancelled"


@pytest.mark.asyncio
async def test_explicit_and_final_speech_deduplicate_only_matching_sentences(root):
    spoken = []
    q = SpeechQueue(
        root, lambda text: (text, 1), lambda text, *_: spoken.append(text) or True
    )
    q.admit("First sentence.", "reply", "epoch", "one")
    result = q.admit("First sentence. The final result.", "reply", "epoch", "one")
    assert not result["duplicate"] and q.queue.qsize() == 2
    worker = asyncio.create_task(q.run())
    await q.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert spoken == ["First sentence.", "The final result."]


def test_grouping_preserves_echo_for_classifier_without_merging_it_into_request():
    from kollabor_voice.observer import connected_speech, utterance_groups

    request = {
        "event_id": "request",
        "text": "Please check this.",
        "owner_epoch": "owner",
        "started_at": "2026-09-26T00:00:00+00:00",
        "ended_at": "2026-09-26T00:00:01+00:00",
    }
    echo = {
        **request,
        "event_id": "echo",
        "text": "I'll check the files.",
        "started_at": "2026-09-26T00:00:02+00:00",
        "ended_at": "2026-09-26T00:00:03+00:00",
        "playback_overlap": [{"reply_id": "reply", "text": "I'll check the files."}],
    }
    assert utterance_groups([request, echo]) == [[request], [echo]]
    assert connected_speech([request, echo], [request]) == [request]
    assert connected_speech([request, echo], [echo]) == [echo]


@pytest.mark.asyncio
async def test_silent_period_never_reaches_synthesizer_or_speech_receipts(root):
    spoken = []
    queue = SpeechQueue(
        root, lambda text: (text, 1), lambda text, *_: spoken.append(text) or True
    )
    assert sentences(" \n.\n ") == []
    assert queue.admit(".", "silence", "epoch", "agent")["silent"]
    assert not queue.jobs and not queue.receipt_path.exists()
    queue.admit("I can help.", "answer", "epoch", "agent")
    worker = asyncio.create_task(queue.run())
    await queue.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert spoken == ["I can help."]


@pytest.mark.asyncio
async def test_delegated_output_cannot_open_mic_read_or_extend_lease(service, root):
    from unittest.mock import AsyncMock

    from kollabor_voice.speech_review import SpeechDecision

    service.classifier.review_speech = AsyncMock(
        return_value=SpeechDecision("speak", provider="laya").to_wire()
    )
    client = VoiceClient(root)
    owner = await claim(client)
    capability = await client.call("delegate", **auth(owner), producer_id="worker")
    for operation in ("read", "heartbeat", "release"):
        with pytest.raises(VoiceError, match="cannot control"):
            await client.call(
                operation, token=capability["token"], epoch=capability["epoch"]
            )
    result = await client.voice_out(
        "A worker update.",
        "worker-reply",
        capability["epoch"],
        "forged",
        capability["token"],
    )
    assert result["ids"]
    assert next(iter(service.output.jobs.values()))["producer_id"] == "worker"
    await client.call("release", **auth(owner))
    with pytest.raises(VoiceError):
        await client.voice_out(
            "Old capability.", "old", capability["epoch"], "worker", capability["token"]
        )


def test_retention_only_prunes_owned_closed_files(root):
    log = TranscriptLog(root)
    old = log.directory / "2020-01-01.jsonl"
    foreign = log.directory / "2019-01-01.jsonl"
    old.write_text("owned")
    foreign.write_text("legacy")
    log.owned.add(old.name)
    log.prune()
    assert not old.exists() and foreign.exists() and log.path.exists()


def test_download_resume_and_checksum_before_publish(root, monkeypatch):
    import hashlib
    import io

    import kollabor_voice.assets as assets
    from kollabor_voice.assets import download

    content = b"verified model bytes"
    item = {
        "name": "model.bin",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    target = root / "model.bin"
    target.with_suffix(".bin.part").write_bytes(content[:5])

    class Response(io.BytesIO):
        status = 206
        headers = {"Content-Range": f"bytes 5-{len(content)-1}/{len(content)}"}

    def fetch(request, **kwargs):
        assert request.get_header("Range") == "bytes=5-"
        return Response(content[5:])

    monkeypatch.setattr(assets.urllib.request, "urlopen", fetch)
    download("https://test.invalid/model", target, item, lambda *args: None)
    assert (
        target.read_bytes() == content and not target.with_suffix(".bin.part").exists()
    )

    class Corrupt(io.BytesIO):
        status = 200
        headers = {}

    monkeypatch.setattr(
        assets.urllib.request, "urlopen", lambda *a, **k: Corrupt(b"x" * len(content))
    )
    with pytest.raises(RuntimeError, match="Checksum"):
        download("https://test.invalid/model", target, item, lambda *args: None)
    assert target.read_bytes() == content


def test_verified_cache_works_without_network(root, monkeypatch):
    import hashlib

    import kollabor_voice.assets as assets

    content = b"cached model"
    bundle = {
        "revision": "immutable",
        "base_url": "https://test.invalid/",
        "files": [
            {
                "name": "test.bin",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    monkeypatch.setattr(assets, "manifest", lambda: {"bundles": {"test": bundle}})
    path = root / "models/test/immutable"
    path.mkdir(parents=True)
    (path / "test.bin").write_bytes(content)

    def offline(*args, **kwargs):
        raise AssertionError("Warm cache must not access the network")

    monkeypatch.setattr(assets.urllib.request, "urlopen", offline)
    assert assets.ensure_models(root, lambda **kwargs: None)["test"] == path


@pytest.mark.asyncio
async def test_withdrawn_activation_cannot_open_mic_even_if_claim_arrives_late(
    service, root
):
    client = VoiceClient(root)
    await client.call("withdraw", client_id="one", claim_id="late")
    with pytest.raises(VoiceError, match="cancelled"):
        await client.call(
            "claim", client_id="one", claim_id="late", activated_at=time.time()
        )
    assert Recorder.active == 0


@pytest.mark.asyncio
async def test_withdraw_releases_owner_when_claim_reply_was_lost(service, root):
    client = VoiceClient(root)
    await claim(client, "lost-ack")
    await client.call("withdraw", client_id="lost-ack", claim_id="lost-ack")
    assert service.owner is None and Recorder.active == 0


def test_low_confidence_repetitions_are_not_published_as_speech():
    from types import SimpleNamespace

    from kollabor_voice.audio import LocalModels

    models = object.__new__(LocalModels)
    good = SimpleNamespace(
        text=" Check the tests.",
        avg_logprob=-0.2,
        no_speech_prob=0.01,
        compression_ratio=1.1,
    )
    hallucination = SimpleNamespace(
        text="I don't know. " * 12,
        avg_logprob=-1.4,
        no_speech_prob=0.8,
        compression_ratio=3.1,
    )
    models.whisper = SimpleNamespace(
        transcribe=lambda *args, **kwargs: (
            [good, hallucination],
            SimpleNamespace(language="en"),
        )
    )
    assert models.transcribe(np.zeros(16000, np.float32)) == ("Check the tests.", "en")


def test_audio_warmup_consumes_lazy_decode_without_playing_or_recording():
    from types import SimpleNamespace

    from kollabor_voice.audio import LocalModels

    calls = []

    def transcribe(audio, **kwargs):
        assert audio.dtype == np.float32 and len(audio) == 16000
        assert not kwargs["vad_filter"]

        def segments():
            calls.append("decoded")
            yield SimpleNamespace(text="Voice is ready.")

        return segments(), None

    models = object.__new__(LocalModels)
    models.whisper = SimpleNamespace(transcribe=transcribe)
    models.synthesize = lambda text: (
        calls.append(text) or np.zeros(24000, np.float32),
        24000,
    )
    models.warmup()
    assert calls == ["Voice is ready.", "decoded"]


@pytest.mark.asyncio
async def test_microphone_waits_for_silent_audio_warmup(root):
    entered, release = threading.Event(), threading.Event()

    class WarmingModels(Models):
        def warmup(self):
            entered.set()
            assert release.wait(3)

    Recorder.active = Recorder.starts = 0
    server = VoiceService(
        root,
        load_assets=lambda *args: {},
        load_models=lambda _: WarmingModels(),
        recorder_factory=Recorder,
        segmenter_factory=lambda: SpeechSegmenter(lambda _: True),
        player=lambda *args: pytest.fail("Warm-up must never play audio"),
    )
    await server.start()
    try:
        await until(entered.is_set)
        client = VoiceClient(root)
        await claim(client)
        status = await client.call("status")
        assert status["state"] == "warming" and not status["ready"]
        assert Recorder.starts == 0
        release.set()
        await until(lambda: server.setup["state"] == "ready")
        assert Recorder.starts == 1
        assert server.audio_warmup_seconds is not None
        assert not server.transcript.read(
            stream_id=server.instance_id, after=0, epoch=server.owner["epoch"]
        )["records"]
    finally:
        release.set()
        await server.close()


@pytest.mark.asyncio
async def test_local_classifier_jobs_do_not_block_capture_or_allow_output_delegates(
    service, root
):
    from kollabor_voice.classifiers import VoiceDecision

    client = VoiceClient(root)
    owner = await claim(client)
    auth = {"token": owner["token"], "epoch": owner["epoch"]}
    started, finish = asyncio.Event(), asyncio.Event()

    async def decide(records, context, transcript_context, context_lines):
        assert transcript_context == [] and context_lines == 10
        started.set()
        await finish.wait()
        return VoiceDecision(
            "respond", [r["event_id"] for r in records], "laya"
        ).to_wire()

    service.classifier.decide = decide
    ticket = await client.call(
        "classifier_submit", **auth, records=[{"event_id": "a"}], context=""
    )
    await started.wait()
    status = await client.call("heartbeat", **auth)
    assert status["owner"] == service.owner["client_id"]
    assert not (
        await client.call("classifier_result", **auth, job_id=ticket["job_id"])
    )["done"]
    delegate = await client.call("delegate", **auth, producer_id="other")
    with pytest.raises(VoiceError, match="Delegated"):
        await client.call(
            "classifier_submit",
            token=delegate["token"],
            epoch=auth["epoch"],
            records=[],
        )
    with pytest.raises(VoiceError, match="already pending"):
        await client.call("classifier_submit", **auth, records=[])
    finish.set()
    await until(lambda: service.classifier_jobs[ticket["job_id"]].done())
    result = await client.call("classifier_result", **auth, job_id=ticket["job_id"])
    assert result["result"]["event_ids"] == ["a"]
    await client.call("release", **auth)
    assert service.classifier_jobs == {}
