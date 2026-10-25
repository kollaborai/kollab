"""Audio of the lines voice says itself, kept on disk so they play at once.

Still-working lines come from a fixed set (acks.LINES). Each is synthesized
once per voice, then replayed from root/cache/speech without the speech
engine. Agent replies are one-off text and never kept.
"""

from __future__ import annotations

import array
import contextlib
import hashlib
import json
import logging
import math
import os
import struct
import tempfile
import time
import zlib
from pathlib import Path

from .control import manifest
from .speech_settings import load_speech_preferences, speech_key

log = logging.getLogger(__name__)

# The producers that only speak acks.LINES; any other producer is agent text.
PRODUCERS = frozenset({"voice-progress"})
KEEP_S = 3 * 24 * 3600  # since the file last played
# A file is this header, then the sentence's float32 samples.
HEADER = struct.Struct("<4sII")  # magic, sample rate, crc32 of the samples
MAGIC = b"KVS1"


class SpeechCache:
    def __init__(self, root: Path):
        self.root, self.dir = root, root / "cache" / "speech"
        self.prune()

    def key(self, producer_id, sentence):
        """The sentence and all that selects its voice, hashed; None if not kept."""
        if producer_id not in PRODUCERS:
            return None
        try:
            voice = dict(speech_key(load_speech_preferences(self.root)))
            if voice.get("vibevoice_prompt"):
                # A custom voice can be recorded again under the same file name.
                stat = Path(voice["vibevoice_prompt"]).expanduser().stat()
                voice["prompt_file"] = [stat.st_size, stat.st_mtime_ns]
            # The model pins too: a file played daily never expires, and an
            # upgraded model must not replay the old voice.
            spec = manifest()
            identity = [sentence, voice, spec["bundles"]["kokoro"], spec["tts"]]
            return hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()
            ).hexdigest()
        except Exception:
            log.warning("Speech cache skipped: no voice key", exc_info=True)
            return None

    def load(self, key):
        """(samples, rate) kept under key, or None: a missing or bad file is a miss."""
        path = self.dir / f"{key}.f32"
        try:
            data = path.read_bytes()
            magic, rate, crc = HEADER.unpack_from(data)
            samples = array.array("f")
            samples.frombytes(data[HEADER.size :])
        except (OSError, ValueError, struct.error):
            return None
        if magic != MAGIC or not rate or not samples or zlib.crc32(samples) != crc:
            return None
        with contextlib.suppress(OSError):
            os.utime(path)  # played now: kept KEEP_S from here
        return samples, rate

    def store(self, key, chunks):
        """Keep a sentence synthesized in full, renamed into place from a temp file."""
        try:
            rates = {rate for _, rate in chunks}
            samples = array.array("f")
            for audio, _ in chunks:
                samples.extend(audio)
            # One rate per file. The player refuses empty or non-finite audio,
            # so it is never kept to replay.
            if len(rates) != 1 or not samples or not all(map(math.isfinite, samples)):
                return
            (rate,) = rates
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f"{key}.", suffix=".tmp", dir=self.dir)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(HEADER.pack(MAGIC, rate, zlib.crc32(samples)))
                    handle.write(samples)
                os.replace(name, self.dir / f"{key}.f32")
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        except Exception:
            log.warning("Speech cache write failed", exc_info=True)
            return
        self.prune()

    def prune(self):
        """Delete files not played for KEEP_S, temp files a crash left included."""
        cutoff = time.time() - KEEP_S
        for path in list(self.dir.glob("*")):
            with contextlib.suppress(OSError):
                if path.lstat().st_mtime < cutoff:
                    path.unlink()
