"""/config on a device whose settings the network's primary manages.

Synced keys show `managed by <primary>` and cannot be edited there; everything
else in /config still edits as before. The loadout rows (Story 8) show what is
active, read-only, for every device.
"""

import json
import re

import pytest

from kollabor_config.managed_config import ManagedConfig, write_managed_config
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.widgets.label import LabelWidget
from plugins.altview.config_altview import ConfigAltView

ANSI = re.compile(r"\x1b\[[0-9;]*m")
FAKE_KEY = "sk-fake-never-drawn-4c1d"


class FakeConfigService:
    """A config service that is stale on purpose: disk is where the truth is."""

    def __init__(self, values):
        self.values = values
        self.saved = []

    def get(self, path, default=None):
        node = self.values
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return default if node is None else node

    def set(self, path, value):
        self.values[path] = value

    def save_key(self, path, value, save_target=None):
        self.saved.append((path, value, save_target))
        return True


def text_of(widget):
    return ANSI.sub("", "\n".join(widget.render_modern(width=100, position="only")))


def find(view, config_path):
    return [
        w for ws in view._section_widgets for w in ws if w.config_path == config_path
    ]


def sync_to(tmp_path, monkeypatch, settings, keys):
    monkeypatch.setenv("HOME", str(tmp_path))
    kollab = tmp_path / ".kollab"
    kollab.mkdir()
    (kollab / "config.json").write_text(json.dumps(settings))
    write_managed_config(
        ManagedConfig(
            primary_key="a" * 64,
            primary_name="laptop-kollab",
            revision=3,
            digest="d" * 64,
            keys=tuple(tuple(k.split(".")) for k in keys),
        )
    )


def opened(values):
    view = ConfigAltView()
    view.config_service = FakeConfigService(values)
    view._load_widgets()
    return view


SYNCED = {
    "kollabor": {
        "llm": {
            "active_profile": "anthropic",
            "terminal_timeout": 50,
            "profiles": {
                "anthropic": {"model": "claude-opus-5-5", "api_key": FAKE_KEY}
            },
        }
    },
    "terminal": {"local_only": 1},
}
SYNCED_KEYS = [
    "kollabor.llm.active_profile",
    "kollabor.llm.terminal_timeout",
    "kollabor.llm.profiles.anthropic.model",
    "kollabor.llm.profiles.anthropic.api_key",
]
STALE = {"kollabor": {"llm": {"active_profile": "old-loadout", "terminal_timeout": 120}}}


def test_a_synced_loadout_shows_managed_by_the_primary_with_the_value_from_disk(
    tmp_path, monkeypatch
):
    sync_to(tmp_path, monkeypatch, SYNCED, SYNCED_KEYS)

    view = opened(STALE)

    (loadout,) = find(view, "kollabor.llm.active_profile")
    (model,) = find(view, "kollabor.llm.profiles.anthropic.model")
    assert isinstance(loadout, LabelWidget) and isinstance(model, LabelWidget)
    assert "Loadout" in text_of(loadout) and "anthropic" in text_of(loadout)
    assert "old-loadout" not in text_of(
        loadout
    )  # the stale in-memory value is not shown
    assert "managed by laptop-kollab" in text_of(loadout)
    assert "claude-opus-5-5" in text_of(model) and "managed by laptop-kollab" in text_of(
        model
    )


def test_other_synced_settings_become_read_only_labels_and_the_rest_still_edit(
    tmp_path, monkeypatch
):
    sync_to(tmp_path, monkeypatch, SYNCED, SYNCED_KEYS)

    view = opened(STALE)

    (timeout,) = find(view, "kollabor.llm.terminal_timeout")
    assert isinstance(timeout, LabelWidget)
    assert "50" in text_of(timeout) and "managed by laptop-kollab" in text_of(timeout)
    (streaming,) = find(view, "kollabor.llm.enable_streaming")  # not synced
    assert not isinstance(streaming, LabelWidget)
    assert "managed by" not in text_of(streaming)


def test_a_synced_row_cannot_be_edited_or_saved(tmp_path, monkeypatch):
    sync_to(tmp_path, monkeypatch, SYNCED, SYNCED_KEYS)
    view = opened(STALE)
    (timeout,) = find(view, "kollabor.llm.terminal_timeout")

    consumed = [
        timeout.handle_input(KeyPress(name=name, code=char or name, char=char or None))
        for name, char in (
            ("Enter", "\r"),
            ("Space", " "),
            ("ArrowRight", ""),
            ("ArrowLeft", ""),
        )
    ]

    assert consumed == [False] * 4
    assert not timeout.has_pending_changes()
    view._do_save("global")
    assert view.config_service.saved == []  # nothing was written anywhere


def test_a_synced_secret_is_never_drawn():
    view = ConfigAltView()
    view._managed = ManagedConfig(
        primary_key="a" * 64,
        primary_name="laptop-kollab",
        keys=(("kollabor", "llm", "profiles", "anthropic", "api_key"),),
    )
    view._global_settings = SYNCED
    widget = view._managed_label(
        {
            "type": "text_input",
            "label": "API Key",
            "config_path": "kollabor.llm.profiles.anthropic.api_key",
        },
        "laptop-kollab",
    )

    rendered = text_of(widget)

    assert "set" in rendered and "managed by laptop-kollab" in rendered
    assert FAKE_KEY not in rendered


def test_a_device_nobody_manages_shows_the_loadout_without_a_label(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    values = {
        "kollabor": {
            "llm": {
                "active_profile": "mine",
                "profiles": {"mine": {"model": "gpt-5.5"}},
            }
        }
    }

    view = opened(values)

    (loadout,) = find(view, "kollabor.llm.active_profile")
    (model,) = find(view, "kollabor.llm.profiles.mine.model")
    assert "mine" in text_of(loadout) and "gpt-5.5" in text_of(model)
    assert "managed by" not in text_of(loadout) + text_of(model)
    (timeout,) = find(view, "kollabor.llm.terminal_timeout")
    assert not isinstance(timeout, LabelWidget)


def test_the_loadout_rows_lead_the_llm_settings_section(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    view = opened({"kollabor": {"llm": {"active_profile": "mine"}}})

    index = view._sections.index("LLM Settings")

    assert [w.get_label() for w in view._section_widgets[index][:2]] == [
        "Loadout",
        "Enable Streaming",
    ]


def test_search_finds_a_managed_row_by_its_label(tmp_path, monkeypatch):
    sync_to(tmp_path, monkeypatch, SYNCED, SYNCED_KEYS)
    view = opened(STALE)

    view._search_query = "loadout"

    assert "LLM Settings" in [view._sections[i] for i in view._visible_sections()]
    assert "Loadout" in [w.get_label() for w in view._visible_widgets()]


@pytest.mark.parametrize("record_text", ["{not json", '{"version": 7}'])
def test_a_damaged_record_leaves_config_fully_editable(
    tmp_path, monkeypatch, record_text
):
    sync_to(tmp_path, monkeypatch, SYNCED, SYNCED_KEYS)
    (tmp_path / ".kollab" / "private" / "managed-config.json").write_text(record_text)

    view = opened(STALE)

    (timeout,) = find(view, "kollabor.llm.terminal_timeout")
    assert not isinstance(timeout, LabelWidget)
