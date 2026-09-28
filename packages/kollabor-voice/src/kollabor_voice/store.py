"""Durable device log. Consumers can only see records after fsync succeeds."""

from __future__ import annotations

import json
import os
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .control import VoiceError, atomic_json


def utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(
        timespec="milliseconds"
    )


def repair_tail(path: Path) -> None:
    """Preserve incomplete crash bytes before the next JSONL append."""
    if not path.exists() or not path.stat().st_size:
        return
    with path.open("rb+") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return
        handle.seek(0)
        content = handle.read()
        end = content.rfind(b"\n") + 1
        tail = path.with_name(path.name + f".torn-{uuid.uuid4().hex}")
        with tail.open("wb") as saved:
            os.chmod(tail, 0o600)
            saved.write(content[end:])
            saved.flush()
            os.fsync(saved.fileno())
        handle.truncate(end)
        handle.flush()
        os.fsync(handle.fileno())


class TranscriptLog:
    def __init__(self, root: Path, stream_id: str | None = None, capacity=512):
        self.directory = root / "transcripts"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stream_id = stream_id or str(uuid.uuid4())
        self.seq = 0
        self.recent = deque(maxlen=capacity)
        self.path = self.directory / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
        self.ownership_path = self.directory / ".owned.json"
        try:
            self.owned = set(json.loads(self.ownership_path.read_text())["files"])
        except (OSError, ValueError, KeyError):
            self.owned = set()
        self.probe()
        self.prune()

    def probe(self):
        # An actual open+flush, not just os.access (which misses read-only disks).
        with self.path.open("a") as handle:
            os.chmod(self.path, 0o600)
            handle.flush()
            os.fsync(handle.fileno())
        self._remember()

    def _remember(self):
        if self.path.name not in self.owned:
            self.owned.add(self.path.name)
            atomic_json(self.ownership_path, {"files": sorted(self.owned)})

    def prune(self, days=30):
        """Only closed, explicitly owned daily logs are eligible for retention."""
        import re

        cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
        for name in list(self.owned):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", name):
                continue
            path = self.directory / name
            try:
                day = datetime.strptime(name[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if path != self.path and day < cutoff:
                path.unlink(missing_ok=True)
                self.owned.remove(name)
        atomic_json(self.ownership_path, {"files": sorted(self.owned)})

    def append(self, record: dict) -> dict:
        import time

        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "stream_id": self.stream_id,
            "seq": self.seq + 1,
            **record,
        }
        previous_path = self.path
        self.path = (
            self.directory
            / f"{datetime.fromtimestamp(time.time(), timezone.utc):%Y-%m-%d}.jsonl"
        )
        # One service writer; serialize callers on its event loop. Each record is
        # one complete JSON line. Repair only an incomplete tail after a crash.
        repair_tail(self.path)
        with self.path.open("a") as handle:
            os.chmod(self.path, 0o600)
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.seq = event["seq"]
        self.recent.append(event)
        self._remember()
        if previous_path != self.path:
            self.prune()
        return event

    def read(self, stream_id: str, after: int, epoch: str) -> dict:
        if stream_id != self.stream_id or after > self.seq or after < 0:
            raise VoiceError(
                "cursor_expired",
                "Voice service restarted; enable voice again to start at the current transcript",
            )
        if self.recent and after < self.recent[0]["seq"] - 1:
            raise VoiceError(
                "cursor_expired",
                "Transcript consumer fell behind; earlier speech remains in the transcript file",
            )
        records = [
            r for r in self.recent if r["seq"] > after and r.get("owner_epoch") == epoch
        ][:8]
        return {
            "records": records,
            "cursor": records[-1]["seq"] if len(records) == 8 else self.seq,
            "stream_id": self.stream_id,
        }
