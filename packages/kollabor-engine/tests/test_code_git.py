"""The engine's git API for the floating code panel, part 1: the read actions and the checks around them.

Covers the envelope and failure codes of docs/specs/floating-code-shell.md, the folder rule on ``workspace``, and the
refusals in routes/code_git.py: option-like values, control characters, and paths that leave the workspace.
"""

import os
import subprocess
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.server import create_app  # type: ignore[import-not-found]


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as ac:
        yield ac


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
async def test_get_answers_only_the_three_read_actions(client, machine):
    query = {"workspace": str(machine.repo)}

    status = await client.get("/code/git", params={**query, "action": "status"})
    current = await client.get("/code/git", params={**query, "action": "current_branch"})
    listed = await client.get("/code/git", params={**query, "action": "list_branches"})
    refused = await client.get("/code/git", params={**query, "action": "show"})

    assert status.json()["data"]["branch"] == "main"
    assert current.json()["data"]["current"] == "main"
    assert listed.json()["data"]["current"] == "main"
    assert refused.status_code == 400
    assert _error_code(refused) == "unknown_action"


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
async def test_a_workspace_outside_the_folder_rule_is_400(client, machine):
    response = await client.post("/code/git", json={"action": "status", "workspace": str(machine.elsewhere)})

    assert response.status_code == 400
    assert _error_code(response) == "bad_workspace"
    assert "outside" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_a_missing_or_nonexistent_workspace_is_400(client, machine):
    no_folder = await client.post("/code/git", json={"action": "status"})
    nowhere = await client.post("/code/git", json={"action": "status", "workspace": str(machine.home / "nope")})

    assert no_folder.status_code == 400
    assert _error_code(no_folder) == "workspace_required"
    assert nowhere.status_code == 400
    assert _error_code(nowhere) == "bad_workspace"
    assert "does not exist" in nowhere.json()["error"]["message"]


@pytest.mark.asyncio
async def test_bad_json_and_an_unknown_action_are_400(client, machine):
    not_json = await client.post("/code/git", content=b"not json", headers={"content-type": "application/json"})
    unknown = await client.post("/code/git", json=_body(machine, "push_everything"))

    assert not_json.status_code == 400
    assert _error_code(not_json) == "invalid_body"
    assert unknown.status_code == 400
    assert _error_code(unknown) == "unknown_action"
