"""Removing and purging cannot race an in-flight settings save."""

import json
import threading

from kollabor_plugins import cli, settings


def test_purge_waits_for_settings_save(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(
        json.dumps({"id": "hello", "name": "Hello", "settings": "settings.json", "services": {}})
    )
    (root / "settings.json").write_text(json.dumps({"widgets": [{"type": "checkbox", "config_path": "flag"}]}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {"enabled": False}}, "dev": {}}))
    entered = threading.Event()
    release = threading.Event()
    removed = threading.Event()
    errors = []
    original = settings.setting_widgets

    def pause(*args):
        result = original(*args)
        entered.set()
        assert release.wait(5)
        return result

    def save():
        try:
            settings.save("hello", {"flag": True})
        except Exception as exc:
            errors.append(exc)

    def remove():
        try:
            assert cli.main(["remove", "hello", "--purge"]) == 0
        except Exception as exc:
            errors.append(exc)
        finally:
            removed.set()

    monkeypatch.setattr(settings, "setting_widgets", pause)
    writer = threading.Thread(target=save)
    remover = threading.Thread(target=remove)
    writer.start()
    try:
        assert entered.wait(5)
        remover.start()
        assert not removed.wait(0.3), "purge passed an in-flight save"
    finally:
        release.set()
        writer.join(5)
        if remover.ident is not None:
            remover.join(5)
    assert not errors
    assert removed.is_set()
    assert not (home / "plugin-data" / "hello").exists()
