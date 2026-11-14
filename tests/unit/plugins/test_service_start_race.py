"""A service cannot run stale code after approval changes while acquiring its lock."""

import json
import sys

import pytest


def test_service_rechecks_grant_after_lock(tmp_path, monkeypatch):
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": [sys.executable, "-c", "pass"]}}})
    )
    registry_path = home / "plugins.json"
    registry = {
        "plugins": {
            "hello": {"enabled": True, "enabled_path": "/bin", "granted": [], "files_sha256": files_sha256(root)}
        },
        "dev": {},
    }
    registry_path.write_text(json.dumps(registry))
    lock = services.try_lock

    def change_during_lock(fd):
        ok = lock(fd)
        registry["plugins"]["hello"]["enabled"] = False
        registry_path.write_text(json.dumps(registry))
        return ok

    monkeypatch.setattr(services, "try_lock", change_during_lock)
    with pytest.raises(ValueError, match="changed|enabled"):
        services.run_service("hello", "worker", "abcdef")
