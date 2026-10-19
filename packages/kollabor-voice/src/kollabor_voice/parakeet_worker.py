"""Isolated Parakeet v3 worker. One load from verified local weights, JSON lines on stdio."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    output = sys.stdout

    def reply(response):
        output.write(json.dumps(response) + "\n")
        output.flush()

    try:
        # Library chatter goes to the log, never into the protocol.
        with contextlib.redirect_stdout(sys.stderr):
            started = time.monotonic()
            import mlx.core as mx
            import numpy as np
            from parakeet_mlx import from_pretrained
            from parakeet_mlx.audio import get_logmel

            model = from_pretrained(args.model)

            def transcribe(audio):
                mel = get_logmel(mx.array(audio), model.preprocessor_config)
                return model.generate(mel)[0].text.strip()

            # The first full-size decode costs ~1.5 s once. Pay it before ready,
            # at the segmenter's 8 s cap so no utterance is longer.
            transcribe(np.zeros(8 * 16000, dtype=np.float32))
            # MLX keeps freed buffers: the 2.5 GB float32 load, then up to 1 GB
            # per decode. Hand them back (5.1 GB resident -> 1.6 GB measured).
            mx.set_cache_limit(256 * 2**20)
            mx.clear_cache()
        reply(
            {
                "ready": True,
                "cold_seconds": round(time.monotonic() - started, 2),
                "device": str(mx.default_device()),
            }
        )
    except Exception as exc:
        reply({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            audio = np.frombuffer(
                base64.b64decode(request["audio"], validate=True), dtype="<f4"
            )
            if not len(audio) or not np.isfinite(audio).all():
                raise ValueError("Invalid audio samples")
            with contextlib.redirect_stdout(sys.stderr):
                response = {"ok": True, "text": transcribe(audio)}
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        response["id"] = request.get("id")
        reply(response)


if __name__ == "__main__":
    main()
