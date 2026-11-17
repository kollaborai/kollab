"""config.json is replaced whole: a second kollab reading it never finds half a file.

Opening it with "w" empties it first. Windows started together hit that gap, read an
empty file, "started fresh" and saved a config holding only their own key over the
user's profiles and API keys.
"""

import json
import os
import stat
from pathlib import Path

import pytest

from kollabor_config.atomic_write import write_bytes_atomic, write_text_atomic
from kollabor_config.config_utils import set_default_agent
from kollabor_config.manager import ConfigManager
from kollabor_config.service import ConfigService


@pytest.fixture
def replaced(monkeypatch):
    """Every os.replace as (what the target held at that moment, what replaces it)."""
    seen: list = []
    real = os.replace

    def spy(src, dst):
        target = Path(dst)
        seen.append(
            (target.read_text() if target.exists() else None, Path(src).read_text())
        )
        real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    return seen


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".kollab").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def test_the_target_is_never_half_written(tmp_path, replaced):
    target = tmp_path / "config.json"
    target.write_text("old")

    write_text_atomic(target, "new")

    assert replaced == [("old", "new")]
    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_binary_atomic_write_preserves_exact_bytes(tmp_path):
    target = tmp_path / "config.json"
    target.write_bytes(b"old")
    contents = b'{"key":"value"}\r\n\xff'

    write_bytes_atomic(target, contents)

    assert target.read_bytes() == contents
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_a_failed_write_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    target.write_text("old")

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        write_text_atomic(target, "new")

    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]  # the temp file is gone too


def test_a_symlinked_config_stays_a_link(tmp_path):
    real, link = tmp_path / "dotfiles.json", tmp_path / "config.json"
    real.write_text("old")
    link.symlink_to(real)

    write_text_atomic(link, "new")

    assert link.is_symlink()
    assert real.read_text() == "new"


def test_save_key_keeps_everything_else_and_writes_whole(home, replaced):
    path = home / ".kollab" / "config.json"
    mine = {"mine": {"api_key": "k"}}
    path.write_text(json.dumps({"kollabor": {"llm": {"profiles": mine}}}))
    service = ConfigService(path, fast_mode=True)
    replaced.clear()  # the startup migration wrote too

    assert service.save_key("terminal.render_fps", 7, "global")

    held_while_replacing, _ = replaced[0]
    assert json.loads(held_while_replacing)["kollabor"]["llm"]["profiles"] == mine
    saved = json.loads(path.read_text())
    assert saved["kollabor"]["llm"]["profiles"] == mine
    assert saved["terminal"]["render_fps"] == 7
    service.shutdown()


def test_the_startup_migration_writes_whole(home, replaced):
    path = home / ".kollab" / "config.json"
    path.write_text('{"keep": true}')  # no config_version: the migration rewrites it

    service = ConfigService(path, fast_mode=True)

    assert json.loads(replaced[0][0]) == {"keep": True}
    assert json.loads(path.read_text())["keep"] is True
    service.shutdown()


def test_a_malformed_config_is_preserved_at_startup(home):
    path = home / ".kollab" / "config.json"
    original = b'{"kollabor": {"llm": {"profiles": '
    path.write_bytes(original)  # a hand edit gone wrong
    os.chmod(path, 0o640)

    service = ConfigService(path, fast_mode=True)

    assert path.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert service.has_config_error()
    assert not path.with_name("config.json.corrupt").exists()
    service.shutdown()


def test_a_malformed_config_changed_after_load_is_preserved_on_save(home):
    path = home / ".kollab" / "config.json"
    service = ConfigService(path, fast_mode=True)
    original = b'{"api_key":"DO_NOT_ECHO",'
    path.write_bytes(original)
    os.chmod(path, 0o640)

    assert service.save_key("terminal.render_fps", 7, "global") is False

    assert path.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert not path.with_name("config.json.corrupt").exists()
    error = service.get_config_error()
    assert error and "changed" in error.lower()
    assert "DO_NOT_ECHO" not in error
    service.shutdown()


def test_the_manager_writes_whole(tmp_path, replaced):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1}')

    assert ConfigManager(path).save_config_file({"a": 2})

    assert replaced == [('{"a": 1}', '{\n  "a": 2\n}')]


def test_set_default_agent_writes_whole(home, replaced):
    path = home / ".kollab" / "config.json"
    path.write_text('{"keep": true}')

    assert set_default_agent("coder", "global")

    assert json.loads(replaced[0][0]) == {"keep": True}
    assert json.loads(path.read_text())["kollabor"]["llm"]["default_agent"] == {
        "name": "coder",
        "level": "global",
    }
