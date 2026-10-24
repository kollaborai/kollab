"""Tests for exact-checkout provenance and its artifact representation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from kollabor.build_info import (
    BUILD_BRANCH_ENV,
    BUILD_SHA_ENV,
    UNKNOWN,
    BuildProvenance,
    read_embedded_provenance,
    resolve_build_provenance,
    write_embedded_provenance,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repository(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet")
    _git(root, "checkout", "--quiet", "-b", "provenance-test")
    _git(root, "config", "user.name", "Kollab Test")
    _git(root, "config", "user.email", "kollab-test@example.invalid")
    (root / "content.txt").write_text("test\n", encoding="utf-8")
    _git(root, "add", "content.txt")
    _git(root, "commit", "--quiet", "-m", "test provenance")
    return _git(root, "rev-parse", "--verify", "HEAD")


def test_attached_checkout_reports_its_branch_and_full_head(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    sha = _repository(root)

    provenance = resolve_build_provenance(root, environ={})

    assert provenance == BuildProvenance("provenance-test", sha)
    assert len(provenance.sha) == 40
    assert provenance.short_sha == sha[:8]


def test_detached_checkout_uses_unknown_branch_but_keeps_head_sha(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    sha = _repository(root)
    _git(root, "checkout", "--quiet", "--detach", "HEAD")

    provenance = resolve_build_provenance(root, environ={})

    assert provenance == BuildProvenance(UNKNOWN, sha)


def test_environment_fallback_works_without_git(tmp_path: Path) -> None:
    root = tmp_path / "source-archive"
    root.mkdir()
    sha = "abcdef01" * 5

    provenance = resolve_build_provenance(
        root,
        environ={BUILD_BRANCH_ENV: "release/0.18.0", BUILD_SHA_ENV: sha},
    )

    assert provenance == BuildProvenance("release/0.18.0", sha)


def test_missing_or_invalid_values_are_unknown_without_git(tmp_path: Path) -> None:
    root = tmp_path / "source-archive"
    root.mkdir()

    assert resolve_build_provenance(root, environ={}) == BuildProvenance(
        UNKNOWN, UNKNOWN
    )
    assert resolve_build_provenance(
        root,
        environ={
            BUILD_BRANCH_ENV: "release/0.18.0\nforged",
            BUILD_SHA_ENV: "not-a-sha",
        },
    ) == BuildProvenance(UNKNOWN, UNKNOWN)


def test_sdist_metadata_is_preserved_when_git_is_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "source-archive"
    root.mkdir()
    inherited = BuildProvenance("release/0.18.0", "a" * 40)
    metadata = root / "kollabor" / "_build_info.py"
    write_embedded_provenance(metadata, inherited)

    loaded = read_embedded_provenance(metadata)
    resolved = resolve_build_provenance(root, environ={}, inherited=loaded)

    assert loaded == inherited
    assert resolved == inherited


def test_sdist_inside_other_git_repo_uses_embedded_provenance(
    tmp_path: Path,
) -> None:
    outer_root = tmp_path / "outer-checkout"
    outer_sha = _repository(outer_root)
    source_root = outer_root / "unpacked-sdist"
    source_root.mkdir()
    embedded = BuildProvenance("release/0.18.0", "b" * 40)
    metadata = source_root / "kollabor" / "_build_info.py"
    write_embedded_provenance(metadata, embedded)

    loaded = read_embedded_provenance(metadata)
    resolved = resolve_build_provenance(source_root, environ={}, inherited=loaded)

    assert resolved == embedded
    assert resolved.sha != outer_sha


def test_embedded_metadata_reader_rejects_non_generated_code(tmp_path: Path) -> None:
    invalid = tmp_path / "_build_info.py"
    invalid.write_text("raise RuntimeError('do not execute')\n", encoding="utf-8")

    assert read_embedded_provenance(invalid) is None
