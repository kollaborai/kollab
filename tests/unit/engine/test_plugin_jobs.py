"""Local management jobs stage exact Git versions before confirmation."""

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def managed_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_CONFIG_DIR", str(tmp_path / "engine-config"))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    _git("config", "user.name", "Test", cwd=source)
    _git("config", "user.email", "test@example.invalid", cwd=source)
    (source / "kollab-plugin.json").write_text(json.dumps({
        "format": 1, "id": "hello", "name": "Hello", "version": "1.0.0",
        "description": "Example", "kollab": ">=0", "uses": [],
    }))
    (source / "content.txt").write_text("one")
    _git("add", ".", cwd=source)
    _git("commit", "-m", "first", cwd=source)
    bare = tmp_path / "repo.git"
    _git("clone", "--bare", str(source), str(bare), cwd=tmp_path)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{bare.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://example.invalid/repo")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "always")
    return source, bare


@pytest.fixture
def client(managed_repo, monkeypatch):
    import asyncio

    from kollabor_engine.auth import generate_token
    from kollabor_engine.server import create_app

    from kollabor_plugins import services

    async def idle():
        await asyncio.Event().wait()

    monkeypatch.setattr(services, "supervise_services", idle)
    token = generate_token()
    with TestClient(create_app(), headers={"Authorization": f"Bearer {token}"}) as client:
        yield client


def _ready(client, job_id):
    for _ in range(500):
        response = client.get(f"/plugins/jobs/{job_id}")
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] != "staging":
            return state
        time.sleep(0.01)
    pytest.fail("plugin staging did not finish")


def test_install_job_requires_exact_commit_and_file_hash(client, managed_repo, tmp_path):
    source, _ = managed_repo
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo#main"})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    assert ready["status"] == "ready", ready
    assert ready["commit"] == _git("rev-parse", "HEAD", cwd=source)
    assert len(ready["files_sha256"]) == 64
    assert ready["manifest"]["id"] == "hello"
    root = tmp_path / ".kollab" / "plugins" / "hello"
    assert not root.exists()
    wrong = {"commit": ready["commit"], "files_sha256": "0" * 64}
    assert client.post(f"/plugins/jobs/{job_id}/confirm", json=wrong).status_code == 409
    assert not root.exists()
    accepted = {"commit": ready["commit"], "files_sha256": ready["files_sha256"]}
    assert client.post(f"/plugins/jobs/{job_id}/confirm", json=accepted).status_code == 200
    assert (root / "content.txt").read_text() == "one"
    assert client.post(f"/plugins/jobs/{job_id}/confirm", json=accepted).status_code == 409


def test_install_reports_manifest_review_limit_refusal(client, managed_repo):
    source, bare = managed_repo
    manifest = json.loads((source / "kollab-plugin.json").read_text())
    manifest["uses"] = [f"net:host-{index}.example" for index in range(257)]
    (source / "kollab-plugin.json").write_text(json.dumps(manifest))
    _git("add", "kollab-plugin.json", cwd=source)
    _git("commit", "-m", "exceed review limit", cwd=source)
    _git("push", str(bare), "main", cwd=source)

    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo#main"})
    job = _ready(client, response.json()["job_id"])

    assert job["status"] == "error"
    assert job["error"] == (
        "Plugin manifest exceeds the review limits (128 services, 64 secrets per service, 256 uses)"
    )


def test_cancel_ready_job_removes_stage_and_rejects_confirmation(client, tmp_path):
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    assert ready["status"] == "ready"
    base = client.app.state.plugin_jobs.jobs[job_id]["base"]
    response = client.delete(f"/plugins/jobs/{job_id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert not base.exists()
    assert client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    }).status_code == 409
    assert not (tmp_path / ".kollab" / "plugins" / "hello").exists()


def test_cancel_reports_cleanup_failure_and_allows_retry(client, monkeypatch):
    import shutil

    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    base = client.app.state.plugin_jobs.jobs[job_id]["base"]
    original = shutil.rmtree

    def fail_base(path, *args, **kwargs):
        if path == base:
            raise PermissionError("private path")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_base)
    result = client.delete(f"/plugins/jobs/{job_id}")
    assert result.status_code == 503
    assert "private path" not in result.text
    assert base.exists()
    assert client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    }).status_code == 409
    monkeypatch.setattr(shutil, "rmtree", original)
    assert client.delete(f"/plugins/jobs/{job_id}").status_code == 200
    assert not base.exists()


