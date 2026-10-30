"""Speech preferences and validation; safe to import without audio libraries."""

from __future__ import annotations

import json
import math
import os
import platform
import sys
from pathlib import Path

from .control import atomic_json, manifest, service_root

# Cosine score at or above which a segment is the enrolled user (speaker.py).
# Measured false accepts and rejects: bench/README.md.
SPEAKER_THRESHOLD = 0.45

# Engines by preference id; the labels are what people read.
STT_ENGINES = {"whisper": "Whisper Base", "parakeet": "Parakeet v3"}
TTS_ENGINES = {"kokoro": "Kokoro", "vibevoice": "VibeVoice"}

# Earlier microphone lines sent to the agent beside a new utterance.
DEFAULT_CONTEXT_LINES = 10
MAX_CONTEXT_LINES = 50


def apple_silicon():
    """MLX, which Parakeet v3 runs on, needs an Apple silicon Mac."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def available_stt_engines():
    """The transcription engines this machine can run: Parakeet only on Apple silicon."""
    return {k: v for k, v in STT_ENGINES.items() if k != "parakeet" or apple_silicon()}


KOKORO_VOICES = (
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
)


def speech_defaults():
    model = manifest()["tts"]["vibevoice"]["model"]
    installed = Path.home() / "dev/vibevoice/models/VibeVoice-Streaming-0.5B"
    if (installed / "config.json").is_file():
        model = str(installed)
    return {
        # Voice in the web UI transcribes only with Parakeet (map_readiness).
        "stt_engine": "parakeet" if apple_silicon() else "whisper",
        "tts_engine": "kokoro",
        "kokoro_voice": "af_heart",
        "kokoro_speed": 1.0,
        "vibevoice_voice": "Carter",
        "vibevoice_prompt": "",
        "vibevoice_model": os.environ.get("VIBEVOICE_MODEL_PATH", model),
        "vibevoice_device": "auto",
        "vibevoice_steps": 5,
        "vibevoice_cfg": 1.5,
        # Filters in front of transcription (echo.py, speaker.py); off until
        # turned on.
        "echo_cancellation": False,
        "speaker_gate": False,
        "speaker_threshold": SPEAKER_THRESHOLD,
        "context_lines": DEFAULT_CONTEXT_LINES,
    }


def speech_errors(preferences, *, check_files=False):
    errors = {}
    choices = {
        "stt_engine": tuple(STT_ENGINES),
        "tts_engine": tuple(TTS_ENGINES),
        "kokoro_voice": KOKORO_VOICES,
        "vibevoice_voice": tuple(manifest()["tts"]["vibevoice"]["voices"]),
        "vibevoice_device": ("auto", "mps", "cuda", "cpu"),
    }
    for key, options in choices.items():
        if preferences.get(key) not in options:
            errors[key] = "Choose one of the available options"
    if preferences.get("stt_engine") == "parakeet" and not apple_silicon():
        errors["stt_engine"] = "Parakeet v3 needs an Apple silicon Mac"
    for key in ("echo_cancellation", "speaker_gate"):
        if not isinstance(preferences.get(key), bool):
            errors[key] = "Must be on or off"
    for key, low, high, step in (
        ("kokoro_speed", 0.5, 2.0, 0.1),
        ("vibevoice_steps", 1, 20, 1),
        ("vibevoice_cfg", 1.0, 3.0, 0.1),
        ("speaker_threshold", 0.0, 1.0, 0.01),
    ):
        value = preferences.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            errors[key] = "Must be a finite number"
        elif (
            not low <= value <= high
            or abs((value - low) / step - round((value - low) / step)) > 1e-6
        ):
            errors[key] = f"Choose {low}–{high} in steps of {step}"
    lines = preferences.get("context_lines")
    if type(lines) is not int or not 1 <= lines <= MAX_CONTEXT_LINES:
        errors["context_lines"] = f"Choose a whole number from 1 to {MAX_CONTEXT_LINES}"
    for key in ("vibevoice_model", "vibevoice_prompt"):
        value = preferences.get(key)
        if not isinstance(value, str) or len(value.encode()) > 4096 or "\x00" in value:
            errors[key] = "Must be a valid path or model name"
    if check_files and preferences.get("tts_engine") == "vibevoice" and not errors:
        model = preferences["vibevoice_model"]
        if model != manifest()["tts"]["vibevoice"]["model"]:
            path = Path(model).expanduser()
            if not path.is_absolute() or not (path / "config.json").is_file():
                errors["vibevoice_model"] = (
                    "Choose the default Microsoft model or an existing absolute model directory"
                )
        prompt = preferences["vibevoice_prompt"]
        if prompt:
            path = Path(prompt).expanduser()
            if not path.is_absolute() or path.suffix != ".pt" or not path.is_file():
                errors["vibevoice_prompt"] = (
                    "Choose an existing absolute .pt voice prompt, or leave blank for the preset"
                )
    return errors


def load_speech_preferences(root=None):
    """Read validated voice settings; a bad stored value falls back to its default."""
    try:
        value = json.loads(((root or service_root()) / "preferences.json").read_text())
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    defaults = speech_defaults()
    preferences = {**defaults, **{key: value[key] for key in defaults if key in value}}
    for key in speech_errors(preferences):
        preferences[key] = defaults[key]
    return preferences


def save_speech_preferences(changes, root=None):
    """Publish one validated speech update without losing unrelated preference keys."""
    from . import file_lock

    defaults = speech_defaults()
    if not isinstance(changes, dict) or set(changes) - set(defaults):
        raise ValueError("Unknown speech preference")

    path = (root or service_root()) / "preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (path.parent / "preferences.lock").open("a") as lock:
        file_lock.lock(lock.fileno())
        try:
            stored = json.loads(path.read_text())
        except FileNotFoundError:
            stored = {}
        except ValueError as exc:
            raise ValueError(
                "Voice preferences are unreadable; repair preferences.json before saving"
            ) from exc
        if not isinstance(stored, dict):
            raise ValueError("Voice preferences must be an object")

        speech = {
            **defaults,
            **{key: value for key, value in stored.items() if key in defaults},
            **changes,
        }
        errors = speech_errors(speech, check_files=True)
        if errors:
            raise ValueError(
                "; ".join(f"{key}: {error}" for key, error in errors.items())
            )
        atomic_json(path, {**stored, **changes})
    return load_speech_preferences(root)


def speech_key(preferences):
    keys = (
        ("tts_engine", "kokoro_voice", "kokoro_speed")
        if preferences["tts_engine"] == "kokoro"
        else (
            "tts_engine",
            "vibevoice_voice",
            "vibevoice_prompt",
            "vibevoice_model",
            "vibevoice_device",
            "vibevoice_steps",
            "vibevoice_cfg",
        )
    )
    return tuple((key, preferences[key]) for key in keys)
