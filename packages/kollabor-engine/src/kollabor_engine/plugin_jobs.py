"""Bounded local plugin install and update approval jobs."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)
_MAX_JOBS = 32
_JOB_TTL = 3600


class PluginJobs:
    def __init__(self, hub: Any, dispatcher: Any, controls: Any, engine_url: str | None = None):
        self.hub = hub
        self.dispatcher = dispatcher
        self.controls = controls
        self.engine_url = engine_url
        self.jobs: dict[str, dict] = {}
        self.tasks: set[asyncio.Task] = set()
        self.reaper: asyncio.Task | None = None
        self.closing = False

    def _track(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def finished(done: asyncio.Task) -> None:
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()  # Also retrieve errors if the client disconnected.

        task.add_done_callback(finished)
        return task

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(60)
            self._expire()

    def _expire(self) -> None:
        cutoff = time.monotonic() - _JOB_TTL
        for job_id, job in list(self.jobs.items()):
            if job["status"] not in {"staging", "installing"} and job["updated"] < cutoff:
                del self.jobs[job_id]
                self._track(asyncio.to_thread(self._cleanup, job))

    @staticmethod
    def _cleanup(job: dict) -> None:
        base = job.pop("base", None)
        if base is not None:
            try:
                shutil.rmtree(base)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Cannot clean plugin job staging directory: %s", type(exc).__name__)

    @staticmethod
    def _public(job: dict) -> dict:
        public = {"job_id": job["id"], "status": job["status"], "action": job["action"]}
        for key in ("commit", "files_sha256", "manifest", "source", "ref", "error", "plugin_id"):
            if key in job:
                public[key] = job[key]
        return public

    def get(self, job_id: str) -> dict:
        self._expire()
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Plugin job not found")
        return self._public(job)

    def start_install(self, source: str) -> dict:
        from kollabor_plugins.packages import parse_git_source

        try:
            parse_git_source(source)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid plugin Git source") from exc
        return self._start("install", source)

    def _start(self, action: str, source: str, **extra) -> dict:
        if self.closing:
            raise HTTPException(status_code=503, detail="Plugin manager is shutting down")
        self._expire()
        if len(self.jobs) >= _MAX_JOBS:
            raise HTTPException(status_code=429, detail="Too many plugin jobs; try again later")
        home = Path.home() / ".kollab"
        plugins = home / "plugins"
        if plugins.is_symlink():
            raise HTTPException(status_code=409, detail="Plugin directory cannot be a symlink")
        try:
            plugins.mkdir(parents=True, exist_ok=True)
            base = Path(tempfile.mkdtemp(prefix=".engine-plugin-job-", dir=plugins))
        except OSError as exc:
            raise HTTPException(status_code=503, detail="Cannot create plugin staging directory") from exc
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id, "action": action, "status": "staging", "updated": time.monotonic(),
            "base": base, "requested_source": source, **extra,
        }
        self.jobs[job_id] = job
        if self.reaper is None:
            self.reaper = asyncio.create_task(self._reap(), name="plugin-job-reaper")
        self._track(self._stage(job))
        return self._public(job)

    async def _stage(self, job: dict) -> None:
        from kollabor.version import get_runtime_version
        from kollabor_plugins.packages import stage_git_source

        try:
            staged = await asyncio.to_thread(
                stage_git_source, job["requested_source"], job["base"] / "package", get_runtime_version()
            )
            job["staged"] = staged
            for key in ("commit", "files_sha256", "manifest", "source", "ref"):
                job[key] = staged[key]
            job["plugin_id"] = staged["manifest"]["id"]
            job["status"] = "ready"
        except Exception as exc:
            logger.warning("Plugin staging failed: %s", type(exc).__name__)
            job["error"] = "Cannot stage plugin source; check the URL, ref, and plugin manifest"
            job["status"] = "error"
            await asyncio.to_thread(self._cleanup, job)
        finally:
            job["updated"] = time.monotonic()
            self.hub.publish_change()

    async def confirm(self, job_id: str, commit: str, digest: str) -> dict:
        if self.closing:
            raise HTTPException(status_code=503, detail="Plugin manager is shutting down")
        self._expire()
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Plugin job not found")
        if job["status"] != "ready":
            raise HTTPException(status_code=409, detail="Plugin job is not ready for confirmation")
        if commit != job["commit"] or digest != job["files_sha256"]:
            raise HTTPException(status_code=409, detail="Confirm the exact displayed commit and files hash")
        job["status"] = "installing"
        job["updated"] = time.monotonic()
        task = self._track(self._apply(job, commit, digest))
        return await asyncio.shield(task)

    async def _apply(self, job: dict, commit: str, digest: str) -> dict:
        from kollabor_plugins.packages import EXPECT_ABSENT, install_stage

        try:
            await asyncio.to_thread(
                install_stage, job["base"] / "package", job["staged"], commit, digest,
                expected_record=EXPECT_ABSENT,
            )
            job["status"] = "complete"
            return self._public(job)
        except ValueError as exc:
            job["status"] = "error"
            job["error"] = "Plugin approval or files changed; stage it again"
            raise HTTPException(status_code=409, detail=job["error"]) from exc
        except OSError as exc:
            job["status"] = "error"
            job["error"] = "Cannot install plugin; check the plugin directory and try again"
            raise HTTPException(status_code=503, detail=job["error"]) from exc
        except Exception as exc:
            logger.warning("Plugin installation failed: %s", type(exc).__name__)
            job["status"] = "error"
            job["error"] = "Cannot install plugin; stage it again"
            raise HTTPException(status_code=503, detail=job["error"]) from exc
        finally:
            job["updated"] = time.monotonic()
            await asyncio.to_thread(self._cleanup, job)
            self.hub.publish_change()

    async def close(self) -> None:
        self.closing = True
        if self.reaper is not None:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        for job in self.jobs.values():
            await asyncio.to_thread(self._cleanup, job)
