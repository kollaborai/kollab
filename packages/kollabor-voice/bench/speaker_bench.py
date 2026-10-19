"""Speaker gate bench: false accepts and false rejects on production-sized segments.

Every source is cut by the production segmenter (Silero VAD, 700 ms endpoint, 8 s
cap). Proxy users are the 40 LibriSpeech dev-clean readers: each enrolls with its
first 30 seconds (with the same consistency pass as /voice enroll) and is scored
against its remaining segments (false rejects) and against everything else (false
accepts): the other 39 readers, a TV film, Kokoro playback and macOS `say` voices.

    python speaker_bench.py prep DATA       # segments.npz from the sources below
    python speaker_bench.py embed DATA MODEL.onnx
    python speaker_bench.py score DATA MODEL.onnx [THRESHOLD]

DATA holds the inputs, none of which are committed:
- ls-validation.parquet: openslr/librispeech_asr clean/validation/0000.parquet
- hgf.mp3: His Girl Friday (1940, public domain) from archive.org; minutes 2-42
- kokoro/*.npy + rate.npy: Kokoro lines written with the voice runtime
Run with the voice runtime python plus pyarrow and soundfile, and ffmpeg on PATH.
"""

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

RATE = 16000
LINES = [
    "Let me check on that.",
    "On it. I will look at the build logs and tell you what failed.",
    "Sorry, was that for me?",
    "The tests passed, but two warnings are worth a look before you merge.",
    "I found the bug in the session manager; it drops the lease after three seconds.",
    "Still working on it. The download is about halfway done.",
    "Yes, I can hear you.",
    "Here is what I changed: the parser now keeps quoted strings together.",
    "That command failed because the file was missing, so I created it first.",
    "You are welcome. Let me know when you want to start the release.",
]
SAY_VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Tessa"]


def decode(path, start=None, seconds=None):
    cmd = ["ffmpeg", "-v", "quiet"]
    cmd += ["-ss", str(start)] if start is not None else []
    cmd += ["-t", str(seconds)] if seconds else []
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    run = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(run.stdout, np.float32)


