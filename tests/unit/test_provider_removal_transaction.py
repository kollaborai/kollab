import json
from pathlib import Path

import kollabor_ai.profile_manager as profile_module
from kollabor_ai.profile_manager import ProfileManager


def test_denied_later_config_write_keeps_earlier_config_unchanged(monkeypatch, tmp_path):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project" / ".kollab" / "config.json"
    original = {
        "kollabor": {
            "llm": {
                "profiles": {"anthropic-cloud": {"provider": "anthropic"}},
                "active_profile": "anthropic-cloud",
            }
        }
    }
    for path in (global_path, local_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(original), encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda self: None)
    monkeypatch.setattr(
        profile_module, "get_existing_global_config_path", lambda: global_path
    )
    monkeypatch.setattr(
        profile_module,
        "get_local_config_path_candidates",
        lambda folder=None: [local_path],
    )
    manager = ProfileManager()

    replace = profile_module.os.replace
    writes = type(global_path).write_text
    global_replacements = 0

    def deny_local_and_global_rollback(source, destination):
        nonlocal global_replacements
        if Path(destination).resolve() == local_path.resolve():
            raise PermissionError("local replacement denied")
        if Path(destination).resolve() == global_path.resolve():
            global_replacements += 1
            if global_replacements > 1:
                raise PermissionError("global rollback denied")
        return replace(source, destination)

    def deny_direct_global_rollback(path, *args, **kwargs):
        if path == global_path:
            raise PermissionError("global direct restore denied")
        return writes(path, *args, **kwargs)

    monkeypatch.setattr(profile_module.os, "replace", deny_local_and_global_rollback)
    monkeypatch.setattr(
        type(global_path), "write_text", deny_direct_global_rollback
    )

    removed = manager.remove_provider_profiles("anthropic")
    assert removed == []
    assert json.loads(global_path.read_text(encoding="utf-8")) == original
    assert json.loads(local_path.read_text(encoding="utf-8")) == original


def test_late_config_failure_restores_earlier_file_after_atomic_rollback_fails(
    monkeypatch, tmp_path
):
    global_path = tmp_path / "home" / ".kollab" / "config.json"
    local_path = tmp_path / "project" / ".kollab" / "config.json"
    original = {
        "kollabor": {
            "llm": {
                "profiles": {"anthropic-cloud": {"provider": "anthropic"}},
                "active_profile": "anthropic-cloud",
            }
        }
    }
    for path in (global_path, local_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(original), encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda self: None)
    monkeypatch.setattr(
        profile_module, "get_existing_global_config_path", lambda: global_path
    )
    monkeypatch.setattr(
        profile_module,
        "get_local_config_path_candidates",
        lambda folder=None: [local_path],
    )
    manager = ProfileManager()

    replace = profile_module.os.replace
    writes = type(global_path).write_text
    replacement_counts = {global_path.resolve(): 0, local_path.resolve(): 0}

    def deny_late_local_and_atomic_rollback(source, destination):
        target = Path(destination).resolve()
        if target in replacement_counts:
            replacement_counts[target] += 1
            count = replacement_counts[target]
            if target == local_path.resolve() and count == 2:
                raise PermissionError("late local replacement denied")
            if target == global_path.resolve() and count == 3:
                raise PermissionError("atomic global rollback denied")
        return replace(source, destination)

    def deny_direct_global_rollback(path, *args, **kwargs):
        if Path(path).resolve() == global_path.resolve():
            raise PermissionError("Path.write_text global restore denied")
        return writes(path, *args, **kwargs)

    monkeypatch.setattr(
        profile_module.os, "replace", deny_late_local_and_atomic_rollback
    )
    monkeypatch.setattr(type(global_path), "write_text", deny_direct_global_rollback)

    assert manager.remove_provider_profiles("anthropic") == []
    assert json.loads(global_path.read_text(encoding="utf-8")) == original
    assert json.loads(local_path.read_text(encoding="utf-8")) == original
