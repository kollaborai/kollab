"""Explicit source-checkout updater for Kollab.

This module is intentionally only used by ``kollab --update``. Startup
may check for newer versions, but it should not mutate a user's checkout.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UpdateResult:
    """Result of a source checkout update attempt."""

    success: bool
    message: str


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command against repo and capture text output."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_cmd(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a command in cwd and capture text output."""
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _current_source_root() -> Path:
    """Return the repo root for a source checkout invocation."""
    return Path(__file__).resolve().parents[2]


def run_source_update(repo_root: Path | None = None) -> UpdateResult:
    """Update a source checkout from Git using a fast-forward pull.

    The updater refuses to run on dirty working trees and only performs
    fast-forward pulls. That keeps the command useful for installed-from-source
    users without stomping on local development work.
    """
    repo = (repo_root or _current_source_root()).resolve()
    pyproject = repo / "pyproject.toml"
    if not pyproject.exists():
        return UpdateResult(
            False,
            "Update unavailable: this does not look like a source checkout.",
        )

    root_result = _run_git(repo, "rev-parse", "--show-toplevel")
    if root_result.returncode != 0:
        return UpdateResult(
            False,
            "Update unavailable: this source checkout is not inside a Git repo.",
        )
    git_root = Path(root_result.stdout.strip()).resolve()

    status_result = _run_git(git_root, "status", "--porcelain")
    if status_result.returncode != 0:
        detail = status_result.stderr.strip() or status_result.stdout.strip()
        return UpdateResult(False, f"Unable to inspect Git status:\n{detail}")
    if status_result.stdout.strip():
        return UpdateResult(
            False,
            (
                "Refusing to update because the working tree has local changes.\n"
                "Commit, discard, or move those changes first, then run "
                "`kollab --update` again."
            ),
        )

    branch_result = _run_git(git_root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "HEAD"

    fetch_result = _run_git(git_root, "fetch", "--tags", "--prune")
    if fetch_result.returncode != 0:
        detail = fetch_result.stderr.strip() or fetch_result.stdout.strip()
        return UpdateResult(False, f"Unable to fetch updates from Git:\n{detail}")

    upstream_result = _run_git(
        git_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
    )
    if upstream_result.returncode != 0:
        return UpdateResult(
            False,
            (
                f"Branch `{branch}` has no upstream configured.\n"
                "Set an upstream branch or update manually with Git."
            ),
        )
    upstream = upstream_result.stdout.strip()

    local_result = _run_git(git_root, "rev-parse", "HEAD")
    remote_result = _run_git(git_root, "rev-parse", upstream)
    if local_result.returncode != 0 or remote_result.returncode != 0:
        return UpdateResult(False, "Unable to compare local and upstream revisions.")

    local_sha = local_result.stdout.strip()
    remote_sha = remote_result.stdout.strip()
    if local_sha == remote_sha:
        return UpdateResult(
            True,
            f"Kollab is already up to date on `{branch}` ({local_sha[:8]}).",
        )

    ancestor_result = _run_git(git_root, "merge-base", "--is-ancestor", "HEAD", upstream)
    if ancestor_result.returncode != 0:
        return UpdateResult(
            False,
            (
                f"Refusing to update `{branch}` because it cannot fast-forward "
                f"to `{upstream}`. Pull or rebase manually."
            ),
        )

    pull_result = _run_git(git_root, "pull", "--ff-only")
    if pull_result.returncode != 0:
        detail = pull_result.stderr.strip() or pull_result.stdout.strip()
        return UpdateResult(False, f"Git update failed:\n{detail}")

    install_result = _run_cmd(git_root, sys.executable, "-m", "pip", "install", "-e", ".")
    if install_result.returncode != 0:
        detail = install_result.stderr.strip() or install_result.stdout.strip()
        return UpdateResult(
            False,
            (
                "Git update succeeded, but refreshing the editable install failed:\n"
                f"{detail}"
            ),
        )

    new_sha_result = _run_git(git_root, "rev-parse", "HEAD")
    new_sha = new_sha_result.stdout.strip() if new_sha_result.returncode == 0 else remote_sha
    return UpdateResult(
        True,
        (
            f"Kollab updated on `{branch}`.\n"
            f"  Before: {local_sha[:8]}\n"
            f"  After:  {new_sha[:8]}\n"
            "  Refreshed editable install with pip."
        ),
    )
