"""Echo cancellation bench: what Whisper hears with and without it.

A simulated room plays voice's Kokoro lines back into the microphone (direct
path plus a decaying 120 ms tail, arriving DELAY after the time the speaker
queue predicted) while `say` talks over them. EchoCanceller gets the playback
reference exactly as the service feeds it; the production segmenter and Whisper
base transcribe the raw microphone and the cancelled signal.

    python echo_bench.py DATA WHISPER_DIR > echo-cancellation-results.json

DATA holds kokoro/*.npy + rate.npy (Kokoro lines written with the voice runtime);
WHISPER_DIR is the verified models/whisper-base/<revision> folder. Run with the
voice runtime python on macOS (`say`, ffmpeg on PATH).
"""

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from kollabor_voice.audio import SpeechSegmenter
from kollabor_voice.echo import EchoCanceller, PlaybackReference, to_rate

RATE = 16000
HUMAN = "Hey, stop for a second and open the readme file instead."
DELAYS_MS = (-40, 0, 120, 300)  # echo arrival past the predicted time
ORIGIN = 1000.0  # monotonic time of the first sample


def words(text):
    return set(re.findall(r"[a-z']+", text.lower()))


def say(text):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "line.aiff"
        subprocess.run(["say", "-v", "Daniel", "-o", str(path), text], check=True, timeout=30)
        run = subprocess.run(
            ["ffmpeg", "-v", "quiet", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
            capture_output=True,
            check=True,
        )
    return np.frombuffer(run.stdout, np.float32)


def transcribe(model, audio):
    segmenter, text = SpeechSegmenter(), []
    cut = []
    for i in range(0, len(audio), 480):
        cut += segmenter.feed(audio[i : i + 480], ORIGIN + i / RATE)
    cut += [s for s in [segmenter.flush()] if s]
    for segment in cut:
        parts, _ = model.transcribe(segment.audio, language="en", beam_size=1)
        text.append(" ".join(p.text.strip() for p in parts))
    return " ".join(t for t in text if t)


def main(data: Path, whisper: Path):
    rate = int(np.load(data / "kokoro" / "rate.npy")[0])
    picked = (1, 3, 4, 7)
    lines = [np.load(data / "kokoro" / f"af_heart-{i}.npy") for i in picked]
    voice = np.concatenate([np.concatenate([line, np.zeros(rate // 3, np.float32)]) for line in lines])
    texts = json.loads((data / "kokoro" / "lines.json").read_text())
    voice_words = words(" ".join(texts[i] for i in picked)) - words(HUMAN)
    human_words = words(HUMAN) - voice_words
    spoken = say(HUMAN)
    model = WhisperModel(str(whisper), device="cpu", compute_type="int8")
    far = to_rate(voice, rate)
    over_at = 4 * RATE  # the human talks over voice 3 s into its reply
    after_at = RATE + len(far) + RATE // 2  # and again once voice has stopped
    total = after_at + len(spoken) + RATE
    rng = np.random.default_rng(1)
    tail = np.arange(1, int(0.12 * RATE))
    impulse = np.concatenate([[0.5], 0.15 * rng.standard_normal(len(tail)) * np.exp(-tail / (0.03 * RATE))])
    echo = np.convolve(far, impulse)[: len(far)].astype(np.float32)
    regions = {
        "voice_alone": (RATE + RATE // 2, over_at),
        "talk_over": (over_at, over_at + len(spoken) + RATE // 2),
        "human_alone": (after_at, total),
    }

    def share(found, wanted):
        return round(len(found & wanted) / len(wanted), 2)

    def heard(audio):
        result = {}
        for name, (a, b) in regions.items():
            text = transcribe(model, audio[a:b])
            found = words(text)
            result[name] = {
                "heard": text,
                "voice_words": share(found, voice_words),
                "human_words": share(found, human_words),
            }
        return result

    results = {"human": HUMAN, "voice_seconds": round(len(far) / RATE, 1), "runs": []}
    for gain in (0.6, 1.2):
        human = gain * spoken
        for delay_ms in DELAYS_MS:
            start = RATE + int(delay_ms * RATE / 1000)
            mic = 0.002 * rng.standard_normal(total).astype(np.float32)
            mic[start : start + len(echo)] += echo
            over = mic[over_at : over_at + len(human)].copy()
            for at in (over_at, after_at):
                mic[at : at + len(human)] += human
            # The player hands over 512-sample chunks a little ahead of time;
            # the reference learns when each will sound (predicted: 1 s in).
            reference = PlaybackReference()
            chunks = [(ORIGIN + 1.0 + i / rate, voice[i : i + 512]) for i in range(0, len(voice), 512)]
            canceller = EchoCanceller(reference)
            out, spent = np.zeros(total, np.float32), []
            for i in range(0, total - 479, 480):
                now = ORIGIN + i / RATE
                while chunks and chunks[0][0] <= now + 0.3:
                    reference.play(chunks[0][1], rate, chunks.pop(0)[0])
                began = time.perf_counter()
                out[i : i + 480] = canceller.process(mic[i : i + 480], now)
                spent.append(time.perf_counter() - began)
            quiet = slice(int(1.5 * RATE), over_at)

            def level(x):
                return 10 * np.log10(np.mean(x**2) + 1e-12)

            results["runs"].append(
                {
                    "echo_delay_ms": delay_ms,
                    "talk_over_speech_to_echo_db": round(float(level(human) - level(over)), 1),
                    "erle_db": round(float(level(mic[quiet]) - level(out[quiet])), 1),
                    "ms_per_30ms_frame": round(1000 * float(np.mean(spent)), 2),
                    "without": heard(mic),
                    "with": heard(out),
                }
            )
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
