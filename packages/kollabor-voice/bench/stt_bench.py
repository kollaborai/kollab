"""Whisper base vs Parakeet v3 on the same clips, through the service's own code.

Run with the voice runtime's Python (it has faster-whisper and numpy):

    PYTHONPATH=packages/kollabor-voice/src \\
      ~/.kollab/voice/runtimes/<runtime>/bin/python \\
      packages/kollabor-voice/bench/stt_bench.py --clips /tmp/stt-clips

Clips are spoken by macOS `say` (16 kHz mono) and kept outside the repo. The
babble condition mixes another voice saying another sentence 10 dB below the
speaker, a stand-in for a second person talking in the room. Whisper is primed
with the agent names exactly as the service primes it; Parakeet has no priming.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from kollabor_voice.assets import ensure_models
from kollabor_voice.audio import LocalModels
from kollabor_voice.control import manifest, service_root
from kollabor_voice.transcription import ParakeetWorker

NAMES = ("aquamarine", "lapis", "zircon", "sapphire", "kollab")
VOICES = ("Samantha", "Daniel", "Karen", "Rishi")
SENTENCES = (
    "Aquamarine, can you run the tests and tell me what failed?",
    "Hey lapis, what is the status of the build?",
    "Zircon, open the config file and check the port.",
    "Kollab, stop talking for a second.",
    "Sapphire, summarize the latest commits for me.",
    "Lapis, push the branch when the checks pass.",
    "Aquamarine, how much disk space is left on the server?",
    "Zircon, restart the voice service.",
    "Thanks lapis, that looks good.",
    "Aquamarine, are you there?",
    "Can you hear me?",
    "Switch the transcription engine to Parakeet.",
    "Remind me to call the dentist tomorrow morning.",
    "What is the weather like in San Francisco this weekend?",
    "Zircon and lapis, compare your answers and pick the better one.",
    "Kollab, read me the last message from sapphire.",
    "Add a unit test for the speech cache and run it.",
    "Open the pull request and approve it if the checks are green.",
    "Aquamarine, what time is it in Tokyo?",
    "Never mind, I was talking to my wife.",
)
BABBLE_SNR_DB = 10


def words(text):
    return re.sub(r"[^a-z0-9']+", " ", text.lower().replace("'", "")).split()


def errors(reference, hypothesis):
    """Word-level edit distance."""
    row = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        prev, row[0] = row[0], i
        for j, hyp in enumerate(hypothesis, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ref != hyp))
    return row[-1]


def read(path):
    with wave.open(str(path)) as handle:
        assert handle.getframerate() == 16000 and handle.getnchannels() == 1
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, "<i2").astype(np.float32) / 32768


def spoken(clips, voice, index):
    path = clips / f"{voice}-{index:02d}.wav"
    if not path.exists():
        subprocess.run(
            [
                "say",
                "-v",
                voice,
                "-o",
                str(path),
                "--data-format=LEI16@16000",
                SENTENCES[index],
            ],
            check=True,
        )
    return read(path)


def babble(clips, voice, index):
    """Another voice and sentence under the speaker, BABBLE_SNR_DB quieter."""
    speech = spoken(clips, voice, index)
    other = spoken(
        clips,
        VOICES[(VOICES.index(voice) + 1) % len(VOICES)],
        (index + 7) % len(SENTENCES),
    )
    other = np.resize(other, len(speech))
    rms = lambda x: float(np.sqrt(np.mean(x**2))) or 1.0  # noqa: E731
    mixed = speech + other * rms(speech) / rms(other) / 10 ** (BABBLE_SNR_DB / 20)
    return (mixed / max(1.0, float(np.abs(mixed).max()))).astype(np.float32)


def summary(rows, engine):
    ms = [r[engine]["ms"] for r in rows]
    seconds = [r["seconds"] for r in rows]
    names = [
        (name, name in words(r[engine]["text"]))
        for r in rows
        for name in NAMES
        if name in words(r["text"])
    ]
    return {
        "utterances": len(rows),
        "latency_ms": {
            "p50": round(statistics.median(ms)),
            "p95": round(float(np.percentile(ms, 95))),
            "mean": round(statistics.mean(ms)),
            "max": round(max(ms)),
        },
        "real_time_factor_p50": round(
            statistics.median(m / 1000 / s for m, s in zip(ms, seconds)), 3
        ),
        "wer": round(
            sum(errors(words(r["text"]), words(r[engine]["text"])) for r in rows)
            / sum(len(words(r["text"])) for r in rows),
            4,
        ),
        "names_correct": f"{sum(hit for _, hit in names)}/{len(names)}",
        "per_name": {
            name: f"{sum(hit for n, hit in names if n == name)}/{sum(n == name for n, _ in names)}"
            for name in NAMES
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=service_root())
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).with_name("stt-results.json")
    )
    parser.add_argument(
        "--idle",
        type=lambda text: [int(s) for s in text.split(",")],
        default=[],
        help="Seconds of silence before one more utterance, e.g. 30,120,300",
    )
    args = parser.parse_args()
    args.clips.mkdir(parents=True, exist_ok=True)
    quiet = lambda **_: None  # noqa: E731

    whisper = LocalModels(ensure_models(args.root, quiet))
    whisper.warmup()
    started = time.monotonic()
    parakeet = ParakeetWorker(args.root, quiet)
    parakeet_cold = time.monotonic() - started
    engines = {
        "whisper": lambda audio: whisper.transcribe(audio, NAMES)[0],
        "parakeet": parakeet.transcribe,
    }
    # Speak every clip first: `say` running between clips slows both engines.
    clips = [
        (condition, voice, index, make(args.clips, voice, index))
        for condition, make in (("clean", spoken), ("babble", babble))
        for voice in VOICES
        for index in range(len(SENTENCES))
    ]
    rows = [
        {
            "condition": condition,
            "voice": voice,
            "text": SENTENCES[index],
            "seconds": round(len(audio) / 16000, 2),
        }
        for condition, voice, index, audio in clips
    ]
    load = {}
    try:
        # One engine at a time, as in the service: Whisper's CPU threads
        # winding down slow a Parakeet call that follows straight after.
        for engine, transcribe in engines.items():
            load[engine] = round(os.getloadavg()[0], 1)
            for row, (*_, audio) in zip(rows, clips):
                started = time.monotonic()
                hypothesis = transcribe(audio)
                row[engine] = {
                    "text": hypothesis,
                    "ms": round((time.monotonic() - started) * 1000, 1),
                }
        # A person pauses between requests: time the first utterance after each gap.
        idle = {engine: {} for engine in engines}
        for engine, transcribe in engines.items():
            for seconds in args.idle:
                time.sleep(seconds)
                started = time.monotonic()
                transcribe(clips[0][3])
                idle[engine][seconds] = round((time.monotonic() - started) * 1000, 1)
    finally:
        parakeet.close()

    spec = manifest()
    chip = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
    ).stdout.strip()
    result = {
        "date": time.strftime("%Y-%m-%d"),
        "machine": f"{chip}, macOS {platform.mac_ver()[0]}",
        "audio": f"macOS say voices {', '.join(VOICES)}, 16 kHz mono, {len(SENTENCES)} sentences each; "
        f"babble = another voice and sentence {BABBLE_SNR_DB} dB below the speaker",
        "method": "Each engine runs the service's own code path on every clip once: Whisper through "
        "LocalModels.transcribe (CPU int8, Silero VAD, primed with the agent names), Parakeet through "
        "ParakeetWorker (MLX on the GPU, its own process), one engine after the other. Latency is wall time "
        "per utterance after warm-up; the load average and swap in use are recorded with it.",
        "engines": {
            "whisper": {
                "model": "faster-whisper base (Systran)",
                "revision": spec["bundles"]["whisper-base"]["revision"],
                "runtime": spec["runtime"],
            },
            "parakeet": {
                "model": spec["stt"]["parakeet"]["model"],
                "revision": spec["stt"]["parakeet"]["bundle"]["revision"],
                "runtime": spec["stt"]["parakeet"]["runtime"],
                "cold_start_seconds": round(parakeet_cold, 1),
            },
        },
        "load_average_at_start": load,
        "first_utterance_after_idle_ms": idle,
        "swap_used_at_end": subprocess.run(
            ["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True
        )
        .stdout.split("used = ")[-1]
        .split()[0],
        "summary": {
            engine: {
                condition: summary(
                    [r for r in rows if r["condition"] == condition], engine
                )
                for condition in ("clean", "babble")
            }
            for engine in engines
        },
        "clips": rows,
    }
    args.out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result["summary"], indent=1))


if __name__ == "__main__":
    main()
