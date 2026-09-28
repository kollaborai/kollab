"""Streaming Silero segmentation and real local model adapters.

Imported only by the device service after isolated support is ready.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

RATE = 16000
BLOCK = 512


@dataclass
class AudioSegment:
    audio: np.ndarray
    started_at: float
    ended_at: float
    final: bool = True


class StreamingVad:
    def __init__(self):
        from faster_whisper.vad import get_vad_model

        self.session = get_vad_model().session
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros_like(self.h)
        self.context = np.zeros(64, dtype=np.float32)

    def __call__(self, samples):
        output, self.h, self.c = self.session.run(
            None,
            {
                "input": np.concatenate((self.context, samples))[None, :],
                "h": self.h,
                "c": self.c,
            },
        )
        self.context = samples[-64:]
        return float(output.reshape(-1)[0]) >= 0.5


class SpeechSegmenter:
    """700ms endpoint, 8s cap, one copy of a 320ms preroll."""

    def __init__(self, detector=None):
        self.detector = detector if detector is not None else StreamingVad()
        self.pending = np.empty(0, dtype=np.float32)
        self.preroll = deque(maxlen=10)
        self.active = []
        self.silent_samples = 0
        self.position = 0
        self.origin = None
        self.start = 0

    def feed(self, audio, started_at) -> list[AudioSegment]:
        if self.origin is None:
            self.origin = started_at
        self.pending = np.concatenate((self.pending, audio))
        results = []
        while len(self.pending) >= BLOCK:
            block, self.pending = self.pending[:BLOCK], self.pending[BLOCK:]
            speech = self.detector(block)
            if not self.active:
                if speech:
                    self.start = self.position - len(self.preroll) * BLOCK
                    self.active = list(self.preroll)
                    self.preroll.clear()
                    self.silent_samples = 0
                else:
                    self.preroll.append(block)
            if self.active or speech:
                self.active.append(block)
                self.silent_samples = 0 if speech else self.silent_samples + BLOCK
                if (
                    self.silent_samples >= 0.7 * RATE
                    or len(self.active) * BLOCK >= 8 * RATE
                ):
                    results.append(self._finish(True))
            self.position += BLOCK
        return results

    def _finish(self, final):
        samples = np.concatenate(self.active)
        result = AudioSegment(
            samples,
            self.origin + self.start / RATE,
            self.origin + (self.start + len(samples)) / RATE,
            final,
        )
        self.active = []
        self.silent_samples = 0
        return result

    def flush(self):
        if not self.active:
            return None
        if len(self.pending):
            self.active.append(self.pending)
            self.pending = np.empty(0, dtype=np.float32)
        return self._finish(False)


class LocalModels:
    def __init__(self, paths):
        from faster_whisper import WhisperModel
        from kokoro_onnx import Kokoro

        self.whisper = WhisperModel(
            str(paths["whisper-base"]),
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )
        self.kokoro = Kokoro(
            str(paths["kokoro"] / "kokoro-v1.0.int8.onnx"),
            str(paths["kokoro"] / "voices-v1.0.bin"),
        )
        # Validate the bundled VAD before claiming input readiness.
        StreamingVad()(np.zeros(BLOCK, dtype=np.float32))

    def warmup(self):
        """Exercise both inference engines silently before opening any device."""
        samples, rate = self.synthesize("Voice is ready.")
        audio = np.interp(
            np.arange(int(len(samples) * RATE / rate)) * rate / RATE,
            np.arange(len(samples)),
            samples,
        ).astype(np.float32)
        segments, _ = self.whisper.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0,
        )
        # Decoding is lazy: consuming the generator is part of the warm-up.
        for _ in segments:
            pass

    def transcribe(self, audio):
        segments, info = self.whisper.transcribe(
            audio,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt="Kollab is the assistant's name.",
            temperature=0,
        )
        # Whisper can hallucinate repetitions on a tiny trailing fragment at the
        # hard segment boundary. Keep only acoustically supported decodes, with
        # no temperature fallback that invents text from the residual noise.
        accepted = [
            s.text.strip()
            for s in segments
            if s.avg_logprob >= -1.0
            and s.no_speech_prob < 0.6
            and s.compression_ratio <= 2.4
        ]
        return " ".join(accepted).strip(), info.language

    def synthesize(self, text):
        return self.kokoro.create(text, voice="af_heart", speed=1.0, lang="en-us")


def play_audio(samples, rate, cancelled: threading.Event):
    """Blocking chunked playback. stop() drains; abort() never reports played."""
    import sounddevice as sd

    if cancelled.is_set():
        return False
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Speech engine returned invalid waveform samples")
    stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32")
    try:
        stream.start()
        for offset in range(0, len(audio), 512):
            if cancelled.is_set():
                stream.abort()
                return False
            stream.write(audio[offset : offset + 512, None])
        if cancelled.is_set():
            stream.abort()
            return False
        stream.stop()  # Wait for actual device completion before the receipt.
        return not cancelled.is_set()
    finally:
        stream.close()
