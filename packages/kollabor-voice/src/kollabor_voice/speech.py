"""The device service's selectable synthesis backend; playback stays in SpeechQueue."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import select
import subprocess
import threading
import uuid
from pathlib import Path

from .control import manifest
from .speech_settings import speech_errors, speech_key


class VibeVoiceWorker:
    def __init__(self, root, preferences, progress):
        from .assets import download, verified
        from .bootstrap import ensure_runtime

        spec = manifest()["tts"]["vibevoice"]
        python = ensure_runtime(
            root,
            spec["runtime"],
            "from vibevoice.modular.modeling_vibevoice_streaming_inference "
            "import VibeVoiceStreamingForConditionalGenerationInference; "
            "from vibevoice.processor.vibevoice_streaming_processor import VibeVoiceStreamingProcessor",
            lambda state, detail: progress(state=state, detail=detail),
            detail="Installing isolated VibeVoice support",
        )
        prompt = preferences["vibevoice_prompt"]
        if not prompt:
            item = spec["voices"][preferences["vibevoice_voice"]]
            path = root / "models/vibevoice-prompts" / spec["revision"] / item["name"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if not verified(path, item):
                download(
                    spec["preset_base_url"] + item["name"], path, item, lambda *_: None
                )
            prompt = str(path)
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "HOME",
                "PATH",
                "TMPDIR",
                "LANG",
                "USER",
                "LOGNAME",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "HF_HOME",
                "HF_HUB_CACHE",
            }
        }
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        with (logs / "vibevoice.log").open("ab") as log:
            self.process = subprocess.Popen(
                [str(python), str(Path(__file__).with_name("vibevoice_worker.py"))],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                env=env,
            )
        try:
            progress(state="warming", detail="Warming VibeVoice Realtime 0.5B")
            self.ready = self.exchange(
                {
                    "op": "prepare",
                    "config": {
                        **preferences,
                        "prompt_path": str(Path(prompt).expanduser()),
                        "model_revision": spec["model_revision"],
                        "default_model": spec["model"],
                    },
                },
                timeout=300,
            )
        except BaseException:
            self.close()
            raise

    def exchange(self, request, timeout=120):
        response = self._receive(self._send(request), timeout)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "VibeVoice inference failed"))
        return response

    def stream(self, text, timeout=120):
        """Yield float32 chunks while VibeVoice is still generating the rest."""
        import numpy as np

        identity = self._send({"op": "stream", "text": text})
        finished = False
        try:
            while True:
                response = self._receive(identity, timeout)
                if "chunk" not in response:
                    finished = True
                    if not response.get("ok"):
                        raise RuntimeError(
                            response.get("error", "VibeVoice inference failed")
                        )
                    return
                audio = np.frombuffer(
                    base64.b64decode(response["chunk"], validate=True), dtype="<f4"
                ).copy()
                if not len(audio) or not np.isfinite(audio).all():
                    raise RuntimeError("VibeVoice returned invalid waveform samples")
                yield audio
        finally:
            # Stopped early (a cancelled sentence): read the rest of this answer
            # so the next request never receives its chunks.
            while not finished:
                finished = "chunk" not in self._receive(identity, timeout)

    def _send(self, request):
        identity = uuid.uuid4().hex
        request = {**request, "id": identity}
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        self.process.stdin.flush()
        return identity

    def _receive(self, identity, timeout):
        if not select.select([self.process.stdout], [], [], timeout)[0]:
            self.close()
            raise TimeoutError("VibeVoice inference timed out")
        line = self.process.stdout.readline(16 * 1024 * 1024)
        if not line.endswith(b"\n"):
            raise RuntimeError("VibeVoice worker stopped or returned excessive audio")
        response = json.loads(line)
        if response.get("id") != identity:
            self.close()
            raise RuntimeError("VibeVoice response identity differs")
        return response

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process.stdin.close()
        self.process.stdout.close()


class SpeechEngine:
    def __init__(self, root, kokoro):
        self.root, self.kokoro = root, kokoro
        self.lock = threading.RLock()
        self.worker = None
        self.key = None
        self.preferences = None
        self.state = {"state": "off", "engine": None}

    def status(self):
        state = dict(self.state)
        if self.worker and self.worker.process.poll() is not None:
            state.update(
                state="error", detail="VibeVoice worker stopped"
            )
        return state

    def configure(self, force=False):
        from .classifiers import load_preferences

        with self.lock:
            preferences = load_preferences(self.root)
            key = speech_key(preferences)
            if not force and key == self.key:
                return
            self.key = key
            engine = preferences["tts_engine"]
            self.state = {
                "state": "loading",
                "engine": engine,
                "detail": "Preparing speech engine",
            }
            try:
                errors = speech_errors(preferences, check_files=True)
                if errors:
                    raise ValueError("; ".join(errors.values()))
                if self.worker:
                    self.worker.close()
                    self.worker = None
                if engine == "vibevoice":
                    self.worker = VibeVoiceWorker(
                        self.root, preferences, lambda **state: self.state.update(state)
                    )
                self.state = {
                    "state": "ready",
                    "engine": engine,
                    "voice": (
                        preferences["vibevoice_prompt"]
                        or preferences["vibevoice_voice"]
                        if engine == "vibevoice"
                        else preferences["kokoro_voice"]
                    ),
                    "pid": self.worker.process.pid if self.worker else None,
                    "device": self.worker.ready["device"] if self.worker else "cpu",
                    "detail": "Speech engine ready",
                }
                self.preferences = preferences
            except Exception as exc:
                self.state.update(state="error", detail=str(exc))
                raise

    def stream(self, text):
        """Yield (samples, rate) chunks; VibeVoice yields while it generates."""
        with self.lock:
            # A failed engine (timed out, worker died) starts again here, on the
            # next sentence, instead of failing every sentence after it.
            # ponytail: a crash-looping engine pays its load per sentence; add a
            # backoff if that shows up.
            self.configure(force=self.status()["state"] == "error")
            if self.status()["state"] != "ready":
                raise RuntimeError(self.status()["detail"])
            preferences = self.preferences
            if preferences["tts_engine"] == "vibevoice":
                rate = self.worker.ready["rate"]
                try:
                    with contextlib.closing(self.worker.stream(text)) as chunks:
                        for samples in chunks:
                            yield samples, rate
                except Exception as exc:
                    self.state.update(state="error", detail=str(exc))
                    raise
                return
            if (
                preferences["kokoro_voice"] == "af_heart"
                and preferences["kokoro_speed"] == 1
            ):
                yield self.kokoro(text)
            else:
                yield self.kokoro(
                    text,
                    voice=preferences["kokoro_voice"],
                    speed=preferences["kokoro_speed"],
                )

    def close(self):
        with self.lock:
            if self.worker:
                self.worker.close()
                self.worker = None
