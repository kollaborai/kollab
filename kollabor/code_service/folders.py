"""Discover linked Git worktrees on the session daemon's machine."""

import os

from . import git


def linked_folders(root: str) -> list[dict[str, str]]:
    root = os.path.realpath(root)
    folders = [{"path": root, "branch": ""}]
    try:
        result = git._run_git(
            root, ["worktree", "list", "--porcelain", "-z"], True, timeout=10, errors="surrogateescape"
        )
    except git.GitRequestError as exc:
        if exc.code == "git_output_too_large":
            raise
        return folders
    if result.returncode:
        return folders
    entries = result.stdout.split("\0\0")
    seen = {root}
    for entry in entries:
        fields = entry.strip("\0").split("\0")
        path = next((x[9:] for x in fields if x.startswith("worktree ")), "")
        branch = next((x[7:].removeprefix("refs/heads/") for x in fields if x.startswith("branch ")), "")
        if not path or not os.path.isdir(path):
            continue
        path = os.path.realpath(path)
        if path == root:
            folders[0]["branch"] = branch
        elif path not in seen:
            seen.add(path)
            folders.append({"path": path, "branch": branch})
    return folders


def browse(path: str) -> list[dict[str, str]]:
    """List directories, including hidden directories, on the daemon's machine."""
    with os.scandir(path) as entries:
        result = []
        for entry in entries:
            try:
                if entry.is_dir():
                    result.append({"name": entry.name, "path": os.path.realpath(entry.path)})
            except OSError:
                continue
    return sorted(result, key=lambda item: (item["name"].casefold(), item["name"]))
