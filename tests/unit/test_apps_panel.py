"""Tests for the Apps panels: the picker, the install form and the row actions.

The service runs on temp dirs with fake which seams. The MCP config is a temp
file and the session is a fake ctx that counts reloads. No real home, no network.
"""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

import kollabor.panels.apps as apps_panel
from kollabor.apps.service import AppsService
from kollabor.panels.base import PanelError, get_panel
from kollabor_agent import MCPManager

PY = sys.executable  # absolute path: the MCP validator checks the command exists
SECRET = "sk-test-SECRET-7f3a"


def _catalog():
    return {
        "recommended": ["cli:tool"],
        "featured": ["mcp:search"],
        "apps": [
            {
                "id": "search",
                "kind": "mcp",
                "name": "Search",
                "description": "Searches the web.",
                "publisher": "Test",
                "homepage": "https://example.com/search",
                "official": False,
                "requires": [],
                "mcp": {
                    "command": PY,
                    "args": ["-c", "pass"],
                    "inputs": [{"name": "KEY", "label": "API Key", "to": "env", "secret": True, "required": True}],
                },
            },
            {
                "id": "plain",
                "kind": "mcp",
                "name": "Plain",
                "description": "Needs no input.",
                "publisher": "Test",
                "homepage": "https://example.com/plain",
                "official": False,
                "requires": [],
                "mcp": {"command": PY, "args": ["-c", "pass"], "inputs": []},
            },
            {
                "id": "tool",
                "kind": "cli",
                "name": "Tool",
                "description": "A command line tool.",
                "publisher": "Test",
                "homepage": "https://example.com/tool",
                "official": False,
                "cli": {"bin": "tool", "install": [{"manager": "brew", "package": "tool"}]},
            },
        ],
    }


class FakeCtx:
    """The daemon's LocalStateService, reduced to the two MCP methods the panels call."""

    def __init__(self):
        self.reloads = 0
        self.fail_reload = False

    async def get_mcp_state(self):
        return SimpleNamespace(servers=[])

    async def reload_mcp_servers(self):
        if self.fail_reload:
            raise RuntimeError("reload broke")
        self.reloads += 1
        return {}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    present = {"brew", "tool"}  # the cli row is added while "tool" is on PATH
    service = AppsService(
        catalog=_catalog(),
        mcp_manager=MCPManager(mcp_dir=home / ".kollab" / "mcp"),
        home=home,
        workspace=tmp_path,
        which=lambda name: f"/bin/{name}" if name in present else None,
    )
    monkeypatch.setattr(apps_panel, "_service", lambda: service)
    fake = FakeCtx()
    fake.service = service
    return fake


def _run(coro):
    return asyncio.run(coro)


def _install(ctx, value=SECRET):
    return _run(
        get_panel("apps-install").act(ctx, "install", {"changes": {"KEY": value}, "context": {"id": "mcp:search"}})
    )


def test_picker_shape_and_sections(ctx):
    shape = _run(get_panel("apps").describe(ctx, {}))

    assert shape["panel"] == "apps" and shape["kind"] == "picker" and shape["title"] == "Apps"
    assert {"summary", "sections", "rows", "row_actions", "toolbar_actions"} <= shape.keys()
    # Only the cli tool counts as installed; the bundled skills are always there and do not.
    assert shape["summary"] == [{"label": "Installed", "value": "1"}]
    assert [section["id"] for section in shape["sections"]] == ["featured", "mcp", "cli", "built-in"]
    search = next(row for row in shape["rows"] if row["id"] == "mcp:search")
    assert search["state"] == "available" and search["actions"] == ["add"]


def test_add_that_needs_a_secret_opens_the_install_form_and_writes_nothing(ctx):
    out = _run(get_panel("apps").act(ctx, "add", {"id": "mcp:search"}))

    assert out["ok"] is True
    assert out["open"]["panel"] == "apps-install"
    assert out["open"]["context"] == {"id": "mcp:search"}
    assert out["panel"]["kind"] == "picker"
    assert "search" not in (ctx.service.mcp_manager.load_config() or {}).get("servers", {})
    assert ctx.reloads == 0


