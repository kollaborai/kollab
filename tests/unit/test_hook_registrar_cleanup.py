"""Regression coverage for #735: HookRegistrar cleanup must detach EventBus hooks.

unregister_all_hooks() used to pass a Hook object to
EventBus.unregister_hook(plugin_name, hook_name). The resulting TypeError was
caught and logged, the registrar cleared its local list, and every hook stayed
active in the EventBus registry — stale input hooks kept receiving events.
"""

import asyncio

from kollabor_events import EventBus

CUSTOM_EVENT = "registrar735:test"


class RecordingListener:
    def __init__(self):
        self.events = []

    async def on_event(self, data, event):
        self.events.append(data["value"])
        return data


def _registered_hooks(bus):
    return [hook for hooks in bus.hook_registry.hooks.values() for hook in hooks]


def test_unregister_all_hooks_detaches_every_event_bus_hook():
    async def scenario():
        bus = EventBus()
        listener = RecordingListener()
        from kollabor_tui.input.hook_registrar import HookRegistrar

        # The registrar builds its eight real hooks from identical handlers;
        # one listener keeps the probe simple while the bus still tracks the
        # same eight registrations the input handler ships.
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

        # The fix contract: cleanup removed what register added.
        assert _registered_hooks(bus) == []
        assert len(bus.hook_registry.hook_status) == 0

        # And nothing fires afterward.
        for hook in hooks:
            await bus.emit_with_hooks(hook.event_type, {"value": hook.name}, "test")
        assert listener.events == []

    asyncio.run(scenario())


def test_unregister_hook_takes_plugin_and_hook_name():
    """Pin the EventBus API the registrar must call (the #735 confusion)."""
    import inspect

    from kollabor_events import EventBus

    signature = inspect.signature(EventBus.unregister_hook)
    parameter_names = list(signature.parameters)
    assert parameter_names == ["self", "plugin_name", "hook_name"]
