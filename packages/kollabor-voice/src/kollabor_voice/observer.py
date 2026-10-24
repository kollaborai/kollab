"""Utterance grouping, playback-echo handling and durable admission of microphone speech."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .control import VoiceError


def admission_id(conversation: str, records: list[dict]) -> str:
    key = conversation + ":" + ":".join(r["event_id"] for r in records)
    return hashlib.sha256(key.encode()).hexdigest()


def playback_text(records):
    return list(
        dict.fromkeys(
            item["text"]
            for record in records
            for item in record.get("playback_overlap") or []
            if item.get("text")
        )
    )


def _words(text):
    return re.findall(r"\w+", re.sub(r"['‘’`]", "", text.lower()))


def is_playback_echo(record):
    """The microphone heard this agent's own voice, not the human."""
    sentences = [
        _words(i["text"]) for i in record.get("playback_overlap") or [] if i.get("text")
    ]
    heard = _words(record.get("text") or "")
    if not heard or not sentences:
        return False
    if len(heard) < 3:
        # "stop" over a reply that says "stop" is a barge-in; only a whole sentence is echo
        return heard in sentences
    spoken = [w for sentence in sentences for w in sentence]
    blocks = SequenceMatcher(None, heard, spoken, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks) / len(heard) >= 0.8



def strip_playback(record):
    """The human's own words in a line that also caught this agent's playback.

    Talking over voice merges both into one line ("Bear with me still looking
    into- Hello, can you hear me?"). Runs of three or more words matching what
    voice was saying are removed, so only the human's words are judged.
    """
    text = record.get("text") or ""
    spoken = [
        w
        for item in record.get("playback_overlap") or []
        if item.get("text")
        for w in _words(item["text"])
    ]
    tokens = text.split()
    heard, owner = [], []
    for index, token in enumerate(tokens):
        for word in _words(token):
            heard.append(word)
            owner.append(index)
    if not spoken or not heard:
        return text
    drop = set()
    for block in SequenceMatcher(None, heard, spoken, autojunk=False).get_matching_blocks():
        if block.size >= 3:
            drop.update(owner[block.a : block.a + block.size])
    if not drop:
        return text
    return " ".join(t for i, t in enumerate(tokens) if i not in drop).strip(" -,;:")

def utterance_groups(records):
    """Storage boundaries reconnect; playback boundaries remain distinguishable."""

    def playback_ids(record):
        return {item.get("reply_id") for item in record.get("playback_overlap") or []}

    groups, group = [], []
    for record in records:
        if group:
            gap = (
                datetime.fromisoformat(record["started_at"])
                - datetime.fromisoformat(group[-1]["ended_at"])
            ).total_seconds()
            if (
                gap > 2
                or record.get("owner_epoch") != group[-1].get("owner_epoch")
                or playback_ids(record) != playback_ids(group[-1])
            ):
                groups.append(group)
                group = []
        group.append(record)
    if group:
        groups.append(group)
    return groups


class AdmissionStore:
    """Reserve before enqueue, acknowledge only after commit. Never blind-replay."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS admissions (id TEXT PRIMARY KEY, digest TEXT NOT NULL, "
            "state TEXT NOT NULL, detail TEXT, created REAL NOT NULL)"
        )
        self.db.commit()
        import os

        os.chmod(path, 0o600)

    def reserve(self, identity: str, text: str):
        digest = hashlib.sha256(text.encode()).hexdigest()
        row = self.db.execute(
            "SELECT digest,state,detail FROM admissions WHERE id=?", (identity,)
        ).fetchone()
        if row:
            if row[0] != digest:
                raise VoiceError(
                    "identity_conflict",
                    "Voice admission ID was reused with different text",
                )
            return {
                "status": row[1],
                "detail": row[2],
                "duplicate": True,
                "admission_id": identity,
            }
        with self.db:
            self.db.execute(
                "INSERT INTO admissions VALUES (?,?, 'delivery_unknown',NULL,?)",
                (identity, digest, time.time()),
            )
        return None

    def finish(self, identity, state, detail=None):
        with self.db:
            self.db.execute(
                "UPDATE admissions SET state=?,detail=? WHERE id=?",
                (state, detail, identity),
            )

    def lookup(self, identity):
        row = self.db.execute(
            "SELECT state,detail FROM admissions WHERE id=?", (identity,)
        ).fetchone()
        return (
            {"status": row[0], "detail": row[1], "admission_id": identity}
            if row
            else None
        )

    def close(self):
        self.db.close()
