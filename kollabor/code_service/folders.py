"""Discover linked Git worktrees on the session daemon's machine."""

import heapq
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


FOLDER_PAGE_SIZE = 200


def browse(path: str, after: str = "") -> tuple[list[dict[str, str]], str | None]:
    """Return one sorted page while retaining only a bounded number of entries."""
    with os.scandir(path) as entries:
        def candidates():
            for entry in entries:
                try:
                    if entry.is_dir() and (
                        not after or (entry.name.casefold(), entry.name) > (after.casefold(), after)
                    ):
                        yield entry.name
                except OSError:
                    continue

        names = heapq.nsmallest(FOLDER_PAGE_SIZE + 1, candidates(), key=lambda name: (name.casefold(), name))
    more = len(names) > FOLDER_PAGE_SIZE
    names = names[:FOLDER_PAGE_SIZE]
    return ([{"name": name, "path": os.path.realpath(os.path.join(path, name))} for name in names],
            names[-1] if more else None)
