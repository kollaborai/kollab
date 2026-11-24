"""Staging jobs publish terminal state only after cleanup finishes (#1373)."""

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from kollabor_engine.plugin_jobs import PluginJobs


class _Hub:
    def publish_change(self):
        pass


def _job_manager():
    manager = PluginJobs(_Hub(), dispatcher=None, controls=None)
    job = {
        "id": "issue-1373",
        "action": "install",
        "status": "staging",
        "updated": time.monotonic(),
        "base": Path("unused-staging-path"),
        "requested_source": "https://example.invalid/plugin",
    }
    manager.jobs[job["id"]] = job
    return manager, job


def _gate_cleanup(monkeypatch, manager, job, *, result=None, run_cleanup=False):
    started = threading.Event()
    release = threading.Event()
    cleanup = manager._cleanup

    def controlled_cleanup(current_job):
        if current_job is job:
            started.set()
            release.wait()
            if run_cleanup:
                return cleanup(current_job)
            return result
        return cleanup(current_job)

    monkeypatch.setattr(manager, "_cleanup", controlled_cleanup)
    return started, release


async def _wait_for_thread_event(event):
    deadline = asyncio.get_running_loop().time() + 1
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.001)
    assert event.is_set()


@pytest.mark.asyncio
async def test_error_status_waits_for_staging_cleanup(monkeypatch):
    manager, job = _job_manager()

    async def fail_staging(*_args):
        raise ValueError("staging failed")

    monkeypatch.setattr(manager, "_run_staging", fail_staging)
    cleanup_started, finish_cleanup = _gate_cleanup(monkeypatch, manager, job)
    task = asyncio.create_task(manager._stage(job))
    try:
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "staging"
    finally:
        finish_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)

    assert manager.get(job["id"])["status"] == "error"


