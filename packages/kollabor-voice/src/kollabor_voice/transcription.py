"""The device service's selectable speech-to-text engine.

Whisper base is always loaded in the service. Parakeet v3 runs in its own pinned
runtime and process; Whisper transcribes while Parakeet prepares or after it
stops, and the status says so.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import select
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from .control import manifest
from .speech_settings import STT_ENGINES

log = logging.getLogger(__name__)

# A Parakeet that failed is prepared again after this long. Whisper covers it.
RETRY_SECONDS = 300


class ParakeetWorker:
    """Parakeet TDT 0.6B v3 on MLX: one load, one JSON line per utterance."""

    def __init__(self, root, progress):
        from .assets import ensure_models
        from .bootstrap import ensure_runtime

        spec = manifest()["stt"]["parakeet"]
        python = ensure_runtime(
            root,
            spec["runtime"],
            "import mlx.core, parakeet_mlx",
            lambda state, detail: progress(state=state, detail=detail),
            detail="Installing isolated Parakeet v3 support",
        )
        paths = ensure_models(root, progress, {"parakeet-v3": spec["bundle"]})
        progress(state="warming", detail="Loading and warming Parakeet v3")
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"HOME", "PATH", "TMPDIR", "LANG", "LC_ALL"}
        }
        # Every weight file was verified above; inference needs no network.
        env.update(
            PYTHONPATH=str(Path(__file__).resolve().parent.parent),
            HF_HUB_OFFLINE="1",
        )
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        with (logs / "parakeet.log").open("ab") as log_file:
            self.process = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "kollabor_voice.parakeet_worker",
                    "--model",
                    str(paths["parakeet-v3"]),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_file,
                env=env,
            )
        try:
            self.ready = self._receive(None, 300)
            if not self.ready.get("ready"):
                raise RuntimeError(self.ready.get("error", "worker did not start"))
        except BaseException:
            self.close()
            raise

    def transcribe(self, audio, timeout=30):
        identity = uuid.uuid4().hex
        samples = np.ascontiguousarray(audio, dtype="<f4").tobytes()
        request = {"id": identity, "audio": base64.b64encode(samples).decode()}
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        self.process.stdin.flush()
        response = self._receive(identity, timeout)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "transcription failed"))
        return response["text"]

    def _receive(self, identity, timeout):
        # One reply per request, so nothing is ever buffered past this line.
        if not select.select([self.process.stdout], [], [], timeout)[0]:
            raise TimeoutError(f"no answer within {timeout}s")
        line = self.process.stdout.readline(1024 * 1024)
        if not line.endswith(b"\n"):
            raise RuntimeError("worker stopped")
        response = json.loads(line)
        if response.get("id") != identity:
            raise RuntimeError("response identity differs")
        return response

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


class TranscriptionEngine:
    def __init__(self, root, whisper):
        self.root, self.whisper = root, whisper
        self.lock = threading.Lock()  # held while the worker transcribes
        self.worker = None
        self.engine = None
        self.failed_at = None
        self.closed = False
        self.state = {"state": "ready", "engine": "whisper", "detail": ""}

    def status(self):
        return dict(self.state)

    def retry_due(self):
        return (
            self.engine == "parakeet"
            and self.failed_at is not None
            and time.monotonic() - self.failed_at >= RETRY_SECONDS
        )

    def configure(self, force=False):
        """Start the selected engine. Slow for Parakeet; run it off the loop."""
        from .speech_settings import load_speech_preferences

        engine = load_speech_preferences(self.root)["stt_engine"]
        if engine == self.engine and not force:
            return
        self.engine, self.failed_at, error, worker = engine, None, None, None
        if engine == "parakeet":

            def progress(**state):
                detail = state.get("detail", "Preparing Parakeet v3")
                if state.get("total"):
                    detail += f" {state['downloaded'] / state['total']:.0%}"
                self.state = {
                    "engine": engine,
                    **state,
                    "detail": f"{detail}. Whisper Base transcribes until it is ready",
                }

            progress(state="loading", detail="Preparing Parakeet v3")
            try:
                worker = ParakeetWorker(self.root, progress)
            except Exception as exc:
                log.exception("Parakeet v3 setup failed")
                error = f"Parakeet v3 setup failed: {exc}"
        with self.lock:
            old = self.worker
            self.worker = None if self.closed else worker
        if old is not None:
            old.close()
        if worker is not None and self.worker is not worker:
            worker.close()
        if error:
            self._fail(error)
        elif engine == "parakeet":
            self.state = {
                "state": "ready",
                "engine": engine,
                "device": worker.ready.get("device"),
                "cold_seconds": worker.ready.get("cold_seconds"),
                "detail": "Parakeet v3 ready",
            }
        else:
            self.state = {"state": "ready", "engine": engine, "detail": ""}

    def transcribe(self, audio, names=()):
        """(text, language); Parakeet reports no language, Whisper does."""
        started = time.monotonic()
        with self.lock:
            worker = self.worker
            if worker is not None:
                try:
                    text = worker.transcribe(audio)
                except Exception as exc:
                    log.exception("Parakeet v3 transcription failed")
                    self.worker = None
                    worker.close()
                    self._fail(f"Parakeet v3 stopped: {exc}")
                else:
                    self._log("parakeet", audio, started)
                    return text, None
        text, language = self.whisper(audio, names)
        self._log("whisper", audio, started)
        return text, language

    def _fail(self, detail):
        self.failed_at = time.monotonic()
        self.state = {
            "state": "error",
            "engine": self.engine,
            "detail": f"{detail}. Whisper Base transcribes until it starts again",
        }

    @staticmethod
    def _log(engine, audio, started):
        log.info(
            "%s transcribed %.1fs of speech in %.2fs",
            STT_ENGINES[engine],
            len(audio) / 16000,
            time.monotonic() - started,
        )

    def close(self):
        with self.lock:
            self.closed = True
            worker, self.worker = self.worker, None
        if worker:
            worker.close()
