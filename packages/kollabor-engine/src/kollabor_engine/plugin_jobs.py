"""Bounded local plugin install and update approval jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)
_MAX_JOBS = 32
_MAX_STAGING = 4
_JOB_TTL = 3600
_CLEANUP_RETRY_DELAY = 60
_STAGE_TIMEOUT = 120
_MANIFEST_REVIEW_LIMIT_ERROR = (
    "Plugin manifest exceeds the review limits (128 services, 64 secrets per service, 256 uses)"
)


class PluginJobs:
    def __init__(self, hub: Any, dispatcher: Any, controls: Any, engine_url: str | None = None):
        self.hub = hub
        self.dispatcher = dispatcher
        self.controls = controls
        self.engine_url = engine_url
        self.jobs: dict[str, dict] = {}
        self.expired_jobs: dict[str, dict] = {}
        self.expiring_jobs: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.staging_tasks: set[asyncio.Task] = set()
        self.stage_slots = asyncio.Semaphore(_MAX_STAGING)
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
            cancellation_running = job["status"] == "cancelling" and job.get("cancel_task") is not None
            if (
                job["status"] not in {"staging", "installing"}
                and not cancellation_running
                and job["updated"] < cutoff
            ):
                del self.jobs[job_id]
                self.expired_jobs[job_id] = job
        for job_id, job in list(self.expired_jobs.items()):
            if (
                job_id not in self.expiring_jobs
                and job.get("_cleanup_retry_at", 0) <= time.monotonic()
            ):
                self.expiring_jobs.add(job_id)
                self._track(self._cleanup_expired(job_id, job))

    async def _cleanup_expired(self, job_id: str, job: dict) -> None:
        try:
            cleaned, interrupted = await self._run_cleanup(job)
        finally:
            self.expiring_jobs.discard(job_id)
        if cleaned and self.expired_jobs.get(job_id) is job:
            del self.expired_jobs[job_id]
        elif not cleaned:
            job["_cleanup_retry_at"] = time.monotonic() + _CLEANUP_RETRY_DELAY
        if interrupted:
            raise asyncio.CancelledError

    @staticmethod
    def _cleanup(job: dict) -> bool:
        base = job.get("base")
        if base is None:
            return True
        try:
            shutil.rmtree(base)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Cannot clean plugin job staging directory: %s", type(exc).__name__)
            return False
        job.pop("base", None)
        return True

    @staticmethod
    def _public(job: dict) -> dict:
        public = {"job_id": job["id"], "status": job["status"], "action": job["action"]}
        for key in (
            "commit", "files_sha256", "manifest", "source", "ref", "error", "plugin_id",
            "previous_commit", "changed_folders", "warning",
        ):
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

    def start_update(self, plugin_id: str) -> dict:
        from kollabor_plugins.packages import _ID, _read_registry, parse_git_source

        if not _ID.fullmatch(plugin_id):
            raise HTTPException(status_code=404, detail="Installed plugin not found")
        home = Path.home() / ".kollab"
        try:
            record = _read_registry(home / "plugins.json")["plugins"].get(plugin_id)
            if not isinstance(record, dict):
                raise HTTPException(status_code=404, detail="Installed plugin not found")
            source, ref = record.get("source"), record.get("ref")
            if not isinstance(source, str) or (ref is not None and not isinstance(ref, str)):
                raise ValueError("Invalid installed plugin source")
            source = source + ("#" + ref if ref else "")
            parse_git_source(source)
            root = home / "plugins" / plugin_id
            if root.is_symlink() or not root.is_dir():
                raise ValueError("Invalid installed plugin directory")
        except HTTPException:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=409, detail="Cannot safely update this plugin") from exc
        return self._start("update", source, expected_record=record, target_id=plugin_id)

    def _start(self, action: str, source: str, **extra) -> dict:
        if self.closing:
            raise HTTPException(status_code=503, detail="Plugin manager is shutting down")
        self._expire()
        if len(self.jobs) + len(self.expired_jobs) >= _MAX_JOBS:
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
        task = self._track(self._stage(job))
        job["stage_task"] = task
        self.staging_tasks.add(task)
        task.add_done_callback(self.staging_tasks.discard)
        return self._public(job)

    @staticmethod
    def _snapshot(folder: Path) -> dict[str, str]:
        files = {}
        for path in folder.rglob("*"):
            if path.is_symlink():
                raise ValueError("Plugin folder contains a symlink")
            if path.is_file():
                with path.open("rb") as stream:
                    files[path.relative_to(folder).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        return files

    @classmethod
    def _update_preview(cls, job: dict, staged: dict) -> dict:
        from kollabor_plugins.packages import _read_registry, files_sha256

        home = Path.home() / ".kollab"
        plugin_id = job["target_id"]
        if staged["manifest"]["id"] != plugin_id:
            raise ValueError("Updated plugin id does not match the installed plugin")
        if _read_registry(home / "plugins.json")["plugins"].get(plugin_id) != job["expected_record"]:
            raise ValueError("Plugin registry changed during staging")
        root = home / "plugins" / plugin_id
        if root.is_symlink() or not root.is_dir() or files_sha256(root) != job["expected_record"].get("files_sha256"):
            raise ValueError("Installed plugin files changed")
        before, after = cls._snapshot(root), cls._snapshot(job["base"] / "package")
        changed = sorted({
            str(Path(name).parent)
            for name in before.keys() | after.keys() if before.get(name) != after.get(name)
        })
        return {"previous_commit": job["expected_record"].get("commit"), "changed_folders": changed}

    @staticmethod
    async def _stop_staging_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        # Kill this job's private process group together. Waiting for the worker
        # alone after SIGTERM could leave a Git/SSH child that ignores SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.communicate()

    @classmethod
    async def _run_staging(cls, source: str, stage: Path, update: dict | None = None) -> tuple[dict, dict]:
        # This is a short-lived Python helper, not a second Kollab entrypoint or
        # agent. Its process group contains only this job's Git/SSH children.
        payload = json.dumps({"source": source, "stage": str(stage), "update": update}).encode("utf-8")
        if len(payload) > 1024 * 1024:
            raise ValueError("Plugin staging request is too large")
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        creation = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-m", "kollabor_engine.plugin_stage",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True, env=env,
        ))
        try:
            process = await asyncio.shield(creation)
        except asyncio.CancelledError:
            process = await creation
            await cls._stop_staging_process(process)
            raise
        try:
            output, _ = await asyncio.wait_for(process.communicate(payload), timeout=_STAGE_TIMEOUT)
        except BaseException:
            await cls._stop_staging_process(process)
            raise
        if process.returncode == 2:
            raise ValueError(_MANIFEST_REVIEW_LIMIT_ERROR)
        if process.returncode != 0 or len(output) > 1024 * 1024:
            raise ValueError("Plugin staging worker failed")
        result = json.loads(output)
        if (
            not isinstance(result, dict) or result.get("ok") is not True
            or not isinstance(result.get("result"), dict) or not isinstance(result.get("preview"), dict)
        ):
            raise ValueError("Invalid plugin staging result")
        return result["result"], result["preview"]

    async def _run_cleanup(self, job: dict) -> tuple[bool, bool]:
        # Keep cleanup as an executor Future, not a Task: event-loop shutdown
        # cancels Tasks globally, while shutdown_default_executor waits for
        # this worker to finish.
        cleanup = asyncio.get_running_loop().run_in_executor(None, self._cleanup, job)
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # A shutdown cancellation must not publish a terminal status
                # while the worker thread is still removing files.
                interrupted = True
            except Exception:
                break
        try:
            cleaned = cleanup.result() is not False
        except Exception as exc:
            logger.warning("Cannot clean plugin job staging directory: %s", type(exc).__name__)
            cleaned = False
        return cleaned, interrupted

    async def _cleanup_staging(self, job: dict) -> bool:
        cleaned, interrupted = await self._run_cleanup(job)
        if not cleaned:
            job["warning"] = "Plugin staging files could not be removed"
        return interrupted

    async def _stage(self, job: dict) -> None:
        try:
            update = None
            if job["action"] == "update":
                update = {"target_id": job["target_id"], "expected_record": job["expected_record"]}
            async with self.stage_slots:
                staged, preview = await self._run_staging(job["requested_source"], job["base"] / "package", update)
            job.update(preview)
            job["staged"] = staged
            for key in ("commit", "files_sha256", "manifest", "source", "ref"):
                job[key] = staged[key]
            job["plugin_id"] = staged["manifest"]["id"]
            job["status"] = "ready"
        except asyncio.CancelledError:
            await self._cleanup_staging(job)
            if job["status"] != "cancelling":
                job["status"] = "cancelled"
            raise
        except Exception as exc:
            logger.warning("Plugin staging failed: %s", type(exc).__name__)
            job["error"] = (
                _MANIFEST_REVIEW_LIMIT_ERROR
                if str(exc) == _MANIFEST_REVIEW_LIMIT_ERROR
                else "Cannot stage plugin source; check the URL, ref, and plugin manifest"
            )
            interrupted = await self._cleanup_staging(job)
            if job["status"] != "cancelling":  # a cancel that arrived during cleanup owns the state
                job["status"] = "error"
            if interrupted:
                raise asyncio.CancelledError
        finally:
            job["updated"] = time.monotonic()
            self.hub.publish_change()

    async def cancel(self, job_id: str) -> dict:
        if self.closing:
            raise HTTPException(status_code=503, detail="Plugin manager is shutting down")
        self._expire()
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Plugin job not found")
        status = job["status"]
        if status in {"cancelling", "cancelled"} and job.get("cancel_task") is not None:
            return await asyncio.shield(job["cancel_task"])
        if status == "cancelled" and "base" not in job:
            return self._public(job)
        if status not in {"staging", "ready", "cancelling", "cancelled"}:
            raise HTTPException(status_code=409, detail="Plugin job cannot be cancelled")
        # Terminal "cancelled" is published only after the staged files are gone (#1373).
        job["status"] = "cancelling"
        job["updated"] = time.monotonic()
        stage_task = job.get("stage_task")
        if stage_task is not None and not stage_task.done():
            stage_task.cancel()

        async def finish() -> dict:
            try:
                if stage_task is not None:
                    await asyncio.gather(stage_task, return_exceptions=True)
                cleaned, interrupted = await self._run_cleanup(job)
                if cleaned:
                    job.pop("warning", None)
                    job["status"] = "cancelled"
                else:
                    job["warning"] = "Plugin staging files could not be removed"
                job["updated"] = time.monotonic()
                self.hub.publish_change()
                if interrupted:
                    raise asyncio.CancelledError
                if not cleaned:
                    raise HTTPException(status_code=503, detail="Cannot remove staged plugin files; try again")
                return self._public(job)
            finally:
                job.pop("cancel_task", None)

        task = self._track(finish())
        job["cancel_task"] = task
        return await asyncio.shield(task)

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

    @staticmethod
    def _install_update(job: dict, commit: str, digest: str) -> None:
        from kollabor_plugins.cli import _locked_service_files, _stop_for_change
        from kollabor_plugins.packages import _read_registry, files_sha256, install_stage, verify_stage
        from kollabor_plugins.services import _recorded_process_alive

        from .routes.plugins import _package_context, _with_inactive_runs

        home = Path.home() / ".kollab"
        plugin_id = job["target_id"]
        root = home / "plugins" / plugin_id
        expected = job["expected_record"]
        stage = job["base"] / "package"
        verify_stage(stage, job["staged"], commit, digest)
        if _read_registry(home / "plugins.json")["plugins"].get(plugin_id) != expected:
            raise ValueError("Plugin registry changed during approval")
        if root.is_symlink() or not root.is_dir() or files_sha256(root) != expected.get("files_sha256"):
            raise ValueError("Installed plugin files changed")
        context = _package_context(plugin_id)
        if context is None:
            raise ValueError("Installed plugin is unavailable")
        old_manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
        old_services = old_manifest.get("services", {})
        new_services = job["staged"]["manifest"].get("services", {})
        if not isinstance(old_services, dict) or not isinstance(new_services, dict):
            raise ValueError("Invalid plugin services")
        run = home / "plugin-data" / plugin_id / "run"

        def swap() -> None:
            job["restart_services"] = [
                name for name in old_services if _recorded_process_alive(run / f"{name}.pid")
            ]
            _stop_for_change(home, plugin_id, old_services)
            with _locked_service_files(home, plugin_id, {**old_services, **new_services}):
                install_stage(stage, job["staged"], commit, digest, expected_record=expected)

        _with_inactive_runs(context, swap)

    async def _restart_after_change(self, job: dict, *, completed: bool) -> None:
        from kollabor_plugins.services import control_service, supervisor_tick

        plugin_id = job["plugin_id"]
        options = {"engine_url": self.engine_url} if self.engine_url else {}
        for name in job.get("restart_services", []):
            try:
                await asyncio.to_thread(control_service, plugin_id, name, "start", **options)
            except Exception as exc:
                logger.warning("Plugin service restart failed: %s", type(exc).__name__)
                job["warning"] = "A previously running service could not be restarted; check its status"
        if completed:
            try:
                await asyncio.to_thread(supervisor_tick, **options)
            except Exception as exc:
                logger.warning("Plugin service supervisor failed: %s", type(exc).__name__)
                job["warning"] = "Plugin installed, but its services could not be started; check their status"

    async def _apply(self, job: dict, commit: str, digest: str) -> dict:
        from kollabor_plugins.packages import EXPECT_ABSENT, install_stage

        changing = False
        terminal_status: str | None = None
        try:
            if job["action"] == "update":
                await self.dispatcher.begin_change(job["target_id"])
                changing = True
                await self.controls.cancel_plugin(job["target_id"])
                await asyncio.to_thread(self._install_update, job, commit, digest)
            else:
                await asyncio.to_thread(
                    install_stage, job["base"] / "package", job["staged"], commit, digest,
                    expected_record=EXPECT_ABSENT,
                )
            terminal_status = "complete"
        except ValueError as exc:
            terminal_status = "error"
            job["error"] = "Plugin approval, files, or active runs changed; check plugin status and stage again"
            raise HTTPException(status_code=409, detail=job["error"]) from exc
        except OSError as exc:
            terminal_status = "error"
            job["error"] = "Cannot install plugin; check the plugin directory and try again"
            raise HTTPException(status_code=503, detail=job["error"]) from exc
        except Exception as exc:
            logger.warning("Plugin installation failed: %s", type(exc).__name__)
            terminal_status = "error"
            job["error"] = "Cannot install plugin; stage it again"
            raise HTTPException(status_code=503, detail=job["error"]) from exc
        finally:
            try:
                if changing:
                    await self.dispatcher.end_change(job["target_id"])
                if terminal_status == "complete" or job.get("restart_services"):
                    await self._restart_after_change(
                        job, completed=terminal_status == "complete"
                    )
            finally:
                cleaned, interrupted = await self._run_cleanup(job)
                if not cleaned:
                    warning = "Plugin staging files could not be removed"
                    previous_warning = job.get("warning")
                    job["warning"] = (
                        f"{previous_warning}; {warning}" if previous_warning else warning
                    )
                if terminal_status is not None:
                    job["status"] = terminal_status
                job["updated"] = time.monotonic()
                self.hub.publish_change()
                if interrupted:
                    raise asyncio.CancelledError
        return self._public(job)

    async def close(self) -> None:
        self.closing = True
        if self.reaper is not None:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        for task in list(self.staging_tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        for job in list(self.jobs.values()):
            await asyncio.to_thread(self._cleanup, job)
        for job_id, job in list(self.expired_jobs.items()):
            if await asyncio.to_thread(self._cleanup, job):
                self.expired_jobs.pop(job_id, None)
