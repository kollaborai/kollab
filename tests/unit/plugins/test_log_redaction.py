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


def test_plugin_secret_redaction_boundaries_are_shared():
    from kollabor_plugins.packages import _normalize_plugin_use
    from kollabor_plugins.registry import _bounded_inventory_text
    from kollabor_plugins.services import redact_log

    credential = "ghp_fakecredentialvalue123"
    cases = [
        ("manifest use", "run:GITHUB_TOKEN=" + credential, None),
        ("inventory token assignment", "GITHUB_TOKEN=" + credential, None),
        ("inventory bearer", "Bearer abc123XYZ987", None),
        ("inventory key shape", "sk-live-abcdefghijklmnop", None),
        ("token label", "Token-Budget", "Token-Budget"),
        ("password help", "Password: required", "Password: required"),
        (
            "provenance URL",
            "https://github.com/acme/token-manager.git",
            "https://github.com/acme/token-manager.git",
        ),
        (
            "log assignment",
            "PluginError: GITHUB_TOKEN=" + credential,
            "PluginError: GITHUB_TOKEN: [redacted]",
        ),
        (
            "log filesystem path",
            "RuntimeError: /private/tmp/account/config.json",
            "RuntimeError: [path]",
        ),
    ]
    actual = []
    for surface, value, _ in cases:
        if surface == "manifest use":
            actual.append(_normalize_plugin_use(value))
        elif surface.startswith("inventory") or surface in {"token label", "password help", "provenance URL"}:
            actual.append(_bounded_inventory_text(value))
        else:
            actual.append(redact_log(value))

    failures = [
        f"{surface}: expected {expected!r}, got {result!r}"
        for (surface, _, expected), result in zip(cases, actual)
        if result != expected
    ]
    assert failures == [], "; ".join(failures)


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