def test_failed_cancel_cleanup_expires_and_retries(client, monkeypatch):
    import asyncio
    import shutil

    from kollabor_engine import plugin_jobs

    manager = client.app.state.plugin_jobs
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    _ready(client, job_id)
    job = manager.jobs[job_id]
    base = job["base"]
    original = shutil.rmtree

    def fail_base(path, *args, **kwargs):
        if path == base:
            raise PermissionError("private path")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_base)
    assert client.delete(f"/plugins/jobs/{job_id}").status_code == 503
    assert job["status"] == "cancelling"
    assert "cancel_task" not in job

    monkeypatch.setattr(shutil, "rmtree", original)
    monkeypatch.setattr(plugin_jobs, "_JOB_TTL", 1)
    job["updated"] = time.monotonic() - 2

    async def expire_and_wait_for_cleanup():
        manager._expire()
        await asyncio.gather(*list(manager.tasks))

    client.portal.call(expire_and_wait_for_cleanup)

    assert job_id not in manager.jobs
    assert job_id not in manager.expired_jobs
    assert not base.exists()


def test_cancel_staging_job_stops_worker_and_cleans_stage(client, monkeypatch):
    import asyncio
    import threading

    entered = threading.Event()
    cancelled = threading.Event()

    async def slow_staging(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(client.app.state.plugin_jobs, "_run_staging", slow_staging)
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    assert response.status_code == 202
    assert entered.wait(2)
    job_id = response.json()["job_id"]
    base = client.app.state.plugin_jobs.jobs[job_id]["base"]
    response = client.delete(f"/plugins/jobs/{job_id}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert cancelled.wait(2)
    assert not base.exists()
    assert client.get(f"/plugins/jobs/{job_id}").json()["status"] == "cancelled"


def test_job_management_is_local_even_with_user_token(client):
    response = client.post("/plugins/install", headers={"x-kollab-plugin-remote": "1"},
                           json={"source": "https://example.invalid/repo"})
    assert response.status_code == 403
    assert client.get("/plugins/jobs/missing", headers={"x-kollab-plugin-remote": "1"}).status_code == 403
    assert client.delete("/plugins/jobs/missing", headers={"x-kollab-plugin-remote": "1"}).status_code == 403


def test_cancel_discards_ready_plugin_job(client):
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    assert _ready(client, job_id)["status"] == "ready"
    base = client.app.state.plugin_jobs.jobs[job_id]["base"]

    response = client.delete(f"/plugins/jobs/{job_id}")

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] == "cancelled"
    assert not base.exists()
    # The cancelled job stays queryable until it expires, so a repeated DELETE is safe.
    assert client.get(f"/plugins/jobs/{job_id}").json()["status"] == "cancelled"


@pytest.mark.parametrize("status", ["installing", "complete"])
def test_cancel_preserves_installing_or_completed_job(client, status):
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    assert _ready(client, job_id)["status"] == "ready"
    jobs = client.app.state.plugin_jobs
    job = jobs.jobs[job_id]
    base = job["base"]
    job["status"] = status

    response = client.delete(f"/plugins/jobs/{job_id}")

    assert response.status_code == 409
    assert base.exists()
    assert jobs.jobs[job_id]["status"] == status


def test_cancel_reserves_ready_job_while_cleanup_runs(client, monkeypatch):
    import asyncio
    import threading

    from fastapi import HTTPException

    jobs = client.app.state.plugin_jobs
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    cleanup_started, release_cleanup = threading.Event(), threading.Event()
    release_apply = threading.Event()
    original_cleanup = jobs._cleanup
    cleanups = []

    def slow_cleanup(job):
        cleanups.append(job["id"])
        cleanup_started.set()
        assert release_cleanup.wait(5)
        return original_cleanup(job)

    async def blocked_apply(job, commit, digest):
        assert await asyncio.to_thread(release_apply.wait, 5)
        return jobs._public(job)

    monkeypatch.setattr(jobs, "_cleanup", slow_cleanup)
    monkeypatch.setattr(jobs, "_apply", blocked_apply)
    # The finished staging task belongs to the TestClient's loop; this race runs in its own loop.
    jobs.jobs[job_id].pop("stage_task", None)

    async def race():
        cancel = asyncio.create_task(jobs.cancel(job_id))
        try:
            assert await asyncio.to_thread(cleanup_started.wait, 2)
            duplicate_cancel = asyncio.create_task(jobs.cancel(job_id))
            confirm = asyncio.create_task(jobs.confirm(job_id, ready["commit"], ready["files_sha256"]))
            await asyncio.sleep(0.05)
            assert confirm.done(), "confirm must be rejected while cancellation owns the job"
            with pytest.raises(HTTPException) as confirm_error:
                await confirm
            assert confirm_error.value.status_code == 409
            assert not duplicate_cancel.done(), "a second DELETE must join the running cancellation"
            release_cleanup.set()
            first, second = await asyncio.gather(cancel, duplicate_cancel)
            assert first["status"] == second["status"] == "cancelled"
            assert cleanups == [job_id], "cleanup must run once for both DELETEs"
        finally:
            release_cleanup.set()
            release_apply.set()
            await asyncio.gather(cancel, return_exceptions=True)

    asyncio.run(race())


def test_cancel_rejects_new_work_while_manager_is_closing(client):
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    assert _ready(client, job_id)["status"] == "ready"
    client.app.state.plugin_jobs.closing = True

    response = client.delete(f"/plugins/jobs/{job_id}")

    assert response.status_code == 503


def test_cancel_stops_staging_job_and_removes_its_files(client, monkeypatch):
    import asyncio
    import threading

    stopped = threading.Event()

    async def stalled(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    monkeypatch.setattr(client.app.state.plugin_jobs, "_run_staging", stalled)
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    base = client.app.state.plugin_jobs.jobs[job_id]["base"]
    assert client.get(f"/plugins/jobs/{job_id}").json()["status"] == "staging"

    response = client.delete(f"/plugins/jobs/{job_id}")

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] == "cancelled"
    assert stopped.is_set()
    assert not base.exists()
    assert client.get(f"/plugins/jobs/{job_id}").json()["status"] == "cancelled"


def test_install_staging_is_background_and_does_not_reveal_errors(client, monkeypatch):
    import asyncio
    import threading

    entered, release = threading.Event(), threading.Event()

    async def slow_failure(*args):
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        raise ValueError("private-git-secret https://secret.invalid")

    monkeypatch.setattr(client.app.state.plugin_jobs, "_run_staging", slow_failure)
    try:
        response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
        assert response.status_code == 202
        assert entered.wait(2)
        job_id = response.json()["job_id"]
        assert client.get(f"/plugins/jobs/{job_id}").json()["status"] == "staging"
    finally:
        release.set()
    state = _ready(client, job_id)
    assert state["status"] == "error"
    assert "private-git-secret" not in json.dumps(state)
    assert "secret.invalid" not in json.dumps(state)


def test_install_rejects_modified_staged_files(client, tmp_path):
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo"})
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    stage = client.app.state.plugin_jobs.jobs[job_id]["base"] / "package"
    (stage / "content.txt").write_text("tampered")
    response = client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    })
    assert response.status_code == 409
    assert not (tmp_path / ".kollab" / "plugins" / "hello").exists()
    assert not stage.exists()


