"""Voice preferences persist across the TUI, web/RPC and active chat boundaries."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_voice import speech_settings
from kollabor_voice.speech_settings import (
    load_speech_preferences as load_preferences,
)
from kollabor_voice.speech_settings import (
    save_speech_preferences as save_preferences,
)
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
    # Speech preferences plus Quick Model, the one editable field saved to
    # config (a loadout name) instead of this device's preferences.json.
    assert {p for p, f in fields.items() if f["editable"]} == set(speech_defaults()) | {
        "quick_model"
    }
    other = VoicePlugin(event_bus=state._event_bus)
    plugin._pending = [{"event_id": "keep"}]
    plugin._client = SimpleNamespace(call=AsyncMock())
    result = await remote.panel_action(
        "voice",
        "save",
        {
            "changes": {"context_lines": 20.0},
            "target": "device",
        },
    )
    assert result["ok"]
    saved = load_preferences()
    assert saved["context_lines"] == 20
    assert json.loads((root / "preferences.json").read_text())["context_lines"] == 20
    assert other.status()["context_lines"] == 20
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
    fields = {f["path"]: f for s in fresh["sections"] for f in s["fields"]}
    assert fields["context_lines"]["value"] == 25
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
        {"classifier": "provider"},
        {"tts_voice": "other"},
        {"stt_engine": "nemo"},
        {"unknown": "x"},
        {"classifier": "provider", "context_lines": 10},
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
    assert plugin.context_lines == 10


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
                [{"kokoro_voice": "bf_emma"}, {"context_lines": 23}] * 20,
            )
        )
    assert json.loads((root / "preferences.json").read_text()) == {
        "private_setting": "keep",
        "kokoro_voice": "bf_emma",
        "context_lines": 23,
    }


@pytest.mark.asyncio
async def test_transcription_engine_is_selectable_with_readable_labels(
    voice, monkeypatch
):

    state, plugin, root = voice
    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: True)
    panel = await VoicePanel().describe(state, {})
    fields = {f["path"]: f for s in panel["sections"] for f in s["fields"]}
    engine = fields["stt_engine"]
    # Parakeet is the default on Apple silicon: voice in the web UI needs it.
    assert (engine["type"], engine["value"]) == ("dropdown", "parakeet")
    assert engine["option_labels"] == {
        "whisper": "Whisper Base",
        "parakeet": "Parakeet v3",
    }
    assert fields["transcription_status"]["value"] == "not loaded · off"
    view = VoiceConfigAltView(plugin)
    view._load_widgets()
    widgets = {w.config_path: w for ws in view._section_widgets for w in ws}
    shown = "".join(view._render_widget_modern(widgets["stt_engine"], 80, "last"))
    assert "Transcription Engine: Parakeet v3" in re.sub(r"\x1b\[[0-9;]*m", "", shown)

    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: False)
    # Elsewhere Parakeet is not offered, and saving it anyway is refused.
    panel = await VoicePanel().describe(state, {})
    fields = {f["path"]: f for s in panel["sections"] for f in s["fields"]}
    assert fields["stt_engine"]["options"] == ["whisper"]
    assert fields["stt_engine"]["value"] == "whisper"
    assert fields["stt_engine"]["help"] == "Local speech recognition with Whisper Base."
    refused = await VoicePanel().act(
        state, "save", {"changes": {"stt_engine": "parakeet"}, "target": "device"}
    )
    assert not refused["ok"] and "stt_engine" in refused["errors"]
    assert not (root / "preferences.json").exists()

    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: True)
    saved = await VoicePanel().act(
        state, "save", {"changes": {"stt_engine": "parakeet"}, "target": "device"}
    )
    assert saved["ok"]
    assert load_preferences()["stt_engine"] == "parakeet"
    fresh = {f["path"]: f for s in saved["panel"]["sections"] for f in s["fields"]}
    assert fresh["stt_engine"]["value"] == "parakeet"


@pytest.mark.asyncio
async def test_voice_command_has_no_classifier_surface(voice):
    state, plugin, _ = voice
    commands = []
    handler = VoiceModeCommandHandler(
        SimpleNamespace(register_command=commands.append), state._event_bus
    )
    handler.register_commands()
    assert "classifier" not in [s.name for s in commands[0].subcommands]
    refused = await handler.handle_voicemode(SimpleNamespace(args=["classifier"]))
    assert not refused.success and "classifier" not in refused.message
    panel = await VoicePanel().describe(state, {})
    paths = {f["path"] for s in panel["sections"] for f in s["fields"]}
    assert not {p for p in paths if "classifier" in p}
    status = await handler.handle_voicemode(SimpleNamespace(args=["status"]))
    assert status.success and "lassifier" not in status.message


@pytest.mark.asyncio
async def test_voice_status_says_which_install_the_service_runs(voice, monkeypatch):
    from kollabor_voice.client import VoiceClient

    state, _, _ = voice
    handler = VoiceModeCommandHandler(
        SimpleNamespace(register_command=lambda _: None), state._event_bus
    )
    install = {
        "path": "/Applications/Kollab/kollabor_voice",
        "code": "0123456789abcdef",
    }

    async def call(client, op):
        return {"pid": 4242, "owner": None, "install": install}

    monkeypatch.setattr(VoiceClient, "call", call)
    status = await handler.handle_voicemode(SimpleNamespace(args=["status"]))
    lines = status.message.splitlines()
    assert lines[1:3] == [
        "Service: 4242 · owner: none",
        "Service code: /Applications/Kollab/kollabor_voice · 0123456789abcdef",
    ]
