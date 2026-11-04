"""Regression coverage for listener and hook registry lifecycles."""

import asyncio

from kollabor_events import EventBus, Hook
from kollabor_tui.display_tap import DisplayTap
from kollabor_tui.input.hook_registrar import HookRegistrar

CUSTOM_EVENT = "lifecycle:test"


def _run(coro):
    return asyncio.run(coro)


class RecordingListener:
    def __init__(self):
        self.events = []

    async def on_event(self, data, event):
        self.events.append(data["value"])
        return data


def _hook(name, callback, *, plugin_name="lifecycle_tests", event_type=CUSTOM_EVENT):
    return Hook(
        name=name,
        plugin_name=plugin_name,
        event_type=event_type,
        priority=50,
        callback=callback,
    )


def _registered_hooks(bus):
    return [hook for hooks in bus.hook_registry.hooks.values() for hook in hooks]


def test_display_tap_unsubscribe_restores_count_and_stops_delivery():
    tap = DisplayTap()
    baseline = tap.subscriber_count

    subscriber = tap.subscribe("client")
    assert tap.subscriber_count == baseline + 1

    tap.unsubscribe("client")
    assert tap.subscriber_count == baseline

    tap.publish({"type": "after-unsubscribe"})
    assert subscriber.empty()


def test_display_tap_replacement_detaches_old_queue_and_delivers_once():
    tap = DisplayTap()
    baseline = tap.subscriber_count
    old_queue = tap.subscribe("client")
    new_queue = tap.subscribe("client")

    assert tap.subscriber_count == baseline + 1
    tap.publish({"type": "replacement"})

    assert old_queue.empty()
    assert new_queue.get_nowait()["type"] == "replacement"
    assert new_queue.empty()
    tap.unsubscribe("client")
    assert tap.subscriber_count == baseline


def test_display_tap_repeated_subscribe_unsubscribe_does_not_grow():
    tap = DisplayTap()
    baseline = tap.subscriber_count

    for index in range(50):
        client_id = f"client-{index}"
        tap.subscribe(client_id)
        assert tap.subscriber_count == baseline + 1
        tap.unsubscribe(client_id)
        assert tap.subscriber_count == baseline


def test_event_bus_add_remove_restores_registry_and_stops_callback():
    async def scenario():
        bus = EventBus()
        listener = RecordingListener()
        baseline = len(bus.hook_registry.hook_status)
        hook = _hook("event", listener.on_event)

        assert await bus.register_hook(hook)
        assert len(bus.hook_registry.hook_status) == baseline + 1
        await bus.emit_with_hooks(CUSTOM_EVENT, {"value": "before"}, "test")
        assert listener.events == ["before"]

        assert await bus.unregister_hook(hook.plugin_name, hook.name)
        assert len(bus.hook_registry.hook_status) == baseline
        await bus.emit_with_hooks(CUSTOM_EVENT, {"value": "after"}, "test")
        assert listener.events == ["before"]

    _run(scenario())


def test_event_bus_replacement_removes_old_hook_and_fires_new_once():
    async def scenario():
        bus = EventBus()
        old_listener = RecordingListener()
        new_listener = RecordingListener()
        hook_key = ("lifecycle_tests", "replace")

        assert await bus.register_hook(_hook(hook_key[1], old_listener.on_event))
        assert await bus.register_hook(_hook(hook_key[1], new_listener.on_event))
        assert len(bus.hook_registry.hook_status) == 1
        assert bus.get_hooks_for_event(CUSTOM_EVENT) == 1

        await bus.emit_with_hooks(CUSTOM_EVENT, {"value": "new"}, "test")
        assert old_listener.events == []
        assert new_listener.events == ["new"]

    _run(scenario())


def test_event_bus_repeated_register_unregister_does_not_grow():
    async def scenario():
        bus = EventBus()
        listener = RecordingListener()
        baseline = len(bus.hook_registry.hook_status)

        for index in range(50):
            hook = _hook(f"cycle-{index}", listener.on_event)
            assert await bus.register_hook(hook)
            assert len(bus.hook_registry.hook_status) == baseline + 1
            assert await bus.unregister_hook(hook.plugin_name, hook.name)
            assert len(bus.hook_registry.hook_status) == baseline

    _run(scenario())


def test_hook_registrar_shutdown_detaches_all_hooks_and_nothing_fires_after():
    async def scenario():
        bus = EventBus()
        listener = RecordingListener()
        registrar = HookRegistrar(
            bus,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
        )

        await registrar.register_all_hooks()
        hooks = _registered_hooks(bus)
        assert len(hooks) == 8
        for hook in hooks:
            await bus.emit_with_hooks(hook.event_type, {"value": hook.name}, "test")
        assert len(listener.events) == 8

        listener.events.clear()
        await registrar.unregister_all_hooks()

        assert len(bus.hook_registry.hook_status) == 0
        assert _registered_hooks(bus) == []
        for hook in hooks:
            await bus.emit_with_hooks(hook.event_type, {"value": hook.name}, "test")
        assert listener.events == []

    _run(scenario())


def test_hook_registrar_repeated_lifecycle_cycles_do_not_grow_registry():
    async def scenario():
        bus = EventBus()
        listener = RecordingListener()
        registrar = HookRegistrar(
            bus,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
            listener.on_event,
        )
        baseline = len(bus.hook_registry.hook_status)

        for _ in range(10):
            await registrar.register_all_hooks()
            assert len(bus.hook_registry.hook_status) == baseline + 8
            await registrar.unregister_all_hooks()
            assert len(bus.hook_registry.hook_status) == baseline
            assert registrar._registered_hooks == []

    _run(scenario())