@pytest.mark.asyncio
async def test_shutdown_waits_for_cancelled_staging_cleanup(monkeypatch):
    manager, job = _job_manager()
    staging_started = asyncio.Event()
    hold_staging = asyncio.Event()

    async def hold_staging_open(*_args):
        staging_started.set()
        await hold_staging.wait()

    monkeypatch.setattr(manager, "_run_staging", hold_staging_open)
    cleanup_started, finish_cleanup = _gate_cleanup(monkeypatch, manager, job)
    task = manager._track(manager._stage(job))
    manager.staging_tasks.add(task)
    task.add_done_callback(manager.staging_tasks.discard)
    shutdown = None
    try:
        await asyncio.wait_for(staging_started.wait(), timeout=1)
        shutdown = asyncio.create_task(manager.close())
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "staging"
        assert not shutdown.done()
        task.cancel()
        await asyncio.sleep(0)
        assert manager.get(job["id"])["status"] == "staging"
        assert not shutdown.done()
        finish_cleanup.set()
        await shutdown
    finally:
        finish_cleanup.set()
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
        if not task.done():
            task.cancel()
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert manager.get(job["id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_staging_failure_status_is_published_after_cleanup_fails(monkeypatch, caplog):
    manager, job = _job_manager()

    async def fail_staging(*_args):
        raise ValueError("staging failed")

    def fail_cleanup(_job):
        raise PermissionError("staging directory is locked")

    monkeypatch.setattr(manager, "_run_staging", fail_staging)
    monkeypatch.setattr(manager, "_cleanup", fail_cleanup)
    cleanup_started, finish_cleanup = _gate_cleanup(
        monkeypatch, manager, job, run_cleanup=True
    )
    task = asyncio.create_task(manager._stage(job))
    try:
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "staging"
        finish_cleanup.set()
        await task
    finally:
        finish_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)

    public = manager.get(job["id"])
    assert public["status"] == "error"
    assert public["error"] == "Cannot stage plugin source; check the URL, ref, and plugin manifest"
    assert "Cannot clean plugin job staging directory: PermissionError" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_survives_cleanup_failure(monkeypatch, caplog):
    manager, job = _job_manager()
    staging_started = asyncio.Event()
    hold_staging = asyncio.Event()

    async def hold_staging_open(*_args):
        staging_started.set()
        await hold_staging.wait()

    def fail_cleanup(_job):
        raise PermissionError("staging directory is locked")

    monkeypatch.setattr(manager, "_run_staging", hold_staging_open)
    monkeypatch.setattr(manager, "_cleanup", fail_cleanup)
    cleanup_started, finish_cleanup = _gate_cleanup(
        monkeypatch, manager, job, run_cleanup=True
    )
    task = asyncio.create_task(manager._stage(job))
    try:
        await asyncio.wait_for(staging_started.wait(), timeout=1)
        task.cancel()
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "staging"
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        finish_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert manager.get(job["id"])["status"] == "cancelled"
    assert "Cannot clean plugin job staging directory: PermissionError" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_during_failed_staging_cleanup_keeps_failure_status(monkeypatch):
    manager, job = _job_manager()

    async def fail_staging(*_args):
        raise ValueError("staging failed")

    monkeypatch.setattr(manager, "_run_staging", fail_staging)
    cleanup_started, finish_cleanup = _gate_cleanup(monkeypatch, manager, job)
    task = manager._track(manager._stage(job))
    manager.staging_tasks.add(task)
    task.add_done_callback(manager.staging_tasks.discard)
    shutdown = None
    try:
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "staging"
        shutdown = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        assert manager.get(job["id"])["status"] == "staging"
        assert not shutdown.done()
        finish_cleanup.set()
        await shutdown
    finally:
        finish_cleanup.set()
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
        if not task.done():
            task.cancel()
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    public = manager.get(job["id"])
    assert public["status"] == "error"
    assert public["error"] == "Cannot stage plugin source; check the URL, ref, and plugin manifest"


@pytest.mark.asyncio
async def test_loop_shutdown_cannot_cancel_cleanup_worker_before_terminal_publish(monkeypatch):
    manager, job = _job_manager()
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    cleanup_finished = threading.Event()
    publications = []

    async def fail_staging(*_args):
        raise ValueError("staging failed")

    def blocked_cleanup(_job):
        cleanup_started.set()
        release_cleanup.wait()
        cleanup_finished.set()

    monkeypatch.setattr(manager, "_run_staging", fail_staging)
    monkeypatch.setattr(manager, "_cleanup", blocked_cleanup)
    monkeypatch.setattr(
        manager.hub,
        "publish_change",
        lambda: publications.append((job["status"], cleanup_finished.is_set())),
    )
    stage = asyncio.create_task(manager._stage(job))
    current = asyncio.current_task()
    cancelled = []
    try:
        await _wait_for_thread_event(cleanup_started)

        # Mirror asyncio.run's shutdown cancellation: every other pending
        # Task is cancelled, including the cleanup child Task in the old code.
        cancelled = [task for task in asyncio.all_tasks() if task is not current]
        for task in cancelled:
            task.cancel()
        await asyncio.sleep(0)

        assert not cleanup_finished.is_set()
        assert not stage.done()
        assert publications == []
        assert manager.get(job["id"])["status"] == "staging"
    finally:
        release_cleanup.set()
        await asyncio.gather(*cancelled, return_exceptions=True)
        await _wait_for_thread_event(cleanup_finished)

    assert cleanup_finished.is_set()
    assert stage.cancelled()
    assert manager.get(job["id"])["status"] == "error"
    assert job["error"] == "Cannot stage plugin source; check the URL, ref, and plugin manifest"
    assert publications == [("error", True)]


@pytest.mark.asyncio
async def test_real_cleanup_failure_preserves_path_and_close_retries(monkeypatch):
    from kollabor_engine import plugin_jobs

    manager, job = _job_manager()
    base = job["base"]
    removals = []

    async def fail_staging(*_args):
        raise ValueError("staging failed")

    def fail_once(path):
        assert Path(path) == base
        removals.append(Path(path))
        if len(removals) == 1:
            raise PermissionError("staging directory is locked")

    monkeypatch.setattr(manager, "_run_staging", fail_staging)
    monkeypatch.setattr(plugin_jobs, "shutil", SimpleNamespace(rmtree=fail_once))

    await manager._stage(job)

    public = manager.get(job["id"])
    assert public["status"] == "error"
    assert public["error"] == "Cannot stage plugin source; check the URL, ref, and plugin manifest"
    assert public["warning"] == "Plugin staging files could not be removed"
    assert job["base"] == base

    await manager.close()

    assert removals == [base, base]
    assert "base" not in job


@pytest.mark.asyncio
async def test_expiry_retries_cleanup_failure_without_exposing_expired_job(monkeypatch):
    from fastapi import HTTPException
    from kollabor_engine import plugin_jobs

    manager, job = _job_manager()
    base = job["base"]
    job["status"] = "error"
    job["updated"] = 0
    removals = []

    def fail_once(path):
        assert Path(path) == base
        removals.append(Path(path))
        if len(removals) == 1:
            raise PermissionError("staging directory is locked")

    monkeypatch.setattr(plugin_jobs, "_JOB_TTL", -1)
    monkeypatch.setattr(plugin_jobs, "shutil", SimpleNamespace(rmtree=fail_once))

    manager._expire()
    assert job["id"] not in manager.jobs
    with pytest.raises(HTTPException) as missing:
        manager.get(job["id"])
    assert missing.value.status_code == 404
    await asyncio.gather(*list(manager.tasks), return_exceptions=True)
    await asyncio.sleep(0)

    assert job["base"] == base
    assert manager.expired_jobs[job["id"]] is job

    job["_cleanup_retry_at"] = 0
    manager._expire()
    await asyncio.gather(*list(manager.tasks), return_exceptions=True)
    await asyncio.sleep(0)

    assert removals == [base, base]
    assert job["id"] not in manager.expired_jobs
    assert "base" not in job


@pytest.mark.parametrize(
    ("install_fails", "expected_status", "expected_error"),
    [
        (False, "complete", None),
        (True, "error", "Cannot install plugin; check the plugin directory and try again"),
    ],
)
@pytest.mark.asyncio
async def test_apply_waits_for_cleanup_and_reports_cleanup_failure(
    monkeypatch, install_fails, expected_status, expected_error
):
    from fastapi import HTTPException

    from kollabor_plugins import packages

    manager, job = _job_manager()
    job["status"] = "installing"
    job["staged"] = {}
    if install_fails:
        job["restart_services"] = ["service"]
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    cleanup_finished = threading.Event()
    publications = []
    restart_results = []

    def apply(*_args, **_kwargs):
        if install_fails:
            raise OSError("install failed")

    async def no_restart(_job, *, completed):
        restart_results.append(completed)
        if not install_fails:
            job["warning"] = "Plugin installed, but its services could not be started; check their status"

    def fail_cleanup(_job):
        cleanup_started.set()
        release_cleanup.wait()
        cleanup_finished.set()
        return False

    monkeypatch.setattr(packages, "install_stage", apply)
    monkeypatch.setattr(manager, "_restart_after_change", no_restart)
    monkeypatch.setattr(manager, "_cleanup", fail_cleanup)
    monkeypatch.setattr(
        manager.hub,
        "publish_change",
        lambda: publications.append((job["status"], cleanup_finished.is_set())),
    )

    task = asyncio.create_task(manager._apply(job, "commit", "digest"))
    try:
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "installing"
        assert publications == []
        release_cleanup.set()
        if expected_error is None:
            result = await task
            assert result["status"] == expected_status
        else:
            with pytest.raises(HTTPException) as failure:
                await task
            assert failure.value.status_code == 503
    finally:
        release_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)

    public = manager.get(job["id"])
    assert public["status"] == expected_status
    assert public.get("error") == expected_error
    assert job["base"] == Path("unused-staging-path")
    expected_warning = "Plugin staging files could not be removed"
    if not install_fails:
        expected_warning = (
            "Plugin installed, but its services could not be started; check their status; "
            + expected_warning
        )
    assert public["warning"] == expected_warning
    assert publications == [(expected_status, True)]
    assert restart_results == [not install_fails]


