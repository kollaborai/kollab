"""Tests for /mcp browse, search, add and remove over the Apps service."""

import asyncio
import sys

import pytest

from kollabor.apps import registry
from kollabor.apps.service import AppsService
from kollabor.commands import mcp_apps as apps_command
from kollabor.commands.mcp_command import MCPCommandHandler
from kollabor.commands.registry import SlashCommandRegistry
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
    # no daemon: no MCP session to reload
    handler = MCPCommandHandler(SlashCommandRegistry(), mcp_integration=None)
    args = list(args) or ["browse"]
    command = SlashCommand(name="mcp", args=args, raw_input="/mcp " + " ".join(args))
    return asyncio.run(handler.handle_mcp(command))


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


def test_search_with_no_words_prints_usage(service):
    result = _run("search")

    assert result.success is False
    assert result.message == apps_command.USAGE


def test_mcp_lists_the_apps_verbs_and_apps_is_gone():
    registry = SlashCommandRegistry()
    MCPCommandHandler(registry, mcp_integration=None).register_commands()

    names = {sub.name for sub in registry.get_command("mcp").subcommands}
    assert {"browse", "search", "add", "remove", "list", "tools"} <= names
    assert registry.get_command("apps") is None
