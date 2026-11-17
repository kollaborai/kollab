"""Service startup refuses installed files that changed after approval."""

import json
import sys

import pytest


def test_service_refuses_modified_package(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.services import run_service

    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    marker = tmp_path / "executed"
    (root / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": [sys.executable, "-c", f"open({str(marker)!r},'w').close()"]}}})
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {"hello": {"enabled": True, "enabled_path": "/usr/bin:/bin", "files_sha256": "0" * 64}},
                "dev": {},
            }
        )
    )
    with pytest.raises(ValueError, match="changed"):
        run_service("hello", "worker", "abcd")
    assert not marker.exists()
