"""Plugin web assets are isolated to their approved distribution directory."""

import json

import pytest
from fastapi.testclient import TestClient
from kollabor_webui.server import create_app

from kollabor_plugins.packages import files_sha256


def write_registry(home, root):
    digest = files_sha256(root)
    (home / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": True, "files_sha256": digest}}, "dev": {}})
    )


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    dist = home / "plugins" / "hello" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "main.js").write_text("export default 1")
    (dist / "page.html").write_text("private html")
    (dist / ".secret.js").write_text("private")
    outside = tmp_path / "outside.js"
    outside.write_text("private outside")
    (dist / "escape.js").symlink_to(outside)
    write_registry(home, home / "plugins/hello")
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        yield client


def test_plugin_dist_asset_served_with_nosniff(assets):
    response = assets.get("/plugins/hello/web/main.js?v=abc")
    assert response.status_code == 200
    assert response.text == "export default 1"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["page.html", ".secret.js", "escape.js", "missing.js", "nested/.secret.js"])
def test_plugin_unsafe_assets_never_fall_through_to_spa(assets, path):
    response = assets.get(f"/plugins/hello/web/{path}")
    assert response.status_code == 404


def test_installed_plugin_root_cannot_be_symlinked_outside_plugin_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    outside = tmp_path / "external"
    dist = outside / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "main.js").write_text("external code")
    (plugins / "hello").symlink_to(outside, target_is_directory=True)
    write_registry(home, outside)
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        assert client.get("/plugins/hello/web/main.js").status_code == 404


def test_installed_plugin_asset_is_not_served_after_approved_files_change(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    root = home / "plugins/hello"
    dist = root / "web/dist"
    dist.mkdir(parents=True)
    asset = dist / "lazy.js"
    asset.write_text("approved")
    digest = files_sha256(root)
    (home / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": True, "files_sha256": digest}}, "dev": {}})
    )
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        assert client.get("/plugins/hello/web/lazy.js").status_code == 200
        asset.write_text("modified after approval")
        assert client.get("/plugins/hello/web/lazy.js").status_code == 404


def test_installed_asset_cannot_change_between_hash_and_response_open(tmp_path, monkeypatch):
    from kollabor_webui import server

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins/hello"
    dist = root / "web/dist"
    dist.mkdir(parents=True)
    asset = dist / "main.js"
    asset.write_text("approved")
    write_registry(home, root)
    original = server.FileResponse

    def swap_before_open(path, *args, **kwargs):
        if path == asset:
            asset.write_text("unapproved after hash")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(server, "FileResponse", swap_before_open)
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        response = client.get("/plugins/hello/web/main.js")
        assert response.status_code == 404 or response.text == "approved"


def test_missing_approval_hash_fails_closed_and_dev_remains_unpinned(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    installed = home / "plugins/hello/web/dist"
    installed.mkdir(parents=True)
    (installed / "main.js").write_text("unapproved")
    dev = tmp_path / "dev/web/dist"
    dev.mkdir(parents=True)
    (dev / "main.js").write_text("first")
    (home / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": True}}, "dev": {"local": str(tmp_path / "dev")}})
    )
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        assert client.get("/plugins/hello/web/main.js").status_code == 404
        assert client.get("/plugins/local@dev/web/main.js").text == "first"
        (dev / "main.js").write_text("changed")
        assert client.get("/plugins/local@dev/web/main.js").text == "changed"


def test_installed_plugin_root_cannot_alias_another_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    plugins = home / "plugins"
    dist = plugins / "other" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "main.js").write_text("other plugin code")
    (plugins / "hello").symlink_to(plugins / "other", target_is_directory=True)
    write_registry(home, plugins / "other")
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        assert client.get("/plugins/hello/web/main.js").status_code == 404


def test_allowed_name_cannot_alias_hidden_or_disallowed_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    dist = home / "plugins/hello/web/dist"
    dist.mkdir(parents=True)
    (dist / ".private.js").write_text("hidden")
    (dist / "page.html").write_text("<html>private</html>")
    (dist / "alias.js").symlink_to(dist / ".private.js")
    (dist / "other.js").symlink_to(dist / "page.html")
    write_registry(home, home / "plugins/hello")
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        assert client.get("/plugins/hello/web/alias.js").status_code == 404
        assert client.get("/plugins/hello/web/other.js").status_code == 404


@pytest.mark.parametrize("component", ["web", "dist"])
def test_plugin_dist_root_cannot_be_a_symlink_to_other_plugin_content(tmp_path, monkeypatch, component):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins/hello"
    private = root / "private"
    private.mkdir(parents=True)
    (private / "main.js").write_text("private")
    if component == "web":
        (private / "dist").mkdir()
        (private / "dist/main.js").write_text("private")
        (root / "web").symlink_to(private, target_is_directory=True)
    else:
        (root / "web").mkdir()
        (root / "web/dist").symlink_to(private, target_is_directory=True)
    write_registry(home, root)
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        assert client.get("/plugins/hello/web/main.js").status_code == 404
