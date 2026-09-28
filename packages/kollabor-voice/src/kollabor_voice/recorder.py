"""kollabor-voice recorder: always-on mic capture via sounddevice.

Implements aquamarine v3 §2 + §4 + §6:
- 16 kHz mono float32 capture, 30 ms frames (480 samples)
- PortAudio callback -> frames queue (never blocks the audio thread)
- mic-permission dead-stream watchdog (1.5s) -> actionable error event
- frame schema: (seq, monotonic_ts, np.ndarray)

Engine contract: Recorder is surface-agnostic. Consumers poll
`recorder.frames` (a queue.Queue) or subscribe to lifecycle events via
`recorder.events` (queue.Queue of dicts). Zero TUI imports.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    import sounddevice as _sd
except ImportError:  # pragma: no cover - optional extra guard
    _sd = None

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
DEAD_STREAM_TIMEOUT_S = 1.5

PERMISSION_HELP = (
    "Microphone unavailable or permission denied. Grant access to your "
    "terminal app in System Settings > Privacy & Security > Microphone, "
    "then run /voicemode again."
)


@dataclass
class RecorderConfig:
    sample_rate: int = SAMPLE_RATE
    frame_ms: int = FRAME_MS
    input_device: int | None = None  # None = system default
    queue_maxsize: int = 512  # ~15s of frames before backpressure drops
    dead_stream_timeout_s: float = DEAD_STREAM_TIMEOUT_S


@dataclass
class Frame:
    seq: int
    ts: float  # time.monotonic() at capture
    audio: np.ndarray  # float32 [frame_samples]


@dataclass
class Recorder:
    """Owns the mic. start() -> frames flow; stop() drains and joins.

    Lifecycle events are dicts on `events`:
      {"type": "started", "device": str}
      {"type": "frames", "count": int}        # watchdog healthy tick
      {"type": "dead_stream", "detail": str}  # actionable error
      {"type": "stopped", "frames_captured": int}
      {"type": "error", "detail": str}
    """

    config: RecorderConfig = field(default_factory=RecorderConfig)
    frames: "queue.Queue[Frame]" = field(default_factory=queue.Queue)
    events: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)

    def __post_init__(self) -> None:
        self.events = queue.Queue(maxsize=256)
        if self.config.queue_maxsize > 0:
            # bounded queue -> callback backpressure drops oldest (§2)
            self.frames = queue.Queue(maxsize=self.config.queue_maxsize)
        self._stream = None
        self._seq = 0
        self._frames_captured = 0
        self._last_frame_ts: float | None = None
        self.level = 0.0
        self.device_name = ""
        self._stop_flag = threading.Event()
        self._watchdog: threading.Thread | None = None

    # -- public API ----------------------------------------------------

    def start(self) -> None:
        """Open the stream, spawn watchdog. Raises RuntimeError with an
        actionable message on missing dep / no input device."""
        if _sd is None:
            raise RuntimeError(
                "sounddevice is not installed. Run: pip install 'kollab[voice]'"
            )
        if self._stream is not None:
            return  # already running

        device = self.config.input_device
        if device is None:
            try:
                device = _sd.default.device[0]
            except Exception as exc:  # noqa: BLE001 - portaudio query failure
                raise RuntimeError(PERMISSION_HELP) from exc
            if device is None:
                raise RuntimeError(
                    "No input device found. Connect a microphone or select "
                    "one via voice.input_device."
                )

        self._stop_flag.clear()
        self._last_frame_ts = None
        try:
            self.device_name = str(_sd.query_devices(device, "input")["name"])
            self._stream = _sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._frame_samples(),
                device=device,
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - portaudio open failure
            if self._stream is not None:
                with contextlib.suppress(Exception):
                    self._stream.close()
            self._stream = None
            raise RuntimeError(PERMISSION_HELP) from exc

        self._watchdog = threading.Thread(
            target=self._watch_loop, name="voice-recorder-watchdog", daemon=True
        )
        self._watchdog.start()
        self._emit({"type": "started", "device": self.device_name})

    def stop(self) -> dict[str, Any]:
        """Stop stream, stop watchdog. Returns a summary dict. Safe to call
        twice."""
        self._stop_flag.set()
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass
            finally:
                with contextlib.suppress(Exception):
                    self._stream.close()
            self._stream = None
        if self._watchdog is not None:
            self._watchdog.join(timeout=2.0)
            self._watchdog = None
        summary = {"type": "stopped", "frames_captured": self._frames_captured}
        self._emit(summary)
        return summary

    # -- internals ------------------------------------------------------

    def _frame_samples(self) -> int:
        return self.config.sample_rate * self.config.frame_ms // 1000

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Keep the newest diagnostic, especially a dead-stream event.
            with contextlib.suppress(queue.Empty):
                self.events.get_nowait()
            with contextlib.suppress(queue.Full):
                self.events.put_nowait(event)

    def _on_audio(self, indata, _frames, _time_info, status) -> None:
        """PortAudio callback. MUST NOT block. Runs on the audio thread."""
        if self._stop_flag.is_set():
            raise _sd.CallbackStop  # stop pulling frames
        now = time.monotonic()
        self._seq += 1
        self._frames_captured += 1
        self._last_frame_ts = now
        self.level = float(np.sqrt(np.mean(indata[:, 0] ** 2)))
        frame = Frame(seq=self._seq, ts=now, audio=indata[:, 0].copy())
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            self._emit({"type": "gap", "detail": "Microphone frame queue overflow"})
            # backpressure: drop the OLDEST frame to keep latency bounded
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(frame)
            except queue.Full:  # pragma: no cover
                pass
        if status and status.input_overflow:
            self._emit({"type": "warning", "detail": f"input overflow: {status}"})

    def _watch_loop(self) -> None:
        """Dead-stream watchdog (v3 §4): zero frames within
        dead_stream_timeout_s of start, or a stall longer than
        2x timeout mid-run, is a dead stream (TCC denial or device yank)
        -> actionable error event. Engine decides to stop."""
        start_ts = time.monotonic()
        timeout = self.config.dead_stream_timeout_s
        saw_first_frame = False
        while not self._stop_flag.wait(timeout=0.1):
            now = time.monotonic()
            last = self._last_frame_ts
            if not saw_first_frame:
                if last is not None:
                    saw_first_frame = True
                    self._emit({"type": "frames", "count": self._frames_captured})
                elif now - start_ts > timeout:
                    self._emit({"type": "dead_stream", "detail": PERMISSION_HELP})
                    return
            else:
                if last is not None and now - last > 2 * timeout:
                    self._emit({"type": "dead_stream", "detail": PERMISSION_HELP})
                    return
