"""Device-owned persistent worker: setup and inference never block audio capture."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path

from .control import MAX_REQUEST, VoiceError, manifest


class LayaWorker:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.log = None
        self.exchange_lock = asyncio.Lock()
        self.state = {"state": "off", "model": "typed-decisions"}
        self.setup_task = None

    def status(self):
        if (
            self.process
            and self.process.returncode is not None
            and self.state["state"] == "ready"
        ):
            self.state = {
                "state": "error",
                "detail": "Laya worker stopped; /voicemode retry",
            }
        return {
            **self.state,
            "pid": (
                self.process.pid
                if self.process and self.process.returncode is None
                else None
            ),
        }

    def prepare(self):
        if self.status()["state"] == "ready" or (
            self.setup_task and not self.setup_task.done()
        ):
            return
        self.state = {"state": "installing", "detail": "Preparing Laya classifier"}
        self.setup_task = asyncio.create_task(self._prepare(), name="voice-laya-setup")

    async def _prepare(self):
        from .assets import ensure_models
        from .bootstrap import ensure_runtime

        loop = asyncio.get_running_loop()

        def progress(**state):
            loop.call_soon_threadsafe(lambda: self.state.update(state))

        try:
            spec = manifest()["classifiers"]["laya"]
            python = await asyncio.to_thread(
                ensure_runtime,
                self.root,
                spec["runtime"],
                "import laya, torch, transformers",
                lambda state, detail: progress(state=state, detail=detail),
                "Preparing Laya classifier",
            )
            paths = await asyncio.to_thread(
                ensure_models, self.root, progress, {"laya": spec["bundle"]}
            )
            self.state = {
                "state": "warming",
                "detail": "Loading and warming Laya typed-decisions",
            }
            logs = self.root / "logs"
            logs.mkdir(exist_ok=True)
            self.log = (logs / "laya.log").open("ab")
            # The service environment already excludes credentials. No network
            # is needed in inference; every checkpoint file was verified above.
            env = {
                key: value
                for key, value in os.environ.items()
                if key in {"HOME", "PATH", "TMPDIR", "LANG", "LC_ALL"}
            }
            env.update(
                PYTHONPATH=str(Path(__file__).resolve().parent.parent),
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                TOKENIZERS_PARALLELISM="false",
            )
            self.process = await asyncio.create_subprocess_exec(
                str(python),
                "-m",
                "kollabor_voice.laya_worker",
                "--model",
                str(paths["laya"]),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=self.log,
                env=env,
                limit=MAX_REQUEST,
            )
            ready = json.loads(
                await asyncio.wait_for(self.process.stdout.readline(), 180)
            )
            if not ready.get("ready"):
                raise RuntimeError("Laya worker failed to initialize")
            self.state = {
                "state": "ready",
                "model": "typed-decisions",
                "cold_seconds": ready["cold_seconds"],
            }
        except asyncio.CancelledError:
            await self._stop_process()
            raise
        except Exception as exc:
            await self._stop_process()
            self.state = {
                "state": "error",
                "detail": f"Laya setup failed: {exc}. /voicemode retry or /voicemode classifier provider",
            }

    async def decide(self, records, context, transcript_context=None, context_lines=10):
        if len(records) > 16:
            raise VoiceError(
                "too_large", "Classifier batch exceeds 16 transcript records"
            )
        from .classifiers import MAX_CONTEXT_LINES

        if len(transcript_context or []) > MAX_CONTEXT_LINES:
            raise VoiceError("too_large", "Transcript context exceeds 50 lines")
        return await self._exchange(
            {
                "records": records,
                "context": context[-2000:],
                "transcript_context": transcript_context or [],
                "context_lines": context_lines,
            }
        )

    async def review_speech(self, text):
        return await self._exchange({"task": "speech", "text": text})

    async def _exchange(self, payload):
        if self.status()["state"] != "ready":
            raise VoiceError(
                "classifier_not_ready", "Laya is not ready; /voicemode retry"
            )
        async with self.exchange_lock:
            request_id = uuid.uuid4().hex
            try:
                data = json.dumps({"id": request_id, **payload}).encode() + b"\n"
                if len(data) > MAX_REQUEST:
                    raise VoiceError(
                        "too_large", "Classifier request exceeds the size limit"
                    )
                self.process.stdin.write(data)
                await self.process.stdin.drain()
                reply = json.loads(
                    await asyncio.wait_for(self.process.stdout.readline(), 10)
                )
                if reply.get("id") != request_id:
                    raise ValueError("Laya worker response identity differs")
                if reply.get("error"):
                    raise ValueError(reply["error"])
                return reply["result"]
            except BaseException:
                # A cancelled/late reply cannot be mistaken for the next request.
                await self._stop_process()
                self.state = {
                    "state": "error",
                    "detail": "Laya request stopped; /voicemode retry",
                }
                raise

    async def _stop_process(self):
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            await self.process.wait()
        self.process = None
        if self.log:
            self.log.close()
            self.log = None

    async def close(self):
        if self.setup_task and not self.setup_task.done():
            self.setup_task.cancel()
            await asyncio.gather(self.setup_task, return_exceptions=True)
        await self._stop_process()
