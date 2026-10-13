"""Voice settings use the same widgets and save action as the web panel."""

from types import SimpleNamespace

from kollabor.panels.voice import TITLE, VoicePanel, voice_sections
from plugins.altview.config_altview import ConfigAltView


class VoiceConfigAltView(ConfigAltView):
    def __init__(self, plugin):
        super().__init__()
        self.title = TITLE
        self.plugin = plugin
        self.metadata.plugin_type = "voice-config"
        self.metadata.description = "Configure device voice preferences"
        self._panel = None

    async def on_enter(self, renderer):
        self._panel = await VoicePanel().describe(
            SimpleNamespace(_event_bus=self.plugin._event_bus), {}
        )
        await super().on_enter(renderer)

    def _build_sections(self):
        sections = (
            self._panel["sections"] if self._panel else voice_sections(self.plugin)
        )
        values = {f["path"]: f["value"] for s in sections for f in s["fields"]}
        self.config_service = SimpleNamespace(get=values.get)
        return [
            {
                "title": section["title"],
                "widgets": [
                    {
                        **field,
                        "config_path": field["path"],
                        "current_value": field["value"],
                        "decimal_places": (
                            1 if isinstance(field.get("step"), float) else 0
                        ),
                    }
                    for field in section["fields"]
                ],
            }
            for section in sections
        ]

    async def handle_input(self, key_press):
        if key_press.name == "Ctrl+S":
            changes = {
                w.config_path: w.get_pending_value()
                for widgets in self._section_widgets
                for w in widgets
                if w.has_pending_changes()
            }
            if not changes:
                self.status_message = "No unsaved voice changes"
                return False
            ctx = SimpleNamespace(_event_bus=self.plugin._event_bus)
            result = await VoicePanel().act(
                ctx, "save", {"changes": changes, "target": "device"}
            )
            self.status_message = result["message"]
            if result["errors"]:
                self.status_message += ": " + "; ".join(result["errors"].values())
            if result["ok"]:
                self._panel = result["panel"]
                self._load_widgets()
            return False
        self.status_message = ""
        return await super().handle_input(key_press)
