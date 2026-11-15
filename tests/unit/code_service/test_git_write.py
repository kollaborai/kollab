"""The daemon's git API (#987), part 2: the write actions and their refusals.

Round trips run on a scratch machine shaped like part 1's: a home folder holding a repository and a bare remote, the
engine's folder, and a folder outside home. Git's identity comes from the environment; the global git config is empty.
"""

import os
import subprocess
from types import SimpleNamespace

import pytest

from kollabor.code_service import git as code_git
from tests.unit.code_service.daemon_client import AsyncDaemonClient


@pytest.fixture
def client():
    return AsyncDaemonClient()


def _git(repo, *args):
    """Run git in a test repository and return its stdout. A failing command raises, which stops the test."""
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def machine(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()  # macOS keeps /var as a link to /private/var
    home, engine, elsewhere = (tmp_path / name for name in ("home", "engine", "elsewhere"))
    repo, remote = home / "repo", home / "origin.git"
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
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return SimpleNamespace(home=home, engine=engine, elsewhere=elsewhere, repo=repo, remote=remote)


def _body(machine, action, **fields):
    return {"action": action, "workspace": str(machine.repo), **fields}


async def _act(client, machine, action, **fields):
    return await client.post("/code/git", json=_body(machine, action, **fields))


def _error_code(response):
    """The failure envelope's code, after checking the envelope's shape."""
    payload = response.json()
    assert payload["success"] is False
    assert set(payload["error"]) == {"code", "message"}
    return payload["error"]["code"]


def _entry(status, path):
    """The status row for ``path``; None when git reports the file as clean."""
    return next((entry for entry in status["files"] if entry["path"] == path), None)


@pytest.mark.asyncio
async def test_successful_stage_reports_when_followup_status_is_too_large(client, machine, monkeypatch):
    from kollabor.code_service import git

    monkeypatch.setattr(git, "MAX_STATUS_ENTRIES", 1)
    (machine.repo / "first.txt").write_text("one")
    (machine.repo / "second.txt").write_text("two")
    response = await _act(client, machine, "stage", paths=["first.txt"])
    assert response.status_code == 502
    assert _error_code(response) == "git_status_after_write_failed"
    assert "operation ran" in response.json()["error"]["message"]
    assert "first.txt" in _git(machine.repo, "diff", "--cached", "--name-only")


@pytest.mark.asyncio
async def test_branch_creation_reports_when_followup_read_fails(client, machine, monkeypatch):
    from kollabor.code_service import git

    original = git._git

    async def fail_head(root, args, **kwargs):
        if args[0] in ("symbolic-ref", "rev-parse"):
            return subprocess.CompletedProcess(args, 1, "", "failed")
        return await original(root, args, **kwargs)

    monkeypatch.setattr(git, "_git", fail_head)
    response = await _act(client, machine, "create_branch", branchName="new-branch")
    assert response.status_code == 502
    assert "operation ran" in response.json()["error"]["message"]
    assert "new-branch" in _git(machine.repo, "branch", "--list")


@pytest.mark.asyncio
async def test_stage_reports_nonzero_followup_status(client, machine, monkeypatch):
    from kollabor.code_service import git

    original = git._git

    async def fail_status(root, args, **kwargs):
        if args[0] == "status":
            return subprocess.CompletedProcess(args, 1, "", "failed")
        return await original(root, args, **kwargs)

    monkeypatch.setattr(git, "_git", fail_status)
    (machine.repo / "first.txt").write_text("one")
    response = await _act(client, machine, "stage", paths=["first.txt"])
    assert response.status_code == 502
    assert "operation ran" in response.json()["error"]["message"]
    assert "first.txt" in _git(machine.repo, "diff", "--cached", "--name-only")


@pytest.mark.asyncio
async def test_stash_apply_refuses_when_scope_prefix_cannot_be_read(client, machine, monkeypatch):
    from kollabor.code_service import git

    (machine.repo / "tracked.txt").write_text("stashed\n")
    created = await _act(client, machine, "create_stash")
    assert created.status_code == 200
    original = git._run_git

    def fail_prefix(root, args, read, timeout=git.GIT_TIMEOUT, **kwargs):
        if args == ["rev-parse", "--show-prefix"]:
            return subprocess.CompletedProcess(args, 1, "", "failed")
        return original(root, args, read, timeout, **kwargs)

    monkeypatch.setattr(git, "_run_git", fail_prefix)
    result = await _act(client, machine, "apply_stash", stashId="stash@{0}")
    assert result.status_code == 502
    assert (machine.repo / "tracked.txt").read_text() == "hello\n"


@pytest.mark.asyncio
async def test_stash_apply_refuses_failed_untracked_parent_probe(client, machine, monkeypatch):
    from kollabor.code_service import git

    inside = machine.repo / "inside"
    outside = machine.repo / "outside.txt"
    outside.write_text("outside")
    _git(machine.repo, "stash", "push", "-u")
    inside.mkdir(exist_ok=True)
    original = git._git

    async def fail_probe(root, args, **kwargs):
        if args[0] == "rev-parse" and args[-1].endswith("^3"):
            return subprocess.CompletedProcess(args, 128, "", "probe failed")
        return await original(root, args, **kwargs)

    monkeypatch.setattr(git, "_git", fail_probe)
    response = await client.post("/code/git", json={
        "workspace": str(inside), "action": "apply_stash", "stashId": "stash@{0}",
    })
    assert response.status_code == 409
    assert not outside.exists()


@pytest.mark.asyncio
async def test_stage_unstage_and_commit_round_trip(client, machine):
    (machine.repo / "new.txt").write_text("x\n")

    staged = (await _act(client, machine, "stage", paths=["new.txt"])).json()["data"]["status"]
    assert _entry(staged, "new.txt")["staged"] is True

    unstaged = (await _act(client, machine, "unstage", paths=["new.txt"])).json()["data"]["status"]
    assert _entry(unstaged, "new.txt")["staged"] is False
    assert _entry(unstaged, "new.txt")["untracked"] is True

    await _act(client, machine, "stage", paths=["new.txt"])
    committed = await _act(client, machine, "commit", message="  add new file  ")

    assert committed.status_code == 200
    assert committed.json()["data"]["ok"] is True
    assert committed.json()["data"]["status"]["files"] == []
    assert _git(machine.repo, "log", "-1", "--format=%s") == "add new file\n"


@pytest.mark.asyncio
async def test_stage_all_and_unstage_all(client, machine):
    (machine.repo / "a.txt").write_text("a\n")
    (machine.repo / "tracked.txt").write_text("changed\n")

    staged = (await _act(client, machine, "stage_all")).json()["data"]["status"]
    assert _entry(staged, "a.txt")["staged"] is True
    assert _entry(staged, "tracked.txt")["staged"] is True

    cleared = (await _act(client, machine, "unstage_all")).json()["data"]["status"]
    assert _entry(cleared, "tracked.txt")["staged"] is False
    assert _entry(cleared, "tracked.txt")["unstaged"] is True
    assert _entry(cleared, "a.txt")["untracked"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("paths", "code"),
    [
        ([], "paths_required"),
        ("tracked.txt", "paths_required"),
        (["-p"], "invalid_path"),
        (["a\nb"], "invalid_path"),
        ([""], "path_required"),
    ],
)
async def test_stage_needs_a_list_of_plain_paths(client, machine, paths, code):
    response = await _act(client, machine, "stage", paths=paths)

    assert response.status_code == 400
    assert _error_code(response) == code


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stage", "unstage"])
@pytest.mark.parametrize("path", ["../../elsewhere/secret.txt", "/etc/hosts"])
async def test_a_path_outside_the_workspace_is_403(client, machine, action, path):
    response = await _act(client, machine, action, paths=["tracked.txt", path])

    assert response.status_code == 403
    assert _error_code(response) == "path_outside_workspace"


@pytest.mark.asyncio
async def test_a_commit_needs_a_message_and_refuses_an_empty_commit(client, machine):
    (machine.repo / "tracked.txt").write_text("changed\n")
    await _act(client, machine, "stage", paths=["tracked.txt"])

    blank = await _act(client, machine, "commit", message="   ")
    assert blank.status_code == 400
    assert _error_code(blank) == "message_required"

    assert (await _act(client, machine, "commit", message="change")).status_code == 200
    nothing = await _act(client, machine, "commit", message="again")
    assert nothing.status_code == 409
    assert _error_code(nothing) == "git_failed"
    assert "nothing to commit" in nothing.json()["error"]["message"]


@pytest.mark.asyncio
async def test_a_commit_message_that_looks_like_an_option_is_only_text(client, machine):
    (machine.repo / "new.txt").write_text("x\n")
    await _act(client, machine, "stage_all")

    response = await _act(client, machine, "commit", message="--amend")

    assert response.status_code == 200
    assert _git(machine.repo, "log", "-1", "--format=%s") == "--amend\n"
    assert _git(machine.repo, "rev-list", "--count", "HEAD") == "2\n"  # a new commit, not an amend of init


@pytest.mark.asyncio
async def test_create_switch_and_delete_a_branch(client, machine):
    created = (await _act(client, machine, "create_branch", branchName="feature/x")).json()["data"]
    switched = (await _act(client, machine, "switch_branch", branchName="feature/x")).json()["data"]
    own = await _act(client, machine, "delete_branch", branchName="feature/x")
    back = (await _act(client, machine, "switch_branch", branchName="main")).json()["data"]
    deleted = (await _act(client, machine, "delete_branch", branchName="feature/x")).json()["data"]

    assert created == {"ok": True, "branch": "feature/x", "current": "main"}
    assert switched == {"ok": True, "current": "feature/x", "previous": "main", "hasUncommittedChanges": False}
    assert own.status_code == 409
    assert _error_code(own) == "current_branch"
    assert back["current"] == "main"
    assert deleted == {"ok": True, "deleted": "feature/x", "forceUsed": False}
    assert "feature/x" not in _git(machine.repo, "branch", "--format=%(refname:short)")


@pytest.mark.asyncio
async def test_delete_refuses_an_unmerged_branch_until_it_is_forced(client, machine):
    await _act(client, machine, "create_branch", branchName="wip")
    await _act(client, machine, "switch_branch", branchName="wip")
    (machine.repo / "wip.txt").write_text("x\n")
    await _act(client, machine, "stage_all")
    await _act(client, machine, "commit", message="wip commit")
    await _act(client, machine, "switch_branch", branchName="main")

    safe = await _act(client, machine, "delete_branch", branchName="wip")
    forced = (await _act(client, machine, "delete_branch", branchName="wip", force=True)).json()["data"]

    assert safe.status_code == 409
    assert _error_code(safe) == "not_fully_merged"
    assert forced == {"ok": True, "deleted": "wip", "forceUsed": True}


@pytest.mark.asyncio
async def test_switch_refuses_local_changes_when_git_would_refuse(client, machine):
    """Do not implicitly merge local changes when Git refuses a switch."""
    repo = machine.repo
    (repo / "tracked.txt").write_text("a\nb\nc\n")
    _git(repo, "commit", "-am", "three lines")
    _git(repo, "branch", "feature")
    _git(repo, "switch", "feature")
    (repo / "tracked.txt").write_text("A\nb\nc\n")
    _git(repo, "commit", "-am", "first line")
    _git(repo, "switch", "main")
    (repo / "tracked.txt").write_text("a\nb\nC\n")  # an uncommitted edit to the last line

    response = await _act(client, machine, "switch_branch", branchName="feature")

    assert response.status_code == 409
    assert _git(repo, "branch", "--show-current").strip() == "main"
    assert (repo / "tracked.txt").read_text() == "a\nb\nC\n"


@pytest.mark.asyncio
async def test_stash_create_apply_and_drop(client, machine):
    (machine.repo / "tracked.txt").write_text("hello\nwip\n")

    created = (await _act(client, machine, "create_stash", stashMessage="wip work")).json()["data"]
    assert created == {"ok": True}
    assert (machine.repo / "tracked.txt").read_text() == "hello\n"
    commit = (await _act(client, machine, "list_stashes")).json()["data"]["stashes"][0]["commitHash"]

    applied = (await _act(client, machine, "apply_stash", stashId="stash@{0}")).json()["data"]
    assert applied["ok"] is True
    assert applied["appliedStashId"] == "stash@{0}"
    assert (machine.repo / "tracked.txt").read_text() == "hello\nwip\n"
    assert len(_git(machine.repo, "stash", "list").splitlines()) == 1  # apply keeps the stash
    _git(machine.repo, "checkout", "--", "tracked.txt")

    by_commit = (await _act(client, machine, "apply_stash", stashCommit=commit)).json()["data"]
    assert by_commit["ok"] is True
    _git(machine.repo, "checkout", "--", "tracked.txt")

    dropped = await _act(client, machine, "drop_stash", stashCommit=commit)
    assert dropped.status_code == 409
    assert _error_code(dropped) == "stash_drop_unavailable"
    assert len(_git(machine.repo, "stash", "list").splitlines()) == 1


@pytest.mark.asyncio
async def test_a_stash_with_nothing_to_save_is_409(client, machine):
    response = await _act(client, machine, "create_stash")

    assert response.status_code == 409
    assert _error_code(response) == "nothing_to_stash"


@pytest.mark.asyncio
async def test_external_stash_does_not_make_empty_create_look_successful(client, machine, monkeypatch):
    original_write = code_git._write

    async def external_stash_after_write(root, args, *rest, **kwargs):
        result = await original_write(root, args, *rest, **kwargs)
        if args[:2] == ["stash", "push"]:
            (machine.repo / "tracked.txt").write_text("another writer\n")
            _git(machine.repo, "stash", "push", "-m", "external")
        return result

    monkeypatch.setattr(code_git, "_write", external_stash_after_write)
    response = await _act(client, machine, "create_stash")
    assert response.status_code != 200
    assert "external" in _git(machine.repo, "stash", "list")


@pytest.mark.asyncio
async def test_stash_create_does_not_claim_concurrent_top_as_its_result(client, machine, monkeypatch):
    (machine.repo / "tracked.txt").write_text("own change\n")
    original_write = code_git._write

    async def external_stash_after_write(root, args, *rest, **kwargs):
        result = await original_write(root, args, *rest, **kwargs)
        if args[:2] == ["stash", "push"]:
            (machine.repo / "tracked.txt").write_text("another writer\n")
            _git(machine.repo, "stash", "push", "-m", "external")
        return result

    monkeypatch.setattr(code_git, "_write", external_stash_after_write)
    response = await _act(client, machine, "create_stash", stashMessage="own")
    assert response.status_code == 200
    assert response.json()["data"] == {"ok": True}
    assert len(_git(machine.repo, "stash", "list").splitlines()) == 2


@pytest.mark.asyncio
async def test_unrecognized_stash_output_is_uncertain(client, machine, monkeypatch):
    async def unfamiliar(*args, **kwargs):
        return subprocess.CompletedProcess([], 0, "unexpected output\n", "")

    monkeypatch.setattr(code_git, "_write", unfamiliar)
    response = await _act(client, machine, "create_stash")
    assert response.status_code == 502
    assert _error_code(response) == "stash_outcome_unknown"


@pytest.mark.asyncio
async def test_failed_stash_command_is_uncertain(client, machine, monkeypatch):
    async def failed(*args, **kwargs):
        raise code_git.GitRequestError(409, "git_failed", "injected failure after mutation")

    monkeypatch.setattr(code_git, "_write", failed)
    response = await _act(client, machine, "create_stash")
    assert response.status_code == 502
    assert _error_code(response) == "stash_outcome_unknown"


@pytest.mark.asyncio
async def test_stash_output_is_recognized_under_a_different_locale(client, machine, monkeypatch):
    monkeypatch.setenv("LC_ALL", "not-a-real-locale")
    monkeypatch.setenv("LANGUAGE", "not-a-real-language")
    (machine.repo / "tracked.txt").write_text("own change\n")
    response = await _act(client, machine, "create_stash")
    assert response.status_code == 200
    assert response.json()["data"] == {"ok": True}


@pytest.mark.asyncio
async def test_a_stash_apply_that_conflicts_answers_the_conflicting_files(client, machine):
    (machine.repo / "tracked.txt").write_text("stashed\n")
    await _act(client, machine, "create_stash")
    (machine.repo / "tracked.txt").write_text("committed elsewhere\n")
    _git(machine.repo, "commit", "-am", "other line")

    response = await _act(client, machine, "apply_stash", stashId="0")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["hasUnmergedPaths"] is True
    assert data["conflicts"] == ["tracked.txt"]
    assert len(_git(machine.repo, "stash", "list").splitlines()) == 1  # a refused apply keeps the stash


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create_branch", "switch_branch", "delete_branch"])
@pytest.mark.parametrize(
    "name",
    ["-D", "--force", "--output=x", "+main", "main:other", "a..b", "x.lock", "a b", "@", "a//b", ".hidden"],
)
async def test_a_branch_name_git_could_read_as_an_option_or_refspec_is_400(client, machine, action, name):
    response = await _act(client, machine, action, branchName=name)

    assert response.status_code == 400
    assert _error_code(response) == "invalid_branch"


@pytest.mark.asyncio
async def test_push_sends_the_commit_to_the_configured_remote(client, machine):
    _git(machine.repo, "remote", "add", "origin", str(machine.remote))
    (machine.repo / "pushed.txt").write_text("p\n")
    await _act(client, machine, "stage_all")
    await _act(client, machine, "commit", message="pushed")

    response = await _act(client, machine, "push", remote="origin", branch="main")

    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True
    assert _git(machine.remote, "rev-parse", "main") == _git(machine.repo, "rev-parse", "HEAD")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote", "code"),
    [
        ("https://example.invalid/repo.git", "invalid_remote"),
        ("git@example.invalid:repo.git", "invalid_remote"),
        ("file:///etc", "invalid_remote"),
        ("--upload-pack=touch x", "invalid_remote"),
        ("nowhere", "unknown_remote"),
    ],
)
async def test_push_refuses_a_url_an_option_or_an_unconfigured_remote(client, machine, remote, code):
    response = await _act(client, machine, "push", remote=remote)

    assert response.status_code == 400
    assert _error_code(response) == code


