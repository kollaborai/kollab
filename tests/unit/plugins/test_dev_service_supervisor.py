"""Development services participate in the ordinary supervisor lifecycle."""

import json
import subprocess
import sys

from kollabor_plugins import cli, services


def test_dev_service_start_and_autostart(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    source = tmp_path / "source"
    source.mkdir()
    (source / "kollab-plugin.json").write_text(
        json.dumps(
            {
                "format": 1,
                "id": "hello",
                "name": "Hello",
                "version": "1.0.0",
                "description": "Example",
                "kollab": ">=0.1",
                "uses": [],
                "services": {"worker": {"command": [sys.executable, "-c", "pass"], "autostart": True}},
            }
        )
    )
    assert cli.main(["dev", str(source)]) == 0
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: launches.append(args[0]))
    services.supervisor_tick()
    assert len(launches) == 1
    assert launches[0][-3:-1] == ["hello@dev", "worker"]
    launches.clear()
    services.control_service("hello@dev", "worker", "start")
    assert len(launches) == 1
    assert launches[0][-3:-1] == ["hello@dev", "worker"]
