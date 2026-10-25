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
async def test_a_late_heartbeat_renews_routing_after_a_daemon_stall():
    daemon = VoicePlugin(event_bus=FakeBus(), config={})
    await daemon._remote_bind({"epoch": "e", "client_id": "c"})
    daemon._remote_binding["heartbeat"] -= 10  # this daemon's loop was busy
    assert await daemon._remote_heartbeat({"epoch": "e"}) == {"alive": True}
    daemon._check_remote({"epoch": "e"})  # routing works again
    with pytest.raises(RuntimeError, match="expired"):
        await daemon._remote_heartbeat({"epoch": "another window"})


@pytest.mark.asyncio
async def test_routing_rides_out_timeouts_and_binds_again_when_refused(monkeypatch):
    from kollabor_rpc import RpcTimeoutError

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.requested = True
    plugin._lease = {"epoch": "e"}
    real_sleep = asyncio.sleep

    async def no_wait(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    calls = []

    async def call(method, params, timeout=None):
        calls.append(method)
        if len(calls) == 1:
            raise RpcTimeoutError("busy daemon")
        if len(calls) == 2:
            assert plugin._observer_error is None, "a timeout never stops routing"
            raise RuntimeError("Remote voice lease expired")
        if method == "voice.bind":
            assert plugin._observer_error.startswith("AI connection lost; reconnecting")
            return {"bound": True, "names": ["Kollab", "aquamarine"]}
        plugin.requested = False
        return {"alive": True}

    await asyncio.wait_for(plugin._keep_remote_alive(SimpleNamespace(call=call)), 2)
    assert calls == [
        "voice.heartbeat",
        "voice.heartbeat",
        "voice.bind",
        "voice.heartbeat",
    ]
    assert plugin._routing_error is None and plugin._observer_error is None
    assert plugin._retry.is_set()
    assert plugin._agent_names == ["Kollab", "aquamarine"]


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
    bus.register_service(
        "voice_plugin",
        SimpleNamespace(
            status=lambda: {
                "state": "listening",
                "last_transcript": {"text": "check the microphone"},
            }
        ),
    )
    rendered = render_microphone(40, WidgetContext(event_bus=bus))
    assert "mic listening" in rendered
    assert "check the microphone" in rendered


def test_microphone_widget_shows_the_level_stage_and_heard_line():
    from kollabor_tui.status.core_widgets import WidgetContext, render_microphone
    from kollabor_tui.status.utils import strip_ansi

    activity = {"stage": "hearing", "level": 0.5}
    bus = FakeBus()
    bus.register_service(
        "voice_plugin",
        SimpleNamespace(
            status=lambda: {
                "state": "listening",
                "activity": activity,
                "last_transcript": {"text": "check the tests"},
            }
        ),
    )
    ctx = WidgetContext(event_bus=bus)
    assert "mic ▃▅▁ hearing: check the tests" in strip_ansi(render_microphone(80, ctx))
    activity.update(stage="speaking", level=0.0)
    rendered = strip_ansi(render_microphone(80, ctx))
    assert "mic ▁▁▁ speaking: check the tests" in rendered


def test_microphone_widget_renders_off_state():
    from kollabor_tui.status.core_widgets import WidgetContext, render_microphone

    bus = FakeBus()
    bus.register_service(
        "voice_plugin", SimpleNamespace(status=lambda: {"state": "off"})
    )
    assert "mic off" in render_microphone(20, WidgetContext(event_bus=bus))


@pytest.mark.asyncio
async def test_own_voice_echo_never_reaches_the_agent():
    from kollabor_voice.store import utc

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.requested = True
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
        "event_id": "request",
        "text": "Please check the README.",
        "started_at": utc(now - 1),
        "ended_at": utc(now),
    }
    plugin._pending = [echo, request]
    delivered = []

    async def deliver(data):
        delivered.append(data)
        plugin.requested = False
        return {"status": "queued"}

    plugin._deliver = deliver
    await asyncio.wait_for(asyncio.create_task(plugin._deliver_loop()), 1)
    assert [d["message"] for d in delivered] == ["Please check the README."]
    assert plugin.status()["pending"] == 0


