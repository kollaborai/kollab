"""PRE_MESSAGE_INJECT answers with a phase envelope, not the context (#304)."""

import asyncio

from plugins.agent_orchestrator.message_injector import MessageInjector


class _Conversation:
    def __init__(self):
        self.messages = []

    def add_message(self, **message):
        self.messages.append(message)


class _Bus:
    """Answers like EventBus.emit_with_hooks: pre/main/post, data in main.final_data."""

    def __init__(self, hook=None):
        self.hook = hook
        self.events = []

    async def emit_with_hooks(self, event_type, data, source):
        self.events.append(event_type)
        final = self.hook(dict(data)) if self.hook else data
        return {"pre": {}, "main": {"final_data": final}, "post": {}, "cancelled": False}


def _injector(bus):
    conv = _Conversation()
    return MessageInjector(bus, conv), conv


def test_inject_reads_content_from_the_envelope():
    bus = _Bus()
    injector, conv = _injector(bus)
    assert asyncio.run(injector.inject("agent", "done", trigger_llm=False)) is True
    assert conv.messages == [{"role": "user", "content": "<sys_msg>\ndone\n</sys_msg>"}]


def test_inject_uses_what_a_hook_changed():
    bus = _Bus(hook=lambda ctx: {**ctx, "content": "rewritten"})
    injector, conv = _injector(bus)
    assert asyncio.run(injector.inject("agent", "done", trigger_llm=False)) is True
    assert conv.messages[0]["content"] == "rewritten"


def test_inject_honors_a_hook_block():
    bus = _Bus(hook=lambda ctx: {**ctx, "blocked": True})
    injector, conv = _injector(bus)
    assert asyncio.run(injector.inject("agent", "done", trigger_llm=False)) is False
    assert conv.messages == []
