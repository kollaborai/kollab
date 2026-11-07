"""Engine runtime version identity (issue #630).

The engine runs as its own process and cannot import the kollab core package,
so it derives its own PEP 440 identity: the engine base version plus a local
segment (``0.18.5+<sha8>``) when running from a source checkout, and the exact
distribution version when installed. In the same checkout, the engine's
self-reported version must equal the CLI identity.
"""

import subprocess

import pytest
from fastapi.testclient import TestClient
from kollabor_engine.server import create_app


def test_installed_mode_reports_exact_base(monkeypatch):
    from kollabor_engine import runtime_version

    monkeypatch.setattr(runtime_version, "_is_running_from_source", lambda: False)
    assert runtime_version.get_engine_runtime_version() == runtime_version._base_version()


def test_source_mode_appends_local_segment(monkeypatch):
    from kollabor_engine import runtime_version

    monkeypatch.setattr(runtime_version, "_is_running_from_source", lambda: True)
    monkeypatch.setattr(runtime_version, "_base_version", lambda: "0.18.5")
    monkeypatch.setattr(
        runtime_version, "_resolve_revision", lambda: "1528fa9e" + "0" * 32
    )
    assert runtime_version.get_engine_runtime_version() == "0.18.5+1528fa9e"


def test_unresolved_revision_reports_unknown_suffix(monkeypatch):
    from kollabor_engine import runtime_version

    monkeypatch.setattr(runtime_version, "_is_running_from_source", lambda: True)
    monkeypatch.setattr(runtime_version, "_base_version", lambda: "0.18.5")
    monkeypatch.setattr(runtime_version, "_resolve_revision", lambda: "unknown")
    assert runtime_version.get_engine_runtime_version() == "0.18.5+unknown"


def test_revision_resolves_from_git_at_engine_root(monkeypatch):
    from kollabor_engine import runtime_version

    seen = {}

    def fake_run(cmd, cwd=None, **kwargs):
        seen["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0, stdout="f" * 40 + "\n", stderr="")

    monkeypatch.setattr(runtime_version.subprocess, "run", fake_run)
    assert runtime_version._resolve_revision() == "f" * 40
    assert seen["cwd"] == str(runtime_version._package_root())


def test_env_fallback_when_git_is_unavailable(monkeypatch):
    from kollabor_engine import runtime_version

    def explode(*args, **kwargs):
        raise FileNotFoundError("git missing in this environment")

    monkeypatch.setattr(runtime_version.subprocess, "run", explode)
    monkeypatch.setenv("KOLLAB_BUILD_SHA", "A" * 40)
    assert runtime_version._resolve_revision() == "a" * 40


def test_version_and_status_report_runtime_identity(monkeypatch):
    """/version and /status must stop reporting the bare dist version."""
    from kollabor_engine import runtime_version

    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    client = TestClient(create_app())

    body = client.get("/version").json()
    status = client.get("/status").json()

    assert body["version"] == body["engine"] == runtime_version.get_engine_runtime_version()
    assert status["version"] == runtime_version.get_engine_runtime_version()


def test_engine_identity_matches_cli_identity_in_same_checkout():
    """One checkout, one identity string across the CLI and the engine."""
    from kollabor_engine import runtime_version

    from kollabor import version as kollabor_version

    assert runtime_version.get_engine_runtime_version() == kollabor_version.__version__


@pytest.mark.parametrize("sha", ["nothex", "z" * 40, ""], ids=["word", "nonhex", "empty"])
def test_malformed_revision_is_rejected(sha):
    from kollabor_engine import runtime_version

    assert runtime_version._is_valid_sha(sha) is False
