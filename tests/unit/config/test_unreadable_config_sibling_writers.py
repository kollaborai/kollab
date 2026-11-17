"""Config writers must preserve malformed files for explicit recovery."""

import asyncio

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
