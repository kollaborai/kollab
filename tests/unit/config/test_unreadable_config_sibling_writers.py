"""Config writers must preserve malformed files for explicit recovery."""

import asyncio
import json
from pathlib import Path

import kollabor.cli as cli
import kollabor_config.config_utils as config_utils
from kollabor_config.manager import ConfigManager


def test_hub_off_preserves_malformed_global_config(tmp_path, monkeypatch):
    path = tmp_path / "global.json"
    original = b'{"api_key":"SYNTH_KEY_123",'
    path.write_bytes(original)
    monkeypatch.setattr(config_utils, "get_existing_global_config_path", lambda: path)

    asyncio.run(cli._handle_cli_hub(["off"]))

    assert path.read_bytes() == original


def test_config_manager_refuses_to_replace_malformed_config(tmp_path):
    path = tmp_path / "config.json"
    original = b'{"api_key":"SYNTH_KEY_123",'
    path.write_bytes(original)
    manager = ConfigManager(path)

    assert manager.load_config_file() == {}
    assert manager.save_config_file({"layout": {}}) is False
    assert path.read_bytes() == original


def test_hub_off_refuses_a_config_changed_after_read(tmp_path, monkeypatch, capsys):
    path = tmp_path / "global.json"
    path.write_bytes(b'{"plugins":{"hub":{"enabled":true}}}')
    concurrent = b'{"outside":{"keep":true},"plugins":{"hub":{"enabled":true}}}'
    monkeypatch.setattr(config_utils, "get_existing_global_config_path", lambda: path)

    read_bytes = Path.read_bytes
    json_load = json.load
    injected = False

    def replace_after_read():
        nonlocal injected
        if not injected:
            injected = True
            path.write_bytes(concurrent)

    def read_path(self):
        data = read_bytes(self)
        if self == path:
            replace_after_read()
        return data

    def load_json(stream, *args, **kwargs):
        value = json_load(stream, *args, **kwargs)
        replace_after_read()
        return value

    monkeypatch.setattr(Path, "read_bytes", read_path)
    monkeypatch.setattr(json, "load", load_json)

    asyncio.run(cli._handle_cli_hub(["off"]))

    output = capsys.readouterr()
    assert injected
    assert path.read_bytes() == concurrent
    assert "changed" in output.err.lower()
    assert "hub disabled" not in output.out.lower()


def test_config_manager_refuses_a_valid_file_changed_after_load(tmp_path):
    path = tmp_path / "config.json"
    original = b'{"settings":{"before":true}}'
    concurrent = b'{"settings":{"outside":true}}'
    path.write_bytes(original)
    manager = ConfigManager(path)

    assert manager.load_config_file() == {"settings": {"before": True}}
    path.write_bytes(concurrent)

    assert manager.save_config_file({"settings": {"ours": True}}) is False
    assert path.read_bytes() == concurrent


def test_config_manager_refuses_to_save_after_an_unreadable_load(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    original = b'{"layout":{"v":1}}'
    path.write_bytes(original)
    manager = ConfigManager(path)
    read_bytes = Path.read_bytes

    def unreadable(target):
        if target == path:
            raise PermissionError("permission denied")
        return read_bytes(target)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    assert manager.load_config_file() == {}
    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    assert manager.save_config_file({"layout": {"v": 2}}) is False
    assert path.read_bytes() == original


def test_config_manager_refuses_to_replace_wrong_shape_config(tmp_path):
    path = tmp_path / "config.json"
    original = b"[]"
    path.write_bytes(original)
    manager = ConfigManager(path)

    assert manager.load_config_file() == {}
    assert manager.save_config_file({"layout": {}}) is False
    assert path.read_bytes() == original
