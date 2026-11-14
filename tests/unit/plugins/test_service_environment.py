"""A service receives its private plugin data path."""
import json
import sys


def test_service_receives_plugin_data(tmp_path, monkeypatch):
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".kollab"
    package = root / "plugins" / "hello"
    package.mkdir(parents=True)
    command = [sys.executable, "-c", "import os; print(os.environ['KOLLAB_PLUGIN_DATA'])"]
    (package / "kollab-plugin.json").write_text(json.dumps({"services": {"worker": {"command": command, "restart": "never"}}}))
    (root / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [], "files_sha256": files_sha256(package)}}, "dev": {}}))
    assert run_service("hello", "worker", "abcdef") == 0
    log = root / "plugin-data" / "hello" / "run" / "worker.log"
    assert str(root / "plugin-data" / "hello") in log.read_text()
