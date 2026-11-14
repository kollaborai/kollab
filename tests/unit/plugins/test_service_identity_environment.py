"""Service subprocesses know their plugin identity and package root."""

import json
import sys


def test_service_gets_identity_and_root(tmp_path, monkeypatch):
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    package = home / "plugins" / "hello"
    package.mkdir(parents=True)
    script = "import os; print(os.environ['KOLLAB_PLUGIN_ID'], os.environ['KOLLAB_PLUGIN_ROOT'])"
    manifest = {"services": {"worker": {"command": [sys.executable, "-c", script], "restart": "never"}}}
    (package / "kollab-plugin.json").write_text(json.dumps(manifest))
    record = {"enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [], "files_sha256": files_sha256(package)}
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": record}, "dev": {}}))
    assert run_service("hello", "worker", "abcdef") == 0
    log = (home / "plugin-data" / "hello" / "run" / "worker.log").read_text()
    assert "hello " + str(package) in log
