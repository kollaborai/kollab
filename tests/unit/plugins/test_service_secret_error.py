"""Secret backend failures must not expose credentials in exception text."""

import json
import sys

import pytest


def test_backend_exception_does_not_expose_secret(tmp_path, monkeypatch):
    import kollabor_ai.providers.security as security
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": [sys.executable, "-c", "pass"], "secrets": ["TOKEN"]}}})
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {
                        "enabled": True,
                        "enabled_path": "/bin",
                        "granted": ["secret:TOKEN"],
                        "files_sha256": files_sha256(root),
                    }
                },
                "dev": {},
            }
        )
    )

    class Store:
        async def get_key(self, name):
            raise ValueError("credential-value-must-not-leak")

    monkeypatch.setattr(security, "APIKeyManager", Store)
    with pytest.raises(ValueError) as exc:
        run_service("hello", "worker", "abcdef")
    assert "credential-value-must-not-leak" not in str(exc.value)
