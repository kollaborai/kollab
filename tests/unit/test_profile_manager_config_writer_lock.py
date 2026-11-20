"""Regression coverage for profile manager config writer coordination."""

import json
from threading import Event, Thread

import kollabor_ai.profile_manager as profile_manager_module
from kollabor_ai.profile_manager import ProfileManager
from kollabor_config import config_utils


def test_profile_activation_preserves_concurrent_default_profile_update(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"kollabor": {"llm": {"profiles": {}}}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        profile_manager_module, "get_global_config_path", lambda: config_path
    )
    monkeypatch.setattr(
        profile_manager_module, "get_existing_global_config_path", lambda: config_path
    )
    monkeypatch.setattr(config_utils, "get_global_config_path", lambda: config_path)

    original_write = profile_manager_module.write_text_atomic
    writer_started = Event()
    writer_attempting_update = Event()
    writer_finished = Event()
    writer_result = []
    writer_errors = []
    writer_blocked_by_profile_save = []
    writer = None

    def set_default_profile():
        writer_attempting_update.set()
        try:
            writer_result.append(config_utils.set_default_profile("new-profile", "global"))
        except BaseException as exc:  # surfaced in the test thread below
            writer_errors.append(exc)
        finally:
            writer_finished.set()

    def interleaved_atomic_write(path, contents):
        nonlocal writer
        if not writer_started.is_set():
            writer_started.set()
            writer = Thread(target=set_default_profile)
            writer.start()
            assert writer_attempting_update.wait(timeout=2)
            writer_blocked_by_profile_save.append(not writer_finished.wait(timeout=0.05))
        original_write(path, contents)

    monkeypatch.setattr(profile_manager_module, "write_text_atomic", interleaved_atomic_write)

    manager = ProfileManager.__new__(ProfileManager)
    saved = manager._save_active_profile_to_config("next")
    assert writer is not None
    writer.join(timeout=2)

    assert not writer.is_alive()
    assert not writer_errors
    assert writer_result == [True]
    assert saved is True
    assert writer_blocked_by_profile_save == [True]

    llm_config = json.loads(config_path.read_text(encoding="utf-8"))["kollabor"]["llm"]
    assert llm_config["active_profile"] == "new-profile"
    assert llm_config["default_profile"] == {"name": "new-profile", "level": "global"}
