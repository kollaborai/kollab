"""One sentence FIFO with durable receipts and cancellation fences."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from collections import deque
from pathlib import Path

from .control import VoiceError
from .store import repair_tail


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
    def __init__(self, root: Path, synthesize, play, capacity=64):
        self.synthesize, self.play = synthesize, play
        self.capacity = capacity
        self.queue = asyncio.Queue(maxsize=capacity)
        self.jobs = {}
        self.replies = {}
        self.active = None
        self.intervals = deque(maxlen=128)
        self.error = None
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
        if len(candidates) + self.queue.qsize() + bool(self.active) > self.capacity:
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
        while True:
            item = await self.queue.get()
            self.active = item
            interval = None
            try:
                if item["cancelled"].is_set():
                    continue
                self._state(item, "synthesizing")
                samples, rate = await asyncio.to_thread(
                    self.synthesize, item["sentence"]
                )
                if item["cancelled"].is_set():
                    self._state(item, "cancelled")
                    continue
                self._state(item, "playing")
                interval = {
                    "reply_id": item["reply_id"],
                    "text": item["sentence"],
                    "started_at": time.time(),
                    "ended_at": None,
                }
                self.intervals.append(interval)
                played = await asyncio.to_thread(
                    self.play, samples, rate, item["cancelled"]
                )
                self._state(
                    item,
                    (
                        "played"
                        if played and not item["cancelled"].is_set()
                        else "cancelled"
                    ),
                )
            except Exception as exc:
                self.error = str(exc)
                self._state(item, "failed", error=str(exc))
            finally:
                if interval:
                    interval["ended_at"] = time.time()
                self.active = None
                self.queue.task_done()

    def overlap(self, start, end):
        return [
            dict(i)
            for i in self.intervals
            if i["started_at"] <= end and (i["ended_at"] or time.time()) + 1.0 >= start
        ]

    def status(self):
        return {
            "state": self.active["receipt"]["state"] if self.active else "idle",
            "depth": self.queue.qsize(),
            "error": self.error,
        }
