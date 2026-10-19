"""Speaker gate: does a speech segment sound like the enrolled user?

The user enrolls once with about 30 seconds of speech (`/voice enroll`). Only
the voiceprint (one embedding, no audio) is kept, in the voice root: private to
this OS user and never synced. With the gate on, each speech segment is scored
against it. A segment below the threshold is `other_speaker` (a TV, other
people, voice's own playback): it stays in the transcript as context and is
never sent to the classifier or the agent.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

from .control import atomic_json, manifest
from .speech_settings import SPEAKER_THRESHOLD as DEFAULT_THRESHOLD

RATE = 16000
VOICEPRINT = "voiceprint.json"
ENROLL_SECONDS = 30.0
# Shorter segments ("yes", "stop") carry too little voice to judge: unverified.
MIN_SECONDS = 1.0


def model_id(bundle=None) -> str:
    """The pinned model a voiceprint belongs to; another model needs a new one."""
    bundle = bundle or manifest()["speaker"]
    return f"{bundle['model']}@{bundle['files'][0]['sha256'][:16]}"


class SpeakerModel:
    """ONNX speaker embedding with the Kaldi fbank sherpa-onnx feeds it."""

    def __init__(self, path: Path):
        import kaldi_native_fbank as knf
        import onnxruntime as ort

        options = ort.SessionOptions()
        # Runs beside Whisper on every segment; one core is enough.
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path), options, providers=["CPUExecutionProvider"]
        )
        meta = self.session.get_modelmeta().custom_metadata_map
        self.scale = 1.0 if meta.get("normalize_samples", "1") == "1" else 32768.0
        self.global_mean = meta.get("feature_normalize_type") == "global-mean"
        self.input = self.session.get_inputs()[0].name
        self.knf = knf
        self.options = knf.FbankOptions()
        self.options.frame_opts.dither = 0
        self.options.frame_opts.snip_edges = False
        self.options.frame_opts.samp_freq = RATE
        self.options.mel_opts.num_bins = 80
        self.options.mel_opts.high_freq = -400

    def embed(self, audio) -> np.ndarray:
        fbank = self.knf.OnlineFbank(self.options)
        fbank.accept_waveform(RATE, np.asarray(audio, dtype=np.float32) * self.scale)
        fbank.input_finished()
        frames = np.array(
            [fbank.get_frame(i) for i in range(fbank.num_frames_ready)],
            dtype=np.float32,
        )
        if self.global_mean:
            frames -= frames.mean(axis=0)
        embedding = self.session.run(None, {self.input: frames[None]})[0][0]
        return embedding / max(float(np.linalg.norm(embedding)), 1e-9)


def load_voiceprint(root: Path, model: str):
    """The saved voiceprint for this model, or None (none, unreadable, other model)."""
    import json

    try:
        value = json.loads((root / VOICEPRINT).read_text())
        if value.get("model") != model:
            return None
        embedding = np.asarray(value["embedding"], dtype=np.float32)
        if embedding.ndim != 1 or not np.isfinite(embedding).all():
            return None
        return embedding / max(float(np.linalg.norm(embedding)), 1e-9), value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_voiceprint(root: Path, model: str, embedding, seconds, segments):
    path = root / VOICEPRINT
    atomic_json(
        path,
        {
            "model": model,
            "embedding": [round(float(x), 6) for x in embedding],
            "seconds": round(seconds, 1),
            "segments": segments,
            "created_at": time.time(),
        },
    )
    os.chmod(path, 0o600)


def forget_voiceprint(root: Path) -> bool:
    try:
        (root / VOICEPRINT).unlink()
        return True
    except FileNotFoundError:
        return False


class Enrollment:
    """Collects segments until ENROLL_SECONDS of one consistent voice.

    A segment unlike the rest (the TV, somebody else) is left out, so a stray
    line does not end up in the voiceprint.
    """

    def __init__(self, model: SpeakerModel, threshold: float, target=ENROLL_SECONDS):
        self.model, self.threshold, self.target = model, threshold, target
        self.items: list[tuple[np.ndarray, float]] = []
        self.kept: list[int] = []

    def add(self, audio) -> None:
        seconds = len(audio) / RATE
        if seconds >= MIN_SECONDS:
            self.items.append((self.model.embed(audio), seconds))
            self._consistent()

    def _mean(self, indexes):
        total = sum((self.items[i][0] * self.items[i][1] for i in indexes), 0)
        return total / max(float(np.linalg.norm(total)), 1e-9)

    def _consistent(self):
        everyone = range(len(self.items))
        mean = self._mean(everyone)
        self.kept = [i for i in everyone if float(self.items[i][0] @ mean) >= self.threshold]

    @property
    def seconds(self) -> float:
        return sum(self.items[i][1] for i in self.kept)

    @property
    def dropped(self) -> int:
        return len(self.items) - len(self.kept)

    def done(self) -> bool:
        return self.seconds >= self.target

    def voiceprint(self) -> np.ndarray:
        return self._mean(self.kept)


def verdict(model: SpeakerModel, voiceprint, audio, threshold) -> dict:
    """Transcript fields for one segment: speaker and, when judged, its score."""
    if len(audio) / RATE < MIN_SECONDS:
        return {"speaker": "unverified"}
    score = float(model.embed(audio) @ voiceprint)
    return {
        "speaker": "user" if score >= threshold else "other_speaker",
        "speaker_score": round(score, 3),
    }


class SpeakerGate:
    """The device service's gate: settings, model, voiceprint and enrollment.

    prepare() and judge() block (model download, inference): the service runs
    them in a worker thread, one segment at a time.
    """

    def __init__(self, root: Path):
        self.root = root
        self.enabled, self.threshold = False, DEFAULT_THRESHOLD
        self.model = None
        self.voiceprint = self.info = None
        self.enroll_requested = False
        self.enrollment = None
        self.saved_at = None  # wall time the latest enrollment was saved
        self.state, self.detail = "off", ""
        self.reload()

    def configure(self, preferences) -> None:
        self.enabled = bool(preferences.get("speaker_gate"))
        self.threshold = float(preferences.get("speaker_threshold", DEFAULT_THRESHOLD))

    @property
    def needed(self) -> bool:
        return self.enabled or self.enroll_requested or self.enrollment is not None

    def reload(self) -> None:
        loaded = load_voiceprint(self.root, model_id())
        self.voiceprint, self.info = loaded or (None, None)

    def prepare(self) -> None:
        """Download the pinned model (verified), load it and read the voiceprint."""
        from .assets import ensure_models

        def progress(**state):
            if state.get("total"):
                done, total = state["downloaded"] >> 20, state["total"] >> 20
                self.detail = f"Downloading the speaker model ({done}/{total} MB)"

        try:
            if self.model is None:
                self.state, self.detail = "preparing", "Preparing the speaker model"
                bundle = manifest()["speaker"]
                folder = ensure_models(self.root, progress, {"speaker": bundle})
                self.model = SpeakerModel(folder["speaker"] / bundle["files"][0]["name"])
            self.reload()
            self.state, self.detail = "ready", ""
        except Exception as exc:
            self.state, self.detail = "error", f"Speaker model failed: {exc}"
            raise

    def enroll(self) -> None:
        """Start over: the next ENROLL_SECONDS of the user's speech."""
        self.enrollment, self.enroll_requested = None, True

    def cancel(self) -> None:
        self.enrollment, self.enroll_requested = None, False

    def forget(self) -> bool:
        self.cancel()
        self.voiceprint = self.info = None
        return forget_voiceprint(self.root)

    def judge(self, audio, overlap=False) -> dict:
        """Transcript fields for one segment; `overlap`: voice was speaking."""
        if self.model is None:
            return {}
        if self.enroll_requested:
            self.enroll_requested = False
            self.enrollment = Enrollment(self.model, self.threshold)
        enrollment = self.enrollment
        if enrollment is not None:
            # Speech heard while voice talks may be voice itself: never enrolled.
            if not overlap:
                enrollment.add(audio)
            if enrollment.done() and self.enrollment is enrollment:
                save_voiceprint(
                    self.root,
                    model_id(),
                    enrollment.voiceprint(),
                    enrollment.seconds,
                    len(enrollment.kept),
                )
                self.enrollment, self.saved_at = None, time.time()
                self.reload()
            return {"speaker": "enrolling"}
        if not self.enabled or self.voiceprint is None:
            return {}
        return verdict(self.model, self.voiceprint, audio, self.threshold)

    def status(self) -> dict:
        enrollment = self.enrollment
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "state": self.state if self.needed or self.model else "off",
            "detail": self.detail,
            "voiceprint": (
                {k: self.info.get(k) for k in ("seconds", "segments", "created_at")}
                if self.info
                else None
            ),
            "enrollment": (
                {
                    "seconds": round(enrollment.seconds, 1),
                    "target": enrollment.target,
                    "dropped": enrollment.dropped,
                }
                if enrollment is not None
                else {"seconds": 0.0, "target": ENROLL_SECONDS, "dropped": 0}
                if self.enroll_requested
                else None
            ),
            "saved_at": self.saved_at,
        }
