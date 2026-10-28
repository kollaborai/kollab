"""Isolated VibeVoice inference worker. Streams waveform chunks and never plays audio."""

from __future__ import annotations

import base64
import contextlib
import copy
import json
import sys
import threading


class Engine:
    def __init__(self, config):
        import torch
        from vibevoice.modular.modeling_vibevoice_streaming_inference import (
            VibeVoiceStreamingForConditionalGenerationInference,
        )
        from vibevoice.processor.vibevoice_streaming_processor import (
            VibeVoiceStreamingProcessor,
        )

        self.torch = torch
        device = config["vibevoice_device"]
        if device == "auto":
            device = (
                "mps"
                if torch.backends.mps.is_available()
                else "cuda" if torch.cuda.is_available() else "cpu"
            )
        if device == "mps" and not torch.backends.mps.is_available():
            raise ValueError("MPS is unavailable; choose auto or cpu in /voice config")
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable; choose auto or cpu in /voice config")
        self.device, self.cfg = device, config["vibevoice_cfg"]
        model = config["vibevoice_model"]
        if model == config["default_model"]:
            # Pin Microsoft's files by fetching that revision and loading the local
            # copy: a revision kwarg would also reach the processor's Qwen tokenizer,
            # a repo where that sha does not exist.
            from huggingface_hub import snapshot_download

            model = snapshot_download(
                model,
                revision=config["model_revision"],
                allow_patterns=["*.json", "*.safetensors"],
            )
        self.processor = VibeVoiceStreamingProcessor.from_pretrained(model)
        self.model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            model,
            torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            attn_implementation="sdpa",
            device_map=None,
        ).to(
            device
        )
        self.model.eval()
        self.model.set_ddpm_inference_steps(num_steps=config["vibevoice_steps"])
        self.prompt = torch.load(
            config["prompt_path"], map_location=device, weights_only=False
        )
        self.rate = int(
            getattr(
                getattr(self.processor, "audio_processor", None), "sampling_rate", 24000
            )
        )

    def stream(self, text, emit):
        """Pass each audio chunk to emit while the rest is still generating."""
        import numpy as np
        from vibevoice.modular.streamer import AudioStreamer

        inputs = self.processor.process_input_with_cached_prompt(
            text=text,
            cached_prompt=self.prompt,
            padding=True,
            return_tensors="pt",
            return_attention_mask=True,
        )
        inputs = {
            key: value.to(self.device) if self.torch.is_tensor(value) else value
            for key, value in inputs.items()
        }
        streamer, stop, failed = AudioStreamer(batch_size=1), threading.Event(), []

        def generate():
            try:
                with self.torch.inference_mode():
                    self.model.generate(
                        **inputs,
                        cfg_scale=self.cfg,
                        tokenizer=self.processor.tokenizer,
                        generation_config={"do_sample": False},
                        verbose=False,
                        all_prefilled_outputs=copy.deepcopy(self.prompt),
                        audio_streamer=streamer,
                        return_speech=False,
                        stop_check_fn=stop.is_set,
                    )
            except BaseException as exc:  # noqa: BLE001 - reported to the service
                failed.append(exc)
            finally:
                streamer.end()

        thread = threading.Thread(target=generate, name="vibevoice", daemon=True)
        thread.start()
        total = 0
        try:
            for chunk in streamer.get_stream(0):
                audio = chunk.float().numpy().reshape(-1)
                total += audio.size
                if total > self.rate * 120 or not np.isfinite(audio).all():
                    raise RuntimeError("VibeVoice returned an invalid waveform")
                if audio.size:
                    emit(audio.astype("<f4"))
        finally:
            stop.set()  # a no-op once generation ended; halts it after an error
            thread.join()
        if failed:
            raise failed[0]
        if not total:
            raise RuntimeError("VibeVoice returned no waveform")


def main():
    output = sys.stdout
    engine = None
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if request["op"] == "prepare":
                    engine = Engine(request["config"])
                    engine.stream("Voice is ready to respond.", lambda _: None)
                    response = {
                        "ok": True,
                        "device": engine.device,
                        "rate": engine.rate,
                    }
                elif request["op"] == "stream" and engine is not None:
                    identity = request.get("id")

                    def emit(samples):
                        chunk = base64.b64encode(samples.tobytes()).decode()
                        output.write(
                            json.dumps({"id": identity, "chunk": chunk}) + "\n"
                        )
                        output.flush()

                    engine.stream(request["text"], emit)
                    response = {"ok": True, "done": True, "rate": engine.rate}
                else:
                    raise ValueError("Unknown speech request")
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        response["id"] = request.get("id")
        output.write(json.dumps(response) + "\n")
        output.flush()


if __name__ == "__main__":
    main()
