"""The source Web UI must serve the build just verified in this checkout."""

from fastapi.testclient import TestClient
from kollabor_webui import server


def configure_bundles(monkeypatch, tmp_path):
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


def test_a_failed_rebuild_keeps_serving_the_previous_build(monkeypatch, tmp_path, capsys):
    import subprocess

    from kollabor_webui import rebuild_stale_bundle

    checkout(monkeypatch, tmp_path, built=1000, edited=2000)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "error TS2304"))
    rebuild_stale_bundle()
    out = capsys.readouterr().out
    assert "rebuild failed" in out and "error TS2304" in out