@pytest.mark.asyncio
async def test_push_refuses_a_forced_refspec_and_a_branch_without_a_remote(client, machine):
    _git(machine.repo, "remote", "add", "origin", str(machine.remote))

    forced = await _act(client, machine, "push", remote="origin", branch="+main")
    loose = await _act(client, machine, "push", branch="main")

    assert forced.status_code == 400
    assert _error_code(forced) == "invalid_branch"
    assert loose.status_code == 400
    assert _error_code(loose) == "remote_required"


@pytest.mark.asyncio
async def test_stash_apply_uses_object_selected_before_reflog_moves(client, machine, monkeypatch):
    from kollabor.code_service import git as service_git

    (machine.repo / "tracked.txt").write_text("first stash\n")
    _git(machine.repo, "stash", "push", "-m", "first")
    commit = _git(machine.repo, "rev-parse", "stash@{0}").strip()
    original = service_git._list_stashes
    async def list_then_insert(root, body, settings=None):
        result = await original(root, body, settings)
        (machine.repo / "tracked.txt").write_text("second stash\n")
        _git(machine.repo, "stash", "push", "-m", "second")
        return result
    monkeypatch.setattr(service_git, "_list_stashes", list_then_insert)
    result = await _act(client, machine, "apply_stash", stashCommit=commit)
    assert result.status_code == 200
    assert (machine.repo / "tracked.txt").read_text() == "first stash\n"


