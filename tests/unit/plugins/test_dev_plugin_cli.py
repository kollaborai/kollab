"""Dev registration shadows without changing installed settings."""

import json


def test_dev_registration_copies_settings_once_and_detaches(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins import cli

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    main = cli.main

    home = tmp_path / "home" / ".kollab"
    home.mkdir(parents=True)
    source = tmp_path / "checkout"
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
            }
        )
    )
    installed_data = home / "plugin-data" / "hello"
    installed_data.mkdir(parents=True)
    (installed_data / "values.json").write_text('{"count": 2}')
    assert main(["dev", str(source)]) == 0
    registry_path = home / "plugins.json"
    registry = json.loads(registry_path.read_text())
    assert registry["dev"]["hello"] == str(source.resolve())
    dev_values = home / "plugin-data" / "hello@dev" / "values.json"
    assert json.loads(dev_values.read_text()) == {"count": 2}
    dev_values.write_text('{"count": 3}')
    assert main(["dev", str(source)]) == 0
    assert json.loads(dev_values.read_text()) == {"count": 3}
    assert main(["dev", "off", "hello"]) == 0
    assert json.loads(registry_path.read_text())["dev"] == {}
    assert json.loads(dev_values.read_text()) == {"count": 3}
    assert json.loads((installed_data / "values.json").read_text()) == {"count": 2}
