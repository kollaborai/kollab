"""Plugin log output must not expose granted credentials."""

import json
import sys

from kollabor_ai.profile_manager import _keyring_set
from kollabor_plugins.cli import main
from kollabor_plugins.packages import files_sha256
from kollabor_plugins.services import run_service


def test_log_filter_redacts_across_chunks_and_suppresses_oversized_lines():
    from kollabor_plugins.services import _LogFilter

    filt = _LogFilter(["arbitrary-secret"])
    output = filt.feed(b"prefix arbitrary-") + filt.feed(b"secret suffix\n")
    assert b"arbitrary-secret" not in output
    assert b"[redacted]" in output
    oversized = filt.feed(b"a" * (1024 * 1024 + 1)) + filt.feed(b"arbitrary-secret\n")
    assert b"arbitrary-secret" not in oversized
    assert b"oversized" in oversized


def test_echoed_secret_and_token_are_redacted_in_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    root = home / "plugins" / "demo"
    root.mkdir(parents=True)
    command = [
        sys.executable,
        "-c",
        "import os; print(os.environ['TEST_TOKEN']); "
        "print('https://x.test/?token=hidden'); print('Authorization: Bearer abc123')",
    ]
    manifest = {"services": {"worker": {"command": command, "secrets": ["TEST_TOKEN"]}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "demo": {
                        "enabled": True,
                        "enabled_path": "/bin",
                        "granted": ["secret:TEST_TOKEN"],
                        "files_sha256": files_sha256(root),
                    }
                },
                "dev": {},
            }
        )
    )
    assert _keyring_set("TEST_TOKEN", "arbitrary-value-xyz")
    assert run_service("demo", "worker", "abcdef") == 0
    assert _keyring_set("TEST_TOKEN", "rotated-value")
    assert main(["logs", "demo", "worker"]) == 0
    output = capsys.readouterr().out
    assert "[redacted]" in output
    assert "arbitrary-value-xyz" not in output
    assert "hidden" not in output
    assert "abc123" not in output
