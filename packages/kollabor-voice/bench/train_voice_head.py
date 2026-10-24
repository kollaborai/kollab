"""Train the small voice intent head; never use evaluation labels for fitting.

Run in the pinned Laya environment with kollabor_voice on PYTHONPATH. The frozen
Laya encoder is shared with runtime inference. Training consumes only the train
split; hyperparameters use calibration only. Development/heldout/acceptance data
are evaluation artifacts, not inputs to model selection.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import laya
import numpy as np
import torch
from kollabor_voice.classifiers import LAYA_QUESTION
from kollabor_voice.laya_voice_model import features
from safetensors.torch import save_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--task", choices=["intent", "speech"], default="intent")
    parser.add_argument(
        "--fixtures", type=Path, default=Path(__file__).with_name("voice-intent.json")
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.task == "speech":
        from kollabor_voice.speech_review import SPEECH_QUESTION

        questions, positive = SPEECH_QUESTION, "speak"
    else:
        questions, positive = LAYA_QUESTION, "respond"
    cases = json.loads(args.fixtures.read_text())["cases"]
    started = time.monotonic()
    agent = laya.load(str(args.model))
    load_seconds = time.monotonic() - started
    vectors, durations = [], []
    for case in cases:
        started = time.monotonic()
        vectors.append(
            features(
                agent,
                (
                    {"text": case["text"]}
                    if args.task == "speech"
                    else {"text": case["text"], "recent_conversation": case["context"]}
                ),
                questions,
            )
            .cpu()
            .numpy()
            .astype(np.float64)
        )
        durations.append(1000 * (time.monotonic() - started))
    x = np.stack(vectors)
    train = np.array([c["split"] == "train" for c in cases])
    calibration = np.array([c["split"] == "calibration" for c in cases])
    yt = np.array(
        [1 if c["expected"] == positive else -1 for c in cases if c["split"] == "train"]
    )
    yc = np.array(
        [c["expected"] == positive for c in cases if c["split"] == "calibration"]
    )
    mean = x[train].mean(0)
    x -= mean
    x /= np.linalg.norm(x, axis=1, keepdims=True).clip(min=1e-12)
    centres = x[train]
    distances = ((x[:, None, :] - centres[None, :, :]) ** 2).sum(-1)
    candidates = []
    for gamma in (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64):
        kernel = np.exp(-gamma * distances)
        for regularization in (0.001, 0.01, 0.1, 1, 10):
            alpha = np.linalg.solve(
                kernel[train] + regularization * np.eye(train.sum()), yt
            )
            scores = kernel @ alpha
            for temperature in (0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2):
                p = 1 / (
                    1 + np.exp(-np.clip(scores[calibration] / temperature, -40, 40))
                )
                loss = float(
                    -np.mean(yc * np.log(p + 1e-9) + (~yc) * np.log(1 - p + 1e-9))
                )
                candidates.append(
                    (loss, gamma, regularization, temperature, alpha, scores)
                )
    loss, gamma, regularization, temperature, alpha, scores = min(
        candidates, key=lambda c: c[0]
    )
    save_file(
        {
            "mean": torch.tensor(mean, dtype=torch.float32),
            "centres": torch.tensor(centres, dtype=torch.float32),
            "alpha": torch.tensor(alpha / temperature, dtype=torch.float32),
        },
        str(args.out),
    )
    probability = 1 / (1 + np.exp(-np.clip(scores / temperature, -40, 40)))
    report = {
        "method": "Frozen Laya typed-decisions encoder with a trained radial-basis decision head",
        "task": args.task,
        "question_sha256": hashlib.sha256(
            json.dumps(questions, sort_keys=True).encode()
        ).hexdigest(),
        "fixtures_sha256": hashlib.sha256(args.fixtures.read_bytes()).hexdigest(),
        "head_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
        "gamma": gamma,
        "regularization": regularization,
        "temperature": temperature,
        "calibration_log_loss": loss,
        "model_load_seconds_excluding_imports": load_seconds,
        "first_inference_ms": durations[0],
        "warm_mean_ms": float(np.mean(durations[1:])),
        "splits": {},
    }
    for split in ("train", "calibration", "development", "heldout"):
        indices = [i for i, c in enumerate(cases) if c["split"] == split]
        y = np.array([cases[i]["expected"] == positive for i in indices])
        p = probability[indices]
        automatic = np.maximum(p, 1 - p) >= 0.8
        report["splits"][split] = {
            "count": len(indices),
            "correct": int(np.sum((p >= 0.5) == y)),
            "automatic": int(automatic.sum()),
            "abstentions": int((~automatic).sum()),
            "automatic_errors": int(np.sum(((p >= 0.5) != y) & automatic)),
        }
    args.out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