@pytest.mark.asyncio
async def test_first_bind_timeout_retries_and_never_kills_the_voice_client(
    tmp_path_factory,
):
    """Cold-start regression: a daemon still busy with startup must not kill voice.

    Before the fix, the very first voice.bind (timeout=2s) firing during daemon
    startup congestion flipped requested=False and tore the client down. Now the
    bind retries, the mic stays up, and heartbeat routing starts only once bound.
    """
    from kollabor_rpc import RpcTimeoutError

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.requested = True
    plugin._lease = {"epoch": "cold", "token": "t", "cursor": 0, "stream_id": "s"}
    plugin._state = {"state": "ready"}

    plugin.classifier_name = "provider"  # skip laya classifier_prepare in setup

    async def local_status(op, progress=None, **params):
        if op == "claim":
            return plugin._lease
        if op == "heartbeat":
            return {"state": "ready"}
        if op == "read":
            return {"cursor": 0, "records": []}
        return {"ready": True, "state": "ready", "cursor": 0}

    async def ensure(progress):
        return {"ready": True, "state": "ready", "cursor": 0}

    tmp_consumers = tmp_path_factory.mktemp("voice-consumers")
    fake_client = SimpleNamespace(
        root=tmp_consumers,
        call=AsyncMock(side_effect=local_status),
        ensure=ensure,
        shutdown=AsyncMock(),
    )
    plugin._client = fake_client

    # _run() constructs its own VoiceClient; hand back the stub instead.
    import kollabor_voice.client as client_module

    try:
        real_cls = client_module.VoiceClient
        client_module.VoiceClient = lambda *a, **k: fake_client

        timeouts = {"n": 0}

        async def flaky_bind(method, params=None, timeout=None, **kwargs):
            if method == "voice.bind":
                timeouts["n"] += 1
                if timeouts["n"] == 1:
                    raise RpcTimeoutError("rpc call 'voice.bind' timed out after 5s")
                assert timeout == 2, "retry must use the steady-state budget"
                return {"bound": True}
            if method == "heartbeat":
                return {"state": "ready"}
            return {}

        rpc = SimpleNamespace(call=AsyncMock(side_effect=flaky_bind))
        plugin._event_bus.register_service("rpc_client", rpc)

        stopped = []

        async def run_client():
            try:
                await plugin._run()
            except Exception as exc:  # pragma: no cover - failure detail on assert
                stopped.append(exc)

        task = asyncio.create_task(run_client())

        async def wait_for(predicate, budget=3.0):
            deadline = time.monotonic() + budget
            while not predicate():
                if time.monotonic() > deadline:
                    return False
                await asyncio.sleep(0.01)
            return True

        assert await wait_for(
            lambda: timeouts["n"] == 2
        ), f"bind must retry exactly once (saw {timeouts['n']})"

        assert not stopped, f"voice client died: {stopped}"
        assert plugin.requested is True, "a bind timeout must never flip requested off"
        assert plugin._bound_epoch == "cold"
        assert plugin._routing_error is None
        assert plugin._remote_heartbeat_task is not None
        assert plugin._remote_heartbeat_task.get_name() == "voice-routing-heartbeat"

        plugin.requested = False
        await asyncio.gather(
            asyncio.wait_for(task, 1),
            return_exceptions=True,
        )
    finally:
        client_module.VoiceClient = real_cls


