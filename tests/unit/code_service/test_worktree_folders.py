import asyncio
import subprocess

from kollabor import code_service


def run(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_linked_worktrees_are_listed_and_selected_on_the_daemon(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run("init", "-q", cwd=repo)
    run("-c", "user.name=Test", "-c", "user.email=t@example.invalid",
        "commit", "--allow-empty", "-qm", "init", cwd=repo)
    other = tmp_path / "elsewhere" / "branch"
    other.parent.mkdir()
    run("worktree", "add", "-qb", "worker", str(other), cwd=repo)
    (other / "worker.txt").write_text("worker")
    (repo / "main.txt").write_text("main")
    listing = asyncio.run(code_service.call("folders", {}, root=str(repo)))
    assert str(other) in [f["path"] for f in listing["body"]["data"]["folders"]]
    tree = asyncio.run(code_service.call("fs.tree", {"codeRoot": str(other)}, root=str(repo)))
    assert [item["name"] for item in tree["body"]["data"]["tree"]] == ["worker.txt"]
    status = asyncio.run(code_service.call("git", {"action": "status", "codeRoot": str(other)}, root=str(repo)))
    assert status["body"]["data"]["branch"] == "worker"
    other_folder = tmp_path / "independent"
    other_folder.mkdir()
    run("init", "-q", "-b", "separate", cwd=other_folder)
    (other_folder / "independent.txt").write_text("elsewhere")
    browse = asyncio.run(code_service.call("folders.browse", {"path": str(tmp_path)}, root=str(repo)))
    assert str(other_folder) in [entry["path"] for entry in browse["body"]["data"]["folders"]]
    tree = asyncio.run(code_service.call("fs.tree", {"codeRoot": str(other_folder)}, root=str(repo)))
    assert [item["name"] for item in tree["body"]["data"]["tree"]] == ["independent.txt"]
    git = asyncio.run(code_service.call("git", {"action": "status", "codeRoot": str(other_folder)}, root=str(repo)))
    assert git["body"]["data"]["branch"] == "separate"
    assert git["body"]["data"]["files"][0]["path"] == "independent.txt"


def test_non_git_folder_and_invalid_selection(tmp_path):
    listing = asyncio.run(code_service.call("folders", {}, root=str(tmp_path)))
    assert listing["body"]["data"]["folders"] == [{"path": str(tmp_path), "branch": ""}]
    bad = asyncio.run(code_service.call("fs.tree", {"codeRoot": "\0"}, root=str(tmp_path)))
    assert bad["status"] == 400


def test_worktree_discovery_surfaces_oversized_output(tmp_path, monkeypatch):
    from kollabor.code_service import git

    run("init", "-q", cwd=tmp_path)
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", 1)
    listing = asyncio.run(code_service.call("folders", {}, root=str(tmp_path)))
    assert listing["status"] == 413
    assert listing["body"]["error"]["code"] == "git_output_too_large"


def test_browse_rejects_missing_and_relative_directories(tmp_path):
    for path in ("relative", str(tmp_path / "missing"), "\0"):
        answer = asyncio.run(code_service.call("folders.browse", {"path": path}, root=str(tmp_path)))
        assert answer["status"] in (400, 404)


def test_browse_pages_all_directories_in_order(tmp_path, monkeypatch):
    from kollabor.code_service import folders

    monkeypatch.setattr(folders, "FOLDER_PAGE_SIZE", 2)
    for name in ("zebra", "alpha", "Beta", "delta", "gamma"):
        (tmp_path / name).mkdir()
    names = []
    cursor = ""
    while True:
        answer = asyncio.run(
            code_service.call("folders.browse", {"path": str(tmp_path), "after": cursor}, root=str(tmp_path))
        )
        data = answer["body"]["data"]
        assert len(data["folders"]) <= 2
        names.extend(item["name"] for item in data["folders"])
        cursor = data["next"]
        if cursor is None:
            break
    assert names == ["alpha", "Beta", "delta", "gamma", "zebra"]
