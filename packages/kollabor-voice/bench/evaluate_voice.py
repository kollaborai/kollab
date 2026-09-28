"""Evaluate the installed voice head without fitting or sending data to a provider."""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from kollabor_voice.control import manifest
from kollabor_voice.laya_worker import predict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).with_name("voice-acceptance.json"),
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    import kollabor_voice
    import laya
    from kollabor_voice.laya_voice_model import VoiceIntentModel

    head = manifest()["classifiers"]["laya"]["voice_head"]
    head_path = Path(kollabor_voice.__file__).with_name(head["name"])
    model = VoiceIntentModel(laya.load(str(args.model)), head_path)
    cold = time.monotonic() - started
    results = []
    for index, case in enumerate(json.loads(args.fixtures.read_text())["cases"]):
        records = [
            {
                "event_id": str(index),
                "owner_epoch": "benchmark",
                "text": case["text"],
                "language": "en",
                "started_at": "2026-09-26T00:00:00+00:00",
                "ended_at": "2026-09-26T00:00:01+00:00",
                "playback_overlap": [
                    {"reply_id": "benchmark-reply", "text": text}
                    for text in case.get("assistant_playback", [])
                ],
            }
        ]
        started = time.monotonic()
        decision = predict(model, records, case["context"])
        results.append(
            {
                **case,
                "decision": decision.decision,
                "confidence": decision.confidence,
                "elapsed_ms": 1000 * (time.monotonic() - started),
            }
        )
    automatic = [r for r in results if r["decision"] != "defer"]
    report = {
        "fixtures_sha256": hashlib.sha256(args.fixtures.read_bytes()).hexdigest(),
        "head_sha256": hashlib.sha256(head_path.read_bytes()).hexdigest(),
        "minimum_probability": manifest()["classifiers"]["laya"]["minimum_probability"],
        "cold_import_and_load_seconds": cold,
        "first_inference_ms": results[0]["elapsed_ms"],
        "warm_mean_ms": statistics.mean(r["elapsed_ms"] for r in results[1:]),
        "count": len(results),
        "automatic": len(automatic),
        "deferred_to_agent": len(results) - len(automatic),
        "automatic_correct": sum(r["decision"] == r["expected"] for r in automatic),
        "automatic_errors": sum(r["decision"] != r["expected"] for r in automatic),
        "results": results,
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
