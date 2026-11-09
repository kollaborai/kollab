"""A ConfigService built on a path that does not exist never touches another config file (#967).

A review probe redirected one module's global-path function to a temp file and built a ConfigService
on it. The service wrote its starting config to the layered save location instead of the file it was
given: the real ~/.kollab/config.json, which went from the user's profiles and loadouts to the fresh
defaults.
"""

import json

import pytest

from kollabor_config.service import ConfigService

SAVED = {
    "kollabor": {
        "llm": {
            "profiles": {"mine": {"provider": "custom", "model": "m1", "api_key": "secret-key"}},
            "loadouts": {"fast": {"provider_profile": "mine", "model": "m1", "effort": "max"}},
            "default_loadout": {"name": "fast", "level": "global"},
        }
    }
}


@pytest.fixture
def real_config(tmp_path, monkeypatch):
    """The user's saved config under an isolated HOME, in a working directory with no .kollab of its own."""
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".kollab").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    path = home / ".kollab" / "config.json"
    path.write_text(json.dumps(SAVED))
    return path


def test_a_service_on_a_missing_path_leaves_the_saved_config_alone(real_config, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "config.json"
    # How a probe or a test redirects the global path for itself: the one name in config_utils.
    monkeypatch.setattr("kollabor_config.config_utils.get_global_config_path", lambda: elsewhere)
    before = real_config.read_bytes()

    ConfigService(elsewhere, fast_mode=True)

    assert real_config.read_bytes() == before
    assert elsewhere.exists()  # the file it was asked for is the one it creates


def test_a_service_on_the_saved_config_keeps_its_profiles(real_config):
    ConfigService(real_config, fast_mode=True)

    saved = json.loads(real_config.read_text())
    for key in SAVED["kollabor"]["llm"]:
        assert saved["kollabor"]["llm"][key] == SAVED["kollabor"]["llm"][key]
    assert saved["last_app_version"]  # stamped, nothing dropped


def test_a_service_on_a_missing_standard_path_creates_it(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".kollab").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    path = home / ".kollab" / "config.json"

    ConfigService(path, fast_mode=True)

    assert json.loads(path.read_text())["last_app_version"]
