"""The daemon's git API for the code editor view (#987), part 1: the read actions and the checks around them.

Covers the envelope and failure codes of docs/specs/floating-code-shell.md and the refusals in
kollabor/code_service/git.py: option-like values, control characters, and paths that leave the workspace.
"""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tests.unit.code_service.daemon_client import AsyncDaemonClient


def test_git_output_is_bounded_without_deadlocking_either_pipe(tmp_path, monkeypatch):
    from kollabor.code_service import git

    fake_git = tmp_path / "git"
    fake_git.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write('a' * 200000)\nsys.stderr.write('b' * 200000)\n"
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", 1024)
    with pytest.raises(git.GitRequestError) as exc:
        git._run_git(str(tmp_path), ["status"], True)
    assert exc.value.code == "git_output_too_large"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_git_timeout_stops_its_child_with_inherited_pipes(tmp_path, monkeypatch):
    from kollabor.code_service import git

    marker = tmp_path / "child-finished"
    fake_git = tmp_path / "git"
    child = f"import time,pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).touch()"
    fake_git.write_text(
        f"#!{sys.executable}\nimport subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\ntime.sleep(10)\n"
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(git.GitRequestError) as exc:
        git._run_git(str(tmp_path), ["status"], True, timeout=1)
    assert exc.value.code == "git_timeout"
    time.sleep(2.2)
    assert not marker.exists()


@pytest.fixture
def client():
    return AsyncDaemonClient()


def _git(repo, *args):
    """Run git in a test repository. The identity comes from the environment; the global git config is empty."""
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A scratch computer: a home folder holding a git repository, the engine's folder, and a folder outside home."""
    tmp_path = tmp_path.resolve()  # macOS keeps /var as a link to /private/var
    home, engine, elsewhere = (tmp_path / name for name in ("home", "engine", "elsewhere"))
    repo = home / "repo"
    for folder in (engine, elsewhere, repo):
        folder.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Kollab Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    monkeypatch.chdir(engine)
    _git(repo, "init", "-b", "main")
    (repo / "tracked.txt").write_text("hello\n")
    (elsewhere / "secret.txt").write_text("not in the workspace\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "init")
    return SimpleNamespace(home=home, engine=engine, elsewhere=elsewhere, repo=repo)


def _body(machine, action, **fields):
    return {"action": action, "workspace": str(machine.repo), **fields}


def _error_code(response):
    """The failure envelope's code, after checking the envelope's shape."""
    payload = response.json()
    assert payload["success"] is False
    assert set(payload["error"]) == {"code", "message"}
    return payload["error"]["code"]


@pytest.mark.asyncio
async def test_both_status_surfaces_refuse_too_many_entries(client, machine, monkeypatch):
    from kollabor.code_service import git

    monkeypatch.setattr(git, "MAX_STATUS_ENTRIES", 2)
    for name in ("a.txt", "b.txt", "c.txt"):
        (machine.repo / name).write_text("x")
    response = await client.post("/code/git", json=_body(machine, "status"))
    assert response.status_code == 413
    assert _error_code(response) == "git_output_too_large"
    response = await client.get("/code/fs/git-status", params={"workspace": str(machine.repo)})
    assert response.status_code == 413
    assert _error_code(response) == "git_output_too_large"


@pytest.mark.asyncio
async def test_status_refuses_failed_workspace_prefix(client, machine, monkeypatch):
    from kollabor.code_service import git

    original = git._run_git

    def fail_prefix(root, args, read, timeout=git.GIT_TIMEOUT, **kwargs):
        if args == ["rev-parse", "--show-prefix"]:
            return subprocess.CompletedProcess(args, 1, "", "failed")
        return original(root, args, read, timeout, **kwargs)

    monkeypatch.setattr(git, "_run_git", fail_prefix)
    result = await client.post("/code/git", json=_body(machine, "status"))
    assert result.status_code == 502
    result = await client.get("/code/fs/git-status", params={"workspace": str(machine.repo)})
    assert result.status_code == 500


@pytest.mark.asyncio
async def test_status_reports_the_branch_and_an_untracked_file(client, machine):
    (machine.repo / "new.txt").write_text("x\n")

    response = await client.post("/code/git", json=_body(machine, "status"))

    assert response.status_code == 200
    assert response.json()["success"] is True
    data = response.json()["data"]
    assert data["branch"] == "main" and data["upstream"] is None
    assert {
        "path": "new.txt",
        "name": "new.txt",
        "staged": False,
        "unstaged": False,
        "untracked": True,
        "statusCode": "??",
    } in data["files"]


@pytest.mark.asyncio
async def test_log_lists_the_commit(client, machine):
    data = (await client.post("/code/git", json=_body(machine, "log"))).json()["data"]

    assert [entry["message"] for entry in data["entries"]] == ["init"]
    assert data["entries"][0]["author"] == "Kollab Test"
    assert len(data["entries"][0]["hash"]) == 40


@pytest.mark.asyncio
@pytest.mark.parametrize("staged", [False, True])
async def test_diff_shows_the_change_to_one_file(client, machine, staged):
    (machine.repo / "tracked.txt").write_text("hello\nchanged\n")
    if staged:
        _git(machine.repo, "add", "tracked.txt")

    response = await client.post("/code/git", json=_body(machine, "diff", path="tracked.txt", staged=staged))

    assert response.status_code == 200
    assert "+changed" in response.json()["data"]["diff"]


@pytest.mark.asyncio
async def test_list_branches_and_current_branch(client, machine):
    _git(machine.repo, "branch", "feature/x")

    listed = (await client.post("/code/git", json=_body(machine, "list_branches"))).json()["data"]
    current = (await client.post("/code/git", json=_body(machine, "current_branch"))).json()["data"]

    by_name = {branch["name"]: branch for branch in listed["branches"]}
    assert by_name["main"]["isCurrent"] is True
    assert by_name["feature/x"]["isCurrent"] is False
    assert listed["current"] == "main" and listed["defaultBranch"] is None
    assert current == {"ok": True, "current": "main"}


@pytest.mark.asyncio
async def test_show_returns_the_file_at_head_and_empty_for_a_new_file(client, machine):
    present = await client.post("/code/git", json=_body(machine, "show", path="tracked.txt"))
    missing = await client.post("/code/git", json=_body(machine, "show", path="never-committed.txt"))

    assert present.json()["data"] == {"content": "hello\n"}
    assert missing.status_code == 200
    assert missing.json()["data"] == {"content": ""}


@pytest.mark.asyncio
async def test_show_commit_returns_the_patch_and_refuses_an_option_like_id(client, machine):
    commit = (await client.post("/code/git", json=_body(machine, "log"))).json()["data"]["entries"][0]["hash"]

    patch = await client.post("/code/git", json=_body(machine, "show_commit", commitHash=commit))
    refused = await client.post("/code/git", json=_body(machine, "show_commit", commitHash="--upload-pack=x"))

    assert "+hello" in patch.json()["data"]["content"]
    assert refused.status_code == 400
    assert _error_code(refused) == "invalid_commit"


@pytest.mark.asyncio
async def test_stash_list_and_show(client, machine):
    (machine.repo / "tracked.txt").write_text("hello\nstashed\n")
    _git(machine.repo, "stash", "push", "-m", "wip")

    listed = (await client.post("/code/git", json=_body(machine, "list_stashes"))).json()["data"]
    shown = (await client.post("/code/git", json=_body(machine, "show_stash", stashId="0"))).json()["data"]
    refused = await client.post("/code/git", json=_body(machine, "show_stash", stashId="--x"))
    missing = await client.post("/code/git", json=_body(machine, "show_stash", stashId="5"))

    assert [stash["id"] for stash in listed["stashes"]] == ["0"]
    assert "wip" in listed["stashes"][0]["message"]
    assert "+stashed" in shown["diff"]
    assert refused.status_code == 400
    assert _error_code(refused) == "invalid_stash"
    assert missing.status_code == 404
    assert _error_code(missing) == "stash_not_found"


@pytest.mark.asyncio
async def test_a_folder_that_is_not_a_repository_reads_as_empty(client, machine):
    plain = machine.home / "plain"
    plain.mkdir()

    data = (await client.post("/code/git", json={"action": "status", "workspace": str(plain)})).json()["data"]

    assert data["branch"] == "HEAD"
    assert data["files"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["show", "diff"])
@pytest.mark.parametrize("path", ["../../elsewhere/secret.txt", "/etc/hosts"])
async def test_a_path_outside_the_workspace_is_403(client, machine, action, path):
    response = await client.post("/code/git", json=_body(machine, action, path=path))

    assert response.status_code == 403
    assert _error_code(response) == "path_outside_workspace"


@pytest.mark.asyncio
async def test_a_symlink_out_of_the_workspace_is_403(client, machine):
    (machine.repo / "leak.txt").symlink_to(machine.elsewhere / "secret.txt")

    response = await client.post("/code/git", json=_body(machine, "diff", path="leak.txt"))

    assert response.status_code == 403
    assert _error_code(response) == "path_outside_workspace"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["show", "diff"])
@pytest.mark.parametrize("path", ["-p", "--output=nowhere", "a\nb", "a\x00b"])
async def test_option_like_or_control_character_paths_are_400(client, machine, action, path):
    response = await client.post("/code/git", json=_body(machine, action, path=path))

    assert response.status_code == 400
    assert _error_code(response) == "invalid_path"


@pytest.mark.asyncio
async def test_a_diff_without_a_path_is_400(client, machine):
    response = await client.post("/code/git", json=_body(machine, "diff"))

    assert response.status_code == 400
    assert _error_code(response) == "path_required"


@pytest.mark.asyncio
async def test_a_folder_that_is_gone_is_400(client, machine):
    nowhere = await client.post("/code/git", json={"action": "status", "workspace": str(machine.home / "nope")})

    assert nowhere.status_code == 400
    assert _error_code(nowhere) == "bad_workspace"


@pytest.mark.asyncio
async def test_an_unknown_action_is_400(client, machine):
    unknown = await client.post("/code/git", json=_body(machine, "push_everything"))

    assert unknown.status_code == 400
    assert _error_code(unknown) == "unknown_action"


@pytest.fixture
def subfolder(machine):
    """The same repository, with the workspace one folder down: ``repo/app`` holds a change, ``repo`` has another."""
    app = machine.repo / "app"
    app.mkdir()
    (app / "inside.txt").write_text("inside\n")
    _git(machine.repo, "add", "app/inside.txt")
    _git(machine.repo, "commit", "-m", "app")
    (app / "inside.txt").write_text("inside edited\n")
    (machine.repo / "tracked.txt").write_text("hello edited\n")  # a change outside the workspace
    machine.app = app
    return machine


@pytest.mark.asyncio
async def test_status_in_a_subfolder_lists_workspace_paths_only(client, subfolder):
    body = {"action": "status", "workspace": str(subfolder.app)}

    files = (await client.post("/code/git", json=body)).json()["data"]["files"]

    assert [f["path"] for f in files] == ["inside.txt"]


@pytest.mark.asyncio
async def test_a_path_from_a_subfolder_status_can_be_staged_and_diffed(client, subfolder):
    workspace = str(subfolder.app)
    await client.post("/code/git", json={"action": "stage", "workspace": workspace, "paths": ["inside.txt"]})

    status = await client.post("/code/git", json={"action": "status", "workspace": workspace})
    staged = status.json()["data"]["files"]
    diff_body = {"action": "diff", "workspace": workspace, "path": "inside.txt", "staged": True}
    diff = (await client.post("/code/git", json=diff_body)).json()["data"]["diff"]

    assert [(f["path"], f["staged"]) for f in staged] == [("inside.txt", True)]
    assert "+inside edited" in diff


@pytest.mark.asyncio
async def test_stage_all_in_a_subfolder_leaves_changes_outside_it_alone(client, subfolder):
    await client.post("/code/git", json={"action": "stage_all", "workspace": str(subfolder.app)})

    command = ["git", "-C", str(subfolder.repo), "diff", "--cached", "--name-only"]
    cached = subprocess.run(command, capture_output=True, text=True, check=True).stdout.split()

    assert cached == ["app/inside.txt"]


@pytest.mark.asyncio
async def test_commit_refuses_when_staged_files_lie_outside_the_workspace(client, subfolder):
    """The panel shows only the workspace's changes, so a commit must not take in staged files it cannot show."""
    _git(subfolder.repo, "add", "tracked.txt")  # staged, outside repo/app
    await client.post("/code/git", json={"action": "stage_all", "workspace": str(subfolder.app)})
    before = subprocess.run(
        ["git", "-C", str(subfolder.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout

    response = await client.post(
        "/code/git", json={"action": "commit", "workspace": str(subfolder.app), "message": "app change"}
    )

    after = subprocess.run(
        ["git", "-C", str(subfolder.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout
    assert response.status_code == 409
    assert before == after
