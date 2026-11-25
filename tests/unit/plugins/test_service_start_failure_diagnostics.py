"""Service start diagnostics must not block or expose the persisted failure state."""

import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("failure", ["credentials", "spawn"])
@pytest.mark.parametrize("blocked_log", [False, True], ids=["writable-log", "log-path-is-directory"])
def test_service_start_failure_persists_safe_error_when_diagnostic_write_fails(
    tmp_path, monkeypatch, failure, blocked_log
):
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    package = home / "plugins" / "example"
    package.mkdir(parents=True)
    (package / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": [sys.executable, "-c", "pass"], "restart": "never"}}})
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "example": {
                        "enabled": True,
                        "enabled_path": "/usr/bin:/bin",
                        "granted": [],
                        "files_sha256": files_sha256(package),
                    }
                },
                "dev": {},
            }
        )
    )
    run_dir = home / "plugin-data" / "example" / "run"
    run_dir.mkdir(parents=True)
    log_path = run_dir / "worker.log"
    if blocked_log:
        log_path.mkdir()

    private_path = tmp_path / "private-account" / "plugin-token"
    if failure == "credentials":
        from kollabor_plugins.tokens import PluginTokenFiles

        def fail_credentials(*_args, **_kwargs):
            raise PermissionError(f"cannot open {private_path}")

        monkeypatch.setattr(PluginTokenFiles, "mint", fail_credentials)
        expected_error = "Could not prepare plugin engine credentials"
    else:
        def fail_spawn(*_args, **_kwargs):
            raise FileNotFoundError(2, "No such file or directory", str(private_path))

        monkeypatch.setattr(subprocess, "Popen", fail_spawn)
        expected_error = "Could not start this process. View Log for details."

    assert run_service("example", "worker", "abcdef") == 1

    state = json.loads((run_dir / "worker.state").read_text())
    assert state["status"] == "error"
    assert state["error"] == expected_error
    if not blocked_log:
        assert str(private_path) not in log_path.read_text()


@pytest.mark.parametrize("failure_stage", ["manifest", "spawn"])
def test_service_supervisor_logs_exception_type_without_local_path(tmp_path, monkeypatch, caplog, failure_stage):
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    home = tmp_path / ".kollab"
    package = home / "plugins" / "example"
    package.mkdir(parents=True)
    manifest = package / "kollab-plugin.json"
    manifest.write_text(
        json.dumps({"services": {"worker": {"command": [sys.executable, "-c", "pass"], "autostart": True}}})
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "example": {
                        "enabled": True,
                        "enabled_path": "/usr/bin:/bin",
                        "granted": [],
                        "files_sha256": files_sha256(package),
                    }
                },
                "dev": {},
            }
        )
    )
    if failure_stage == "manifest":
        monkeypatch.setattr(services, "_approved_service_root", lambda *_args: (package, None))
        manifest.unlink()
        private_path = manifest
    else:
        private_path = tmp_path / "private-account" / "plugin-token"

        def fail_spawn(*_args, **_kwargs):
            raise FileNotFoundError(2, "No such file or directory", str(private_path))

        monkeypatch.setattr(subprocess, "Popen", fail_spawn)

    services.supervisor_tick()

    assert str(private_path) not in caplog.text
    assert "FileNotFoundError" in caplog.text