def test_jobs_are_bounded_and_expire(client, monkeypatch):
    from kollabor_engine import plugin_jobs

    monkeypatch.setattr(plugin_jobs, "_MAX_JOBS", 1)
    first = client.post("/plugins/install", json={"source": "https://example.invalid/repo"}).json()
    ready = _ready(client, first["job_id"])
    assert ready["status"] == "ready"
    assert client.post("/plugins/install", json={"source": "https://example.invalid/repo"}).status_code == 429
    monkeypatch.setattr(plugin_jobs, "_JOB_TTL", -1)
    assert client.get(f"/plugins/jobs/{first['job_id']}").status_code == 404


def _install_hello(client):
    response = client.post("/plugins/install", json={"source": "https://example.invalid/repo#main"})
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    assert ready["status"] == "ready"
    response = client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    })
    assert response.status_code == 200, response.text
    return ready


def _publish_update(managed_repo):
    source, bare = managed_repo
    (source / "content.txt").write_text("two")
    _git("add", ".", cwd=source)
    _git("commit", "-m", "second", cwd=source)
    _git("push", str(bare), "main", cwd=source)
    return _git("rev-parse", "HEAD", cwd=source)


def test_update_job_previews_and_preserves_data_and_disabled_state(client, managed_repo, tmp_path):
    first = _install_hello(client)
    data = tmp_path / ".kollab" / "plugin-data" / "hello"
    data.mkdir(parents=True, exist_ok=True)
    (data / "personal.txt").write_text("keep")
    assert client.post("/plugins/hello/disable").status_code == 200
    new_commit = _publish_update(managed_repo)
    response = client.post("/plugins/hello/update")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    assert ready["status"] == "ready", ready
    assert ready["previous_commit"] == first["commit"]
    assert ready["commit"] == new_commit
    assert "." in ready["changed_folders"]
    root = tmp_path / ".kollab" / "plugins" / "hello"
    assert (root / "content.txt").read_text() == "one"
    response = client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    })
    assert response.status_code == 200, response.text
    assert (root / "content.txt").read_text() == "two"
    assert (data / "personal.txt").read_text() == "keep"
    registry = json.loads((tmp_path / ".kollab" / "plugins.json").read_text())
    assert registry["plugins"]["hello"]["enabled"] is False


def test_update_rejects_registry_changes_before_confirmation(client, managed_repo, tmp_path):
    _install_hello(client)
    _publish_update(managed_repo)
    response = client.post("/plugins/hello/update")
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    ready = _ready(client, job_id)
    assert client.post("/plugins/hello/disable").status_code == 200
    response = client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    })
    assert response.status_code == 409
    assert (tmp_path / ".kollab" / "plugins" / "hello" / "content.txt").read_text() == "one"


