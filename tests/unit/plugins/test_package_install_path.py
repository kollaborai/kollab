"""New installs retain the environment used to resolve background commands."""

import json


def test_install_captures_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("PATH", "/opt/example/bin:/usr/bin")
    from kollabor_plugins.packages import files_sha256, install_stage

    stage = tmp_path / "staged"
    stage.mkdir()
    (stage / "kollab-plugin.json").write_text(json.dumps({"id": "hello"}))
    digest = files_sha256(stage)
    staged = {
        "source": "https://example.invalid/repo",
        "ref": None,
        "commit": "a" * 40,
        "files_sha256": digest,
        "manifest": {"id": "hello", "uses": []},
    }
    install_stage(stage, staged, staged["commit"], digest)
    registry = json.loads((tmp_path / ".kollab" / "plugins.json").read_text())
    assert registry["plugins"]["hello"]["enabled_path"] == "/opt/example/bin:/usr/bin"