@pytest.mark.asyncio
async def test_pending_expired_cleanup_counts_against_admission_capacity(monkeypatch):
    from fastapi import HTTPException
    from kollabor_engine import plugin_jobs

    monkeypatch.setattr(plugin_jobs, "_MAX_JOBS", 1)
    manager, expired = _job_manager()
    expired["_cleanup_retry_at"] = time.monotonic() + 60
    manager.jobs.pop(expired["id"])
    manager.expired_jobs[expired["id"]] = expired
    allow_cleanup = False
    accepted_job = None

    def cleanup(job):
        if job is expired:
            return allow_cleanup
        return PluginJobs._cleanup(job)

    async def ready_stage(job):
        job["status"] = "ready"
        job["updated"] = time.monotonic()

    monkeypatch.setattr(manager, "_cleanup", cleanup)
    monkeypatch.setattr(manager, "_stage", ready_stage)
    try:
        with pytest.raises(HTTPException) as full:
            manager._start("install", "https://example.invalid/plugin")
        assert full.value.status_code == 429

        allow_cleanup = True
        expired["_cleanup_retry_at"] = 0
        manager._expire()
        await asyncio.gather(*list(manager.tasks), return_exceptions=True)
        await asyncio.sleep(0)
        assert expired["id"] not in manager.expired_jobs

        started = manager._start("install", "https://example.invalid/plugin")
        accepted_job = manager.jobs[started["job_id"]]
        stage = next(iter(manager.staging_tasks))
        await stage
        assert manager.get(started["job_id"])["status"] == "ready"
    finally:
        await manager.close()

    if accepted_job is not None:
        assert "base" not in accepted_job


