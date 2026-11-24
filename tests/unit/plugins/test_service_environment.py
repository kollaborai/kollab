"""A service receives its private plugin data path."""

import json
import sys


def test_service_receives_plugin_data(tmp_path, monkeypatch):
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".kollab"
    package = root / "plugins" / "hello"
    package.mkdir(parents=True)
    command = [sys.executable, "-c", "import os; print(os.environ['KOLLAB_PLUGIN_DATA'])"]
    (package / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": command, "restart": "never"}}})
    )
    (root / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {
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
    assert run_service("hello", "worker", "abcdef") == 0
    log = root / "plugin-data" / "hello" / "run" / "worker.log"
    assert str(root / "plugin-data" / "hello") in log.read_text()


def test_service_receives_scoped_engine_credentials_without_logging_token(tmp_path, monkeypatch):
    import stat

    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_ENGINE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("KOLLAB_PLUGIN_TOKEN", "inherited-untrusted-token")
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    command = [sys.executable, "-c", (
        "import os, pathlib; "
        "t=os.environ['KOLLAB_PLUGIN_TOKEN']; "
        "p=pathlib.Path(os.environ['KOLLAB_PLUGIN_DATA'])/'run'/'token'; "
        "print('url_matches='+str(os.environ['KOLLAB_ENGINE_URL']=='http://127.0.0.1:8123')); "
        "print('token_matches='+str(t==p.read_text())); print('raw='+t)"
    )]
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "services": {"worker": {
        "command": command, "restart": "never",
    }}}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [],
        "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    assert run_service("hello", "worker", "abcdef") == 0
    run = home / "plugin-data" / "hello" / "run"
    token = (run / "token").read_text()
    assert stat.S_IMODE((run / "token").stat().st_mode) == 0o600
    assert token != "inherited-untrusted-token"
    from kollabor_engine.routes.plugins import PluginEventHub, PluginTokenStore

    store, hub = PluginTokenStore(), PluginEventHub()
    assert store.authorize(token, "GET", "/plugins/hello/settings", hub)
    assert not store.authorize(token, "GET", "/plugins/other/settings", hub)
    assert not store.authorize(token, "POST", "/plugins/hello/remove", hub)
    log = (run / "worker.log").read_text()
    assert "url_matches=True" in log
    assert "token_matches=True" in log
    assert token not in log
    assert "[redacted]" in log


def test_supervisor_passes_own_engine_origin_to_wrapper(tmp_path, monkeypatch):
    import subprocess

    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import supervisor_tick

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_ENGINE_URL", "http://127.0.0.1:1111")
    monkeypatch.setenv("KOLLAB_PLUGIN_TOKEN", "must-not-inherit")
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"services": {"worker": {
        "command": [sys.executable, "-c", "pass"], "autostart": True,
    }}}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [],
        "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: launches.append(kwargs))
    supervisor_tick(engine_url="http://127.0.0.1:8123")
    assert len(launches) == 1
    assert launches[0]["env"]["KOLLAB_ENGINE_URL"] == "http://127.0.0.1:8123"
    assert "KOLLAB_PLUGIN_TOKEN" not in launches[0]["env"]


def test_service_rejects_non_local_engine_origins():
    import pytest

    from kollabor_plugins.services import _engine_url

    for value in ("https://evil.test:443", "http://example.test:7433", "http://127.0.0.1:1@evil.test:7",
                  "http://127.0.0.1:7433/path", "http://127.0.0.1:7433/?secret=x", "http://127.0.0.1:0"):
        with pytest.raises(ValueError):
            _engine_url(value)


def test_service_credential_failure_is_visible_without_leaking_details(tmp_path, monkeypatch):
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import run_service
    from kollabor_plugins.tokens import PluginTokenFiles

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"services": {"worker": {
        "command": [sys.executable, "-c", "pass"], "restart": "never",
    }}}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [],
        "files_sha256": files_sha256(root),
    }}, "dev": {}}))

    def fail(*args):
        raise ValueError("sensitive failure detail")

    monkeypatch.setattr(PluginTokenFiles, "mint", fail)
    assert run_service("hello", "worker", "abcdef") == 1
    state = json.loads((home / "plugin-data" / "hello" / "run" / "worker.state").read_text())
    assert state["status"] == "error"
    assert state["error"] == "Could not prepare plugin engine credentials"
    assert "sensitive failure detail" not in json.dumps(state)
    log = (home / "plugin-data" / "hello" / "run" / "worker.log").read_text()
    assert "sensitive failure detail" in log