def test_update_does_not_replace_plugin_with_active_agent_run(client, managed_repo, tmp_path):
    _install_hello(client)
    _publish_update(managed_repo)
    run = tmp_path / ".kollab" / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "runs.json").write_text(json.dumps({"runs": [{"status": "running"}]}))
    job_id = client.post("/plugins/hello/update").json()["job_id"]
    ready = _ready(client, job_id)
    response = client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    })
    assert response.status_code == 409
    assert (tmp_path / ".kollab" / "plugins" / "hello" / "content.txt").read_text() == "one"


def test_update_blocks_new_router_leases_and_duplicate_confirm(client, managed_repo, monkeypatch):
    import concurrent.futures
    import threading

    _install_hello(client)
    _publish_update(managed_repo)
    job_id = client.post("/plugins/hello/update").json()["job_id"]
    ready = _ready(client, job_id)
    payload = {"commit": ready["commit"], "files_sha256": ready["files_sha256"]}
    manager = client.app.state.plugin_jobs
    original = manager._install_update
    entered, release = threading.Event(), threading.Event()

    def wait_then_install(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(manager, "_install_update", wait_then_install)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/plugins/jobs/{job_id}/confirm", json=payload)
        try:
            assert entered.wait(2)
            assert client.get("/plugins/hello/api/anything").status_code == 409
            assert client.post(f"/plugins/jobs/{job_id}/confirm", json=payload).status_code == 409
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 200


def test_update_stops_and_restarts_previously_running_services(client, managed_repo, monkeypatch):
    from kollabor_plugins import cli, services

    source, bare = managed_repo
    manifest_path = source / "kollab-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["services"] = {"worker": {"command": ["python", "worker.py"]}}
    manifest_path.write_text(json.dumps(manifest))
    (source / "worker.py").write_text("pass\n")
    _git("add", ".", cwd=source)
    _git("commit", "-m", "add service", cwd=source)
    _git("push", str(bare), "main", cwd=source)
    state = {"active": True, "events": []}
    monkeypatch.setattr(services, "supervisor_tick", lambda **kwargs: None)
    _install_hello(client)
    _publish_update(managed_repo)
    monkeypatch.setattr(services, "_recorded_process_alive", lambda path: state["active"])

    def stop(home, plugin_id, declarations):
        assert plugin_id == "hello" and "worker" in declarations
        state["events"].append("stop")
        state["active"] = False

    def control(plugin_id, name, action, **kwargs):
        state["events"].append((plugin_id, name, action))

    monkeypatch.setattr(cli, "_stop_for_change", stop)
    monkeypatch.setattr(services, "control_service", control)
    job_id = client.post("/plugins/hello/update").json()["job_id"]
    ready = _ready(client, job_id)
    response = client.post(f"/plugins/jobs/{job_id}/confirm", json={
        "commit": ready["commit"], "files_sha256": ready["files_sha256"],
    })
    assert response.status_code == 200, response.text
    assert state["events"] == ["stop", ("hello", "worker", "start")]


def test_shutdown_terminates_owned_stalled_git_without_leaving_stage(client, tmp_path, monkeypatch):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    marker = tmp_path / "git-started"
    fake_git = fake_bin / "git"
    fake_git.write_text(f"#!/bin/sh\necho $$ > '{marker}'\nsleep 4\nexit 1\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    job_id = client.post("/plugins/install", json={"source": "https://example.invalid/repo"}).json()["job_id"]
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    base = client.app.state.plugin_jobs.jobs[job_id]["base"]
    start = time.monotonic()
    client.portal.call(client.app.state.plugin_jobs.close)
    assert time.monotonic() - start < 2
    assert not base.exists()
    assert client.post("/plugins/install", json={"source": "https://example.invalid/repo"}).status_code == 503


def test_staging_timeout_stops_git_and_cleans_own_files(client, tmp_path, monkeypatch):
    from kollabor_engine import plugin_jobs

    fake_bin = tmp_path / "slow-bin"
    fake_bin.mkdir()
    marker = tmp_path / "slow-git-started"
    fake_git = fake_bin / "git"
    fake_git.write_text(f"#!/bin/sh\necho $$ > '{marker}'\nsleep 4\nexit 1\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setattr(plugin_jobs, "_STAGE_TIMEOUT", 1.5)
    job_id = client.post("/plugins/install", json={"source": "https://example.invalid/repo"}).json()["job_id"]
    base = client.app.state.plugin_jobs.jobs[job_id]["base"]
    state = _ready(client, job_id)
    assert marker.exists()
    assert state["status"] == "error"
    assert not base.exists()
