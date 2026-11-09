"""The user config that applies in a folder: global, then the folder's project config, then its local one (#957).

The terminal reads these layers for the folder it runs in. The engine serves New Session for any folder, so it
asks for the layers of the folder a session will run in, not of its own working directory.
"""

import json

import pytest

from kollabor_config import ConfigService
from kollabor_config.config_utils import get_project_data_dir
from kollabor_config.loader import load_layered_user_config


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """A scratch HOME, and two project folders with the working directory in the first."""
    home, a, b = tmp_path / "home", tmp_path / "a", tmp_path / "b"
    for folder in (home, a, b):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(a)
    return home, a, b


def test_the_layers_of_a_folder_merge_global_then_project_then_local(folders):
    home, _, b = folders
    _write(home / ".kollab" / "config.json", {"x": {"from": "global", "g": 1, "p": 1, "l": 1}})
    _write(get_project_data_dir(b) / "config.json", {"x": {"from": "project", "p": 2, "l": 2}})
    _write(b / ".kollab" / "config.json", {"x": {"from": "local", "l": 3}})

    assert load_layered_user_config(b) == {"x": {"from": "local", "g": 1, "p": 2, "l": 3}}


def test_another_folders_local_config_never_leaks_in(folders):
    home, a, b = folders
    _write(home / ".kollab" / "config.json", {"x": {"from": "global"}})
    _write(a / ".kollab" / "config.json", {"x": {"from": "a"}, "only_a": 1})

    assert load_layered_user_config(b) == {"x": {"from": "global"}}


def test_no_folder_means_the_working_directory(folders):
    home, a, _ = folders
    _write(home / ".kollab" / "config.json", {"x": {"from": "global"}})
    _write(a / ".kollab" / "config.json", {"x": {"from": "a"}})

    assert load_layered_user_config() == {"x": {"from": "a"}}
    assert load_layered_user_config(a) == load_layered_user_config()


def test_a_missing_or_unreadable_layer_is_skipped(folders):
    home, _, b = folders
    _write(home / ".kollab" / "config.json", {"x": 1})
    (b / ".kollab").mkdir()
    (b / ".kollab" / "config.json").write_text("{not json")

    assert load_layered_user_config(b) == {"x": 1}
    assert load_layered_user_config(b / "gone") == {"x": 1}


def test_it_reads_what_a_config_service_reads_in_that_folder(folders, monkeypatch):
    # One reading of the layers: the service the terminal builds sees the same keys.
    home, _, b = folders

    def default(name, level):
        return {"kollabor": {"llm": {"default_loadout": {"name": name, "level": level}}}}

    _write(home / ".kollab" / "config.json", default("g", "global"))
    _write(b / ".kollab" / "config.json", default("b", "project"))
    monkeypatch.chdir(b)
    service = ConfigService(home / ".kollab" / "config.json", fast_mode=True)
    try:
        seen = service.get("kollabor.llm.default_loadout")
    finally:
        service.shutdown()

    assert load_layered_user_config(b)["kollabor"]["llm"]["default_loadout"] == seen


def test_reading_another_folder_creates_nothing_where_project_state_is_claimed(folders, monkeypatch):
    # Windows creates and claims a project's state directory the first time it looks it up. Reading a folder's
    # layers only to show its default must leave nothing behind in ~/.kollab/projects.
    home, _, b = folders
    monkeypatch.setattr("kollabor_config.config_utils.IS_WINDOWS", True)

    load_layered_user_config(b)

    assert not (home / ".kollab" / "projects").exists()
