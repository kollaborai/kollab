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
                name="voice",
                description="Turn device voice on or off; automatic setup",
                handler=self.handle_voicemode,
                plugin_name="system",
                category=CommandCategory.SYSTEM,
                mode=CommandMode.INSTANT,
                subcommands=[
                    SubcommandInfo(action, "", description)
                    for action, description in [
                        ("on", "Start listening in this chat"),
                        ("off", "Stop voice in this chat"),
                        ("status", "Show service, microphone and transcript status"),
                        ("retry", "Retry now; voice also retries on its own"),
                        ("config", "Configure voice in the terminal or web UI"),
                        ("context", "Set previous transcript lines to use; default 10"),
                        (
                            "classifier",
                            "See each decision; add laya, qwen or provider to switch",
                        ),
                        (
                            "enroll",
                            "Save your voiceprint from 30 seconds of speech; forget deletes it",
                        ),
                    ]
                ],
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
        if action == "config":
            return await self._open_config(plugin)
        if action == "status":
            return await self._status(plugin)
        if action == "context":
            return CommandResult(
                success=True,
                message=f"Voice context: previous {plugin.context_lines} transcript lines. /voice context 1–50",
            )
        if action.startswith("context "):
            try:
                message = plugin.set_context_lines(int(action.removeprefix("context ")))
                return CommandResult(success=True, message=message)
            except (ValueError, OSError) as exc:
                return CommandResult(
                    success=False, message=str(exc), display_type="error"
                )
        if action == "classifier":
            return await self._open_classifier_log(plugin)
        if action.startswith("classifier "):
            name = action.removeprefix("classifier ").strip()
            try:
                message = await plugin.set_classifier(name)
                return CommandResult(success=True, message=message)
            except (ValueError, OSError, RuntimeError) as exc:
                return CommandResult(
                    success=False, message=str(exc), display_type="error"
                )
        if action in {"enroll", "enroll forget"}:
            try:
                message = await (
                    plugin.forget_voiceprint()
                    if action == "enroll forget"
                    else plugin.enroll_voice()
                )
                return CommandResult(success=True, message=message)
            except (ValueError, OSError, RuntimeError) as exc:
                return CommandResult(
                    success=False, message=str(exc), display_type="error"
                )
        if action not in {"", "on", "off", "retry"}:
            return CommandResult(
                success=False,
                message=(
                    "Usage: /voice [on|off|status|retry|config"
                    "|classifier [laya|qwen|provider]|context [1–50]|enroll [forget]]"
                ),
                display_type="error",
            )
        if action == "retry":
            message = await plugin.retry_voice()
        elif action == "off" or (not action and plugin.requested):
            message = await plugin.stop_voice()
        else:
            message = await plugin.start_voice()
        return CommandResult(success=True, message=message)

    async def _open_config(self, plugin):
        from kollabor_tui.altview.stack_manager import AltViewStackManager
        from plugins.altview.voice_config_altview import VoiceConfigAltView

        stack = self.event_bus.get_service("altview_stack_manager")
        if stack is None:
            stack = AltViewStackManager(
                self.event_bus, self.event_bus.get_service("renderer")
            )
            self.event_bus.register_service("altview_stack_manager", stack)
        await stack.push(VoiceConfigAltView(plugin), "voice-config", reuse=False)
        return CommandResult(success=True, message="")

    async def _open_classifier_log(self, plugin):
        """Fullscreen: each classifier decision, what it saw and what it said."""
        from kollabor_voice.classifiers import CLASSIFIERS, available_classifiers

        from kollabor_tui.altview.stack_manager import (
            AltViewStackManager,
            AltViewUnavailable,
        )
        from plugins.altview.voice_classifier_altview import VoiceClassifierAltView

        stack_mgr = self.event_bus.get_service("altview_stack_manager")
        if stack_mgr is None:
            stack_mgr = AltViewStackManager(
                self.event_bus, self.event_bus.get_service("renderer")
            )
            self.event_bus.register_service("altview_stack_manager", stack_mgr)
        view = VoiceClassifierAltView()
        view.set_plugin(plugin)
        try:
            await stack_mgr.push(view, "voice-classifier", reuse=False)
        except AltViewUnavailable:
            # No terminal (pipe mode, a detached daemon): name the classifier.
            return CommandResult(
                success=True,
                message=(
                    f"Voice classifier: {CLASSIFIERS[plugin.classifier_name]}\n"
                    "/voice classifier laya — local, shared persistent worker (default)\n"
                    + (
                        "/voice classifier qwen — local fine-tuned Qwen3 0.6B, "
                        'also answers "was that for me?"\n'
                        if "qwen" in available_classifiers()
                        else ""
                    )
                    + "/voice classifier provider — active AI provider"
                ),
            )
        return CommandResult(success=True, message="")

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
        except OSError, TimeoutError, ValueError, RuntimeError:
            pass  # not running, an older service, or a socket path too long
        last = state.get("last_transcript") or {}
        output = state.get("output") or {}
        from kollabor_voice.classifiers import (
            CLASSIFIERS,
            LOCAL_CLASSIFIERS,
            load_preferences,
        )
        from kollabor_voice.speech_settings import STT_ENGINES, TTS_ENGINES

        from kollabor.panels.voice import classifier_worker, voiceprint_text

        name = state.get("classifier_name", "laya")
        # The service reports the local classifier it has loaded, maybe the other one.
        classifier = classifier_worker(name, state)
        transcription = state.get("transcription") or {}
        speech_engine = state.get("speech", {}).get("engine")
        # No service running: the saved settings are what will apply.
        saved = load_preferences()
        echo = state.get("echo_cancellation") or {"enabled": saved["echo_cancellation"]}
        speaker = state.get("speaker") or {
            "enabled": saved["speaker_gate"],
            "threshold": saved["speaker_threshold"],
        }
        lines = [
            f"Voice {state.get('state', 'off').replace('_', ' ').title()}",
            f"Service: {state.get('pid') or 'not running'} · owner: {state.get('owner') or 'none'}",
            f"Transcription: {STT_ENGINES.get(transcription.get('engine'), 'not loaded')}"
            f" · {transcription.get('state', 'off')}",
            f"Speech: {TTS_ENGINES.get(speech_engine, speech_engine) or 'not loaded'} · "
            f"{state.get('speech', {}).get('voice') or 'no voice'} · "
            f"{state.get('speech', {}).get('state', 'off')}",
            f"Context: previous {state.get('context_lines', 10)} transcript lines",
            f"Classifier: {CLASSIFIERS.get(name, name)}"
            + (
                f" · {classifier.get('state', 'off')} · worker {classifier.get('pid') or 'not running'}"
                if name in LOCAL_CLASSIFIERS
                else ""
            ),
            "Echo cancellation: "
            + ("on" if echo.get("enabled") else "off")
            + (" · removing voice's speech" if echo.get("active") else ""),
            f"Speaker gate: {'on' if speaker.get('enabled') else 'off'}"
            f" · threshold {speaker['threshold']:.2f}"
            f" · voiceprint: {voiceprint_text(speaker).lower()}",
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
            state.get("speech", {}).get("detail"),
            transcription.get("detail"),
            echo.get("detail"),
            speaker.get("detail"),
            classifier.get("detail") if name in LOCAL_CLASSIFIERS else None,
        ):
            if value:
                lines.append(str(value))
        return CommandResult(success=True, message="\n".join(lines))