@pytest.mark.asyncio
async def test_conflicting_branch_switch_does_not_change_branch_or_create_conflicts(client, machine):
    repo = machine.repo
    _git(repo, "branch", "feature")
    _git(repo, "switch", "feature")
    (repo / "tracked.txt").write_text("feature content\n")
    _git(repo, "commit", "-am", "feature edit")
    _git(repo, "switch", "main")
    (repo / "tracked.txt").write_text("local edit\n")
    response = await _act(client, machine, "switch_branch", branchName="feature")
    assert response.status_code == 409
    assert _git(repo, "branch", "--show-current").strip() == "main"
    assert (repo / "tracked.txt").read_text() == "local edit\n"
    assert _git(repo, "diff", "--name-only", "--diff-filter=U") == ""


@pytest.mark.asyncio
async def test_subfolder_stash_refuses_before_changing_any_files(client, machine):
    inside = machine.repo / "sub"
    inside.mkdir()
    (inside / "file.txt").write_text("base\n")
    _git(machine.repo, "add", "sub/file.txt")
    _git(machine.repo, "commit", "-m", "add subfolder")
    (machine.repo / "tracked.txt").write_text("outside staged\n")
    _git(machine.repo, "add", "tracked.txt")
    (inside / "file.txt").write_text("inside change\n")
    before = _git(machine.repo, "status", "--porcelain")
    result = await client.post("/code/git", json={"workspace": str(inside), "action": "create_stash"})
    assert result.status_code == 409
    assert _git(machine.repo, "status", "--porcelain") == before
    assert _git(machine.repo, "stash", "list") == ""
