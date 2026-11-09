"""Discover linked Git worktrees on the session daemon's machine."""

import os
import subprocess


def linked_folders(root: str) -> list[dict[str, str]]:
    root = os.path.realpath(root)
    folders = [{"path": root, "branch": ""}]
    excluded = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")
    env = {k: v for k, v in os.environ.items() if k not in excluded}
    try:
        result = subprocess.run(
            ["git", "-C", root, "worktree", "list", "--porcelain", "-z"],
            capture_output=True, timeout=10, env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return folders
    if result.returncode:
        return folders
    entries = result.stdout.decode("utf-8", errors="surrogateescape").split("\0\0")
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
