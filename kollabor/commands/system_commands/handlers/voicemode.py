"""Automatic device voice toggle and factual service status."""

from __future__ import annotations

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
)

from ..base import BaseCommandHandler


class VoiceModeCommandHandler(BaseCommandHandler):
    MODAL_ACTIONS: set[str] = set()

    def __init__(self, command_registry, event_bus, config=None):
        super().__init__(command_registry, event_bus)

    def register_commands(self):
        self.command_registry.register_command(
            CommandDefinition(
                name="voicemode",
                description="Turn device voice on or off; automatic setup",
                handler=self.handle_voicemode,
                plugin_name="system",
                category=CommandCategory.SYSTEM,
                mode=CommandMode.INSTANT,
                aliases=["vm", "voice"],
                subcommands=[
                    SubcommandInfo(action, "", description)
                    for action, description in [
                        ("on", "Start listening in this chat"),
                        ("off", "Stop voice in this chat"),
                        ("status", "Show service, microphone and transcript status"),
                        ("retry", "Retry setup or pending speech"),
                        ("context", "Set previous transcript lines to use; default 10"),
                        (
                            "classifier",
                            "Choose laya or provider; preserves pending speech",
                        ),
                    ]
                ],
            )
        )
        self.command_registry.register_command(
            CommandDefinition(
                name="voicemodels",
                description="Show automatic voice setup status",
                handler=self.handle_voicemodels,
                plugin_name="system",
                category=CommandCategory.SYSTEM,
                mode=CommandMode.INSTANT,
                aliases=["vmodels"],
            )
        )

    async def handle_voicemode(self, command: SlashCommand) -> CommandResult:
        raw = command.args or []
        action = (" ".join(raw) if isinstance(raw, list) else raw).strip().lower()
        plugin = self.event_bus.get_service("voice_plugin")
        if plugin is None:
            return CommandResult(
                success=False,
                message="Voice plugin is unavailable; restart Kollab after updating",
                display_type="error",
            )
        if action == "status":
            return await self._status(plugin)
        if action == "context":
            return CommandResult(
                success=True,
                message=f"Voice context: previous {plugin.context_lines} transcript lines. /voicemode context 1–50",
            )
        if action.startswith("context "):
            try:
                message = plugin.set_context_lines(int(action.removeprefix("context ")))
                return CommandResult(success=True, message=message)
            except (ValueError, OSError) as exc:
                return CommandResult(success=False, message=str(exc), display_type="error")
        if action == "classifier":
            from kollabor_voice.classifiers import CLASSIFIERS

            return CommandResult(
                success=True,
                message=(
                    f"Voice classifier: {CLASSIFIERS[plugin.classifier_name]}\n"
                    "/voicemode classifier laya — local, shared persistent worker (default)\n"
                    "/voicemode classifier provider — active AI provider"
                ),
            )
        if action.startswith("classifier "):
            name = action.removeprefix("classifier ").strip()
            try:
                message = await plugin.set_classifier(name)
                return CommandResult(success=True, message=message)
            except (ValueError, OSError, RuntimeError) as exc:
                return CommandResult(
                    success=False, message=str(exc), display_type="error"
                )
        if action not in {"", "on", "off", "retry"}:
            return CommandResult(
                success=False,
                message="Usage: /voicemode [on|off|status|retry|classifier [laya|provider]|context [1–50]]",
                display_type="error",
            )
        if action == "retry":
            message = await plugin.retry_voice()
        elif action == "off" or (not action and plugin.requested):
            message = await plugin.stop_voice()
        else:
            message = await plugin.start_voice()
        return CommandResult(success=True, message=message)

    async def _status(self, plugin):
        from kollabor_voice.client import VoiceClient

        state = plugin.status()
        try:
            live = await VoiceClient().call("status")
            state = {
                **state,
                **live,
                "observer_error": state.get("observer_error"),
                "pending": state.get("pending", 0),
                "classifier_name": state.get("classifier_name", "laya"),
            }
        except (OSError, TimeoutError, ValueError):
            pass
        last = state.get("last_transcript") or {}
        output = state.get("output") or {}
        classifier = state.get("classifier") or {}
        from kollabor_voice.classifiers import CLASSIFIERS

        name = state.get("classifier_name", "laya")
        lines = [
            f"Voice {state.get('state', 'off').replace('_', ' ').title()}",
            f"Service: {state.get('pid') or 'not running'} · owner: {state.get('owner') or 'none'}",
            "Models: Whisper base · Kokoro af_heart",
            f"Context: previous {state.get('context_lines', 10)} transcript lines",
            f"Classifier: {CLASSIFIERS.get(name, name)}"
            + (
                f" · {classifier.get('state', 'off')} · worker {classifier.get('pid') or 'not running'}"
                if name == "laya"
                else ""
            ),
            f"Microphone: {state.get('device') or 'not recording'}",
            f"Transcript: {state.get('transcript_path') or 'not open'}",
            f"Last heard: {last.get('ended_at', 'none')} {last.get('text', '')[:160]}",
            f"Queues: {state.get('input_depth', 0)} transcription · "
            f"{output.get('depth', 0)} speech · {state.get('pending', 0)} pending decisions",
        ]
        decision = state.get("last_decision") or {}
        if decision:
            confidence = decision.get("confidence")
            lines.append(
                f"Last decision: {decision.get('decision', 'unknown')}"
                + (f" · {confidence:.0%}" if confidence is not None else "")
                + (f" · {decision['detail']}" if decision.get("detail") else "")
            )
        speech = state.get("last_speech_decision") or {}
        if speech:
            lines.append(
                f"Speech check: {speech.get('decision')} · {speech.get('provider')} · {speech.get('reason', '')}"
            )
        for value in (
            state.get("detail"),
            state.get("error"),
            state.get("observer_error"),
            output.get("error"),
            classifier.get("detail") if name == "laya" else None,
        ):
            if value:
                lines.append(str(value))
        return CommandResult(success=True, message="\n".join(lines))

    async def handle_voicemodels(self, command):
        command.args = ["status"]
        return await self.handle_voicemode(command)
