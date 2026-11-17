"""Pinned Git staging uses a local bare repository and never a real user home."""

import json
import subprocess

import pytest


def test_git_staging_and_integrity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    from kollabor_plugins.packages import stage_git_source, verify_stage

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        ).stdout.strip()

    source = tmp_path / "source"
    source.mkdir()
    git("init", "-b", "main", cwd=source)
    git("config", "user.name", "Example", cwd=source)
    git("config", "user.email", "test@example.invalid", cwd=source)
    (source / "kollab-plugin.json").write_text(
        json.dumps(
            {
                "format": 1,
                "id": "hello",
                "name": "Hello",
                "version": "1.0.0",
                "description": "Example",
                "kollab": ">=0.23.0",
                "uses": [],
            }
        )
    )
    git("add", ".", cwd=source)
    git("commit", "-m", "test", cwd=source)
    commit = git("rev-parse", "HEAD", cwd=source)
    bare = tmp_path / "repo.git"
    git("clone", "--bare", str(source), str(bare))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{bare.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://example.invalid/repo")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "always")

    stage = tmp_path / "stage"
    result = stage_git_source("https://example.invalid/repo#main", stage, "0.23.0")
    assert result["commit"] == commit
    assert len(result["files_sha256"]) == 64
    assert not (stage / ".git").exists()
    verify_stage(stage, result, commit, result["files_sha256"])
    with pytest.raises(ValueError, match="commit"):
        verify_stage(stage, result, "0" * 40, result["files_sha256"])
    (stage / "kollab-plugin.json").write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        verify_stage(stage, result, commit, result["files_sha256"])
