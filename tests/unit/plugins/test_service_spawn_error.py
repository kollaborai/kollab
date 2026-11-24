"""A missing service executable leaves a visible error state."""

import json


def test_missing_executable_records_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": ["missing-example-command"], "restart": "never"}}})
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {"enabled": True, "enabled_path": "/nonexistent", "files_sha256": files_sha256(root)}
                },
                "dev": {},
            }
        )
    )
    assert run_service("hello", "worker", "abcd") != 0
    state = json.loads((home / "plugin-data" / "hello" / "run" / "worker.state").read_text())
    assert state["status"] == "error"
    assert state["error"] == "Could not start this process. View Log for details."
    log = (home / "plugin-data" / "hello" / "run" / "worker.log").read_text()
    assert "missing-example-command" in log
    assert "No such file or directory" in log
