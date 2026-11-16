"""Stash identity remains a full object ID for both Git object formats."""

import subprocess

import pytest

from tests.unit.code_service.daemon_client import AsyncDaemonClient


@pytest.mark.asyncio
@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
async def test_apply_stash_by_full_object_id(tmp_path, object_format):
    root = tmp_path / "repo"
    root.mkdir()
    init = subprocess.run(["git", "init", "-q", f"--object-format={object_format}", str(root)], capture_output=True)
    if init.returncode and object_format == "sha256":
        pytest.skip("installed Git does not support SHA-256 repositories")
    assert init.returncode == 0

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout

    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (root / "a.txt").write_text("old\n")
    git("add", "a.txt")
    git("commit", "-qm", "initial")
    (root / "a.txt").write_text("changed\n")
    git("stash", "push", "-qm", "fixture")
    oid = git("rev-parse", "stash@{0}").strip()
    assert len(oid) == (40 if object_format == "sha1" else 64)
    client = AsyncDaemonClient()
    result = await client.post("/code/git", json={"workspace": str(root), "action": "apply_stash", "stashCommit": oid})
    assert result.status_code == 200
    assert (root / "a.txt").read_text() == "changed\n"
