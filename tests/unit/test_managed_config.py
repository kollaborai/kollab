"""The managed-config record: what the primary manages on this device."""

import os
import stat

from kollabor_config.managed_config import (
    ManagedConfig,
    clear_managed_config,
    managed_by,
    managed_config_path,
    read_managed_config,
    write_managed_config,
)

PRIMARY = "a" * 64


def record(**overrides):
    values = {
        "primary_key": PRIMARY,
        "primary_name": "laptop-kollab",
        "revision": 5,
        "digest": "d" * 64,
        "keys": (
            ("kollabor", "llm", "active_profile"),
            ("kollabor", "llm", "profiles", "gpt-5.4", "model"),
        ),
        "mcp_servers": ("mentiko",),
        "files": {"skills/tdd/SKILL.md": "e" * 64},
    }
    values.update(overrides)
    return ManagedConfig(**values)


def test_a_device_nobody_manages_reads_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert read_managed_config() is None
    assert managed_by("kollabor.llm.active_profile") is None


def test_write_then_read_round_trips_and_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    write_managed_config(record())

    assert read_managed_config() == record()
    path = managed_config_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_managed_by_names_the_primary_for_synced_keys_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    write_managed_config(record())

    assert managed_by("kollabor.llm.active_profile") == "laptop-kollab"
    # a profile name with a dot is one path segment, and still matches its dotted form
    assert managed_by("kollabor.llm.profiles.gpt-5.4.model") == "laptop-kollab"
    # a section that holds a managed key is marked; a sibling key is not
    assert managed_by("kollabor.llm") == "laptop-kollab"
    assert managed_by("kollabor.llm.terminal_timeout") is None
    assert managed_by("terminal.render_fps") is None
    assert managed_by("") is None


def test_a_damaged_record_reads_as_unmanaged(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    write_managed_config(record())
    managed_config_path().write_text("{not json")
    assert read_managed_config() is None
    managed_config_path().write_text('{"version": 99}')
    assert read_managed_config() is None
    managed_config_path().write_text(
        '{"version": 1, "primary_key": "k", "primary_name": "n", "revision": "x",'
        ' "digest": "", "keys": [], "mcp_servers": [], "files": {}}'
    )
    assert read_managed_config() is None


def test_clearing_needs_the_right_primary_and_keeps_the_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".kollab").mkdir()
    (tmp_path / ".kollab" / "config.json").write_text('{"kept": true}')
    write_managed_config(record())

    assert clear_managed_config(primary_key="b" * 64) is False
    assert read_managed_config() is not None
    assert clear_managed_config(primary_key=PRIMARY) is True
    assert read_managed_config() is None
    assert clear_managed_config() is False
    assert os.path.exists(tmp_path / ".kollab" / "config.json")