def test_install_form_shows_the_secret_as_a_secret_field(ctx):
    form = _run(get_panel("apps-install").describe(ctx, {"id": "mcp:search"}))
    [field] = form["sections"][0]["fields"]

    assert form["actions"] == [{"id": "install", "label": "Add"}]
    assert field["path"] == "KEY" and field["secret"] is True
    assert field["value"] is None and field["is_set"] is False


def test_install_writes_the_server_reloads_and_never_echoes_the_secret(ctx):
    out = _install(ctx)

    assert out["ok"] is True
    assert out["panel"]["panel"] == "apps"
    server = ctx.service.mcp_manager.load_config()["servers"]["search"]
    assert server["enabled"] is True and server["env"] == {"KEY": SECRET}
    assert ctx.reloads == 1
    assert SECRET not in json.dumps(out)
    assert SECRET not in json.dumps(_run(get_panel("apps").describe(ctx, {})))


def test_install_without_the_required_secret_returns_field_errors(ctx):
    out = _install(ctx, value="")

    assert out["ok"] is False
    assert list(out["errors"]) == ["KEY"]
    assert out["panel"]["kind"] == "form"
    assert ctx.reloads == 0


def test_enable_refuses_while_the_secret_is_empty(ctx):
    _run(ctx.service.add("mcp:search"))  # the terminal add: saved off with an empty key
    out = _run(get_panel("apps").act(ctx, "enable", {"id": "mcp:search"}))

    assert out["ok"] is False
    assert "/mcp setup" in out["message"]
    assert ctx.reloads == 0


def test_remove_drops_the_server_and_reloads(ctx):
    _install(ctx)
    out = _run(get_panel("apps").act(ctx, "remove", {"id": "mcp:search"}))

    assert out["ok"] is True
    assert "search" not in ctx.service.mcp_manager.load_config()["servers"]
    assert ctx.reloads == 2


def test_cli_row_has_no_remove_so_remove_is_404(ctx):
    with pytest.raises(PanelError) as info:
        _run(get_panel("apps").act(ctx, "remove", {"id": "cli:tool"}))

    assert info.value.status == 404


def test_unknown_row_is_404(ctx):
    with pytest.raises(PanelError) as info:
        _run(get_panel("apps").act(ctx, "add", {"id": "mcp:nope"}))

    assert info.value.status == 404


def test_unknown_action_is_404(ctx):
    with pytest.raises(PanelError) as info:
        _run(get_panel("apps").act(ctx, "frobnicate", {"id": "mcp:search"}))

    assert info.value.status == 404


def test_failed_reload_is_reported_and_the_change_stays_saved(ctx):
    ctx.fail_reload = True
    out = _install(ctx)

    assert out["ok"] is True
    assert "did not reload" in out["message"]
    assert "search" in ctx.service.mcp_manager.load_config()["servers"]


def test_add_without_inputs_installs_directly_and_reloads(ctx):
    out = _run(get_panel("apps").act(ctx, "add", {"id": "mcp:plain"}))

    assert out["ok"] is True
    assert "open" not in out or out["open"] is None
    assert ctx.service.mcp_manager.load_config()["servers"]["plain"]["enabled"] is True
    assert ctx.reloads == 1


def test_refresh_returns_a_fresh_picker(ctx):
    out = _run(get_panel("apps").act(ctx, "refresh", {}))

    assert out["ok"] is True
    assert out["panel"]["panel"] == "apps"


def test_describe_works_without_mcp_runtime(ctx):
    bare = SimpleNamespace()  # no get_mcp_state: the picker still renders

    shape = _run(get_panel("apps").describe(bare, {}))

    assert shape["kind"] == "picker"
