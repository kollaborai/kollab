"""Unit tests for hub project scoping (kollab-8jd)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_env_and_cache(monkeypatch):
    """Each test starts clean: no env vars, cache cleared."""
    for key in ("KOLLAB_HUB_PROJECT_SCOPED", "KOLLAB_PROJECT_ROOT"):
        monkeypatch.delenv(key, raising=False)

    from plugins.hub import project_scope

    project_scope.resolve_project_root.cache_clear()
    yield
    project_scope.resolve_project_root.cache_clear()


def test_is_project_scoped_default_false():
    from plugins.hub.project_scope import is_project_scoped

    assert is_project_scoped() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", "On"])
def test_is_project_scoped_truthy_values(monkeypatch, val):
    from plugins.hub.project_scope import is_project_scoped

    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", val)
    assert is_project_scoped() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "anything"])
def test_is_project_scoped_falsy_values(monkeypatch, val):
    from plugins.hub.project_scope import is_project_scoped

    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", val)
    assert is_project_scoped() is False


def test_resolve_project_root_uses_env_override_first(monkeypatch, tmp_path):
    from plugins.hub.project_scope import resolve_project_root

    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(tmp_path))
    assert resolve_project_root() == tmp_path.resolve()


def test_resolve_project_root_falls_back_to_git(monkeypatch):
    """When no env override, tries git rev-parse."""
    from plugins.hub import project_scope

    fake_root = "/some/repo/root"

    class FakeResult:
        returncode = 0
        stdout = fake_root + "\n"

    with patch.object(project_scope.subprocess, "run", return_value=FakeResult()):
        project_scope.resolve_project_root.cache_clear()
        assert project_scope.resolve_project_root() == Path(fake_root).resolve()


def test_resolve_project_root_falls_back_to_cwd(monkeypatch, tmp_path):
    """When no env override and git fails, uses cwd."""
    from plugins.hub import project_scope

    class FakeResult:
        returncode = 128
        stdout = ""

    monkeypatch.chdir(tmp_path)
    with patch.object(project_scope.subprocess, "run", return_value=FakeResult()):
        project_scope.resolve_project_root.cache_clear()
        assert project_scope.resolve_project_root() == tmp_path.resolve()


def test_project_id_encoding_matches_conversation_scheme(monkeypatch, tmp_path):
    """Encoding uses / -> _ with leading underscore stripped, matching
    kollabor's existing projects/<encoded>/conversations/ scheme."""
    from plugins.hub.project_scope import resolve_project_id

    # Use tmp_path so we don't hit macOS firmlinks (/home -> /System/Volumes/Data/home)
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(tmp_path))
    expected = str(tmp_path.resolve()).replace("/", "_").lstrip("_")
    assert resolve_project_id() == expected


def test_project_socket_key_is_short_hash(monkeypatch):
    """Unix socket paths cap at 104 bytes on macOS — we hash to stay under."""
    from plugins.hub.project_scope import get_project_socket_key

    monkeypatch.setenv(
        "KOLLAB_PROJECT_ROOT",
        "/home/example/" + "x" * 200 + "/project",
    )
    key = get_project_socket_key()
    assert len(key) == 12
    assert all(c in "0123456789abcdef" for c in key)


def test_project_socket_key_stable_for_same_root(monkeypatch):
    """Same project root always hashes to the same socket key."""
    from plugins.hub.project_scope import get_project_socket_key

    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", "/a/b/c")
    k1 = get_project_socket_key()

    from plugins.hub import project_scope

    project_scope.resolve_project_root.cache_clear()
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", "/a/b/c")
    k2 = get_project_socket_key()
    assert k1 == k2


def test_project_socket_key_differs_for_different_roots(monkeypatch):
    from plugins.hub import project_scope
    from plugins.hub.project_scope import get_project_socket_key

    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", "/a/b/c")
    k1 = get_project_socket_key()

    project_scope.resolve_project_root.cache_clear()
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", "/x/y/z")
    k2 = get_project_socket_key()
    assert k1 != k2


def test_get_hub_dir_global_mode(monkeypatch):
    """Global mode (default) uses ~/.kollab/hub/."""
    from plugins.hub.presence import get_hub_dir

    d = get_hub_dir()
    assert str(d).endswith("/.kollab/hub")


def test_get_hub_dir_project_scoped_mode(monkeypatch, tmp_path):
    """Project scoped mode uses ~/.kollab/projects/<encoded>/hub/."""
    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "1")
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", str(tmp_path))

    from plugins.hub import project_scope
    from plugins.hub.presence import get_hub_dir

    project_scope.resolve_project_root.cache_clear()

    d = get_hub_dir()
    # Must live under projects/<encoded>/hub and NOT be the global path
    assert "projects" in str(d)
    assert str(d).endswith("/hub")
    assert str(d) != str(Path.home() / ".kollab" / "hub")


def test_get_socket_dir_global_mode(monkeypatch):
    from plugins.hub.presence import get_socket_dir

    d = get_socket_dir()
    assert str(d) == "/tmp/kollabor-hub"


def test_get_socket_dir_project_scoped_is_subdir(monkeypatch):
    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "1")
    monkeypatch.setenv("KOLLAB_PROJECT_ROOT", "/test/project")

    from plugins.hub import project_scope
    from plugins.hub.presence import get_socket_dir

    project_scope.resolve_project_root.cache_clear()
    d = get_socket_dir()
    assert str(d).startswith("/tmp/kollabor-hub/")
    assert str(d) != "/tmp/kollabor-hub"
    # 12 hex chars for the project key
    tail = str(d).rsplit("/", 1)[-1]
    assert len(tail) == 12
