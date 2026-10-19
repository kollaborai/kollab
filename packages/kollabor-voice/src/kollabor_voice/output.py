"""Sentence FIFO with durable receipts and cancellation fences.

Synthesis runs ahead of playback: while one sentence plays, the next is
already being generated, and each sentence plays from its first chunk.
Voice's own fixed lines replay from speech_cache instead of being synthesized.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path

from .control import VoiceError
from .speech_cache import SpeechCache
from .store import repair_tail

# Audio held back beyond the computed lead, for synthesis jitter.
LEAD_MARGIN_S = 0.3


def silent_response(text: str) -> bool:
    return text.strip() == "."


def sentences(text: str) -> list[str]:
    if silent_response(text):
        return []
    # Protect common abbreviations/initials and decimal points before splitting.
    text = re.sub(r"```.*?(?:```|$)", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*`#]", "", text).strip()
    text = re.sub(
        r"(?i)\b(?:mr|mrs|ms|dr|prof|sr|jr|vs|etc|e\.g|i\.e)\.|(?<=\d)\.(?=\d)|\b[A-Z]\.(?=\s?[A-Z]\.)",
        lambda m: m[0].replace(".", "\x00"),
        text,
    )
    return [
        part.replace("\x00", ".").strip()
        for part in re.split(r'(?<=[.!?])\s+(?=[\w"“])', text)
        if part.strip()
    ]


class SpeechQueue:
    def __init__(self, root: Path, stream, player, capacity=64):
        # stream(text) yields (samples, rate) chunks; player writes them to the
        # device (write/finish/abort, audio.StreamPlayer).
        self.stream, self.player = stream, player
        self.capacity = capacity
        self.queue = asyncio.Queue(maxsize=capacity)
        # Sentences handed from synthesis to playback, at most two ahead.
        self.ready = asyncio.Queue(maxsize=2)
        self.jobs = {}
        self.replies = {}
        self.active = None
        self.intervals = deque(maxlen=128)
        self.error = None
        # Learned from each sentence: seconds of speech per word, and synthesis
        # seconds per second of speech (above 1 is slower than real time).
        self.word_s, self.rtf = 0.4, 1.25
        self.run_ids, self.last_interval = set(), None  # ids the current lead covers
        self.cache = SpeechCache(root)
        self.receipt_path = root / "speech.jsonl"
        # Crashed speech is never replayed. Finalize its durable receipt instead.
        if self.receipt_path.exists():
            repair_tail(self.receipt_path)
            latest = {}
            with self.receipt_path.open() as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                        if value["state"] in {"played", "failed", "cancelled"}:
                            latest.pop(value["id"], None)
                        else:
                            latest[value["id"]] = value
                    except (ValueError, KeyError):
                        continue
            for value in latest.values():
                if value["state"] not in {"played", "failed", "cancelled"}:
                    self._write(
                        {
                            **value,
                            "state": "cancelled",
                            "reason": "service_restarted",
                            "at": time.time(),
                        }
                    )

    def _write(self, receipt):
        import os

        with self.receipt_path.open("a") as handle:
            os.chmod(self.receipt_path, 0o600)
            handle.write(json.dumps(receipt) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _state(self, item, state, **extra):
        receipt = {
            "id": item["id"],
            "reply_id": item["reply_id"],
            "owner_epoch": item["owner_epoch"],
            "producer_id": item["producer_id"],
            "sentence": item["sentence"],
            "state": state,
            "at": time.time(),
            **extra,
        }
        self._write(receipt)
        item["receipt"] = receipt

    def admit(self, text, reply_id, owner_epoch, producer_id):
        if silent_response(text):
            return {"ids": [], "duplicate": False, "silent": True}
        key = (owner_epoch, reply_id)
        prior = self.replies.get(key, {})
        parts = sentences(text)
        if not parts:
            raise VoiceError("empty", "No speakable text")
        counts, candidates, ids = {}, [], []
        for sentence in parts:
            normalized = " ".join(sentence.casefold().split())
            counts[normalized] = counts.get(normalized, 0) + 1
            occurrence = (normalized, counts[normalized])
            job_id = prior.get(occurrence)
            if job_id is None:
                job_id = hashlib.sha256(
                    f"{owner_epoch}:{reply_id}:{occurrence!r}".encode()
                ).hexdigest()[:32]
                candidates.append(
                    (
                        occurrence,
                        dict(
                            id=job_id,
                            reply_id=reply_id,
                            owner_epoch=owner_epoch,
                            producer_id=producer_id,
                            sentence=sentence,
                            cancelled=threading.Event(),
                        ),
                    )
                )
            ids.append(job_id)
        waiting = self.queue.qsize() + self.ready.qsize() + bool(self.active)
        if len(candidates) + waiting > self.capacity:
            raise VoiceError(
                "queue_full", "Voice output queue is full; the reply was not admitted"
            )
        if len(self.jobs) + len(candidates) > 4096:
            raise VoiceError(
                "session_full", "Voice receipt limit reached; turn voice off and on"
            )
        # No waveform can be scheduled until every candidate's receipt persists.
        # An interrupted write leaves recoverable journal entries, never partial
        # playback of an admission that was rejected to its caller.
        for _, item in candidates:
            self._state(item, "queued")
        admitted = self.replies.setdefault(key, {})
        for occurrence, item in candidates:
            admitted[occurrence] = item["id"]
            self.jobs[item["id"]] = item
            self.queue.put_nowait(item)
        return {"ids": ids, "duplicate": not candidates}

    def cancel(self, epoch, reply_id=None, forget=False):
        for item in self.jobs.values():
            if item["owner_epoch"] == epoch and (
                reply_id is None or item["reply_id"] == reply_id
            ):
                if item["receipt"]["state"] not in {"played", "failed", "cancelled"}:
                    item["cancelled"].set()
                    # Playing changes to cancelled only once the device stops.
                    if item is not self.active:
                        self._state(item, "cancelled")

        if forget:
            self.jobs = {
                key: item
                for key, item in self.jobs.items()
                if item["owner_epoch"] != epoch
            }
            self.replies = {
                key: value for key, value in self.replies.items() if key[0] != epoch
            }

    async def run(self):
        producer = asyncio.create_task(self._produce(), name="voice-synthesis")
        try:
            await self._consume()
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def _produce(self):
        while True:
            item = await self.queue.get()
            chunks = queue.Queue()  # (samples, rate) chunks, then None or the error
            await self.ready.put((item, chunks))
            if item["cancelled"].is_set():
                chunks.put(None)
                continue
            self._state(item, "synthesizing")
            await asyncio.to_thread(self._synthesize, item, chunks)

    def _synthesize(self, item, chunks):
        key = self.cache.key(item["producer_id"], item["sentence"])
        cached = key and self.cache.load(key)
        if cached:
            # One stored chunk and no synthesis, so rtf and word_s stay as learned.
            chunks.put(cached)
            chunks.put(None)
            return
        kept = []
        audio = iter(self.stream(item["sentence"]))
        started, produced = time.monotonic(), 0.0
        try:
            for chunk in audio:
                if item["cancelled"].is_set():
                    break
                chunks.put(chunk)
                if key:
                    kept.append(chunk)
                produced += len(chunk[0]) / chunk[1]
            else:
                if produced:
                    words = max(1, len(item["sentence"].split()))
                    elapsed = time.monotonic() - started
                    self.rtf = 0.7 * self.rtf + 0.3 * elapsed / produced
                    self.word_s = 0.7 * self.word_s + 0.3 * produced / words
                    if key:  # whole sentences only, never a cancelled part
                        self.cache.store(key, kept)
            chunks.put(None)
        except Exception as exc:
            chunks.put(exc)
        finally:
            getattr(audio, "close", lambda: None)()

    async def _consume(self):
        while True:
            if self.ready.empty() and self.queue.empty():
                # Nothing follows: play out the tail and release the device.
                await asyncio.to_thread(self.player.finish)
                self._close_run()
            item, chunks = await self.ready.get()
            self.active = item
            interval = None
            try:
                first = await asyncio.to_thread(chunks.get)
                if isinstance(first, Exception):
                    raise first
                if first is None or item["cancelled"].is_set():
                    continue
                if item["id"] not in self.run_ids:
                    await self._start_run(item)
                self._state(item, "playing")
                interval = {
                    "reply_id": item["reply_id"],
                    "text": item["sentence"],
                    "started_at": time.time(),
                    "ended_at": None,
                }
                self.intervals.append(interval)
                self.last_interval = interval
                played = await asyncio.to_thread(self._play, item, first, chunks)
                if played and not item["cancelled"].is_set():
                    self._state(item, "played")
            except Exception as exc:
                self.error = str(exc)
                self._state(item, "failed", error=str(exc))
                await asyncio.to_thread(self.player.abort)
            finally:
                if item["cancelled"].is_set() and item["receipt"]["state"] not in {
                    "cancelled",
                    "failed",
                }:
                    # Playing changes to cancelled only once the device stops.
                    self._state(item, "cancelled")
                if interval:
                    interval["ended_at"] = time.time()
                self.active = None
                self.queue.task_done()

    async def _start_run(self, item):
        """Hold back enough audio that every sentence known now plays unbroken.

        Synthesis slower than real time (rtf > 1) runs the device dry mid-word
        unless T seconds of speech start with T * (rtf - 1) / rtf buffered.
        """
        if self.run_ids:
            if self.rtf <= 1:
                self.run_ids.add(item["id"])  # synthesis keeps ahead: play straight on
                return
            # Admitted after this run's lead was sized: pause here, between
            # sentences, rather than run dry mid-word later.
            await asyncio.to_thread(self.player.finish)
            self._close_run()
        pending = [
            job
            for job in self.jobs.values()
            if job["receipt"]["state"] in {"queued", "synthesizing"}
        ]
        self.run_ids = {job["id"] for job in pending}
        seconds = self.word_s * sum(len(job["sentence"].split()) for job in pending)
        self.player.lead = seconds * max(0.0, self.rtf - 1) / self.rtf + LEAD_MARGIN_S

    def _close_run(self):
        # finish() returns once the device drained: the run's sound ends now,
        # later than its last chunk was handed over (held lead, device buffer).
        if self.last_interval:
            self.last_interval["ended_at"] = time.time()
        self.run_ids, self.last_interval = set(), None

    def _play(self, item, chunk, chunks):
        """Write each chunk as it arrives; False once the sentence is cancelled."""
        while chunk is not None:
            if isinstance(chunk, Exception):
                raise chunk
            samples, rate = chunk
            if not self.player.write(samples, rate, item["cancelled"]):
                return False
            chunk = chunks.get()
        return True

    def overlap(self, start, end):
        return [
            dict(i)
            for i in self.intervals
            if i["started_at"] <= end and (i["ended_at"] or time.time()) + 1.0 >= start
        ]

    def status(self):
        return {
            "state": self.active["receipt"]["state"] if self.active else "idle",
            "depth": self.queue.qsize() + self.ready.qsize(),
            "error": self.error,
        }
