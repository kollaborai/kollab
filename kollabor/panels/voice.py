"""Device voice preferences shared by the terminal and web settings."""

from __future__ import annotations

from typing import Any

from kollabor_voice.classifiers import CLASSIFIERS, MAX_CONTEXT_LINES, load_preferences
from kollabor_voice.control import service_root

from .base import PanelError, error_result, make_field, ok_result, unknown_action
from .config import validate_config_changes

TITLE = "Voice Configuration"


def voice_plugin(ctx):
    bus = getattr(ctx, "_event_bus", None)
    plugin = bus.get_service("voice_plugin") if bus else None
    if plugin is None:
        raise PanelError(
            "Voice plugin is unavailable; restart Kollab after updating", status=503
        )
    return plugin


def voice_sections(plugin) -> list[dict]:
    """One field definition for both interfaces; fixed engines are read-only."""
    preferences = load_preferences()
    state = plugin.status()
    return [
        {
            "id": "preferences",
            "title": "Voice Preferences",
            "fields": [
                make_field(
                    "classifier",
                    "dropdown",
                    "Classifier",
                    value=preferences["classifier"],
                    options=list(CLASSIFIERS),
                    help=(
                        "Laya runs locally. Provider uses the active AI profile, "
                        "sends transcript context to it and can incur charges."
                    ),
                ),
                make_field(
                    "context_lines",
                    "spinbox",
                    "Transcript Context",
                    value=preferences["context_lines"],
                    min_value=1,
                    max_value=MAX_CONTEXT_LINES,
                    step=1,
                    help="Previous microphone transcript lines used to interpret the next utterance (1–50).",
                ),
            ],
        },
        {
            "id": "engines",
            "title": "Audio and Device",
            "fields": [
                make_field(
                    "stt_engine",
                    "label",
                    "Transcription Engine",
                    value="Whisper base",
                    help="Local speech recognition; currently fixed.",
                ),
                make_field(
                    "tts_engine",
                    "label",
                    "Speech Engine",
                    value="Kokoro",
                    help="Local speech synthesis; currently fixed.",
                ),
                make_field("tts_voice", "label", "Speech Voice", value="af_heart"),
                make_field("tts_speed", "label", "Speech Speed", value="1.0"),
                make_field(
                    "microphone",
                    "label",
                    "Microphone",
                    value=state.get("device") or "System default (not recording)",
                    help=(
                        "Voice captures and plays audio on the device running Kollab. "
                        "This panel does not start the microphone."
                    ),
                ),
                make_field(
                    "voice_state",
                    "label",
                    "Voice State",
                    value=state.get("state", "off").replace("_", " ").title(),
                    help="Use /voice on, /voice off, /voice status or /voice retry in this chat.",
                ),
                make_field(
                    "preferences_path",
                    "label",
                    "Preferences File",
                    value=str(service_root() / "preferences.json"),
                    help="Saved for every chat on this device; independent of Project and Global configuration.",
                ),
            ],
        },
    ]


class VoicePanel:
    name = "voice"
    kind = "form"

    async def describe(self, ctx: Any, params: dict[str, Any]) -> dict:
        return {
            "panel": self.name,
            "kind": self.kind,
            "title": TITLE,
            "scope_note": "Saved for every chat on the device running Kollab. Saving does not enable the microphone.",
            "sections": voice_sections(voice_plugin(ctx)),
            "actions": [
                {"id": "save", "label": "Save for This Device", "targets": ["device"]}
            ],
            "save_targets": {"device": str(service_root() / "preferences.json")},
        }

    async def act(self, ctx: Any, action: str, payload: dict[str, Any]) -> dict:
        if action != "save":
            raise unknown_action(self.name, action)
        if payload.get("target") != "device":
            raise PanelError("Voice preferences must be saved to this device")
        changes = payload.get("changes")
        if not isinstance(changes, dict) or not all(
            isinstance(path, str) for path in changes
        ):
            raise PanelError("changes must be an object keyed by setting name")
        current = await self.describe(ctx, {})
        fields = {f["path"]: f for s in current["sections"] for f in s["fields"]}
        errors = validate_config_changes(fields, changes)
        if errors:
            return error_result("Some voice settings were not saved", errors)
        # JSON numeric controls may send 20.0. The persisted contract uses integers.
        if "context_lines" in changes:
            changes = {**changes, "context_lines": int(changes["context_lines"])}
        try:
            await voice_plugin(ctx).configure_voice(changes)
        except (OSError, ValueError) as exc:
            return error_result(f"Could not save voice settings: {exc}")
        except RuntimeError as exc:
            # Settings reached disk even if the active classifier cannot warm up.
            return ok_result(
                f"Saved for this device. Classifier setup needs attention: {exc}",
                panel=await self.describe(ctx, {}),
            )
        return ok_result(
            "Voice settings saved for this device", panel=await self.describe(ctx, {})
        )


PANELS = {"voice": VoicePanel()}