@pytest.mark.asyncio
async def test_bind_outage_after_retries_keeps_voice_local_only(tmp_path_factory):
    """Every bind attempt timing out must degrade to local-only, not shutdown."""
    from kollabor_rpc import RpcTimeoutError

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.classifier_name = "provider"
    plugin.requested = True
    plugin._lease = {"epoch": "e", "token": "t", "cursor": 0, "stream_id": "s"}
    plugin._state = {"state": "ready"}

    calls = {"bind": 0}

    async def always_timeout(method, params=None, timeout=None, **kwargs):
        if method == "voice.bind":
            calls["bind"] += 1
            raise RpcTimeoutError("rpc call 'voice.bind' timed out after 5s")
        return {}

    plugin._event_bus.register_service(
        "rpc_client", SimpleNamespace(call=AsyncMock(side_effect=always_timeout))
    )

    async def local_status(op, progress=None, **params):
        if op == "claim":
            return plugin._lease
        if op == "heartbeat":
            return {"state": "ready"}
        if op == "read":
            return {"cursor": 0, "records": []}
        return {"ready": True, "state": "ready", "cursor": 0}

    async def ensure(progress):
        return {"ready": True, "state": "ready", "cursor": 0}

    fake_client = SimpleNamespace(
        root=tmp_path_factory.mktemp("voice-consumers"),
        call=AsyncMock(side_effect=local_status),
        ensure=ensure,
        shutdown=AsyncMock(),
    )
    plugin._client = fake_client

    import kollabor_voice.client as client_module

    real_cls = client_module.VoiceClient
    client_module.VoiceClient = lambda *a, **k: fake_client
    try:
        task = asyncio.create_task(plugin._run())

        async def wait_until(predicate, budget=5.0):
            deadline = time.monotonic() + budget
            while not predicate():
                if time.monotonic() > deadline:
                    return False
                await asyncio.sleep(0.01)
            return True

        assert await wait_until(
            lambda: calls["bind"] >= 3
        ), f"bind attempts seen: {calls['bind']}"
        # The ladder ends with a final backoff sleep before surfacing the error.
        assert await wait_until(
            lambda: plugin._routing_error is not None
        ), "terminal routing error must surface after the last retry"
    finally:
        client_module.VoiceClient = real_cls

    assert calls["bind"] == 3, "bind retries 3 times before giving up routing"
    assert plugin.requested is True, "client survives total bind outage"
    assert plugin._bound_epoch is None
    assert plugin._routing_error, "routing error is surfaced for the status line"
    assert plugin._remote_heartbeat_task is None, "no heartbeat without a bind"

    plugin.requested = False
    await asyncio.gather(asyncio.wait_for(task, 1), return_exceptions=True)


class DyingClient:
    """A voice service that fails as soon as voice starts."""

    attempts = 0
    error = RuntimeError("voice service died")

    async def ensure(self, update):
        type(self).attempts += 1
        raise type(self).error

    async def call(self, *args, **kwargs):
        return {}


@pytest.mark.asyncio
async def test_a_stopped_voice_client_starts_again_on_its_own(monkeypatch, tmp_path):
    import kollabor_voice.client

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.setattr(kollabor_voice.client, "VoiceClient", DyingClient)
    DyingClient.attempts, DyingClient.error = 0, RuntimeError("voice service died")
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin._restart_delay = 0.01
    await plugin.start_voice()
    async with asyncio.timeout(2):
        while DyingClient.attempts < 3:
            await asyncio.sleep(0.01)
    assert plugin.requested
    assert "restarting in" in plugin._state["detail"]
    assert plugin._restart_delay > 0.02  # waits longer each time
    await plugin.stop_voice()
    assert not plugin.requested


@pytest.mark.asyncio
async def test_voice_stays_off_when_another_chat_takes_the_microphone(
    monkeypatch, tmp_path
):
    import kollabor_voice.client
    from kollabor_voice.control import VoiceError

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.setattr(kollabor_voice.client, "VoiceClient", DyingClient)
    DyingClient.attempts = 0
    DyingClient.error = VoiceError("superseded", "Voice moved to another chat")
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin._restart_delay = 0.01
    await plugin.start_voice()
    await asyncio.wait_for(plugin._controller, 2)
    await asyncio.sleep(0.05)
    assert DyingClient.attempts == 1
    assert not plugin.requested and plugin._state["state"] == "active_elsewhere"


