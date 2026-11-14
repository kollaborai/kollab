"""Granted service secrets resolve from Kollab's encrypted fallback vault."""

import json
import sys

from kollabor_ai.profile_manager import _keyring_set
from kollabor_plugins.packages import files_sha256
from kollabor_plugins.services import run_service


def test_granted_secret_from_real_encrypted_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    root = home / "plugins" / "demo"
    root.mkdir(parents=True)
    script = "import os; print('found' if os.getenv('PLUGIN_TEST_TOKEN') == 'vault-secret' else 'missing')"
    manifest = {"services": {"worker": {"command": [sys.executable, "-c", script], "secrets": ["PLUGIN_TEST_TOKEN"]}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "demo": {
                        "enabled": True,
                        "enabled_path": "/bin",
                        "granted": ["secret:PLUGIN_TEST_TOKEN"],
                        "files_sha256": files_sha256(root),
                    }
                },
                "dev": {},
            }
        )
    )
    assert _keyring_set("PLUGIN_TEST_TOKEN", "vault-secret")
    assert run_service("demo", "worker", "test-vault") == 0
    assert "found" in (home / "plugin-data/demo/run/worker.log").read_text()
