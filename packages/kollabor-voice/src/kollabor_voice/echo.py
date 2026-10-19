"""Acoustic echo cancellation: voice's own playback is removed from the microphone.

The speaker queue tells PlaybackReference what reaches the speaker and when.
Each microphone frame is paired with the playback sounding while it was
captured, and WebRTC AEC3 (pywebrtc-audio) subtracts that echo. Only voice's
own speech is removed: a TV, other people and other apps keep their sound.
"""

from __future__ import annotations

import threading
import time

import numpy as np

RATE = 16000
# Playback is fed this far ahead of the microphone. AEC3 finds the remaining
# delay itself (up to about 0.5 s) but cannot cancel echo that arrives before
# its reference, so a late playback-time estimate must still land early.
LEAD_S = 0.06


def to_rate(samples, rate) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if rate == RATE:
        return audio
    count = int(len(audio) * RATE / rate)
    return np.interp(
        np.arange(count) * rate / RATE, np.arange(len(audio)), audio
    ).astype(np.float32)


class PlaybackReference:
    """What the speaker plays, at 16 kHz on the monotonic clock (a ring buffer)."""

    def __init__(self, seconds=10):
        self.lock = threading.Lock()
        self.ring = np.zeros(int(seconds * RATE), dtype=np.float32)
        self.end = 0  # absolute sample index after the latest written sample
        self.active_until = 0.0  # monotonic time the latest playback ends

    def _write(self, start, audio):
        size = len(self.ring)
        if len(audio) > size:  # only the latest ring's worth fits
            start, audio = start + len(audio) - size, audio[-size:]
        index = np.arange(start, start + len(audio)) % size
        self.ring[index] = audio

    def play(self, samples, rate, at) -> float:
        """samples start sounding at monotonic time `at`; returns when they end."""
        audio = to_rate(samples, rate)
        start = int(round(at * RATE))
        with self.lock:
            if self.end and start > self.end:
                # Silence since the last playback; the ring still holds older audio.
                gap = min(start - self.end, len(self.ring))
                self._write(start - gap, np.zeros(gap, dtype=np.float32))
            self._write(start, audio)
            self.end = max(self.end, start + len(audio))
            ends = (start + len(audio)) / RATE
            self.active_until = max(self.active_until, ends)
        return ends

    def cut(self, at=None) -> None:
        """Playback stopped early: nothing after `at` will sound."""
        start = int(round((time.monotonic() if at is None else at) * RATE))
        with self.lock:
            if start < self.end:
                self._write(start, np.zeros(self.end - start, dtype=np.float32))
                self.end = start
            self.active_until = min(self.active_until, start / RATE)

    def window(self, at, count) -> np.ndarray:
        """`count` samples of what sounded from monotonic time `at` (zeros if none)."""
        start = int(round(at * RATE))
        out = np.zeros(count, dtype=np.float32)
        with self.lock:
            first = max(start, self.end - len(self.ring))
            last = min(start + count, self.end)
            if last > first:
                index = np.arange(first, last) % len(self.ring)
                out[first - start : last - start] = self.ring[index]
        return out


class EchoCanceller:
    """One AEC3 instance on the capture path, fed the playback reference."""

    def __init__(self, reference: PlaybackReference, lead=LEAD_S):
        from pywebrtc_audio import EchoCanceller as Aec3

        self.reference, self.lead = reference, lead
        self.aec = Aec3(sample_rate=RATE, num_channels=1)

    def process(self, audio, captured_at) -> np.ndarray:
        """Microphone audio captured from monotonic time `captured_at`, echo removed.

        Recorder frames are whole 10 ms frames (30 ms); AEC3 pads any other length.
        """
        audio = np.asarray(audio, dtype=np.float32)
        far = self.reference.window(captured_at + self.lead, len(audio))
        return self.aec.process(audio, far)
