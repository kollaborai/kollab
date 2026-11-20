"""Unreadable config files remain intact until a user explicitly repairs them."""

import asyncio
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor.panels.config import ConfigPanel
from kollabor_config import loader as loader_module
from kollabor_config import service as service_module
from kollabor_config.config_utils import get_global_config_path
from kollabor_config.loader import ConfigLoader, load_layered_user_config
from kollabor_config.manager import ConfigManager
from kollabor_config.service import ConfigService


def _layer_paths(tmp_path, monkeypatch):
    paths = {
        "global": tmp_path / "global.json",
        "project": tmp_path / "project" / "config.json",
        "local": tmp_path / "local" / "config.json",
    }
    monkeypatch.setattr(
        loader_module,
        "get_existing_global_config_path",
        lambda: paths["global"],
    )
    monkeypatch.setattr(
        loader_module,
        "get_project_data_dir_candidates",
        lambda *args, **kwargs: [paths["project"].parent],
    )
    monkeypatch.setattr(
        loader_module,
        "get_local_config_directory",
        lambda *args, **kwargs: paths["local"].parent,
    )
    return paths


def _write_bytes(path: Path, contents: bytes, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    os.chmod(path, mode)


def _explicit_loader(path: Path) -> ConfigLoader:
    loader = ConfigLoader(ConfigManager(path), fast_mode=True)
    loader.get_base_config = lambda: {}
    loader.get_plugin_configs = lambda: {}
    return loader


def _patch_standard_save_paths(paths, monkeypatch):
    monkeypatch.setattr(loader_module, "get_global_config_path", lambda: paths["global"])
    monkeypatch.setattr(
        service_module, "get_global_config_path", lambda: paths["global"]
    )
    monkeypatch.setattr(
        service_module, "get_local_config_path", lambda: paths["local"]
    )
    monkeypatch.setattr(
        loader_module,
        "get_global_config_path_candidates",
        lambda: [paths["global"]],
    )
    monkeypatch.setattr(loader_module, "get_local_config_path", lambda: paths["local"])
    monkeypatch.setattr(
        loader_module,
        "get_local_config_path_candidates",
        lambda: [paths["local"]],
    )


@pytest.mark.parametrize("layer", ["global", "project", "local"])
@pytest.mark.parametrize("contents", [b'{"broken":', b"[]"])
def test_layered_reader_distinguishes_malformed_files(layer, contents, tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    _write_bytes(paths[layer], contents)
    statuses = {}

    assert load_layered_user_config(tmp_path / "workspace", statuses=statuses) == {}
    assert statuses[layer] == "malformed"
    assert set(statuses) == {"global", "project", "local"}
    assert all(status == ("malformed" if name == layer else "absent") for name, status in statuses.items())


@pytest.mark.parametrize("layer", ["global", "project", "local"])
def test_layered_reader_distinguishes_permission_errors(layer, tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    target = paths[layer]
    _write_bytes(target, b'{"private": "DO_NOT_ECHO"}')
    read_bytes = Path.read_bytes

    def unreadable(path):
        if path == target:
            raise PermissionError("DO_NOT_ECHO")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    statuses = {}

    assert load_layered_user_config(tmp_path / "workspace", statuses=statuses) == {}
    assert statuses[layer] == "unreadable"


@pytest.mark.parametrize("layer", ["global", "project", "local"])
def test_layered_reader_marks_a_partial_read_malformed(layer, tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    target = paths[layer]
    _write_bytes(target, b'{"setting": true}')
    read_bytes = Path.read_bytes

    def partial_read(path):
        if path == target:
            return b'{"setting":'
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", partial_read)
    statuses = {}

    assert load_layered_user_config(tmp_path / "workspace", statuses=statuses) == {}
    assert statuses[layer] == "malformed"


def test_layered_reader_does_not_skip_an_unreadable_project_candidate(tmp_path, monkeypatch):
    _layer_paths(tmp_path, monkeypatch)
    unreadable = tmp_path / "old-project" / "config.json"
    later = tmp_path / "new-project" / "config.json"
    _write_bytes(later, b'{"settings":{"later_candidate":true}}')
    monkeypatch.setattr(
        loader_module,
        "get_project_data_dir_candidates",
        lambda *args, **kwargs: [unreadable.parent, later.parent],
    )
    original_stat = Path.stat

    def permission_error(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("DO_NOT_ECHO")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", permission_error)
    statuses = {}

    merged = load_layered_user_config(tmp_path / "workspace", statuses=statuses)

    assert statuses["project"] == "unreadable"
    assert "later_candidate" not in merged.get("settings", {})


@pytest.mark.parametrize(
    ("contents", "status"),
    [
        (b'{"broken":', "malformed"),
        (b"[]", "malformed"),
    ],
)
def test_explicit_config_path_reports_malformed_content(contents, status, tmp_path):
    path = tmp_path / "explicit.json"
    _write_bytes(path, contents)
    loader = _explicit_loader(path)

    loader.load_complete_config()

    assert loader.config_load_status["explicit"] == status


def test_explicit_config_path_reports_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"private": "DO_NOT_ECHO"}')
    read_bytes = Path.read_bytes

    def unreadable(candidate):
        if candidate == path:
            raise PermissionError("DO_NOT_ECHO")
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    loader = _explicit_loader(path)

    loader.load_complete_config()

    assert loader.config_load_status["explicit"] == "unreadable"


def test_explicit_config_path_reports_partial_read(tmp_path, monkeypatch):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"setting": true}')
    read_bytes = Path.read_bytes

    def partial_read(candidate):
        if candidate == path:
            return b'{"setting":'
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", partial_read)
    loader = _explicit_loader(path)

    loader.load_complete_config()

    assert loader.config_load_status["explicit"] == "malformed"


def test_valid_layers_still_merge_and_save(tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    _write_bytes(paths["global"], b'{"settings":{"global":1,"shared":"global"}}')
    _write_bytes(paths["project"], b'{"settings":{"project":2,"shared":"project"}}')
    _write_bytes(paths["local"], b'{"settings":{"local":3,"shared":"local"}}')
    statuses = {}

    merged = load_layered_user_config(tmp_path / "workspace", statuses=statuses)

    assert merged == {
        "settings": {"global": 1, "project": 2, "local": 3, "shared": "local"}
    }
    assert statuses == {"global": "valid", "project": "valid", "local": "valid"}

    explicit_path = tmp_path / "valid-explicit.json"
    _write_bytes(explicit_path, b'{"settings":{"before":1}}')
    loader = _explicit_loader(explicit_path)
    config = loader.load_complete_config()
    config["settings"]["after"] = 2
    assert loader.save_merged_config(config) is True
    assert json.loads(explicit_path.read_text(encoding="utf-8"))["settings"]["after"] == 2


@pytest.mark.parametrize("target", ["global", "project", "local"])
def test_valid_layered_save_targets_still_write_normally(target, tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    _patch_standard_save_paths(paths, monkeypatch)
    _write_bytes(paths[target], b'{"settings":{"before":true}}')
    loader = ConfigLoader(ConfigManager(paths["global"]), fast_mode=True)
    loader.get_base_config = lambda: {"settings": {}}
    loader.get_plugin_configs = lambda: {}
    config = loader.load_complete_config()
    config["settings"]["after"] = target

    save_target = "global" if target == "global" else None
    assert loader.save_merged_config(config, save_target=save_target) is True
    saved = json.loads(paths[target].read_text(encoding="utf-8"))
    assert saved["settings"]["before"] is True
    assert saved["settings"]["after"] == target
    assert all(
        not path.exists() for name, path in paths.items() if name != target
    )


def test_valid_project_save_when_global_config_is_malformed(tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    _patch_standard_save_paths(paths, monkeypatch)
    original_global = b'{"api_key":"DO_NOT_ECHO",'
    _write_bytes(paths["global"], original_global)
    _write_bytes(paths["project"], b'{"settings":{"before":1}}')
    loader = ConfigLoader(ConfigManager(paths["project"]), fast_mode=True)
    loader.get_base_config = lambda: {"settings": {}}
    loader.get_plugin_configs = lambda: {}

    config = loader.load_complete_config()
    config["settings"]["after"] = 2

    assert loader.save_merged_config(config) is True
    assert json.loads(paths["project"].read_text(encoding="utf-8"))["settings"] == {
        "before": 1,
        "after": 2,
    }
    assert paths["global"].read_bytes() == original_global


def test_project_candidate_is_the_layered_save_target(tmp_path, monkeypatch):
    global_path = tmp_path / "global.json"
    project_path = tmp_path / "project" / "config.json"
    local_dir = tmp_path / "local"
    project_path.parent.mkdir(parents=True)
    original_global = b'{"api_key":"DO_NOT_ECHO",'
    _write_bytes(global_path, original_global)
    _write_bytes(project_path, b'{"settings":{"before":1}}')
    monkeypatch.setattr(
        loader_module, "get_existing_global_config_path", lambda: global_path
    )
    monkeypatch.setattr(
        loader_module,
        "get_project_data_dir_candidates",
        lambda *args, **kwargs: [project_path.parent],
    )
    monkeypatch.setattr(
        loader_module, "get_local_config_directory", lambda *args, **kwargs: local_dir
    )
    _patch_standard_save_paths(
        {"global": global_path, "local": local_dir / "config.json"}, monkeypatch
    )
    loader = ConfigLoader(ConfigManager(project_path), fast_mode=True)
    loader.get_base_config = lambda: {"settings": {}}
    loader.get_plugin_configs = lambda: {}

    config = loader.load_complete_config()
    config["settings"]["after"] = 2

    assert loader.save_merged_config(config) is True
    assert json.loads(project_path.read_text(encoding="utf-8"))["settings"]["after"] == 2
    assert global_path.read_bytes() == original_global


def test_settings_panel_reports_and_preserves_an_unreadable_global_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    path = get_global_config_path()
    original = b'{"api_key":"DO_NOT_ECHO",'
    _write_bytes(path, original)
    service = ConfigService(path, fast_mode=True)
    context = SimpleNamespace(_llm_service=SimpleNamespace(config=service))

    try:
        assert path.read_bytes() == original
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        error = service.get_config_error()
        assert error and "repair" in error.lower()
        assert "DO_NOT_ECHO" not in error
        result = asyncio.run(
            ConfigPanel().act(
                context,
                "save",
                {
                    "changes": {"kollabor.updates.auto_update_enabled": True},
                    "target": "global",
                },
            )
        )
        assert result["ok"] is False
        assert "repair" in result["message"].lower()
        assert path.read_bytes() == original
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
    finally:
        service.shutdown()


def test_settings_panel_description_includes_actionable_config_error(tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    _patch_standard_save_paths(paths, monkeypatch)
    _write_bytes(paths["global"], b'{"api_key":"DO_NOT_ECHO",')
    service = ConfigService(paths["global"], fast_mode=True)
    context = SimpleNamespace(_llm_service=SimpleNamespace(config=service))

    try:
        error = service.get_config_error()
        description = asyncio.run(ConfigPanel().describe(context, {}))

        assert error and "repair" in error.lower()
        assert description.get("notice") == error
        assert "DO_NOT_ECHO" not in description["notice"]
    finally:
        service.shutdown()


def test_successful_project_save_keeps_malformed_global_config_error(tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    _patch_standard_save_paths(paths, monkeypatch)
    malformed_global = b'{"api_key":"DO_NOT_ECHO",'
    _write_bytes(paths["global"], malformed_global)
    _write_bytes(paths["project"], b'{"settings":{"before":1}}')
    service = ConfigService(paths["global"], fast_mode=True)

    try:
        assert service.get_config_error()
        assert service.save_config() is True
        assert "repair" in (service.get_config_error() or "").lower()
        assert paths["global"].read_bytes() == malformed_global
    finally:
        service.shutdown()


def test_successful_key_save_keeps_malformed_global_config_error(tmp_path, monkeypatch):
    paths = _layer_paths(tmp_path, monkeypatch)
    _patch_standard_save_paths(paths, monkeypatch)
    malformed_global = b'{"api_key":"DO_NOT_ECHO",'
    _write_bytes(paths["global"], malformed_global)
    _write_bytes(paths["project"], b'{"settings":{"before":1}}')
    service = ConfigService(paths["global"], fast_mode=True)

    try:
        assert service.get_config_error()
        assert service.save_key("settings.after", 2, save_target="local") is True
        assert "repair" in (service.get_config_error() or "").lower()
        assert paths["global"].read_bytes() == malformed_global
    finally:
        service.shutdown()


def test_successful_plugin_update_keeps_malformed_config_error(tmp_path):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"api_key":"DO_NOT_ECHO",')
    service = ConfigService(path, fast_mode=True)
    service.config_loader.update_with_plugins = lambda: True

    try:
        assert service.get_config_error()
        assert service.update_from_plugins() is True
        assert "repair" in (service.get_config_error() or "").lower()
        assert path.read_bytes() == b'{"api_key":"DO_NOT_ECHO",'
    finally:
        service.shutdown()


def test_plugin_update_does_not_replace_an_unreadable_explicit_config(tmp_path):
    path = tmp_path / "explicit.json"
    original = b'{"api_key":"DO_NOT_ECHO",'
    _write_bytes(path, original)
    loader = ConfigLoader(ConfigManager(path), plugin_registry=object(), fast_mode=True)
    loader.get_base_config = lambda: {}
    loader.get_plugin_configs = lambda: {"plugins": {"sample": {"enabled": True}}}

    assert loader.update_with_plugins() is False
    assert path.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_stale_full_config_writer_refuses_a_replaced_file(tmp_path):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"settings":{"value":1}}')
    first = _explicit_loader(path)
    second = _explicit_loader(path)
    first.load_complete_config()
    second.load_complete_config()

    assert first.save_merged_config({"settings": {"value": 2}}) is True
    after_first = path.read_bytes()
    assert second.save_merged_config({"settings": {"value": 3}}) is False
    assert path.read_bytes() == after_first


def test_explicit_backup_and_repair_keep_the_original_bytes_and_mode(tmp_path):
    path = tmp_path / "explicit.json"
    original = b'{"api_key":"DO_NOT_ECHO",'
    _write_bytes(path, original)
    service = ConfigService(path, fast_mode=True)

    try:
        assert path.read_bytes() == original
        backup = service.backup_config()
        assert backup is not None
        assert backup.read_bytes() == original
        assert stat.S_IMODE(backup.stat().st_mode) == 0o640

        _write_bytes(path, b'{"settings":{"repaired":true}}', mode=0o640)
        assert service.reload() is True
        assert service.get_config_error() is None
        assert service.get("settings.repaired") is True
        assert backup.read_bytes() == original
        assert stat.S_IMODE(backup.stat().st_mode) == 0o640
    finally:
        service.shutdown()


def test_restore_from_backup_repairs_unreadable_config_with_exact_bytes_and_mode(tmp_path):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"api_key":"DO_NOT_ECHO",', mode=0o600)
    service = ConfigService(path, fast_mode=True)
    backup = tmp_path / "known-good.json"
    known_good = b'{\n  "settings": {"repaired": true},\n  "api_key": "DO_NOT_ECHO"\n}\n'
    _write_bytes(backup, known_good, mode=0o640)

    try:
        assert service.get_config_error()
        assert service.restore_from_backup(backup) is True
        assert path.read_bytes() == known_good
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert service.get_config_error() is None
        assert service.get("settings.repaired") is True
    finally:
        service.shutdown()


def test_restore_from_backup_refuses_a_target_changed_since_load(tmp_path):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"api_key":"DO_NOT_ECHO",')
    service = ConfigService(path, fast_mode=True)
    backup = tmp_path / "known-good.json"
    _write_bytes(backup, b'{"settings":{"repaired":true}}')
    changed = b'{"settings":{"changed_elsewhere":true}}'

    try:
        _write_bytes(path, changed)
        assert service.restore_from_backup(backup) is False
        assert path.read_bytes() == changed
        error = service.get_config_error()
        assert error and "changed" in error.lower()
        assert "DO_NOT_ECHO" not in error
    finally:
        service.shutdown()


def test_restore_from_backup_repairs_config_after_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"api_key":"DO_NOT_ECHO",', mode=0o600)
    backup = tmp_path / "known-good.json"
    known_good = b'{"settings":{"repaired":true}}'
    _write_bytes(backup, known_good, mode=0o640)
    service = ConfigService(path, fast_mode=True, initialize=False)
    read_bytes = Path.read_bytes
    denied_reads = 0

    def unreadable_for_load_and_restore(target):
        nonlocal denied_reads
        if target == path and denied_reads < 2:
            denied_reads += 1
            raise PermissionError("DO_NOT_ECHO")
        return read_bytes(target)

    monkeypatch.setattr(Path, "read_bytes", unreadable_for_load_and_restore)
    try:
        service.config_loader.load_complete_config()
        assert service.config_loader.config_load_status["explicit"] == "unreadable"
        assert service.restore_from_backup(backup) is True
        assert path.read_bytes() == known_good
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert service.get_config_error() is None
    finally:
        service.shutdown()


def test_restore_from_backup_refuses_unreadable_target_changed_after_load(
    tmp_path, monkeypatch
):
    path = tmp_path / "explicit.json"
    _write_bytes(path, b'{"api_key":"DO_NOT_ECHO",', mode=0o600)
    backup = tmp_path / "known-good.json"
    _write_bytes(backup, b'{"settings":{"repaired":true}}')
    service = ConfigService(path, fast_mode=True, initialize=False)
    read_bytes = Path.read_bytes
    denied = False

    def unreadable_for_load(target):
        nonlocal denied
        if target == path and not denied:
            denied = True
            raise PermissionError("DO_NOT_ECHO")
        return read_bytes(target)

    monkeypatch.setattr(Path, "read_bytes", unreadable_for_load)
    try:
        service.config_loader.load_complete_config()
        assert service.config_loader.config_load_status["explicit"] == "unreadable"
        changed = b'{"settings":{"changed_elsewhere":true}}'
        path.write_bytes(changed)
        assert service.restore_from_backup(backup) is False
        assert path.read_bytes() == changed
        error = service.get_config_error()
        assert error and "changed" in error.lower()
        assert "DO_NOT_ECHO" not in error
    finally:
        service.shutdown()
