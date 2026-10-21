"""The source Web UI must serve the build just verified in this checkout."""

import subprocess

import pytest
from fastapi.testclient import TestClient
from kollabor_webui import server


def configure_bundles(monkeypatch, tmp_path):
    monkeypatch.delenv("KOLLAB_FRONTEND_DIST", raising=False)
    source = tmp_path / "frontend" / "dist"
    packaged = tmp_path / "static" / "assistant"
    monkeypatch.setattr(server, "SOURCE_ASSISTANT_STATIC_DIR", source)
    monkeypatch.setattr(server, "ASSISTANT_STATIC_DIR", packaged)
    return source, packaged


def test_fresh_source_bundle_wins_over_stale_packaged_copy(monkeypatch, tmp_path):
    source, packaged = configure_bundles(monkeypatch, tmp_path)
    (source / "assets").mkdir(parents=True)
    packaged.mkdir(parents=True)
    (source / "index.html").write_text('<script src="/assets/current.js"></script>')
    (source / "assets" / "current.js").write_text("/* current ACP cards */")
    (packaged / "index.html").write_text("stale bundle")
    with TestClient(
        server.create_app(),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 12345),
    ) as client:
        assert "current.js" in client.get("/").text
        assert client.get("/assets/current.js").text == "/* current ACP cards */"


def test_installed_package_uses_its_bundle_without_source_output(monkeypatch, tmp_path):
    _, packaged = configure_bundles(monkeypatch, tmp_path)
    packaged.mkdir(parents=True)
    (packaged / "index.html").write_text("packaged ACP UI")
    with TestClient(
        server.create_app(),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 12345),
    ) as client:
        assert client.get("/").text == "packaged ACP UI"


def test_the_entry_page_is_always_revalidated(monkeypatch, tmp_path):
    source, _ = configure_bundles(monkeypatch, tmp_path)
    source.mkdir(parents=True)
    (source / "index.html").write_text("current")
    with TestClient(
        server.create_app(),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 12345),
    ) as client:
        for path in ("/", "/trajectory"):
            assert client.get(path).headers["cache-control"] == "no-cache", path


def checkout(monkeypatch, tmp_path, built, edited):
    """A source checkout whose dist/index.html and src file carry these mtimes; returns the builds run."""
    import os
    import shutil
    import subprocess

    frontend = tmp_path / "frontend"
    monkeypatch.delenv("KOLLAB_FRONTEND_DIST", raising=False)
    (frontend / "src").mkdir(parents=True)
    (frontend / "node_modules").mkdir()
    (frontend / "src" / "App.tsx").write_text("app")
    os.utime(frontend / "src" / "App.tsx", (edited, edited))
    if built is not None:
        (frontend / "dist").mkdir()
        (frontend / "dist" / "index.html").write_text("build")
        os.utime(frontend / "dist" / "index.html", (built, built))
    monkeypatch.setattr(server, "SOURCE_ASSISTANT_STATIC_DIR", frontend / "dist")
    monkeypatch.setattr(shutil, "which", lambda name: f"/bin/{name}")
    builds = []

    def run(cmd, **kwargs):
        builds.append((cmd, kwargs["cwd"]))
        (frontend / "dist").mkdir(exist_ok=True)
        (frontend / "dist/index.html").write_text("fresh build")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return frontend, builds


def test_a_source_change_after_the_build_rebuilds_it(monkeypatch, tmp_path):
    from kollabor_webui import rebuild_stale_bundle

    frontend, builds = checkout(monkeypatch, tmp_path, built=1000, edited=2000)
    rebuild_stale_bundle()
    assert builds == [(["/bin/npm", "run", "build"], frontend)]


def test_a_missing_build_is_built(monkeypatch, tmp_path):
    from kollabor_webui import rebuild_stale_bundle

    frontend, builds = checkout(monkeypatch, tmp_path, built=None, edited=2000)
    rebuild_stale_bundle()
    assert builds == [(["/bin/npm", "run", "build"], frontend)]


def test_a_current_build_is_left_alone(monkeypatch, tmp_path):
    from kollabor_webui import rebuild_stale_bundle

    _, builds = checkout(monkeypatch, tmp_path, built=2000, edited=1000)
    rebuild_stale_bundle()
    assert builds == []


def test_an_installed_package_never_builds(monkeypatch, tmp_path):
    import shutil

    from kollabor_webui import rebuild_stale_bundle

    frontend, builds = checkout(monkeypatch, tmp_path, built=1000, edited=2000)
    shutil.rmtree(frontend / "src")
    rebuild_stale_bundle()
    assert builds == []


