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


def test_job_management_is_local_even_with_user_token(client):
    response = client.post("/plugins/install", headers={"x-kollab-plugin-remote": "1"},
                           json={"source": "https://example.invalid/repo"})
    assert response.status_code == 403
    assert client.get("/plugins/jobs/missing", headers={"x-kollab-plugin-remote": "1"}).status_code == 403


def test_install_staging_is_background_and_does_not_reveal_errors(client, monkeypatch):
    import threading

    from kollabor_plugins import packages

    entered, release = threading.Event(), threading.Event()

    def slow_failure(*args):
        entered.set()
        assert release.wait(5)
        raise ValueError("private-git-secret https://secret.invalid")

    monkeypatch.setattr(packages, "stage_git_source", slow_failure)
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
