"""Voice speech goes straight to the agent: no classifier code, ops or models remain (#301)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "packages" / "kollabor-voice" / "src" / "kollabor_voice"
SOURCE_DIRS = ("kollabor", "plugins", "packages")
SKIP = {"node_modules", ".venv", "dist", "__pycache__", "bench"}

MODULES = ("classifiers", "laya", "qwen", "laya_voice_model", "laya_worker", "qwen_worker")
OPS = ("classifier_prepare", "classifier_submit", "classifier_result",
       "speech_review_submit", "speech_review_result")
IMPORT = re.compile(
    r"(?:from|import)\s+(?:kollabor_voice)?\.?\s*(?:kollabor_voice\.)?"
    r"(?:%s)\b|from\s+\.(?:%s)\s+import" % ("|".join(MODULES), "|".join(MODULES))
)
OTHER = ("voice_classifier_altview", "voice-classifier", "voice-intent-v1", "spoken-style-v1")


def _sources():
    for top in SOURCE_DIRS:
        for path in (ROOT / top).rglob("*"):
            if path.suffix in {".py", ".ts", ".tsx", ".json"} and not SKIP & set(path.parts):
                yield path


def test_classifier_modules_and_models_are_gone():
    for name in MODULES:
        assert not (VOICE / f"{name}.py").exists(), name
    assert not list(VOICE.glob("*.safetensors"))
    assert not (ROOT / "plugins/altview/voice_classifier_altview.py").exists()


def test_manifest_has_no_classifier_entry():
    manifest = json.loads((VOICE / "manifest.json").read_text())
    assert "classifiers" not in manifest
    assert "livekit" in " ".join(manifest["runtime"])
    text = json.dumps(manifest).lower()
    assert "laya" not in text and "qwen" not in text


def test_nothing_imports_or_names_the_classifier_system():
    offenders = []
    for path in _sources():
        if path == Path(__file__):
            continue
        text = path.read_text(errors="ignore")
        for pattern in (IMPORT,):
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: import")
        for word in (*OPS, *OTHER):
            if word in text:
                offenders.append(f"{path.relative_to(ROOT)}: {word}")
    assert not offenders, offenders
