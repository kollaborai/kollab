"""Runtime version identity tests for source and installed builds."""

from __future__ import annotations

import subprocess
from pathlib import Path

from packaging.version import Version

import kollabor.build_info as build_info
import kollabor.version as version_module
from kollabor.build_info import BuildProvenance
from kollabor.updates.version_check_service import _cache_shows_current_or_older
from kollabor.updates.version_comparator import is_newer_version


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _make_source_tree(root: Path, *, tag: str | None = None) -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "kollab"\nversion = "0.18.5"\n',
        encoding="utf-8",
    )
    (root / "kollabor").mkdir()
    (root / "kollabor" / "version.py").write_text("# test source tree\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Kollab Test")
    _git(root, "config", "user.email", "kollab-test@example.invalid")
    _git(root, "add", "pyproject.toml", "kollabor/version.py")
    _git(root, "commit", "-m", "test source")
    if tag is not None:
        _git(root, "tag", tag)
    return root


def test_untagged_source_version_includes_short_revision(tmp_path, monkeypatch):
    root = _make_source_tree(tmp_path / "source")
    monkeypatch.setattr(version_module, "__file__", str(root / "kollabor" / "version.py"))
    short_sha = _git(root, "rev-parse", "--short=8", "HEAD")

    assert version_module.get_kollabor_version() == f"0.18.5+dev.g{short_sha}"
    assert version_module.get_build_provenance().short_sha == short_sha


def test_live_source_revision_overrides_stale_embedded_provenance(tmp_path, monkeypatch):
    root = _make_source_tree(tmp_path / "source")
    monkeypatch.setattr(version_module, "__file__", str(root / "kollabor" / "version.py"))
    monkeypatch.setattr(
        version_module,
        "_EMBEDDED_PROVENANCE",
        BuildProvenance("stale-embedded", "b" * 40),
    )
    short_sha = _git(root, "rev-parse", "--short=8", "HEAD")

    assert version_module.get_kollabor_version() == f"0.18.5+dev.g{short_sha}"
    assert version_module.get_build_provenance().short_sha == short_sha


def test_unknown_source_revision_is_still_marked_as_dev(tmp_path, monkeypatch):
    root = _make_source_tree(tmp_path / "source")
    monkeypatch.setattr(version_module, "__file__", str(root / "kollabor" / "version.py"))
    monkeypatch.setattr(
        version_module,
        "resolve_build_provenance",
        lambda *_args, **_kwargs: BuildProvenance("unknown", "unknown"),
    )

    assert version_module.get_kollabor_version() == "0.18.5+dev.gunknown"


def test_clean_exact_release_tag_keeps_clean_source_version(tmp_path, monkeypatch):
    root = _make_source_tree(tmp_path / "source", tag="v0.18.5")
    monkeypatch.setattr(version_module, "__file__", str(root / "kollabor" / "version.py"))

    assert version_module.get_kollabor_version() == "0.18.5"
    assert build_info.is_exact_clean_release_tag(root, "0.18.5") is True


def test_release_tag_must_match_version_and_clean_worktree(tmp_path):
    root = _make_source_tree(tmp_path / "source", tag="v0.18.5")

    assert build_info.is_exact_clean_release_tag(root, "0.18.6") is False
    (root / "untracked.txt").write_text("dirty", encoding="utf-8")
    assert build_info.is_exact_clean_release_tag(root, "0.18.5") is False


def test_installed_release_version_remains_clean(monkeypatch):
    monkeypatch.setattr(version_module, "_is_running_from_source", lambda: False)
    monkeypatch.setattr(version_module, "get_version", lambda _package: "0.18.5")

    assert version_module.get_kollabor_version() == "0.18.5"


def test_conversation_logger_uses_canonical_runtime_version(monkeypatch):
    import kollabor_ai.conversation_logger as conversation_logger

    monkeypatch.setattr(conversation_logger, "get_version", lambda _package: "1.0.0")
    monkeypatch.setattr(version_module, "get_kollabor_version", lambda: "unexpected")

    assert conversation_logger._get_app_version() == version_module.__version__


def test_dev_local_version_parses_and_does_not_create_same_release_update():
    current = "0.18.5+dev.g01234567"

    assert str(Version(current)) == current
    assert is_newer_version(current, "0.18.5") is False
    assert is_newer_version(current, "0.18.6") is True
    assert _cache_shows_current_or_older("0.18.5", current) is True
