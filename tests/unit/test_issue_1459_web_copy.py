"""#1459: browser errors and Hub replies do not direct users to a terminal."""

from types import SimpleNamespace

import pytest

from kollabor_engine import server, voice_livekit

from plugins.hub import plugin as hub_plugin

HALTED_WITH_CLI_RESUME = (
    "halt.json is unreadable (invalid json), so every folder is held as halted. "
    "Restore it, or clear it with: kollab --hub resume --local"
)


def test_engine_halt_waiter_copy_does_not_show_the_cli_repair_command():
    message = server._webui_halted_detail(HALTED_WITH_CLI_RESUME)

    assert message == (
        "This workspace is held because its network halt record is unreadable. "
        "The page will keep it closed until the record is repaired."
    )
    assert "kollab --" not in message


def test_missing_voice_server_copy_describes_the_page_state():
    assert voice_livekit.LIVEKIT_MISSING == (
        "Voice is unavailable because this engine is missing its voice server. "
        "The page cannot start a voice room."
    )
    assert "brew install" not in voice_livekit.LIVEKIT_MISSING


def test_hub_copy_uses_request_source_instead_of_daemon_environment(monkeypatch):
    cli_copy = "Run kollab --hub org from the terminal."
    page_copy = "Open Team Launch in the sidebar to choose a team."

    monkeypatch.delenv("KOLLAB_WEBUI_SESSION", raising=False)
    assert hub_plugin.HubPlugin._surface_copy(
        cli_copy, page_copy, input_source="webui"
    ) == page_copy

    monkeypatch.setenv("KOLLAB_WEBUI_SESSION", "1")
    assert hub_plugin.HubPlugin._surface_copy(
        cli_copy, page_copy, input_source="state_rpc"
    ) == cli_copy


@pytest.mark.asyncio
async def test_hub_slash_command_carries_request_source_to_dns_reply(monkeypatch):
    monkeypatch.setattr(hub_plugin, "_DNS_AVAILABLE", False)
    instance = object.__new__(hub_plugin.HubPlugin)
    command = SimpleNamespace(args=["dns"], _input_source="webui")
    monkeypatch.delenv("KOLLAB_WEBUI_SESSION", raising=False)

    page_reply = await instance._handle_hub_command(command)

    assert page_reply == (
        "Agent DNS is unavailable because this engine is missing a required dependency. "
        "Other Hub controls remain available."
    )
    assert "pip install" not in page_reply

    command._input_source = "state_rpc"
    monkeypatch.setenv("KOLLAB_WEBUI_SESSION", "1")
    assert await instance._handle_hub_command(command) == (
        "dns: PyNaCl not installed\nrun: pip install pynacl"
    )
