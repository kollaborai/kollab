"""A ConfigService writes only the file it was given (#967).

A review probe redirected one module's global-path function to a temp file and built a ConfigService
on it. The service wrote its starting config to the layered save location instead of the file it was
given: the real ~/.kollab/config.json, which went from the user's profiles and loadouts to the fresh
defaults. The same service saves through the same layers once it holds a change, so a service built on
a file of its own must save to that file too. Only the real global config follows the layers.

A file of its own that is not there yet starts blank: it never takes the user's layers (and their keys)
into itself when it is created.
"""

import json
from pathlib import Path

import pytest

from kollabor_config.config_utils import get_project_data_dir
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


CUSTOM = {"kollabor": {"llm": {"profiles": {"only-here": {"provider": "custom", "model": "m"}}}}}


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


@pytest.fixture
def custom_file(tmp_path):
    """A config file of a test, a probe or a tool: it exists, and it is not one of the layered files."""
    path = tmp_path / "scratch" / "config.json"
    path.parent.mkdir()
    path.write_text(json.dumps(CUSTOM))
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


def test_save_config_on_a_custom_file_writes_that_file(real_config, custom_file):
    service = ConfigService(custom_file, fast_mode=True)
    before = real_config.read_bytes()
    service.set("kollabor.llm.marker", 1)

    assert service.save_config() is True

    assert real_config.read_bytes() == before
    saved = json.loads(custom_file.read_text())
    assert saved["kollabor"]["llm"]["marker"] == 1
    assert "only-here" in saved["kollabor"]["llm"]["profiles"]


def test_save_key_on_a_custom_file_writes_that_file(real_config, custom_file):
    service = ConfigService(custom_file, fast_mode=True)
    before = real_config.read_bytes()

    assert service.save_key("kollabor.llm.marker", 1) is True

    assert real_config.read_bytes() == before
    assert json.loads(custom_file.read_text())["kollabor"]["llm"]["marker"] == 1


def test_save_config_on_the_saved_config_still_follows_the_layers(real_config):
    # The app's own service: a .kollab/config.json beside the project overrides the global file, so a
    # save lands there. Pinning custom files must not pin this one.
    local = Path.cwd() / ".kollab" / "config.json"  # the fixture's working directory
    local.parent.mkdir()
    local.write_text("{}")
    service = ConfigService(real_config, fast_mode=True)
    before = real_config.read_bytes()
    service.set("kollabor.llm.marker", 1)

    assert service.save_config() is True

    assert real_config.read_bytes() == before
    assert json.loads(local.read_text())["kollabor"]["llm"]["marker"] == 1


SECRETS = {
    "global": "sk-synthetic-global-1111",
    "project": "sk-synthetic-project-2222",
    "local": "sk-synthetic-local-3333",
}


@pytest.fixture
def secret_layers(tmp_path, monkeypatch):
    """A synthetic key in each layer the user's config is read from: global, project and local."""
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".kollab").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    layers = {
        "global": home / ".kollab" / "config.json",
        "project": get_project_data_dir() / "config.json",
        "local": project / ".kollab" / "config.json",
    }
    for name, path in layers.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        profile = {"provider": "custom", "model": "m", "api_key": SECRETS[name]}
        path.write_text(json.dumps({"kollabor": {"llm": {"profiles": {f"{name}-profile": profile}}}}))
    return layers


def _copied_layers(text):
    return [name for name, secret in SECRETS.items() if secret in text or f"{name}-profile" in text]


@pytest.mark.parametrize("redirect_global_path", [False, True], ids=["plain", "probe-redirect"])
def test_a_missing_custom_file_starts_blank_and_takes_nothing_from_the_layers(
    secret_layers, tmp_path, monkeypatch, redirect_global_path
):
    blank = tmp_path / "isolated" / "config.json"
    if redirect_global_path:  # how the review probe redirected the global path for itself
        monkeypatch.setattr("kollabor_config.config_utils.get_global_config_path", lambda: blank)
    before = {name: path.read_bytes() for name, path in secret_layers.items()}

    service = ConfigService(blank, fast_mode=True)

    assert blank.exists()
    assert _copied_layers(blank.read_text()) == []  # no key of any layer landed in the new file
    assert {name: path.read_bytes() for name, path in secret_layers.items()} == before
    profiles = service.get("kollabor.llm.profiles", {}) or {}
    assert [name for name in profiles if name.endswith("-profile")] == []  # a blank config is blank


def test_a_blank_custom_file_still_takes_nothing_from_the_layers_when_it_saves(secret_layers, tmp_path):
    blank = tmp_path / "isolated" / "config.json"
    service = ConfigService(blank, fast_mode=True)
    service.set("kollabor.llm.marker", 1)

    assert service.save_config() is True
    assert service.save_key("kollabor.llm.other", 2) is True

    saved = json.loads(blank.read_text())
    assert saved["kollabor"]["llm"]["marker"] == 1 and saved["kollabor"]["llm"]["other"] == 2
    assert _copied_layers(blank.read_text()) == []
