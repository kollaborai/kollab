"""A delayed wrapper does not undo a user's stop request."""

import json
import sys


def test_wrapper_respects_already_stopped_state(tmp_path, monkeypatch):
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    marker = tmp_path / "must-not-exist"
    (root / "kollab-plugin.json").write_text(
        json.dumps(
            {
                "services": {
                    "worker": {"command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('started')"]}
                }
            }
        )
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {
                        "enabled": True,
                        "enabled_path": "/bin",
                        "granted": [],
                        "files_sha256": files_sha256(root),
                    }
                },
                "dev": {},
            }
        )
    )
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.state").write_text(json.dumps({"desired": "stopped", "status": "stopped"}))
    assert run_service("hello", "worker", "abcdef") == 0
    assert not marker.exists()
