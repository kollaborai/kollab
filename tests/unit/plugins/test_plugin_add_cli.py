"""Adding a plugin requires an interactive confirmation of its pinned contents."""

import json

import pytest


def test_add_requires_confirmation_and_keeps_declined_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    import kollabor_plugins.cli as cli
    from kollabor_plugins.packages import files_sha256

    def stage(source, destination, version):
        destination.mkdir()
        manifest = {"id": "hello", "name": "Hello", "uses": ["secret:KEY"]}
        (destination / "kollab-plugin.json").write_text(json.dumps(manifest))
        return {
            "source": source,
            "ref": None,
            "commit": "a" * 40,
            "files_sha256": files_sha256(destination),
            "manifest": manifest,
        }

    monkeypatch.setattr(cli, "stage_git_source", stage)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert cli.main(["add", "https://example.invalid/repo"]) != 0
    assert not (tmp_path / ".kollab" / "plugins.json").exists()
    output = capsys.readouterr().out
    assert "a" * 40 in output
    assert "secret: configured" in output
    assert "secret:KEY" not in output
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert cli.main(["add", "https://example.invalid/repo"]) == 0
    record = json.loads((tmp_path / ".kollab" / "plugins.json").read_text())["plugins"]["hello"]
    assert record["commit"] == "a" * 40
    assert record["granted"] == ["secret:KEY"]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="interactive"):
        cli.main(["add", "https://example.invalid/repo"])
