"""Plugin web assets are isolated to their approved distribution directory."""

import json

import pytest
from fastapi.testclient import TestClient

from kollabor_webui.server import create_app


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    dist = home / "plugins" / "hello" / "web" / "dist"
    dist.mkdir(parents=True)
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": True}}, "dev": {}}))
    (dist / "main.js").write_text("export default 1")
    (dist / "page.html").write_text("private html")
    (dist / ".secret.js").write_text("private")
    outside = tmp_path / "outside.js"
    outside.write_text("private outside")
    (dist / "escape.js").symlink_to(outside)
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
