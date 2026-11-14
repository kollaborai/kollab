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
        "print('url='+os.environ['KOLLAB_ENGINE_URL']); "
        "print('token_matches='+str(t==p.read_text())); print('raw='+t)"
    )]
    (root / "kollab-plugin.json").write_text(json.dumps({"services": {"worker": {
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
    log = (run / "worker.log").read_text()
    assert "url=http://127.0.0.1:8123" in log
    assert "token_matches=True" in log
    assert token not in log
    assert "[redacted]" in log
