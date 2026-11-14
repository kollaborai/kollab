"""The terminal list exposes installed package details and services."""
import json

from kollabor_plugins.cli import main


def test_list_includes_version_source_and_processes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"version": "1.2.3", "services": {"worker": {}}}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": True, "source": "https://example.test/hello.git"}}, "dev": {}}))
    assert main(["list"]) == 0
    output = capsys.readouterr().out
    for item in ("hello", "1.2.3", "enabled", "worker", "https://example.test/hello.git"):
        assert item in output
