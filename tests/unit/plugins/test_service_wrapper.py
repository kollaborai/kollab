"""The service wrapper owns its lock, child and state files."""

import json
import sys


def test_wrapper_records_child_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.services import run_service

    package = tmp_path / ".kollab" / "plugins" / "hello"
    package.mkdir(parents=True)
    (package / "kollab-plugin.json").write_text(
        json.dumps(
            {"services": {"worker": {"command": [sys.executable, "-c", "print('hello')"], "restart": "on-failure"}}}
        )
    )
    (tmp_path / ".kollab" / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": True, "enabled_path": "/usr/bin:/bin", "granted": []}}, "dev": {}})
    )
    assert run_service("hello", "worker", "0123456789abcdef") == 0
    run = tmp_path / ".kollab" / "plugin-data" / "hello" / "run"
    state = json.loads((run / "worker.state").read_text())
    pid = json.loads((run / "worker.pid").read_text())
    assert state["status"] == "exited"
    assert state["exit_history"][-1]["code"] == 0
    assert pid["wrapper"]["nonce"] == "0123456789abcdef"
    assert pid["child"]["pid"] > 0
    assert "hello" in (run / "worker.log").read_text()