def prep(data: Path):
    import pyarrow.parquet as pq
    import soundfile as sf
    from kollabor_voice.audio import SpeechSegmenter

    items, meta = [], []

    def add(audio, **info):
        segmenter, cut = SpeechSegmenter(), []
        for i in range(0, len(audio), 480):
            cut += segmenter.feed(audio[i : i + 480].astype(np.float32), 0.0)
        cut += [s for s in [segmenter.flush()] if s]
        for segment in cut:
            items.append(segment.audio.astype(np.float32))
            meta.append({**info, "seconds": round(len(segment.audio) / RATE, 3)})

    for row in pq.read_table(data / "ls-validation.parquet").to_pylist():
        audio, rate = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
        assert rate == RATE
        add(audio, kind="person", source=f"librispeech-{row['speaker_id']}")
    add(decode(data / "hgf.mp3", 120, 40 * 60), kind="tv", source="his-girl-friday")
    pad = lambda audio: np.concatenate([np.zeros(RATE // 2), audio, np.zeros(RATE)])  # noqa: E731
    rate = int(np.load(data / "kokoro" / "rate.npy")[0])
    for path in sorted((data / "kokoro").glob("*-*.npy")):
        samples = np.load(path)
        audio = np.interp(
            np.arange(int(len(samples) * RATE / rate)) * rate / RATE,
            np.arange(len(samples)),
            samples,
        )
        add(pad(audio), kind="playback", source="kokoro-" + path.stem.rsplit("-", 1)[0])
    wav = data / "say.aiff"
    for voice in SAY_VOICES:
        for line in LINES:
            subprocess.run(["say", "-v", voice, "-o", str(wav), line], check=True, timeout=30)
            add(pad(decode(wav)), kind="say", source=f"say-{voice}")
    audio = np.empty(len(items), dtype=object)
    audio[:] = items
    np.savez(data / "segments.npz", audio=audio, meta=json.dumps(meta))
    print(len(items), "segments")


def embed(data: Path, model_path: Path):
    from kollabor_voice.speaker import MIN_SECONDS, SpeakerModel

    audio = np.load(data / "segments.npz", allow_pickle=True)["audio"]
    model, rows, cost = SpeakerModel(model_path), [], []
    for samples in audio:
        if len(samples) < MIN_SECONDS * RATE:
            rows.append(None)  # production marks these unverified
            continue
        started = time.perf_counter()
        rows.append(model.embed(samples))
        cost.append((time.perf_counter() - started) / (len(samples) / RATE))
    dim = next(r for r in rows if r is not None).shape[0]
    matrix = np.stack([r if r is not None else np.zeros(dim, np.float32) for r in rows])
    np.save(data / f"embeddings-{model_path.stem}.npy", matrix)
    print(json.dumps({"ms_per_audio_second": round(1000 * float(np.mean(cost)), 2)}))


def score(data: Path, model_path: Path, fixed=None):
    meta = json.loads(str(np.load(data / "segments.npz", allow_pickle=True)["meta"]))
    emb = np.load(data / f"embeddings-{model_path.stem}.npy")
    secs = np.array([m["seconds"] for m in meta])
    kind = np.array([m["kind"] for m in meta])
    source = np.array([m["source"] for m in meta])
    readers = sorted({s for s, k in zip(source, kind) if k == "person"})
    usable = secs >= 1.0

    def voiceprints(consistency=None):
        prints, enrolled = {}, {}
        for reader in readers:
            chosen, total = [], 0.0
            for i in np.where((source == reader) & usable)[0]:
                chosen.append(i)
                total += secs[i]
                if total >= 30:
                    break

            def mean(index):
                weighted = (emb[index] * secs[index, None]).sum(0)
                return weighted / np.linalg.norm(weighted)

            voiceprint = mean(chosen)
            if consistency is not None:  # what Enrollment does
                voiceprint = mean([i for i in chosen if emb[i] @ voiceprint >= consistency])
            prints[reader], enrolled[reader] = voiceprint, set(chosen)
        return prints, enrolled

    def trials(prints, enrolled):
        genuine, impostor = [], {k: [] for k in ("person", "tv", "playback", "say")}
        for reader in readers:
            scores = emb @ prints[reader]
            for i in np.where(usable)[0]:
                if i in enrolled[reader]:
                    continue
                target = genuine if source[i] == reader else impostor[kind[i]]
                target.append((scores[i], secs[i]))
        as_array = lambda rows: np.array(rows).reshape(-1, 2)  # noqa: E731
        return as_array(genuine), {k: as_array(v) for k, v in impostor.items()}

    def equal_error(genuine, impostor):
        everyone = np.concatenate([v[:, 0] for v in impostor.values()])
        rates = [
            (abs(np.mean(genuine[:, 0] < t) - np.mean(everyone >= t)), t)
            for t in np.linspace(-0.2, 1.0, 1201)
        ]
        _, t = min(rates)
        return float(t), float((np.mean(genuine[:, 0] < t) + np.mean(everyone >= t)) / 2)

    genuine, impostor = trials(*voiceprints())
    threshold, _ = equal_error(genuine, impostor)
    genuine, impostor = trials(*voiceprints(consistency=threshold))
    threshold, eer = equal_error(genuine, impostor)

    def rates(t, low=0.0, high=99.0):
        def share(rows, above):
            rows = rows[(rows[:, 1] >= low) & (rows[:, 1] < high)]
            if not len(rows):
                return None
            hits = rows[:, 0] >= t if above else rows[:, 0] < t
            return round(float(np.mean(hits)), 4)

        return {
            "false_reject": share(genuine, False),
            **{f"false_accept_{k}": share(v, True) for k, v in impostor.items()},
        }

    picks = sorted({0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, round(threshold, 2), *([fixed] if fixed else [])})
    result = {
        "model": model_path.name,
        "equal_error_rate": round(eer, 4),
        "equal_error_threshold": round(threshold, 3),
        "trials": {"genuine": len(genuine), **{k: len(v) for k, v in impostor.items()}},
        "sweep": [{"threshold": t, **rates(t)} for t in picks],
        "by_duration": {
            f"{low}-{high}s": rates(fixed or threshold, low, high)
            for low, high in ((1, 2), (2, 4), (4, 99))
        },
        "under_one_second": {k: round(float(np.mean(secs[kind == k] < 1.0)), 3) for k in impostor},
    }
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    command, data = sys.argv[1], Path(sys.argv[2])
    if command == "prep":
        prep(data)
    elif command == "embed":
        embed(data, Path(sys.argv[3]))
    else:
        score(data, Path(sys.argv[3]), float(sys.argv[4]) if len(sys.argv) > 4 else None)
