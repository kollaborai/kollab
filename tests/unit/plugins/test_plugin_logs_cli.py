"""Read service logs without allowing path traversal."""

import json

import pytest

from kollabor_plugins.cli import main


def test_logs_one_or_all_services(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    package = home / "plugins" / "hello"
    package.mkdir(parents=True)
    (package / "kollab-plugin.json").write_text(json.dumps({"services": {"one": {}, "two": {}}}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {}}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "one.log").write_text("first log\n")
    (run / "two.log").write_text("second log\n")
    assert main(["logs", "hello", "one"]) == 0
    out = capsys.readouterr().out
    assert "first log" in out and "second log" not in out
    assert main(["logs", "hello"]) == 0
    out = capsys.readouterr().out
    assert "first log" in out and "second log" in out
    with pytest.raises(ValueError):
        main(["logs", "hello", "../outside"])


def test_log_symlink_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    package = home / "plugins" / "hello"
    package.mkdir(parents=True)
    (package / "kollab-plugin.json").write_text(json.dumps({"services": {"one": {}}}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {}}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("private")
    (run / "one.log").symlink_to(secret)
    with pytest.raises((ValueError, OSError)):
        main(["logs", "hello", "one"])
