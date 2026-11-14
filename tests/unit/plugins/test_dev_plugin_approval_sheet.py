"""The development approval identifies the unpinned code before confirmation."""
import json

from kollabor_plugins import cli


def test_dev_shows_manifest_and_path_before_confirmation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    root = tmp_path / "checkout"
    root.mkdir()
    manifest = {"format": 1, "id": "hello", "name": "Hello Example", "version": "1.0.0", "description": "demo", "kollab": ">=0.21.1", "uses": ["run:python3"], "services": {"worker": {"command": ["python3", "worker.py"]}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    shown = []

    def reject(prompt):
        shown.append(capsys.readouterr().out)
        return "no"

    monkeypatch.setattr("builtins.input", reject)
    assert cli.main(["dev", str(root)]) == 1
    assert str(root) in shown[0]
    assert "Hello Example" in shown[0]
    assert "worker" in shown[0]
    assert "run:python3" in shown[0]
    assert not (tmp_path / "home" / ".kollab" / "plugins.json").exists()
