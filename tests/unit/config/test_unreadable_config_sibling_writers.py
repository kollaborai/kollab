"""Config writers must preserve malformed files for explicit recovery."""

import asyncio
import json
import threading
from pathlib import Path

import pytest

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


def test_hub_off_saves_valid_config_without_dropping_other_settings(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "global.json"
    path.write_bytes(b'{"keep":{"value":1},"plugins":{"hub":{"enabled":true}}}')
    monkeypatch.setattr(config_utils, "get_existing_global_config_path", lambda: path)
    monkeypatch.setattr(cli, "_apply_hub_project_scope_from_config", lambda: None)

    asyncio.run(cli._handle_cli_hub(["off"]))

    output = capsys.readouterr()
    assert json.loads(path.read_bytes()) == {
        "keep": {"value": 1},
        "plugins": {"hub": {"enabled": False}},
    }
    assert "hub disabled" in output.out.lower()


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


@pytest.mark.parametrize(
    ("first_writer", "first_args", "second_writer", "second_args", "agent", "profile"),
    [
        (
            config_utils.set_default_agent,
            ("new-agent", "global"),
            config_utils.set_default_profile,
            ("new-profile", "global"),
            "new-agent",
            "new-profile",
        ),
        (
            config_utils.clear_default_agent,
            ("global",),
            config_utils.set_default_profile,
            ("new-profile", "global"),
            None,
            "new-profile",
        ),
        (
            config_utils.set_default_profile,
            ("new-profile", "global"),
            config_utils.set_default_agent,
            ("new-agent", "global"),
            "new-agent",
            "new-profile",
        ),
        (
            config_utils.clear_default_profile,
            ("global",),
            config_utils.set_default_agent,
            ("new-agent", "global"),
            "new-agent",
            None,
        ),
    ],
)
def test_default_agent_and_profile_writers_serialize_updates(
    first_writer,
    first_args,
    second_writer,
    second_args,
    agent,
    profile,
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "global.json"
    path.write_text(
        json.dumps(
            {
                "settings": {"keep": True},
                "kollabor": {
                    "llm": {
                        "default_agent": {"name": "old-agent", "level": "global"},
                        "default_profile": {
                            "name": "old-profile",
                            "level": "global",
                        },
                        "active_profile": "old-profile",
                    }
                },
            }
        )
    )
    monkeypatch.setattr(config_utils, "get_global_config_path", lambda: path)
    monkeypatch.setattr(config_utils, "get_existing_global_config_path", lambda: path)

    first_waiting = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    results = {}
    failures = []
    original_write = config_utils.write_text_atomic

    def pause_first_before_replace(target, contents):
        if threading.current_thread().name == "first-config-writer":
            first_waiting.set()
            if not release_first.wait(timeout=5):
                raise TimeoutError("first config writer was not released")
        original_write(target, contents)

    def run_writer(key, writer, args, done=None):
        try:
            results[key] = writer(*args)
        except BaseException as exc:
            failures.append(exc)
        finally:
            if done is not None:
                done.set()

    monkeypatch.setattr(config_utils, "write_text_atomic", pause_first_before_replace)
    first = threading.Thread(
        target=run_writer,
        args=("first", first_writer, first_args),
        name="first-config-writer",
    )
    second = threading.Thread(
        target=run_writer,
        args=("second", second_writer, second_args, second_done),
        name="second-config-writer",
    )
    first.start()
    assert first_waiting.wait(timeout=5)
    second.start()
    second_done.wait(timeout=0.5)
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert results == {"first": True, "second": True}
    saved = json.loads(path.read_text())
    llm = saved["kollabor"]["llm"]
    if agent is None:
        assert "default_agent" not in llm
    else:
        assert llm["default_agent"]["name"] == agent
    if profile is None:
        assert "default_profile" not in llm
        assert llm["active_profile"] == "old-profile"
    else:
        assert llm["default_profile"]["name"] == profile
        assert llm["active_profile"] == profile
    assert saved["settings"]["keep"] is True
