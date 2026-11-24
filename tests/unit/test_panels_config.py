"""The config panel and the panel registry: describe, save, whitelist, secrets."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kollabor.panels import (
    PanelError,
    UnknownPanelError,
    get_panel,
    list_panels,
    require_panel,
)
from kollabor.panels.base import display_path
from kollabor.panels.config import apply_config_changes
from kollabor_config.managed_config import ManagedConfig, write_managed_config
from kollabor_config.secrets import is_secret_path

SECRET = "s3cr3t-never-sent-9f2"


class FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}
        self.saved = []
        self._notify_reload_callbacks = Mock()

    def get(self, path, default=None):
        node = self.values
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return default if node is None else node

    def set(self, path, value):
        *parents, last = path.split(".")
        node = self.values
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = value

    def save_key(self, path, value, save_target=None):
        self.saved.append((path, value, save_target))
        return True


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def ctx_for(config):
    return SimpleNamespace(_llm_service=SimpleNamespace(config=config))


def run(coro):
    return asyncio.run(coro)


def describe(config):
    return run(get_panel("config").describe(ctx_for(config), {}))


def fields_of(view):
    return {f["path"]: f for s in view["sections"] for f in s["fields"]}


def first(view, ftype, **where):
    return next(
        f
        for f in fields_of(view).values()
        if f["type"] == ftype
        and f["editable"]
        and all(f.get(k) == v for k, v in where.items())
    )


def save(config, changes, target="global"):
    return run(
        get_panel("config").act(
            ctx_for(config), "save", {"changes": changes, "target": target}
        )
    )


# -- registry -------------------------------------------------------------------


def test_registry_resolves_lazily_and_unknown_is_not_a_crash():
    assert get_panel("config") is list_panels()["config"]
    assert get_panel("nope") is None
    assert get_panel("../etc") is None
    with pytest.raises(UnknownPanelError) as err:
        require_panel("nope")
    assert err.value.status == 404


# -- describe -------------------------------------------------------------------


def test_describe_has_the_wire_shape(home):
    view = describe(FakeConfig({"kollabor": {"llm": {"active_profile": "mine"}}}))

    assert (view["panel"], view["kind"], view["title"]) == (
        "config",
        "form",
        "System Configuration",
    )
    assert [s["id"] for s in view["sections"]] == [
        f"s{i}" for i in range(len(view["sections"]))
    ]
    assert set(view["save_targets"]) == {"local", "global"}
    assert view["actions"][0]["id"] == "save"
    wanted = {"path", "type", "label", "help", "value", "min_value", "max_value"}
    wanted |= {"step", "options", "option_labels", "placeholder", "editable"}
    wanted |= {"managed_by"}
    wanted |= {"secret", "is_set"}
    for field in fields_of(view).values():
        assert wanted <= set(field)
    loadout = fields_of(view)["kollabor.llm.active_profile"]
    assert (loadout["type"], loadout["editable"], loadout["value"]) == (
        "label",
        False,
        "mine",
    )


def test_settings_configuration_excludes_plugin_fields(home, monkeypatch):
    from kollabor_tui.config_widgets import ConfigWidgetDefinitions

    monkeypatch.setattr(
        ConfigWidgetDefinitions,
        "get_config_modal_definition",
        lambda: {
            "sections": [
                {
                    "title": "General",
                    "widgets": [
                        {
                            "type": "checkbox",
                            "label": "Keep history",
                            "config_path": "terminal.keep_history",
                            "default": True,
                        }
                    ],
                },
                {
                    "title": "Plugin Settings",
                    "widgets": [
                        {
                            "type": "checkbox",
                            "label": "Built In",
                            "config_path": "plugins.builtin.enabled",
                        },
                        {
                            "type": "checkbox",
                            "label": "Installed",
                            "config_path": "plugin-registry.installed.enabled",
                        },
                    ],
                },
                {
                    "title": "Installed Plugin",
                    "widgets": [
                        {
                            "type": "text_input",
                            "label": "Greeting",
                            "config_path": "plugin-values.installed.greeting",
                        }
                    ],
                },
            ]
        },
    )

    fields = fields_of(describe(FakeConfig()))

    assert set(fields) == {"terminal.keep_history"}


def test_a_secret_never_leaves_describe(home):
    config = FakeConfig({"plugins": {"hub": {"bridge_token": SECRET}}})
    view = describe(config)

    assert "plugins.hub.bridge_token" not in fields_of(view)
    assert SECRET not in json.dumps(view)


def test_is_secret_path_uses_the_suffix_not_a_substring():
    assert is_secret_path("plugins.hub.bridge_token")
    assert is_secret_path("kollabor.llm.profiles.a.api_key")
    assert not is_secret_path("kollabor.llm.token_threshold_k")
    assert is_secret_path("x.y.anything", {"secret": True})


# -- save -----------------------------------------------------------------------


def test_save_writes_each_key_notifies_and_returns_the_fresh_panel(home):
    config = FakeConfig()
    slider = first(describe(config), "slider")
    new = slider["max_value"]

    out = save(config, {slider["path"]: new}, "global")

    assert out["ok"] and out["errors"] == {}
    assert config.saved == [(slider["path"], new, "global")]
    config._notify_reload_callbacks.assert_called_once()
    assert fields_of(out["panel"])[slider["path"]]["value"] == new
    assert "to ~/.kollab/config.json" in out["message"]


def test_hub_arrival_wake_is_only_in_plugins_settings(home):
    config = FakeConfig({"plugins": {"hub": {"arrival_wake": "none"}}})
    path = "plugins.hub.arrival_wake"

    assert path not in fields_of(describe(config))

    refused = save(config, {path: "koordinator"}, "local")
    assert not refused["ok"]
    assert refused["errors"] == {path: "unknown setting"}
    assert config.saved == []

def test_hub_plugin_fields_are_not_duplicated_in_configuration(home):
    config = FakeConfig({"plugins": {"hub": {"roster": "marketing-crew"}}})
    fields = fields_of(describe(config))

    assert not any(path.startswith("plugins.hub.") for path in fields)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("plugins.voice.mode", "quiet"),
        ("plugins.voice.enabled", False),
    ],
)
def test_builtin_config_changes_preserve_kind_when_installed_plugin_id_collides(
    tmp_path, monkeypatch, path, value,
):
    from kollabor_plugins import registry, services, settings

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    home.mkdir()
    registry_path = home / "plugins.json"
    registry_path.write_text(
        json.dumps({"plugins": {"voice": {"enabled": True}}, "dev": {}}),
        encoding="utf-8",
    )
    builtin = {
        "id": "voice",
        "kind": "built-in",
        "settings": [
            {"path": "plugins.voice.enabled", "type": "checkbox", "editable": True},
            {"path": "plugins.voice.mode", "type": "text_input", "editable": True},
        ],
    }
    builtin_writes = []
    package_writes = []
    monkeypatch.setattr(registry, "plugin_entries", lambda: [builtin])
    monkeypatch.setattr(settings, "_builtin", lambda plugin_id: builtin if plugin_id == "voice" else None)
    monkeypatch.setattr(
        settings,
        "_save_builtin",
        lambda entry, values, **kwargs: builtin_writes.append((entry["id"], values)),
    )
    monkeypatch.setattr(
        settings,
        "_save_locked",
        lambda plugin_id, values: package_writes.append((plugin_id, values)),
    )
    monkeypatch.setattr(services, "stop_services", lambda _plugin_id: [])

    assert apply_config_changes(FakeConfig(), {path: value}, "global")

    assert builtin_writes == [("voice", {path: value})]
    assert package_writes == []
    assert json.loads(registry_path.read_text(encoding="utf-8"))["plugins"]["voice"]["enabled"] is True


def test_option_labels_only_name_options_and_only_dropdowns_carry_them(home):
    for field in fields_of(describe(FakeConfig())).values():
        labels = field["option_labels"]
        if field["type"] != "dropdown":
            assert labels is None, field["path"]
        elif labels:
            assert set(labels) <= set(field["options"]), field["path"]
            assert all(isinstance(text, str) and text for text in labels.values())


def test_save_refuses_labels_loadout_rows_and_unknown_paths(home):
    config = FakeConfig({"kollabor": {"llm": {"active_profile": "mine"}}})

    out = save(
        config,
        {"kollabor.llm.active_profile": "other", "no.such.setting": 1},
    )

    assert not out["ok"]
    assert set(out["errors"]) == {"kollabor.llm.active_profile", "no.such.setting"}
    assert config.saved == []
    config._notify_reload_callbacks.assert_not_called()


def test_save_validates_values_on_the_server(home):
    config = FakeConfig()
    view = describe(config)
    slider = first(view, "slider")
    checkbox = first(view, "checkbox")
    dropdown = first(view, "dropdown")
    text = first(view, "text_input")

    out = save(
        config,
        {
            slider["path"]: slider["max_value"] + 1,
            checkbox["path"]: "yes",
            dropdown["path"]: "zz-not-an-option",
            text["path"]: "x" * 5000,
        },
    )

    assert not out["ok"]
    assert set(out["errors"]) == {
        slider["path"],
        checkbox["path"],
        dropdown["path"],
        text["path"],
    }
    assert config.saved == []
    ok = save(
        config,
        {
            checkbox["path"]: not checkbox["value"],
            dropdown["path"]: dropdown["options"][0],
        },
    )
    assert ok["ok"] and len(config.saved) == 2


def test_save_rejects_a_managed_key_even_if_the_ui_sent_it(home):
    (home / ".kollab").mkdir()
    (home / ".kollab" / "config.json").write_text(
        json.dumps({"terminal": {"render_fps": 50}})
    )
    write_managed_config(
        ManagedConfig(
            primary_key="a" * 64,
            primary_name="laptop-kollab",
            revision=1,
            digest="d" * 64,
            keys=(("terminal", "render_fps"),),
        )
    )
    config = FakeConfig()

    field = fields_of(describe(config))["terminal.render_fps"]
    out = save(config, {"terminal.render_fps": 7})

    assert (field["managed_by"], field["editable"], field["value"]) == (
        "laptop-kollab",
        False,
        50,
    )
    assert out["errors"] == {"terminal.render_fps": "managed by laptop-kollab"}
    assert config.saved == []


def test_bad_requests_raise_typed_errors(home):
    config = FakeConfig()
    with pytest.raises(PanelError) as err:
        save(config, {}, target="nowhere")
    assert err.value.status == 400
    with pytest.raises(PanelError) as err:
        run(get_panel("config").act(ctx_for(config), "explode", {}))
    assert err.value.status == 404
    with pytest.raises(PanelError) as err:
        run(get_panel("config").describe(SimpleNamespace(), {}))
    assert err.value.status == 503


def test_apply_config_changes_reports_a_failed_write_and_skips_notify():
    config = FakeConfig()
    config.save_key = lambda *a, **k: False
    assert apply_config_changes(config, {"a.b": 1}, "local") is False
    config._notify_reload_callbacks.assert_not_called()
    assert apply_config_changes(config, {}, "local") is True


def test_display_path_writes_only_the_home_directory_as_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "me"))
    assert display_path(tmp_path / "me" / ".kollab" / "config.json") == "~/.kollab/config.json"
    assert display_path(tmp_path / "me") == "~"
    assert display_path(tmp_path / "me2" / "x") == str(tmp_path / "me2" / "x")
