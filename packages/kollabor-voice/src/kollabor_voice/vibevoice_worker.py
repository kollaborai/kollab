"""Isolated VibeVoice inference worker. Returns waveforms and never plays audio."""

from __future__ import annotations

import base64
import contextlib
import copy
import json
import sys


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
        kwargs = (
            {"revision": config["model_revision"]}
            if config["vibevoice_model"] == config["default_model"]
            else {}
        )
        self.processor = VibeVoiceStreamingProcessor.from_pretrained(
            config["vibevoice_model"], **kwargs
        )
        self.model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            config["vibevoice_model"],
            torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
            attn_implementation="sdpa",
            device_map=None,
            **kwargs,
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

    def synthesize(self, text):
        import numpy as np

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
        with self.torch.inference_mode():
            result = self.model.generate(
                **inputs,
                cfg_scale=self.cfg,
                tokenizer=self.processor.tokenizer,
                generation_config={"do_sample": False},
                verbose=False,
                all_prefilled_outputs=copy.deepcopy(self.prompt),
            )
        if not result.speech_outputs or result.speech_outputs[0] is None:
            raise RuntimeError("VibeVoice returned no waveform")
        audio = result.speech_outputs[0].detach().float().cpu().numpy().squeeze()
        if (
            audio.ndim != 1
            or not audio.size
            or audio.size > self.rate * 120
            or not np.isfinite(audio).all()
        ):
            raise RuntimeError("VibeVoice returned an invalid waveform")
        return audio.astype("<f4"), self.rate


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
                    engine.synthesize("Voice is ready to respond.")
                    response = {
                        "ok": True,
                        "device": engine.device,
                        "rate": engine.rate,
                    }
                elif request["op"] == "synthesize" and engine is not None:
                    samples, rate = engine.synthesize(request["text"])
                    response = {
                        "ok": True,
                        "rate": rate,
                        "audio": base64.b64encode(samples.tobytes()).decode(),
                    }
                else:
                    raise ValueError("Unknown speech request")
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        response["id"] = request.get("id")
        output.write(json.dumps(response) + "\n")
        output.flush()


if __name__ == "__main__":
    main()
