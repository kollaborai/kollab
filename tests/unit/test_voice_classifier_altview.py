"""`/voicemode classifier`: each decision on the left, what it saw and said on the right."""

from types import SimpleNamespace

import pytest

from kollabor_tui.status.utils import strip_ansi
from plugins.altview.voice_classifier_altview import VoiceClassifierAltView, detail_lines


class _Renderer:
    def __init__(self, width=120, height=30):
        self.width, self.height, self.writes = width, height, []

    def get_terminal_size(self):
        return self.width, self.height

    def write_at(self, x, y, text, style=""):
        self.writes.append((x, y, strip_ansi(str(text))))

    def pane(self, left):
        return "\n".join(t for x, _, t in self.writes if (x == 0) == left)


UNSURE = {
    "seq": 2,
    "at": 1760061094.0,
    "seconds": 0.38,
    "classifier": "laya",
    "records": [{"event_id": "e76", "text": "Hello, can you hear me?"}],
    "transcript_context": [{"event_id": "e74", "text": "I'm online and ready to help."}],
    "context": "assistant: Yes, I can hear you.",
    "decision": {
        "decision": "defer",
        "event_ids": ["e76"],
        "deferred_event_ids": ["e76"],
        "confidence": 0.71,
        "model": "voice-intent-v1",
        "detail": "Laya is uncertain. Asking the agent to decide whether a response is needed.",
        "trace": [
            {
                "state": {
                    "text": "Hello, can you hear me?",
                    "recent_transcripts": ["I'm online and ready to help."],
                },
                "passes": [
                    {"label": "with context", "choice": "respond", "probability": 0.71},
                    {"label": "words alone", "choice": "respond", "probability": 0.99},
                ],
            }
        ],
    },
    "delivered": ["e76"],
}
SENT = {
    "seq": 1,
    "at": 1760061080.0,
    "seconds": 0.03,
    "classifier": "laya",
    "records": [{"event_id": "e72", "text": "First words"}],
    "decision": {"decision": "respond", "event_ids": ["e72"], "confidence": 0.87},
    "delivered": ["e72"],
}


def _view(log):
    view = VoiceClassifierAltView()
    view.set_plugin(SimpleNamespace(classifier_log=lambda: list(log), classifier_name="laya", requested=True))
    view._renderer = _Renderer()
    return view


@pytest.mark.asyncio
async def test_events_left_and_what_laya_saw_and_said_right():
    view = _view([SENT, UNSURE])

    assert await view.render_frame(0.0) is True

    left, right = view._renderer.pane(True), view._renderer.pane(False)
    assert "Voice Events 2" in left
    assert left.index("unsure  Hello, can") < left.index("sent    First words")
    for text in (
        "Heard",
        '"Hello, can you hear me?"',
        "Laya saw",
        "recent_transcripts",
        "I'm online and ready to help.",
        "with context   respond  0.71",
        "words alone    respond  0.99",
        "unsure (defer) · confidence 0.71 · voice-intent-v1 · 0.38s",
        'as unsure speech: "Hello, can you hear me?"',
        "Classifier: laya",
    ):
        assert text in right + left, text


@pytest.mark.asyncio
async def test_a_picked_event_stays_picked_while_new_ones_arrive():
    log = [SENT, UNSURE]
    view = _view(log)
    await view.render_frame(0.0)

    await view.handle_input(SimpleNamespace(name="ArrowDown", char=""))
    log.append({**SENT, "seq": 3, "records": [{"event_id": "e80", "text": "Newer"}]})
    view._renderer.writes.clear()
    await view.render_frame(0.0)

    assert "First words" in view._renderer.pane(False)
    assert await view.handle_input(SimpleNamespace(name="Escape", char="")) is True


