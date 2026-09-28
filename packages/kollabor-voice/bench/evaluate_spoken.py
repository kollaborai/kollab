"""Evaluate the frozen spoken-style policy; this script never fits weights."""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    import kollabor_voice
    import laya
    from kollabor_voice.control import manifest
    from kollabor_voice.laya_voice_model import VoiceIntentModel
    from kollabor_voice.laya_worker import predict_speech
    from kollabor_voice.speech_review import SPEECH_QUESTION

    spec = manifest()["classifiers"]["laya"]["speech_head"]
    head = Path(kollabor_voice.__file__).with_name(spec["name"])
    agent = VoiceIntentModel(laya.load(str(args.model)), head, SPEECH_QUESTION, spec)
    cold = time.monotonic() - started
    fixture = Path(__file__).with_name("spoken-style.json")
    cases = [
        c for c in json.loads(fixture.read_text())["cases"] if c["split"] == "heldout"
    ]
    results = []
    for case in cases:
        started = time.monotonic()
        decision = predict_speech(agent, case["text"])
        results.append(
            {
                **case,
                **decision.to_wire(),
                "elapsed_ms": 1000 * (time.monotonic() - started),
            }
        )
    measured = [r["elapsed_ms"] for r in results if r["provider"] == "laya"]
    report = {
        "boundary": (
            "Synthetic heldout spoken-style policy; encoding repair reused this split; "
            "no human naturalness acceptance"
        ),
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "head_sha256": hashlib.sha256(head.read_bytes()).hexdigest(),
        "cold_import_load_seconds": cold,
        "first_model_inference_ms": measured[0],
        "warm_model_mean_ms": statistics.mean(measured[1:]),
        "count": len(results),
        "deferred": sum(r["decision"] == "defer" for r in results),
        "deferred_suitable": sum(
            r["expected"] == "speak" and r["decision"] == "defer" for r in results
        ),
        "deferred_unsuitable": sum(
            r["expected"] == "rewrite" and r["decision"] == "defer" for r in results
        ),
        "suitable_allowed": sum(
            r["expected"] == "speak" and r["decision"] == "speak" for r in results
        ),
        "suitable_held": sum(
            r["expected"] == "speak" and r["decision"] != "speak" for r in results
        ),
        "unsuitable_allowed": sum(
            r["expected"] == "rewrite" and r["decision"] == "speak" for r in results
        ),
        "unsuitable_held": sum(
            r["expected"] == "rewrite" and r["decision"] != "speak" for r in results
        ),
        "results": results,
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