@pytest.mark.asyncio
async def test_expiry_retries_are_backed_off_and_do_not_overlap(monkeypatch):
    manager, job = _job_manager()
    base = job["base"]
    job["_cleanup_retry_at"] = 0
    manager.expired_jobs[job["id"]] = job
    started = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    attempts = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def controlled_cleanup(candidate):
        nonlocal active, max_active
        with lock:
            index = len(attempts)
            attempts.append(candidate["base"])
            active += 1
            max_active = max(max_active, active)
        started[index].set()
        release[index].wait()
        with lock:
            active -= 1
        if index == 1:
            candidate.pop("base", None)
            return True
        return False

    monkeypatch.setattr(manager, "_cleanup", controlled_cleanup)
    try:
        manager._expire()
        await _wait_for_thread_event(started[0])
        for _ in range(5):
            manager._expire()
        assert attempts == [base]
        assert max_active == 1
        release[0].set()
        await asyncio.gather(*list(manager.tasks), return_exceptions=True)
        await asyncio.sleep(0)

        assert job["_cleanup_retry_at"] > time.monotonic()
        manager._expire()
        await asyncio.sleep(0)
        assert attempts == [base]

        job["_cleanup_retry_at"] = 0
        manager._expire()
        await _wait_for_thread_event(started[1])
        for _ in range(5):
            manager._expire()
        assert attempts == [base, base]
        assert max_active == 1
        release[1].set()
        await asyncio.gather(*list(manager.tasks), return_exceptions=True)
        await asyncio.sleep(0)
    finally:
        for event in release:
            event.set()
        await asyncio.gather(*list(manager.tasks), return_exceptions=True)

    assert job["id"] not in manager.expired_jobs
    assert max_active == 1


@pytest.mark.asyncio
async def test_successful_staging_stays_ready_without_cleanup(monkeypatch):
    manager, job = _job_manager()
    staged = {
        "commit": "abc123",
        "files_sha256": "digest",
        "manifest": {"id": "sample"},
        "source": "https://example.invalid/plugin",
        "ref": None,
    }

    async def stage_successfully(*_args):
        return staged, {}

    def unexpected_cleanup(*_args):
        pytest.fail("ready jobs retain their staging directory for confirmation")

    monkeypatch.setattr(manager, "_run_staging", stage_successfully)
    monkeypatch.setattr(manager, "_cleanup", unexpected_cleanup)

    await manager._stage(job)

    public = manager.get(job["id"])
    assert public["status"] == "ready"
    assert public["commit"] == "abc123"


@pytest.mark.asyncio
async def test_cancel_keeps_status_non_terminal_until_staged_files_are_removed(
    monkeypatch, tmp_path
):
    manager, job = _job_manager()
    staged = tmp_path / "staged"
    (staged / "package").mkdir(parents=True)
    (staged / "package" / "file.txt").write_text("staged")
    job["status"] = "ready"
    job["base"] = staged
    cleanup_started, finish_cleanup = _gate_cleanup(
        monkeypatch, manager, job, run_cleanup=True
    )
    task = asyncio.create_task(manager.cancel(job["id"]))
    public = None
    try:
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "cancelling"
        assert staged.exists()
        finish_cleanup.set()
        public = await task
    finally:
        finish_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)

    assert public["status"] == "cancelled"
    assert not staged.exists()


@pytest.mark.asyncio
async def test_cancelled_staging_task_does_not_publish_terminal_while_retry_is_pending(
    monkeypatch, tmp_path
):
    manager, job = _job_manager()
    staged = tmp_path / "staged"
    (staged / "package").mkdir(parents=True)
    (staged / "package" / "file.txt").write_text("staged")
    job["base"] = staged
    cleanup_started = threading.Event()
    release = threading.Event()
    real_cleanup = manager._cleanup
    attempts = []

    def fail_once_then_gate(current_job):
        attempts.append(current_job)
        if len(attempts) == 1:
            return False  # the staging task's own cleanup fails once
        cleanup_started.set()
        release.wait()
        return real_cleanup(current_job)

    async def hold_staging(*_args):
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_cleanup", fail_once_then_gate)
    monkeypatch.setattr(manager, "_run_staging", hold_staging)
    job["stage_task"] = asyncio.create_task(manager._stage(job))
    await asyncio.sleep(0)
    task = asyncio.create_task(manager.cancel(job["id"]))
    public = None
    try:
        await _wait_for_thread_event(cleanup_started)
        assert manager.get(job["id"])["status"] == "cancelling"
        release.set()
        public = await task
    finally:
        release.set()
        await asyncio.gather(task, job["stage_task"], return_exceptions=True)

    assert public["status"] == "cancelled"
    assert not staged.exists()
