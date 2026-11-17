"""Web descriptors include a stable asset hash for cache invalidation."""

import json

from kollabor_plugins.packages import files_sha256
from kollabor_plugins.registry import plugin_entries


def test_web_descriptor_contains_dist_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    dist = root / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.js").write_text("export default 1")
    (dist / "index.css").write_text("a {}")
    (root / "kollab-plugin.json").write_text(
        json.dumps({"id": "hello", "web": {"entry": "web/dist/index.js", "style": "web/dist/index.css"}})
    )
    (home / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": False, "files_sha256": files_sha256(root)}}, "dev": {}})
    )
    builtin = tmp_path / "builtins"
    builtin.mkdir()
    web = next(e for e in plugin_entries(builtin) if e["id"] == "hello")["web"]
    assert web["hash"] == files_sha256(dist)
