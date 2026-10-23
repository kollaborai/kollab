"""Build provenance resolution and serialization for Kollab artifacts."""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping, NamedTuple

UNKNOWN = "unknown"
BUILD_BRANCH_ENV = "KOLLAB_BUILD_BRANCH"
BUILD_SHA_ENV = "KOLLAB_BUILD_SHA"
_BRANCH_MAX_LENGTH = 255
_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


class BuildProvenance(NamedTuple):
    """The source branch and full commit SHA represented by a build."""

    branch: str
    sha: str

    @property
    def short_sha(self) -> str:
        """Return the display form while retaining the full SHA in artifacts."""

        return UNKNOWN if self.sha == UNKNOWN else self.sha[:8]

    @property
    def display(self) -> str:
        """Return the compact build label shared by the CLI and Web UI."""

        return f"{self.branch} @ {self.short_sha}"


def normalize_branch(value: object) -> str:
    """Return a safe, non-empty branch label or ``unknown``."""

    if not isinstance(value, str):
        return UNKNOWN
    branch = value.strip()
    if (
        not branch
        or branch == UNKNOWN
        or len(branch) > _BRANCH_MAX_LENGTH
        or not all(
            character.isprintable() and not character.isspace() for character in branch
        )
    ):
        return UNKNOWN
    return branch


def normalize_sha(value: object) -> str:
    """Accept only a full SHA-1 or SHA-256 object ID."""

    if not isinstance(value, str):
        return UNKNOWN
    sha = value.strip()
    if not _SHA_PATTERN.fullmatch(sha):
        return UNKNOWN
    return sha.lower()


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def read_embedded_provenance(path: Path) -> BuildProvenance | None:
    """Read the generated metadata module used to carry values through sdists."""

    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except OSError, SyntaxError, UnicodeDecodeError:
        return None

    values: dict[str, object] = {}
    for statement in module.body:
        if (
            not isinstance(statement, ast.Assign)
            or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
        ):
            continue
        name = statement.targets[0].id
        if name not in {"BUILD_BRANCH", "BUILD_SHA"}:
            continue
        try:
            values[name] = ast.literal_eval(statement.value)
        except ValueError, TypeError:
            continue

    if "BUILD_BRANCH" not in values or "BUILD_SHA" not in values:
        return None
    return BuildProvenance(
        normalize_branch(values["BUILD_BRANCH"]),
        normalize_sha(values["BUILD_SHA"]),
    )


def resolve_build_provenance(
    repo_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    inherited: BuildProvenance | None = None,
) -> BuildProvenance:
    """Capture the exact checkout, using environment or sdist data without Git.

    In a Git checkout the checked-out branch and HEAD are authoritative. Detached
    checkouts have no branch name, but still retain their full HEAD SHA. When Git
    metadata is absent, build variables are preferred, followed by values carried
    in an sdist; unresolved fields are explicitly marked ``unknown``.
    """

    root = (
        Path(repo_root)
        if repo_root is not None
        else Path(__file__).resolve().parents[1]
    )
    environment = os.environ if environ is None else environ

    git_root = _git_output(root, "rev-parse", "--show-toplevel")
    if git_root is not None:
        branch = normalize_branch(
            _git_output(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        )
        sha = normalize_sha(_git_output(root, "rev-parse", "--verify", "HEAD"))
        # A supplied full SHA is a safe fallback only if Git cannot resolve HEAD.
        if sha == UNKNOWN:
            sha = normalize_sha(environment.get(BUILD_SHA_ENV))
        return BuildProvenance(branch, sha)

    branch = normalize_branch(environment.get(BUILD_BRANCH_ENV))
    sha = normalize_sha(environment.get(BUILD_SHA_ENV))
    if inherited is not None:
        branch = branch if branch != UNKNOWN else normalize_branch(inherited.branch)
        sha = sha if sha != UNKNOWN else normalize_sha(inherited.sha)
    return BuildProvenance(branch, sha)


def write_embedded_provenance(path: Path, provenance: BuildProvenance) -> None:
    """Write a tiny Python module containing immutable build-time values."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '"""Generated build provenance; do not edit."""\n'
        f"BUILD_BRANCH = {provenance.branch!r}\n"
        f"BUILD_SHA = {provenance.sha!r}\n",
        encoding="utf-8",
    )
