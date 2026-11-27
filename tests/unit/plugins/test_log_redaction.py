"""Plugin log output must not expose granted credentials."""

import json
import sys

import pytest

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
        (
            "short bearer log token",
            "Authorization: Bearer abc123",
            "Authorization: Bearer [redacted]",
        ),
        (
            "letters-only authorization bearer",
            "Authorization: Bearer secrettoken",
            "Authorization: Bearer [redacted]",
        ),
        (
            "proxy authorization basic credential",
            "Proxy-Authorization: Basic dXNlcjpwYXNz",
            "Proxy-Authorization: Basic [redacted]",
        ),
        (
            "token assignment with bearer credential",
            "token: bearer abcdef",
            "token: bearer [redacted]",
        ),
        (
            "authorization basic credential",
            "Authorization: Basic secret",
            "Authorization: Basic [redacted]",
        ),
        ("basic settings label", "Basic settings", "Basic settings"),
        ("bearer help placeholder", "Bearer <key>", "Bearer <key>"),
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
            "private key assignment",
            "PluginError: private_key=private-secret",
            "PluginError: private_key: [redacted]",
        ),
        (
            "hyphenated private key assignment",
            "PluginError: private-key=private-secret",
            "PluginError: private-key: [redacted]",
        ),
        (
            "signing key assignment",
            "PluginError: signing_key=signing-secret",
            "PluginError: signing_key: [redacted]",
        ),
        (
            "encryption key assignment",
            "PluginError: encryption_key=encryption-secret",
            "PluginError: encryption_key: [redacted]",
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


def test_service_output_redaction_preserves_paths_and_hides_credentials():
    from kollabor_plugins.services import _LogFilter

    cases = [
        (
            "KOLLAB_PLUGIN_DATA=/private/tmp/plugin-data/hello\n",
            "KOLLAB_PLUGIN_DATA=/private/tmp/plugin-data/hello\n",
        ),
        (
            "GITHUB_TOKEN=ghp_fakecredentialvalue123\n",
            "GITHUB_TOKEN: [redacted]\n",
        ),
    ]
    actual = [
        _LogFilter([]).feed(value.encode(), final=True).decode()
        for value, _ in cases
    ]
    failures = [
        f"expected {expected!r}, got {result!r}"
        for (_, expected), result in zip(cases, actual)
        if result != expected
    ]
    assert failures == [], "; ".join(failures)


@pytest.mark.parametrize(
    ("exception_message", "expected_fragment", "forbidden"),
    [
        ("Cannot read /private/tmp/account/config.json", "[path]", "/private/tmp/account/config.json"),
        ("GITHUB_TOKEN=ghp_fakecredentialvalue123", "GITHUB_TOKEN: [redacted]", "ghp_fakecredentialvalue123"),
    ],
)
def test_plugin_exception_logs_redact_paths_and_credentials(
    caplog, exception_message, expected_fragment, forbidden
):
    from kollabor_plugins.plugin_utils import get_plugin_config_safely

    class BrokenPlugin:
        @staticmethod
        def get_default_config():
            raise RuntimeError(exception_message)

    assert get_plugin_config_safely(BrokenPlugin) == {}
    assert expected_fragment in caplog.text
    assert forbidden not in caplog.text


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


def test_unlabelled_token_formats_are_redacted_without_short_false_positives():
    from kollabor_plugins.redaction import contains_sensitive_text, redact_sensitive_text

    suffix = "A1b2C3d4E5f6G7h8I9j0"
    aws_access_key_suffix = "A1B2C3D4E5F6G7H8"
    tokens = [
        *(f"xox{kind}-{suffix}" for kind in "abprs"),
        f"xapp-{suffix}",
        *(f"gh{kind}_{suffix}" for kind in "pousr"),
        f"github_pat_{suffix}",
        f"glpat-{suffix}",
        f"sk_live_{suffix}",
        f"sk_test_{suffix}",
        f"rk_live_{suffix}",
        f"rk_test_{suffix}",
        f"pk_live_{suffix}",
        f"pk_test_{suffix}",
        f"AKIA{aws_access_key_suffix}",
        f"ASIA{aws_access_key_suffix}",
        f"AIza{'A' * 35}",
        f"npm_{suffix}",
        f"hf_{suffix}",
        f"eyJ{'A' * 12}.{'B' * 20}.{'C' * 20}",
        f"sk-{suffix}",
    ]
    short_lookalikes = [
        f"xoxb-{'A' * 19}",
        f"xapp-{'A' * 19}",
        f"ghp_{'A' * 19}",
        f"github_pat_{'A' * 19}",
        f"glpat-{'A' * 19}",
        f"sk_live_{'A' * 15}",
        f"sk_test_{'A' * 15}",
        f"rk_live_{'A' * 15}",
        f"rk_test_{'A' * 15}",
        f"AKIA{'A' * 15}",
        f"ASIA{'A' * 15}",
        f"AIza{'A' * 33}",
        f"npm_{'A' * 19}",
        f"hf_{'A' * 19}",
        f"eyJ{'A' * 9}.{'B' * 19}.{'C' * 20}",
        f"sk-{'A' * 19}",
        "xoxb-not-a-token",
        "hf_development",
    ]

    for token in tokens:
        assert contains_sensitive_text(token)
        assert redact_sensitive_text(token, redact_paths=False) == "[redacted]"
    for value in short_lookalikes:
        assert not contains_sensitive_text(value)
        assert redact_sensitive_text(value, redact_paths=False) == value