def test_a_failed_rebuild_refuses_to_serve_the_previous_build(monkeypatch, tmp_path):
    import subprocess

    from kollabor_webui import rebuild_stale_bundle

    checkout(monkeypatch, tmp_path, built=1000, edited=2000)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "error TS2304"))
    with pytest.raises(RuntimeError, match="rebuild failed.*") as caught:
        rebuild_stale_bundle()
    assert "error TS2304" in str(caught.value)


@pytest.mark.parametrize("missing", ["npm", "node_modules"])
def test_stale_source_requires_its_toolchain(monkeypatch, tmp_path, missing):
    import shutil

    from kollabor_webui import rebuild_stale_bundle

    frontend, builds = checkout(monkeypatch, tmp_path, built=1000, edited=2000)
    if missing == "npm":
        monkeypatch.setattr(shutil, "which", lambda _: None)
    else:
        (frontend / "node_modules").rmdir()
    with pytest.raises(RuntimeError, match="source is newer than its build"):
        rebuild_stale_bundle()
    assert builds == []


def test_current_source_output_needs_no_toolchain(monkeypatch, tmp_path):
    import shutil

    from kollabor_webui import rebuild_stale_bundle

    _, builds = checkout(monkeypatch, tmp_path, built=2000, edited=1000)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    rebuild_stale_bundle()
    assert builds == []


@pytest.mark.parametrize("error", [OSError("missing executable"), subprocess.TimeoutExpired("npm", 300)])
def test_stale_source_cannot_fall_back_after_a_build_launch_failure(monkeypatch, tmp_path, error):
    from kollabor_webui import rebuild_stale_bundle

    checkout(monkeypatch, tmp_path, built=1000, edited=2000)

    def failed(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="rebuild could not finish"):
        rebuild_stale_bundle()


@pytest.mark.parametrize("changed", ["public/icon.png", "CHANGELOG.md"])
def test_public_assets_and_packaged_changelog_invalidate_source_build(monkeypatch, tmp_path, changed):
    import os

    from kollabor_webui import rebuild_stale_bundle

    frontend, builds = checkout(monkeypatch, tmp_path, built=2000, edited=1000)
    path = frontend / changed
    path.parent.mkdir(exist_ok=True)
    path.write_text("updated asset")
    os.utime(path, (3000, 3000))
    rebuild_stale_bundle()
    assert builds


def test_explicit_runtime_build_wins_and_never_rebuilds_source(monkeypatch, tmp_path):
    from kollabor_webui import rebuild_stale_bundle

    frontend, builds = checkout(monkeypatch, tmp_path, built=1000, edited=2000)
    selected = tmp_path / "selected"
    (selected / "assets").mkdir(parents=True)
    (selected / "index.html").write_text('<script src="./assets/selected.js"></script>')
    (selected / "assets/selected.js").write_text("/* selected app */")
    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(selected))
    rebuild_stale_bundle()
    assert builds == []
    with TestClient(server.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        assert "selected.js" in client.get("/").text
        assert "selected.js" in client.get("/learn").text
        assert client.get("/assets/selected.js").text == "/* selected app */"
    assert (frontend / "dist/index.html").read_text() == "build"


@pytest.mark.parametrize("invalid", ["empty", "missing", "root_symlink", "asset_symlink"])
def test_invalid_runtime_selection_never_falls_back(monkeypatch, tmp_path, invalid):
    from kollabor_webui import rebuild_stale_bundle

    _, builds = checkout(monkeypatch, tmp_path, built=1000, edited=2000)
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "index.html").write_text("chosen app")
    value = str(selected)
    if invalid == "empty":
        value = ""
    elif invalid == "missing":
        value = str(tmp_path / "missing")
    elif invalid == "root_symlink":
        link = tmp_path / "link"
        link.symlink_to(selected, target_is_directory=True)
        value = str(link)
    else:
        (selected / "leak").symlink_to(tmp_path / "outside")
    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", value)
    with pytest.raises(ValueError):
        rebuild_stale_bundle()
    with pytest.raises(ValueError):
        server.create_app()
    assert builds == []


def test_invalid_explicit_build_fails_before_binding_a_listener(monkeypatch, tmp_path):
    import kollabor_webui

    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(tmp_path / "missing"))
    monkeypatch.setattr(kollabor_webui, "_listen", lambda *_: pytest.fail("opened a listener"))
    with pytest.raises(ValueError):
        kollabor_webui.main()
