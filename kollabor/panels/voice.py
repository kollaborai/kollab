"""Device voice preferences shared by the terminal and web settings."""

from __future__ import annotations

from typing import Any

from kollabor_voice.control import service_root
from kollabor_voice.speech_settings import (
    KOKORO_VOICES,
    MAX_CONTEXT_LINES,
    STT_ENGINES,
    TTS_ENGINES,
    available_stt_engines,
    speech_errors,
)
from kollabor_voice.speech_settings import (
    load_speech_preferences as load_preferences,
)

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


def voice_sections(plugin, live=None) -> list[dict]:
    """One field definition for both interfaces and both speech engines."""
    preferences = load_preferences()
    state = {**plugin.status(), **(live or {})}
    from kollabor_voice.control import manifest

    speech = state.get("speech", {})
    transcription = state.get("transcription", {})
    engines = available_stt_engines()
    return [
        {
            "id": "preferences",
            "title": "Voice Preferences",
            "fields": [
                make_field(
                    "context_lines",
                    "spinbox",
                    "Transcript Context",
                    value=preferences["context_lines"],
                    min_value=1,
                    max_value=MAX_CONTEXT_LINES,
                    step=1,
                    help="Previous microphone transcript lines sent to the agent with each new utterance (1–50).",
                ),
            ],
        },
        {
            "id": "engines",
            "title": "Audio and Device",
            "fields": [
                make_field(
                    "stt_engine",
                    "dropdown",
                    "Transcription Engine",
                    value=preferences["stt_engine"],
                    options=list(engines),
                    option_labels=engines,
                    help=(
                        "Local speech recognition. Parakeet v3 needs an Apple silicon "
                        "Mac and downloads 2.5 GB once; Whisper Base transcribes "
                        "until it is ready."
                        if "parakeet" in engines
                        else "Local speech recognition with Whisper Base."
                    ),
                ),
                make_field(
                    "tts_engine",
                    "dropdown",
                    "Speech Engine",
                    value=preferences["tts_engine"],
                    options=list(TTS_ENGINES),
                    option_labels=TTS_ENGINES,
                    help=("Local synthesis. Selecting VibeVoice prepares its isolated runtime "
                          "and warms the model in the background."),
                ),
                make_field(
                    "kokoro_voice",
                    "dropdown",
                    "Kokoro Voice",
                    value=preferences["kokoro_voice"],
                    options=list(KOKORO_VOICES),
                ),
                make_field(
                    "kokoro_speed",
                    "spinbox",
                    "Kokoro Speed",
                    value=preferences["kokoro_speed"],
                    min_value=0.5,
                    max_value=2.0,
                    step=0.1,
                ),
                make_field(
                    "vibevoice_voice",
                    "dropdown",
                    "VibeVoice Voice",
                    value=preferences["vibevoice_voice"],
                    options=list(manifest()["tts"]["vibevoice"]["voices"]),
                ),
                make_field(
                    "vibevoice_prompt",
                    "text_input",
                    "Custom VibeVoice Prompt",
                    value=preferences["vibevoice_prompt"],
                    help=("Optional absolute path to your trusted cached .pt voice prompt. "
                          "Leave blank to use the selected preset."),
                ),
                make_field(
                    "vibevoice_model",
                    "text_input",
                    "VibeVoice Model",
                    value=preferences["vibevoice_model"],
                    help=("Microsoft VibeVoice-Realtime-0.5B or an existing absolute model directory. "
                          "The default model download is pinned."),
                ),
                make_field(
                    "vibevoice_device",
                    "dropdown",
                    "VibeVoice Device",
                    value=preferences["vibevoice_device"],
                    options=["auto", "mps", "cuda", "cpu"],
                    help="Auto uses Apple MPS or CUDA when available, otherwise CPU.",
                ),
                make_field(
                    "vibevoice_steps",
                    "spinbox",
                    "VibeVoice Diffusion Steps",
                    value=preferences["vibevoice_steps"],
                    min_value=1,
                    max_value=20,
                    step=1,
                ),
                make_field(
                    "vibevoice_cfg",
                    "spinbox",
                    "VibeVoice Guidance",
                    value=preferences["vibevoice_cfg"],
                    min_value=1.0,
                    max_value=3.0,
                    step=0.1,
                ),
                make_field(
                    "transcription_status",
                    "label",
                    "Transcription Readiness",
                    value=(
                        f"{STT_ENGINES.get(transcription.get('engine'), 'not loaded')}"
                        f" · {transcription.get('state', 'off')}"
                    ),
                    help=transcription.get("detail", ""),
                ),
                make_field(
                    "speech_status",
                    "label",
                    "Speech Readiness",
                    value=(
                        f"{TTS_ENGINES.get(speech.get('engine'), speech.get('engine')) or 'not loaded'}"
                        f" · {speech.get('state', 'off')}"
                    ),
                    help=speech.get("detail", ""),
                ),
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
        {
            "id": "filters",
            "title": "Echo and Speaker",
            "fields": [
                make_field(
                    "echo_cancellation",
                    "checkbox",
                    "Echo Cancellation",
                    value=preferences["echo_cancellation"],
                    help=(
                        "Removes voice's own speech from the microphone before "
                        "transcription. A TV, other people and other apps are kept."
                    ),
                ),
                make_field(
                    "speaker_gate",
                    "checkbox",
                    "Speaker Gate",
                    value=preferences["speaker_gate"],
                    help=(
                        "Only your voice reaches the agent. Other voices stay in "
                        "the transcript as context. Needs a voiceprint: /voice enroll."
                    ),
                ),
                make_field(
                    "speaker_threshold",
                    "spinbox",
                    "Speaker Threshold",
                    value=preferences["speaker_threshold"],
                    min_value=0.0,
                    max_value=1.0,
                    step=0.01,
                    help=(
                        "How close to your voiceprint a line must sound (0–1). Higher "
                        "keeps out more of the TV and more of you."
                    ),
                ),
                make_field(
                    "voiceprint",
                    "label",
                    "Voiceprint",
                    value=voiceprint_text(state.get("speaker") or {}),
                    help=(
                        "Made by /voice enroll from 30 seconds of your speech; no audio "
                        "is kept. /voice enroll forget deletes it."
                    ),
                ),
            ],
        },
    ]


def voiceprint_text(speaker: dict) -> str:
    enrollment, saved = speaker.get("enrollment"), speaker.get("voiceprint")
    if enrollment:
        return f"Enrolling: {enrollment['seconds']:.0f} of {enrollment['target']:.0f} seconds"
    if saved:
        return f"Saved from {saved['seconds']:.0f} seconds of speech"
    if "voiceprint" not in speaker and (service_root() / "voiceprint.json").exists():
        return "Saved"  # the voice service is not running to say more
    return "None yet"


class VoicePanel:
    name = "voice"
    kind = "form"

    async def describe(self, ctx: Any, params: dict[str, Any]) -> dict:
        from kollabor_voice.client import VoiceClient

        live = None
        try:
            live = await VoiceClient().call("status")
        except (OSError, TimeoutError, ValueError, RuntimeError):
            pass
        return {
            "panel": self.name,
            "kind": self.kind,
            "title": TITLE,
            "scope_note": "Saved for every chat on the device running Kollab. Saving does not enable the microphone.",
            "sections": voice_sections(voice_plugin(ctx), live),
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
        # JSON numeric controls may send 20.0. The persisted contract uses integers.
        changes = dict(changes)
        for key in ("context_lines", "vibevoice_steps"):
            if isinstance(changes.get(key), float) and changes[key].is_integer():
                changes[key] = int(changes[key])
        if not errors:
            errors.update(
                speech_errors({**load_preferences(), **changes}, check_files=True)
            )
        if errors:
            return error_result("Some voice settings were not saved", errors)
        try:
            await voice_plugin(ctx).configure_voice(changes)
        except (OSError, ValueError) as exc:
            return error_result(f"Could not save voice settings: {exc}")
        return ok_result(
            "Voice settings saved for this device", panel=await self.describe(ctx, {})
        )


PANELS = {"voice": VoicePanel()}