def test_without_a_trace_the_request_and_errors_still_show():
    entry = {
        "classifier": "provider",
        "records": [{"event_id": "e1", "text": "hi", "playback_overlap": [{"text": "Yes, I can hear you."}]}],
        "transcript_context": [{"event_id": "e0", "text": "before"}],
        "context": "user: earlier",
        "error": "Voice observer timed out; /voicemode retry",
    }
    rows = [text for _, text in detail_lines(entry)]
    assert '  while the agent said: "Yes, I can hear you."' in rows
    assert "  previous transcript lines (1)" in rows
    assert "    user: earlier" in rows
    assert "  Voice observer timed out; /voicemode retry" in rows
    assert "  nothing" in rows


@pytest.mark.asyncio
async def test_voicemode_classifier_opens_the_screen_on_the_voice_plugin():
    from kollabor.commands.system_commands.handlers.voicemode import VoiceModeCommandHandler

    pushed = []

    async def push(view, name, reuse=True):
        pushed.append((view, name, reuse))
        return True

    plugin = SimpleNamespace(classifier_name="laya")
    services = {"voice_plugin": plugin, "altview_stack_manager": SimpleNamespace(push=push)}
    bus = SimpleNamespace(get_service=services.get, register_service=services.__setitem__)
    handler = VoiceModeCommandHandler(None, bus)

    result = await handler.handle_voicemode(SimpleNamespace(args=["classifier"]))

    assert result.success and result.message == ""
    [(view, name, reuse)] = pushed
    # By name: other tests re-import plugins.*, so the class object can differ.
    assert type(view).__name__ == "VoiceClassifierAltView" and view.plugin is plugin
    assert (name, reuse) == ("voice-classifier", False)


@pytest.mark.asyncio
async def test_the_plugin_logs_each_call_and_echo_for_the_screen():
    import asyncio
    import time
    from unittest.mock import AsyncMock

    from kollabor_voice.store import utc

    from plugins.voice_plugin import VoicePlugin
    from tests.unit.test_voice_plugin_lifecycle import FakeBus

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.classifier_name = "provider"
    plugin.requested = True
    plugin._state = {"classifier": {"state": "ready"}}
    plugin._lease = {"epoch": "e", "token": "t"}
    plugin._client = SimpleNamespace(call=AsyncMock())
    now = time.time()
    echo = {
        "event_id": "echo",
        "text": "Yes, I can hear you.",
        "started_at": utc(now - 4),
        "ended_at": utc(now - 3),
        "playback_overlap": [{"reply_id": "r", "text": "Yes, I can hear you."}],
    }
    request = {
        "event_id": "ask",
        "text": "Please check the README.",
        "started_at": utc(now - 1),
        "ended_at": utc(now),
    }
    plugin._pending = [echo, request]

    async def provider(records, transcript_context=None):
        return records

    async def deliver(data):
        plugin.requested = False
        return {"status": "queued"}

    plugin._observe_with_provider = provider
    plugin._deliver = deliver
    await asyncio.wait_for(asyncio.create_task(plugin._observe_loop()), 1)

    dropped, decided = plugin.classifier_log()
    assert dropped["echo"] and [r["event_id"] for r in dropped["records"]] == ["echo"]
    assert decided["classifier"] == "provider" and decided["seconds"] is not None
    assert decided["decision"]["decision"] == "respond"
    assert decided["delivered"] == ["ask"] and not decided.get("error")
    assert dropped["seq"] < decided["seq"]


@pytest.mark.asyncio
async def test_a_failed_classifier_call_is_logged_with_its_error():
    from plugins.voice_plugin import VoicePlugin
    from tests.unit.test_voice_plugin_lifecycle import FakeBus

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.classifier_name = "provider"

    async def provider(records, transcript_context=None):
        raise RuntimeError("provider unreachable")

    plugin._observe_with_provider = provider
    with pytest.raises(RuntimeError):
        await plugin.observe_records([{"event_id": "a", "text": "hi"}])

    [entry] = plugin.classifier_log()
    assert entry["error"] == "provider unreachable" and entry["decision"] is None
