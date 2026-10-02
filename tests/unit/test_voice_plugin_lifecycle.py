"""Voice activation never follows global config and stays off after cancelled setup."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.commands.system_commands.handlers.voicemode import VoiceModeCommandHandler
from plugins.voice_plugin import VoicePlugin


class FakeBus:
    def __init__(self):
        self.services = {}

    def register_service(self, name, service):
        self.services[name] = service

    def get_service(self, name):
        return self.services.get(name)


def test_classifier_context_uses_last_exchange_without_tools_or_reasoning():
    bus = FakeBus()
    bus.register_service(
        "llm_service",
        SimpleNamespace(
            conversation_history=[
                SimpleNamespace(role="system", content="System instructions"),
                SimpleNamespace(role="user", content="Can you check voice mode?"),
                SimpleNamespace(role="tool", content="Long file contents " * 300),
                SimpleNamespace(
                    role="assistant",
                    content=[
                        {"type": "reasoning", "text": "Private reasoning"},
                        {"type": "text", "text": "The classifier is Laya. " * 30},
                    ],
                ),
                SimpleNamespace(role="tool", content="More file contents " * 300),
            ]
        ),
    )
    context = VoicePlugin(event_bus=bus, config={})._conversation_context()
    assert context == (
        "user: Can you check voice mode?\nassistant: "
        + ("The classifier is Laya. " * 30)[:200]
    )


@pytest.mark.parametrize("legacy_prefix", [None, True, False])
def test_classifier_context_keeps_speech_instead_of_voice_routing_instructions(
    legacy_prefix,
):
    spoken = "Can you check the microphone?"
    instructions = (
        "[Voice instructions: "
        + "routing instructions " * 50
        + 'Playback context: ["I will check it."]]\n'
    )
    voice = {"epoch": "test"}
    if legacy_prefix is None:
        voice["transcript_text"] = spoken
        content = "<agent_hud>Injected context</agent_hud>" + instructions + spoken
    else:
        content = instructions + spoken if legacy_prefix else spoken + instructions
        content = "<agent_hud>Internal routing context</agent_hud>\n" + content
    bus = FakeBus()
    bus.register_service(
        "llm_service",
        SimpleNamespace(
            conversation_history=[
                SimpleNamespace(
                    role="user", content=content, metadata={"voice": voice}
                ),
                SimpleNamespace(role="assistant", content="I will check it."),
            ]
        ),
    )
    assert VoicePlugin(event_bus=bus, config={})._conversation_context() == (
        "user: " + spoken + "\nassistant: I will check it."
    )


@pytest.mark.asyncio
async def test_initialize_never_starts_from_saved_enabled_flag():
    bus = FakeBus()
    plugin = VoicePlugin(event_bus=bus, config={"kollabor.voice.enabled": True})
    plugin._run = AsyncMock()
    await plugin.initialize()
    plugin._run.assert_not_called()
    assert not plugin.requested
    assert bus.get_service("voice_plugin") is plugin


@pytest.mark.asyncio
async def test_observer_retains_ignored_leadin_when_the_question_arrives_later():
    from kollabor_voice.store import utc

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.classifier_name = "provider"
    plugin.requested = True
    plugin._lease = {"epoch": "e", "token": "t"}
    plugin._client = SimpleNamespace(call=AsyncMock())
    now = time.time()
    first = {
        "event_id": "a",
        "owner_epoch": "e",
        "text": "So I'm using the speech to text right now.",
        "started_at": utc(now - 6),
        "ended_at": utc(now - 2),
    }
    second = {
        "event_id": "b",
        "owner_epoch": "e",
        "text": "And I'm just wondering if you can hear me.",
        "started_at": utc(now - 2),
        "ended_at": utc(now),
    }
    plugin._pending = [first]
    observed, delivered = [], []

    async def observe(records):
        observed.append(list(records))
        return [second] if second in records else []

    async def deliver(data):
        delivered.append(data)
        plugin.requested = False
        return {"status": "queued"}

    plugin.observe_records = observe
    plugin._deliver = deliver
    task = asyncio.create_task(plugin._observe_loop())
    async with asyncio.timeout(2):
        while not plugin._recent_ignored:
            await asyncio.sleep(0.01)
        plugin._pending.append(second)
        await task
    assert observed == [[first], [first, second]]
    assert len(delivered) == 1
    assert delivered[0]["message"] == first["text"] + " " + second["text"]
    assert delivered[0]["voice"]["event_ids"] == ["a", "b"]


@pytest.mark.asyncio
async def test_observer_waits_for_speech_and_discards_result_after_owner_change():
    from kollabor_voice.store import utc

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.classifier_name = "provider"
    plugin.requested = True
    plugin._lease = {"epoch": "old", "token": "t"}
    plugin._client = SimpleNamespace(call=AsyncMock())
    now = time.time()
    record = {
        "event_id": "a",
        "text": "Please check this",
        "started_at": utc(now - 2),
        "ended_at": utc(now),
    }
    plugin._pending = [record]
    plugin._state = {"speech_active": True}
    started = asyncio.Event()
    finish = asyncio.Event()

    async def observe(records):
        started.set()
        await finish.wait()
        return records

    plugin.observe_records = observe
    plugin._deliver = AsyncMock()
    task = asyncio.create_task(plugin._observe_loop())
    await asyncio.sleep(0.05)
    assert not started.is_set()
    plugin._state["speech_active"] = False
    await asyncio.wait_for(started.wait(), 1)
    plugin._lease = {"epoch": "new", "token": "new-token"}
    finish.set()
    await asyncio.wait_for(task, 1)
    plugin._deliver.assert_not_awaited()
    plugin._client.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_speech_output_error_is_visible_without_pausing_transcript_observation():
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.voice_out = AsyncMock(side_effect=OSError("speaker unavailable"))
    plugin.validate_turn = AsyncMock()
    await plugin._spoken_reply(
        {
            "voice": {"epoch": "e", "reply_id": "r"},
            "spoken_text": "I'll check the files.",
        }
    )
    assert "speaker unavailable" in plugin.status()["output"]["error"]
    assert plugin._observer_error is None
    plugin.voice_out.reset_mock()
    await plugin._spoken_reply(
        {
            "voice": {"epoch": "e", "reply_id": "r"},
            "display_text": "```python\nprint('not spoken')",
            "spoken_text": "",
        }
    )
    plugin.voice_out.assert_not_awaited()
    await plugin._spoken_reply(
        {"voice": {"epoch": "e", "reply_id": "r"}, "spoken_text": " \n.\n "}
    )
    plugin.voice_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_returns_immediately_and_off_cancels_setup():
    bus = FakeBus()
    plugin = VoicePlugin(event_bus=bus, config={})
    waiting = asyncio.Event()
    plugin._run = waiting.wait
    await plugin.initialize()
    handler = VoiceModeCommandHandler(None, bus)
    start = time.monotonic()
    result = await handler.handle_voicemode(SimpleNamespace(args=[]))
    assert result.success and time.monotonic() - start < 0.25
    assert plugin.requested and plugin.status()["state"] == "starting"
    result = await handler.handle_voicemode(SimpleNamespace(args="off"))
    waiting.set()
    await asyncio.sleep(0)
    assert result.message == "Voice Off"
    assert not plugin.requested and plugin._controller.cancelled()


@pytest.mark.parametrize("width", [40, 80, 120])
def test_reserved_voice_status_with_empty_old_layout(width, monkeypatch):
    import kollabor_tui.status.layout_renderer as module
    from kollabor_tui.status.core_widgets import WidgetContext
    from kollabor_tui.status.layout_renderer import StatusLayoutRenderer
    from kollabor_tui.status.utils import strip_ansi

    monkeypatch.setattr(module, "get_global_width", lambda: width)
    bus = FakeBus()
    bus.register_service(
        "voice_plugin",
        SimpleNamespace(
            status=lambda: {
                "visible": True,
                "state": "listening",
                "level": 0.03,
                "transcribing": True,
                "device": "MacBook microphone",
                "last_transcript": {
                    "text": "Kollab check the tests",
                    "ended_at": "2026-09-26T14:00:00+00:00",
                },
            }
        ),
    )
    renderer = object.__new__(StatusLayoutRenderer)
    renderer._layout_manager = SimpleNamespace(
        get_layout=lambda: SimpleNamespace(get_visible_rows=lambda: [])
    )
    renderer._navigation_manager = None
    renderer._context = WidgetContext(event_bus=bus)
    renderer.simple_mode = True
    lines = renderer.render()
    assert len(lines) == 2 and "Voice Listening" in lines[0] and "Heard" in lines[1]
    assert all(len(strip_ansi(line)) <= width for line in lines)


@pytest.mark.asyncio
async def test_remote_observation_never_blocks_serial_rpc_heartbeat():
    remote = VoicePlugin(event_bus=FakeBus(), config={})
    await remote._remote_bind({"epoch": "epoch", "client_id": "client"})
    finish = asyncio.Event()
    records = [{"event_id": "one", "text": "check the tests"}]

    async def observe(_, transcript_context):
        assert transcript_context == []
        await finish.wait()
        return records

    remote._observe_with_provider = observe
    handlers = {
        "voice.observe": remote._remote_observe,
        "voice.result": remote._remote_result,
        "voice.heartbeat": remote._remote_heartbeat,
    }
    serial = asyncio.Lock()

    class RPC:
        async def call(self, method, params, **kwargs):
            async with serial:
                return await handlers[method](params)

    local = VoicePlugin(event_bus=FakeBus(), config={})
    local._lease = {"epoch": "epoch"}
    rpc = RPC()
    observing = asyncio.create_task(
        local._remote_job(rpc, "voice.observe", {"epoch": "epoch", "records": records})
    )
    await asyncio.sleep(0.02)
    for _ in range(3):
        result = await asyncio.wait_for(
            rpc.call("voice.heartbeat", {"epoch": "epoch"}), 0.1
        )
        assert result["alive"]
    finish.set()
    assert (await observing)["event_ids"] == ["one"]
    await remote.shutdown()


@pytest.mark.asyncio
async def test_expired_input_is_rejected_before_durable_admission(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin._remote_binding = {"epoch": "epoch", "heartbeat": time.monotonic()}
    data = {
        "message": "Check the tests",
        "voice": {
            "admission_id": "expired",
            "epoch": "epoch",
            "expires_at": time.time() - 1,
        },
    }
    submit = AsyncMock(return_value={"status": "queued"})
    with pytest.raises(RuntimeError, match="expired before admission"):
        await plugin.admit(data, submit)
    submit.assert_not_awaited()
    assert plugin._admissions is None


@pytest.mark.asyncio
async def test_host_admission_is_durable_and_duplicate_submission_is_not_replayed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.validate_turn = AsyncMock()
    data = {
        "message": "Check the tests",
        "voice": {"admission_id": "identity", "epoch": "epoch"},
    }
    submit = AsyncMock(return_value={"status": "queued"})
    assert (await plugin.admit(data, submit))["status"] == "queued"
    assert (await plugin.admit(data, submit))["duplicate"]
    submit.assert_awaited_once()
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_real_event_bus_acknowledges_durable_admission_without_final_data_status(
    tmp_path, monkeypatch
):
    from kollabor_events import EventBus, EventType, Hook

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    bus = EventBus()
    plugin = VoicePlugin(event_bus=bus, config={})
    plugin.validate_turn = AsyncMock()
    data = {
        "message": "Check tests",
        "source": "voice",
        "voice": {"admission_id": "real-hook", "epoch": "e"},
    }
    queued = AsyncMock(return_value={"status": "queued"})

    async def handler(data, event):
        return await plugin.admit(data, queued)

    await bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            callback=handler,
            priority=100,
        )
    )
    assert (await plugin._deliver(data))["status"] == "queued"
    assert (await plugin._deliver(data))["status"] == "queued"
    queued.assert_awaited_once()
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_classifier_choices_persist_without_enabling_or_losing_pending(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    assert plugin.classifier_name == "laya"
    plugin._pending = [{"event_id": "keep"}]
    plugin._client = SimpleNamespace(call=AsyncMock())
    plugin._run = AsyncMock()
    for name in ("provider", "laya"):
        await plugin.set_classifier(name)
        assert plugin.classifier_name == name
        assert VoicePlugin(event_bus=FakeBus(), config={}).classifier_name == name
        assert plugin._pending == [{"event_id": "keep"}]
        assert not plugin.requested
    plugin._client.call.assert_not_awaited()
    plugin._run.assert_not_called()
    with pytest.raises(ValueError):
        await plugin.set_classifier("typo")
    assert plugin.classifier_name == "laya"


@pytest.mark.asyncio
async def test_switching_classifier_fences_old_decision_and_reuses_pending(
    tmp_path, monkeypatch
):
    from kollabor_voice.store import utc

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.classifier_name = "provider"
    plugin.requested = True
    plugin._lease = {"epoch": "e", "token": "t"}
    plugin._client = SimpleNamespace(call=AsyncMock())
    plugin._state = {"classifier": {"state": "ready"}}
    now = time.time()
    records = [
        {
            "event_id": "a",
            "text": "Check the logs.",
            "started_at": utc(now - 1),
            "ended_at": utc(now),
        }
    ]
    plugin._pending = records.copy()
    started, finish = asyncio.Event(), asyncio.Event()
    observed = []

    async def observe(batch):
        observed.append((plugin.classifier_name, list(batch)))
        if len(observed) == 1:
            started.set()
            await finish.wait()
            return batch
        plugin.requested = False
        return []

    plugin.observe_records = observe
    plugin._deliver = AsyncMock()
    task = asyncio.create_task(plugin._observe_loop())
    await asyncio.wait_for(started.wait(), 1)
    await plugin.set_classifier("laya")
    finish.set()
    await asyncio.wait_for(task, 1)
    assert observed == [("provider", records), ("laya", records)]
    plugin._deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_laya_attached_decision_runs_on_device_and_fetches_only_context(
    tmp_path, monkeypatch
):
    from kollabor_voice.classifiers import VoiceDecision

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    bus = FakeBus()
    rpc = SimpleNamespace(
        call=AsyncMock(return_value={"context": "assistant: Should I run the tests?"})
    )
    bus.register_service("rpc_client", rpc)
    plugin = VoicePlugin(event_bus=bus, config={})
    plugin._lease = {"epoch": "e", "token": "t"}
    record = {"event_id": "a", "text": "Yes please."}
    client = SimpleNamespace(
        call=AsyncMock(
            side_effect=[
                {"job_id": "j"},
                {
                    "done": True,
                    "result": VoiceDecision("respond", ["a"], "laya").to_wire(),
                },
            ]
        )
    )
    plugin._client = client
    assert await plugin.observe_records([record]) == [record]
    assert [call.args[0] for call in client.call.call_args_list] == [
        "classifier_submit",
        "classifier_result",
    ]
    assert rpc.call.call_args.args[0] == "voice.context"
    assert client.call.call_args_list[0].kwargs["context"].startswith("assistant:")


@pytest.mark.asyncio
async def test_classifier_command_lists_and_validates_choices(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    bus = FakeBus()
    plugin = VoicePlugin(event_bus=bus, config={})
    bus.register_service("voice_plugin", plugin)
    handler = VoiceModeCommandHandler(None, bus)
    result = await handler.handle_voicemode(SimpleNamespace(args=["classifier"]))
    assert (
        "laya" in result.message
        and "provider" in result.message
        and "default" in result.message
    )
    result = await handler.handle_voicemode(
        SimpleNamespace(args=["classifier", "provider"])
    )
    assert result.success and plugin.classifier_name == "provider"
    result = await handler.handle_voicemode(SimpleNamespace(args="classifier typo"))
    assert not result.success and plugin.classifier_name == "provider"


@pytest.mark.asyncio
async def test_uncertain_background_does_not_block_the_next_user_request():
    from kollabor_voice.store import utc

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.classifier_name = "laya"
    plugin.requested = True
    plugin._state = {"classifier": {"state": "ready"}}
    plugin._lease = {"epoch": "e", "token": "t"}
    plugin._client = SimpleNamespace(call=AsyncMock())
    now = time.time()
    background = {
        "event_id": "background",
        "text": "Unclear background speech.",
        "started_at": utc(now - 8),
        "ended_at": utc(now - 5),
    }
    request = {
        "event_id": "request",
        "text": "Please check the README.",
        "started_at": utc(now - 1),
        "ended_at": utc(now),
    }
    plugin._pending = [background]
    delivered = []

    async def observe(records):
        if records == [background]:
            plugin._decision_deferred_ids = [background["event_id"]]
        return records

    async def deliver(data):
        delivered.append(data)
        if len(delivered) == 1:
            plugin._pending.append(request)
        else:
            plugin.requested = False
        return {"status": "queued"}

    plugin.observe_records = observe
    plugin._deliver = deliver
    task = asyncio.create_task(plugin._observe_loop())
    await asyncio.wait_for(task, 1)
    assert plugin._observer_error is None
    assert delivered[0]["message"] == background["text"]
    assert delivered[0]["voice"]["uncertain_event_ids"] == ["background"]
    assert delivered[1]["message"] == request["text"]
    assert delivered[1]["voice"]["uncertain_event_ids"] == []
    assert plugin.status()["pending"] == 0


def test_microphone_core_widget_registration_and_absent_service():
    from kollabor_tui.status.core_widgets import (
        WidgetContext,
        register_core_widgets,
        render_microphone,
    )
    from kollabor_tui.status.widget_registry import StatusWidgetRegistry

    registry = StatusWidgetRegistry()
    register_core_widgets(registry)
    assert registry.get("microphone") is not None
    assert "mic off" in render_microphone(20, WidgetContext())


def test_microphone_widget_renders_live_listening_and_last_heard():
    from kollabor_tui.status.core_widgets import WidgetContext, render_microphone

    bus = FakeBus()
    bus.register_service("voice_plugin", SimpleNamespace(status=lambda: {
        "state": "listening",
        "last_transcript": {"text": "check the microphone"},
    }))
    rendered = render_microphone(40, WidgetContext(event_bus=bus))
    assert "mic listening" in rendered
    assert "check the microphone" in rendered


def test_microphone_widget_renders_off_state():
    from kollabor_tui.status.core_widgets import WidgetContext, render_microphone

    bus = FakeBus()
    bus.register_service(
        "voice_plugin", SimpleNamespace(status=lambda: {"state": "off"})
    )
    assert "mic off" in render_microphone(20, WidgetContext(event_bus=bus))
