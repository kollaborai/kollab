"""Voice preferences persist across the TUI, web/RPC and active chat boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_voice.classifiers import load_preferences, save_preferences
from kollabor_voice.speech_settings import speech_defaults

from kollabor.commands.system_commands.handlers.voicemode import VoiceModeCommandHandler
from kollabor.panels import PanelError, get_panel
from kollabor.panels.voice import VoicePanel
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService, _needs_terminal_reply
from kollabor.state.remote import RemoteStateService
from kollabor_tui.key_parser import KeyPress
from plugins.altview.voice_config_altview import VoiceConfigAltView
from plugins.voice_plugin import VoicePlugin


@pytest.fixture
def voice(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    services = {}
    bus = SimpleNamespace(
        get_service=services.get, register_service=services.__setitem__
    )
    plugin = VoicePlugin(event_bus=bus)
    services["voice_plugin"] = plugin
    state = LocalStateService(None, None, event_bus=bus)
    return state, plugin, tmp_path


@pytest.mark.asyncio
async def test_canonical_command_and_config_modal(voice):
    state, plugin, _ = voice
    commands = []
    handler = VoiceModeCommandHandler(
        SimpleNamespace(register_command=commands.append), state._event_bus
    )
    handler.register_commands()
    assert [c.name for c in commands] == ["voice"]
    assert not commands[0].aliases
    assert "config" in [s.name for s in commands[0].subcommands]
    stack = SimpleNamespace(push=AsyncMock())
    state._event_bus.register_service("altview_stack_manager", stack)
    result = await handler.handle_voicemode(SimpleNamespace(args=["config"]))
    assert result.success
    assert isinstance(stack.push.await_args.args[0], VoiceConfigAltView)
    assert not plugin.requested
    assert (
        _needs_terminal_reply("voice")
        == "/voice config opens in Settings → Voice in the web UI"
    )


@pytest.mark.asyncio
async def test_web_rpc_save_tui_read_and_fresh_chat(voice):
    state, plugin, root = voice
    handlers = {}
    register_state_handlers(
        SimpleNamespace(register=lambda name, fn: handlers.__setitem__(name, fn)), state
    )

    async def call(method, params, timeout=None):
        return await handlers[method](params)

    remote = RemoteStateService(SimpleNamespace(call=call))
    panel = await remote.get_panel("voice")
    assert get_panel("voice").name == "voice"
    fields = {f["path"]: f for s in panel["sections"] for f in s["fields"]}
    assert {p for p, f in fields.items() if f["editable"]} == {
        "classifier",
        "context_lines",
        *speech_defaults(),
    }
    other = VoicePlugin(event_bus=state._event_bus)
    plugin._pending = [{"event_id": "keep"}]
    plugin._client = SimpleNamespace(call=AsyncMock())
    result = await remote.panel_action(
        "voice",
        "save",
        {
            "changes": {"classifier": "provider", "context_lines": 20.0},
            "target": "device",
        },
    )
    assert result["ok"]
    saved = load_preferences()
    assert (saved["classifier"], saved["context_lines"]) == ("provider", 20)
    assert json.loads((root / "preferences.json").read_text())["context_lines"] == 20
    assert other.status()["context_lines"] == 20
    assert other.classifier_name == "provider"
    view = VoiceConfigAltView(plugin)
    view._load_widgets()
    widgets = {w.config_path: w for ws in view._section_widgets for w in ws}
    assert widgets["context_lines"].get_value() == 20
    assert all(
        "render error" not in line
        for line in view._render_widget_modern(widgets["context_lines"], 80, "last")
    )
    for _ in range(5):
        widgets["context_lines"].handle_input(
            KeyPress(name="ArrowRight", code="\x1b[C")
        )
    widgets["kokoro_speed"].handle_input(KeyPress(name="ArrowRight", code="\x1b[C"))
    await view.handle_input(KeyPress(name="Ctrl+S", code=19))
    assert load_preferences()["context_lines"] == 25
    assert load_preferences()["kokoro_speed"] == pytest.approx(1.1)
    fresh = await remote.get_panel("voice")
    assert fresh["sections"][0]["fields"][1]["value"] == 25
    assert VoicePlugin(event_bus=state._event_bus).context_lines == 25
    assert not plugin.requested and plugin._pending == [{"event_id": "keep"}]
    plugin._client.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"context_lines": 0},
        {"context_lines": 51},
        {"context_lines": 1.5},
        {"context_lines": True},
        {"classifier": "vibevoice"},
        {"tts_voice": "other"},
        {"unknown": "x"},
        {"classifier": "provider", "context_lines": 0},
    ],
)
async def test_invalid_batch_never_changes_preferences_or_runtime(voice, changes):
    state, plugin, root = voice
    save_preferences({"context_lines": 10})
    before = (root / "preferences.json").read_bytes()
    result = await VoicePanel().act(
        state, "save", {"changes": changes, "target": "device"}
    )
    assert not result["ok"] and result["errors"]
    assert (root / "preferences.json").read_bytes() == before
    assert plugin.context_lines == 10 and plugin.classifier_name == "laya"


@pytest.mark.asyncio
async def test_scope_and_corrupt_preferences_are_preserved(voice):
    state, _, root = voice
    with pytest.raises(PanelError):
        await VoicePanel().act(state, "save", {"changes": {}, "target": "global"})
    path = root / "preferences.json"
    path.write_text("broken")
    result = await VoicePanel().act(
        state, "save", {"changes": {"context_lines": 20}, "target": "device"}
    )
    assert not result["ok"]
    assert path.read_text() == "broken"


def test_concurrent_updates_preserve_sibling_and_unknown_keys(voice):
    _, _, root = voice
    (root / "preferences.json").write_text(json.dumps({"private_setting": "keep"}))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                save_preferences,
                [{"classifier": "provider"}, {"context_lines": 23}] * 20,
            )
        )
    assert json.loads((root / "preferences.json").read_text()) == {
        "private_setting": "keep",
        "classifier": "provider",
        "context_lines": 23,
    }
