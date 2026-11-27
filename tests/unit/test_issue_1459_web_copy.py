"""#1459: browser errors and Hub replies do not direct users to a terminal."""

import pytest
from kollabor_engine import daemon_pool, server, voice_livekit

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


def test_engine_marks_webui_spawned_agents_for_browser_specific_hub_copy():
    env = {"KOLLAB_WEBUI_SESSION": "stale", "KEEP_ME": "yes"}

    daemon_pool._set_webui_session_env(env, webui_owned=True)
    assert env == {"KOLLAB_WEBUI_SESSION": "1", "KEEP_ME": "yes"}
    daemon_pool._set_webui_session_env(env, webui_owned=False)
    assert env == {"KEEP_ME": "yes"}


def test_hub_copy_keeps_cli_text_and_uses_page_text_for_webui_sessions(monkeypatch):
    cli_copy = "Run kollab --hub org from the terminal."
    page_copy = "Open Team Launch in the sidebar to choose a team."
    monkeypatch.setenv("KOLLAB_WEBUI_SESSION", "1")

    assert hub_plugin.HubPlugin._surface_copy(cli_copy, page_copy) == page_copy

    monkeypatch.delenv("KOLLAB_WEBUI_SESSION")
    assert hub_plugin.HubPlugin._surface_copy(cli_copy, page_copy) == cli_copy


@pytest.mark.asyncio
async def test_dns_unavailable_reply_has_browser_copy_without_changing_cli(monkeypatch):
    monkeypatch.setattr(hub_plugin, "_DNS_AVAILABLE", False)
    instance = object.__new__(hub_plugin.HubPlugin)
    monkeypatch.setenv("KOLLAB_WEBUI_SESSION", "1")

    page_reply = await instance._handle_dns_command("")

    assert page_reply == (
        "Agent DNS is unavailable because this engine is missing a required dependency. "
        "Other Hub controls remain available."
    )
    assert "pip install" not in page_reply

    monkeypatch.delenv("KOLLAB_WEBUI_SESSION")
    assert await instance._handle_dns_command("") == "dns: PyNaCl not installed\nrun: pip install pynacl"
