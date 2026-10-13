"""Tests for the terminal /apps command: list, search, add and remove over the Apps service."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from kollabor.apps import registry
from kollabor.apps.service import AppsService
from kollabor.commands.registry import SlashCommandRegistry
from kollabor.commands.system_commands.handlers import apps as apps_command
from kollabor_agent import MCPManager
from kollabor_events.models import SlashCommand

PY = sys.executable


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


@pytest.fixture
def service(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    svc = AppsService(
        catalog=_catalog(),
        mcp_manager=MCPManager(mcp_dir=home / ".kollab" / "mcp"),
        home=home,
        workspace=tmp_path,
        which=lambda name: f"/bin/{name}" if name == "tool" else None,
        registry_get=lambda path: {"servers": []},  # search asks the MCP Registry: an empty one here
    )
    registry.clear_cache()
    monkeypatch.setattr(apps_command, "_service", lambda: svc)
    return svc


def _run(*args):
    event_bus = SimpleNamespace(get_service=lambda name: None)  # no daemon: no MCP session to reload
    handler = apps_command.AppsCommandHandler(SlashCommandRegistry(), event_bus)
    command = SlashCommand(name="apps", args=list(args), raw_input="/apps " + " ".join(args))
    return asyncio.run(handler.handle_apps(command))


def test_list_names_the_three_kinds(service):
    result = _run()

    assert result.success is True
    assert "MCP Servers" in result.message
    assert "CLI Tools" in result.message
    assert "Skills" in result.message
    assert "[available] Search (mcp:search)" in result.message


def test_list_counts_the_rest_and_leaves_library_skills_out(service):
    result = _run()

    assert "finds them." in result.message
    assert "(skill:tdd)" not in result.message  # a bundled skill: search finds it, the list does not


def test_search_reaches_library_skills(service):
    result = _run("search", "tdd")

    assert "(skill:tdd)" in result.message


def test_search_matches_a_word_in_the_row(service):
    result = _run("search", "tool")

    assert result.success is True
    assert "(cli:tool)" in result.message
    assert "(mcp:search)" not in result.message


def test_search_with_no_match_says_so(service):
    result = _run("search", "zzqq")

    assert result.success is True
    assert result.message.splitlines() == [
        "No apps in the directory match 'zzqq'.",
        "MCP Registry",
        "  No MCP Registry servers match zzqq.",
    ]


def test_search_without_words_is_usage(service):
    result = _run("search")

    assert result.success is False
    assert result.message.startswith("usage:")


def test_add_with_a_secret_saves_off_and_says_how_to_set_it(service):
    result = _run("add", "mcp:search")

    assert result.success is True
    assert "/mcp setup" in result.message
    assert service.mcp_manager.load_config()["servers"]["search"]["enabled"] is False


def test_add_of_an_unknown_id_is_an_error(service):
    result = _run("add", "mcp:nope")

    assert result.success is False
    assert result.display_type == "error"


def test_remove_of_a_cli_tool_is_refused(service):
    result = _run("remove", "cli:tool")

    assert result.success is False
    assert "can't be removed" in result.message


def test_unknown_verb_prints_usage(service):
    result = _run("frobnicate")

    assert result.success is False
    assert result.message == apps_command.USAGE
