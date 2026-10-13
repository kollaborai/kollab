"""Tests for MCPManager.upsert_server and remove_server, the one writer Apps uses."""

import json
import os
import sys

import pytest

from kollabor_agent import MCPManager


def _definition(**overrides):
    entry = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "demo"],
        "env": {"KEY": "sk-secret"},
        "enabled": True,
        "description": "Demo",
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def manager(tmp_path):
    return MCPManager(mcp_dir=tmp_path / "mcp")


def _settings(manager):
    return json.loads(manager.config_path.read_text())


def _dir_names(manager):
    return sorted(path.name for path in manager.mcp_dir.iterdir())


def test_upsert_creates_the_file_and_masks_env_in_the_reply(manager):
    saved = manager.upsert_server("demo", _definition())

    assert saved["env"] == {"KEY": "***"}
    assert "sk-secret" not in json.dumps(saved)
    assert _settings(manager)["servers"]["demo"]["env"] == {"KEY": "sk-secret"}


def test_upsert_keeps_other_keys_and_servers(manager):
    manager.mcp_dir.mkdir(parents=True)
    manager.config_path.write_text(
        json.dumps({"servers": {"old": {"type": "stdio", "command": sys.executable, "enabled": False}}, "other": 1})
    )

    manager.upsert_server("demo", _definition())

    data = _settings(manager)
    assert data["other"] == 1
    assert data["servers"]["old"]["enabled"] is False
    assert set(data["servers"]) == {"old", "demo"}


def test_upsert_replaces_an_existing_entry(manager):
    manager.upsert_server("demo", _definition())
    manager.upsert_server("demo", _definition(args=["-V"]))

    assert _settings(manager)["servers"]["demo"]["args"] == ["-V"]


def test_upsert_rejects_a_definition_that_breaks_the_rules(manager):
    with pytest.raises(ValueError, match="type"):
        manager.upsert_server("demo", _definition(type="http"))

    assert not manager.config_path.exists()


def test_upsert_refuses_broken_json_without_overwriting(manager):
    manager.mcp_dir.mkdir(parents=True)
    manager.config_path.write_text("{broken")

    with pytest.raises(ValueError, match="not valid JSON"):
        manager.upsert_server("demo", _definition())

    assert manager.config_path.read_text() == "{broken"


def test_remove_unknown_server_raises_key_error(manager):
    with pytest.raises(KeyError):
        manager.remove_server("nope")


def test_remove_deletes_one_server_and_keeps_the_rest(manager):
    manager.upsert_server("demo", _definition())
    manager.upsert_server("other", _definition())

    manager.remove_server("demo")

    assert list(_settings(manager)["servers"]) == ["other"]


def test_writes_leave_no_temp_files(manager):
    manager.upsert_server("demo", _definition())
    manager.remove_server("demo")
    manager.save_config({"servers": {}})

    assert _dir_names(manager) == ["mcp_settings.json"]


def test_failed_replace_keeps_the_old_file_and_cleans_up(manager, monkeypatch):
    manager.upsert_server("demo", _definition())
    before = manager.config_path.read_text()

    def fail(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        manager.upsert_server("other", _definition())

    assert manager.config_path.read_text() == before
    assert _dir_names(manager) == ["mcp_settings.json"]
