"""Speech preferences and validation; safe to import without audio libraries."""

from __future__ import annotations

import math
import os
import platform
import sys
from pathlib import Path

from .control import manifest

# Cosine score at or above which a segment is the enrolled user (speaker.py).
# Measured false accepts and rejects: bench/README.md.
SPEAKER_THRESHOLD = 0.45

# Engines by preference id; the labels are what people read.
STT_ENGINES = {"whisper": "Whisper Base", "parakeet": "Parakeet v3"}
TTS_ENGINES = {"kokoro": "Kokoro", "vibevoice": "VibeVoice"}


def apple_silicon():
    """MLX, which Parakeet v3 and the Qwen classifier run on, needs an Apple silicon Mac."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


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
        "stt_engine": "whisper",
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
