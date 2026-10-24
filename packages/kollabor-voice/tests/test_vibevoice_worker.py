"""The VibeVoice worker pins Microsoft's model files without pinning its Qwen tokenizer."""

import sys
from types import ModuleType, SimpleNamespace

from kollabor_voice.vibevoice_worker import Engine

CONFIG = {
    "vibevoice_device": "cpu",
    "vibevoice_cfg": 1.5,
    "vibevoice_steps": 5,
    "prompt_path": "/voices/carter.pt",
    "default_model": "microsoft/VibeVoice-Realtime-0.5B",
    "model_revision": "6bce5f0",
}


def stub_runtime(monkeypatch):
    """torch, vibevoice and huggingface_hub fakes that record every load."""
    calls = {}

    class Loaded:
        def to(self, device):
            return self

        def eval(self):
            pass

        def set_ddpm_inference_steps(self, num_steps):
            pass

    def loader(name):
        def from_pretrained(path, **kwargs):
            calls[name] = (path, kwargs)
            return Loaded()

        return SimpleNamespace(from_pretrained=from_pretrained)

    def snapshot_download(repo, *, revision, allow_patterns):
        calls["snapshot"] = (repo, revision)
        return f"/hub/{repo}@{revision}"

    torch = ModuleType("torch")
    torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
    torch.cuda = SimpleNamespace(is_available=lambda: False)
    torch.float32 = torch.bfloat16 = object()
    torch.load = lambda *args, **kwargs: None
    modeling = ModuleType("vibevoice.modular.modeling_vibevoice_streaming_inference")
    modeling.VibeVoiceStreamingForConditionalGenerationInference = loader("model")
    processing = ModuleType("vibevoice.processor.vibevoice_streaming_processor")
    processing.VibeVoiceStreamingProcessor = loader("processor")
    hub = ModuleType("huggingface_hub")
    hub.snapshot_download = snapshot_download
    for name, module in {
        "torch": torch,
        "vibevoice": ModuleType("vibevoice"),
        "vibevoice.modular": ModuleType("vibevoice.modular"),
        "vibevoice.processor": ModuleType("vibevoice.processor"),
        modeling.__name__: modeling,
        processing.__name__: processing,
        "huggingface_hub": hub,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_default_model_loads_the_pinned_snapshot_with_no_revision_kwarg(monkeypatch):
    calls = stub_runtime(monkeypatch)

    Engine({**CONFIG, "vibevoice_model": CONFIG["default_model"]})

    assert calls["snapshot"] == ("microsoft/VibeVoice-Realtime-0.5B", "6bce5f0")
    pinned = "/hub/microsoft/VibeVoice-Realtime-0.5B@6bce5f0"
    # a revision kwarg would reach the processor's Qwen tokenizer, where it does not exist
    assert calls["processor"] == (pinned, {})
    assert calls["model"][0] == pinned and "revision" not in calls["model"][1]


def test_local_model_directory_loads_as_given(monkeypatch):
    calls = stub_runtime(monkeypatch)

    Engine({**CONFIG, "vibevoice_model": "/models/VibeVoice-Streaming-0.5B"})

    assert "snapshot" not in calls
    assert calls["processor"] == ("/models/VibeVoice-Streaming-0.5B", {})