@pytest.mark.asyncio
async def test_a_failed_speech_engine_is_prepared_again_on_start(monkeypatch, tmp_path):
    import kollabor_voice.client

    calls = []

    class BrokenSpeech:
        """The speech engine failed while the rest of the service is fine."""

        async def ensure(self, update):
            return {"state": "off", "speech": {"state": "error", "detail": "stopped"}}

        async def call(self, op, **kwargs):
            calls.append(op)
            return {}

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    monkeypatch.setattr(kollabor_voice.client, "VoiceClient", BrokenSpeech)
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    await plugin.start_voice()
    async with asyncio.timeout(2):
        while not calls:
            await asyncio.sleep(0.01)
    # Prepared again first, not restarted forever on the same error.
    assert calls[0] == "retry"
    await plugin.stop_voice()


@pytest.mark.asyncio
async def test_a_queued_voice_turn_watches_for_still_working_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.validate_turn = AsyncMock()
    plugin._watch_progress = lambda voice: setattr(plugin, "watched", voice)
    voice = {"admission_id": "id", "epoch": "epoch"}
    data = {"message": "Can you hear me?", "voice": voice}
    await plugin.admit(data, AsyncMock(return_value={"status": "queued"}))
    assert plugin.watched is voice
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_a_busy_voice_service_is_waited_for_not_a_stop():
    from kollabor_voice.control import VoiceError

    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.requested = True
    late = TimeoutError("voice service did not answer 'status' within 2s")
    plugin._client = SimpleNamespace(
        call=AsyncMock(side_effect=[late, late, {"ok": 1}])
    )
    assert await plugin._patient_call("status") == {"ok": 1}
    plugin._client.call = AsyncMock(side_effect=[late] * 4)
    with pytest.raises(TimeoutError):
        await plugin._patient_call("status")
    refused = VoiceError("lease_expired", "Voice connection expired")
    plugin._client.call = AsyncMock(side_effect=[refused, {"ok": 1}])
    with pytest.raises(VoiceError):
        await plugin._patient_call("heartbeat")
    assert plugin._client.call.await_count == 1


@pytest.mark.parametrize("service_state", ["off", "loading", "installing", "starting"])
def test_voice_reads_starting_until_the_microphone_is_live(service_state):
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.requested = True
    plugin._state = {"state": service_state}
    assert plugin.status()["state"] == "starting"
    assert plugin.status()["activity"]["stage"] == "starting"
    plugin.requested = False
    plugin._state = {"state": "off"}
    assert plugin.status()["state"] == "off"


@pytest.mark.asyncio
async def test_a_final_transcript_goes_straight_to_the_agent_turn(tmp_path, monkeypatch):
    """No intent or style gate: unaddressed words are delivered, with no ack."""
    from kollabor_voice.store import utc

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.requested = True
    plugin._lease = {"epoch": "e", "token": "t", "stream_id": "s"}
    plugin._client = SimpleNamespace(call=AsyncMock())
    now = time.time()
    plugin._pending = [
        {
            "event_id": "a",
            "text": "the build is red again",
            "started_at": utc(now - 2),
            "ended_at": utc(now - 1),
        }
    ]
    delivered = []

    async def deliver(data):
        delivered.append(data)
        plugin.requested = False
        return {"status": "queued"}

    plugin._deliver = deliver
    await asyncio.wait_for(plugin._deliver_loop(), 1)
    assert [d["message"] for d in delivered] == ["the build is red again"]
    voice = delivered[0]["voice"]
    assert voice["event_ids"] == ["a"]
    assert not {"classifier", "spoken_ack", "answered"} & set(voice)
    plugin._client.call.assert_awaited_once()  # the heartbeat; no spoken line
    assert plugin._client.call.await_args.args[0] == "heartbeat"
